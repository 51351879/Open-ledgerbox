// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The controls over the transaction table: the question, not the answer.
//
// Split out of `transactions.js` at the 400-line signal `docs/EXECUTION_PLAN.md`
// §1.3 puts there — the third time this codebase has met that line and the
// third time the seam was already in the file (§5.66). This half owns what a
// person asked for: the seven controls, the option lists that only the server
// can supply, and the query object those add up to. The other half owns what
// came back.
//
// Nothing here filters anything. Every control change produces a new request,
// because a browser that holds the ledger in order to slice it ends up with a
// second definition of every question it slices by.
//
// Two option lists are read from the server rather than written here. The
// months come from `/api/statements`, which is the authority on which months
// exist — including a month whose statement was refused, which has no
// transactions at all, so filtering to it correctly shows none. The categories
// come from `/api/categories`, the mirror of the shipped rules file; the page
// carrying its own copy of twenty-four ids is the two-definitions shape §5.29
// exists to name, and the copy is always the one that goes stale.

import { clear, el, fetchCategories, fetchStatements, isOffline, option } from './api.js';
// One definition of which colour a category takes, shared with the donut.
import { tonesOf } from './category-tones.js';

// The server's sentinel for "nothing claimed this line", spelled here because a
// wire value has to be spelled on both sides of a wire. The parentheses are not
// decoration and must not be tidied away: `repo.NO_CATEGORY` documents that they
// put this value outside the pattern a category id can match, so no rules file
// can ever declare a category that this filter would silently answer for.
const NO_CATEGORY = '(none)';

const COPY = {
  anyMonth: 'Any month',
  anyCategory: 'Any category',
  // Not a category and never rendered as one: there is no `uncategorized` row
  // to select, because a descriptor no rule claimed is stored as NULL on
  // purpose (§5.38).
  noCategory: 'Nothing claimed this',
  monthsFailed: 'The month filter is empty as a result; every other control still works.',
  categoriesFailed: 'No category can be chosen or filtered for until that succeeds.',
};

// The controls, by the `data-txn` name the markup gives them. The two that are
// not plain strings on the wire — the transfer tri-state and the sort direction
// — are translated in `query()`, which is the only place the wire shape is
// decided.
const CONTROLS = ['q', 'month', 'category', 'transfer', 'direction', 'sort', 'order'];

const DEFAULTS = {
  q: '', month: '', category: '', transfer: '', direction: '',
  sort: 'date', order: 'desc',
};

// Income first, transfer last, and the labels the page shows for the server's
// `kind`. Grouped once and handed to both the filter select and every row's
// picker, so a category a person can filter by is exactly one they can set.
const KINDS = [['income', 'Income'], ['expense', 'Expense'], ['transfer', 'Transfer']];

const TYPING_PAUSE = 250;

function named(node, name) {
  node.setAttribute('data-txn', name);
  return node;
}

function labelled(label, node, wide = false) {
  const holder = el('label', wide ? 'control control--wide' : 'control');
  holder.appendChild(el('span', 'control__key', label));
  holder.appendChild(node);
  return holder;
}

function selectControl(name, label, choices) {
  const select = named(el('select', 'control__field'), name);
  for (const [value, text] of choices) {
    select.appendChild(option(value, text));
  }
  return labelled(label, select);
}

/**
 * Build the controls inside the section's one empty shell.
 *
 * This module already owned their values, listeners, option loading and query
 * shape; leaving their markup in `index.html` gave one component two owners and
 * put the page shell one line below its 400-line guard before proposal UI even
 * existed. Native labels and selects are retained exactly -- this is an
 * ownership move, not a new interaction.
 */
export function renderFilterControls(root) {
  const host = root.querySelector('[data-txn="controls"]');
  if (!host) {
    throw new Error('transactions panel is missing its data-txn="controls" shell');
  }
  clear(host);
  host.setAttribute('role', 'group');
  host.setAttribute('aria-label', 'Filter and sort the transactions');

  const search = named(el('input', 'control__field'), 'q');
  search.type = 'search';
  search.maxLength = 200;
  search.placeholder = 'part of a description';
  host.appendChild(labelled("Search the bank's line", search, true));

  // Statement month narrows this table only. The page-wide date range asks a
  // different question -- when money moved -- and is owned by the header.
  host.appendChild(selectControl('month', 'Month', [['', COPY.anyMonth]]));

  // `(none)` is a server sentinel, not a category; its punctuation keeps it
  // outside the grammar of every category id the rules may declare.
  host.appendChild(selectControl('category', 'Category', [
    ['', COPY.anyCategory], [NO_CATEGORY, COPY.noCategory],
  ]));
  host.appendChild(selectControl('transfer', 'Transfers', [
    ['', 'Included'], ['true', 'Only transfers'], ['false', 'Excluding transfers'],
  ]));
  host.appendChild(selectControl('direction', 'Direction', [
    ['', 'Either way'], ['in', 'Into the account'], ['out', 'Out of the account'],
  ]));
  host.appendChild(selectControl('sort', 'Sort by', [
    ['date', 'Date'], ['amount', 'Amount'], ['description', 'Description'],
    ['category', 'Category'], ['month', 'Statement month'],
  ]));
  host.appendChild(selectControl('order', 'Order', [
    ['desc', 'Descending'], ['asc', 'Ascending'],
  ]));
  const reset = named(el('button', 'btn btn--quiet control__reset', 'Clear filters'), 'reset');
  reset.type = 'button';
  host.appendChild(reset);
  return host;
}

/** `[{label, ids}]` for the kinds this ledger actually has categories for. */
function groupsOf(categories) {
  return KINDS
    .map(([kind, label]) => ({
      label,
      ids: (categories || []).filter((row) => row.kind === kind).map((row) => row.id),
    }))
    .filter((group) => group.ids.length > 0);
}

/**
 * Fills a `<select>` from data and keeps the current choice only if it survived.
 *
 * The second part is the point: a control still showing a month whose statement
 * was just deleted would be displaying a filter the request no longer carries.
 */
function refill(select, chosen, fixed, groups) {
  clear(select);
  for (const [value, label] of fixed) {
    select.appendChild(option(value, label));
  }
  for (const group of groups) {
    const box = el('optgroup');
    box.label = group.label;
    for (const id of group.ids) {
      box.appendChild(option(id, id));
    }
    select.appendChild(box);
  }
  select.value = chosen;
  if (select.value !== chosen) {
    select.value = '';
  }
}

/**
 * The control bar. `onChange` fires whenever the question changes — the panel
 * answers it by going back to the first page and issuing one request.
 */
export function createFilters(options) {
  const root = options.root;
  const onChange = options.onChange;
  renderFilterControls(root);
  const control = {};
  for (const name of CONTROLS) {
    control[name] = root.querySelector(`[data-txn="${name}"]`);
  }

  const state = Object.assign({}, DEFAULTS);
  let groups = [];
  let tones = new Map();
  let typingTimer = 0;

  function read() {
    for (const name of CONTROLS) {
      if (control[name]) {
        state[name] = control[name].value;
      }
    }
  }

  function changed() {
    read();
    onChange();
  }

  for (const name of CONTROLS) {
    const node = control[name];
    if (!node) {
      continue;
    }
    if (name === 'q') {
      // Debounced, so typing a merchant name is one request at the end rather
      // than one per keystroke. `change` still fires on blur and on Enter.
      node.addEventListener('input', () => {
        window.clearTimeout(typingTimer);
        typingTimer = window.setTimeout(changed, TYPING_PAUSE);
      });
    }
    node.addEventListener('change', () => {
      window.clearTimeout(typingTimer);
      changed();
    });
  }

  const resetNode = root.querySelector('[data-txn="reset"]');
  if (resetNode) {
    resetNode.addEventListener('click', () => {
      for (const name of CONTROLS) {
        if (control[name]) {
          control[name].value = DEFAULTS[name];
        }
      }
      changed();
    });
  }

  async function loadMonths(problems) {
    let rows = null;
    try {
      rows = await fetchStatements();
    } catch (error) {
      refill(control.month, '', [['', COPY.anyMonth]], []);
      state.month = '';
      // The server's own sentence first, then what it cost this panel -- unless
      // the server is simply not there, which the indicator in the masthead has
      // already said once for the whole page and the table's own placeholder
      // has said again. A third copy of it, inside a notice about a filter, is
      // how one dead process came to speak six times.
      if (!isOffline(error)) {
        problems.push(`${error.message} ${COPY.monthsFailed}`);
      }
      return;
    }
    const months = [];
    for (const row of rows) {
      if (row.statement_month && months.indexOf(row.statement_month) === -1) {
        months.push(row.statement_month);
      }
    }
    refill(control.month, state.month, [['', COPY.anyMonth]], [{ label: 'Month', ids: months }]);
    state.month = control.month.value;
  }

  async function loadCategories(problems) {
    let rows = [];
    try {
      rows = await fetchCategories();
    } catch (error) {
      if (!isOffline(error)) {
        problems.push(`${error.message} ${COPY.categoriesFailed}`);
      }
    }
    // Empty before the first ingest: the rows are created when a statement is
    // booked, not seeded by a migration. That is an absence, not a failure.
    groups = groupsOf(rows);
    // Off the server's order rather than off `groups`, which regroups the same
    // rows for display. A colour that moved because the *display* order changed
    // would be a colour that means nothing.
    tones = tonesOf(rows);
    refill(
      control.category,
      state.category,
      [['', COPY.anyCategory], [NO_CATEGORY, COPY.noCategory]],
      groups,
    );
    state.category = control.category.value;
  }

  return {
    /** Open the actionable omission view without leaving stale filters behind. */
    showUnclassified() {
      for (const name of CONTROLS) {
        if (control[name]) {
          control[name].value = DEFAULTS[name];
        }
      }
      control.category.value = NO_CATEGORY;
      changed();
    },

    /** The filter half of the request. The panel adds `limit` and `offset`. */
    query() {
      return {
        q: state.q.trim(),
        month: state.month,
        category: state.category,
        // '' is "either", which is the absence of the parameter and not
        // `transfer=false`, which means something else entirely.
        transfer: state.transfer === '' ? null : state.transfer === 'true',
        direction: state.direction,
        sort: state.sort,
        descending: state.order !== 'asc',
      };
    },

    /** True when anything narrows the result — which changes the empty state. */
    isFiltered() {
      return Boolean(state.q.trim() || state.month || state.category
        || state.transfer || state.direction);
    },

    /**
     * True when the filter selects on the very thing a person can change from a
     * row. One recorded decision can then move a row out of the result the
     * figures above it were measured over, and the row says so rather than the
     * page silently recomputing anything.
     */
    selectsOnDecision() {
      return Boolean(state.category || state.transfer);
    },

    monthChosen() {
      return Boolean(state.month);
    },

    groups() {
      return groups;
    },

    /**
     * The palette step class for one category id, or `''` for anything this
     * ledger's taxonomy does not contain — which includes the one value that
     * must never be coloured, because `category_id: null` is not a category and
     * is never looked up here.
     */
    sliceOf(categoryId) {
      return tones.get(categoryId) || '';
    },

    /** Both option lists, re-read. `problems` collects the server's sentences. */
    async loadOptions(problems) {
      await Promise.all([loadMonths(problems), loadCategories(problems)]);
      read();
    },
  };
}
