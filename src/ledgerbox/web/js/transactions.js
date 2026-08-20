// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The transaction table: what came back, and what it adds up to.
//
// This is the four figures at the top of the page itemised. It sits between the
// drop zone and the statement list on purpose — the figures are the answer,
// this is that answer line by line, and the statement list below is where each
// line came from (§2.5's M4).
//
// **Every control issues a request.** Filtering, sorting and paging are done by
// the database and stay there; nothing is fetched whole and sliced in the
// browser. Any change to the question also returns to the first page, because
// an offset measured against the previous result points past the end of the
// next one. The controls themselves live in `transaction-filters.js` and one
// row in `transaction-row.js`, both split off at the 400-line signal.
//
// **The figures over this table are the bank leg** — what the matched lines did
// to this account's balance, transfers included. The four at the top of the
// page are measured on the income and expense legs, drop anything flagged as a
// transfer, and never see the opening entry. They do not agree and are not
// supposed to. Every label here says "bank leg", the panel note says it in a
// sentence, and the server's own summary says it a third time in its own words:
// two cashflow figures that merely looked alike cost this project a block-level
// check to settle (§5.45), so this third one arrives already labelled.
//
// Nothing on this page is counted here. After a category is recorded the row is
// re-read from the server's reply and the figures at the top are re-read from
// the server; a number the browser adjusted is a number that drifts.

import { clear, el, fetchTransactions, formatMinor, button, isOffline } from './api.js';
import { CONNECTION_COPY } from './connection.js';
import { localized, t } from './i18n.js';
import { createBulkBar } from './transaction-bulk.js';
import { createFilters } from './transaction-filters.js';
import { createRow, headerRow } from './transaction-row.js';
import { transactionResultStatus } from './transaction-status.js';

export { transactionResultStatus } from './transaction-status.js';

// Looked up as read. The space between a pair of these is at the reading site:
// keys are normalised, so a leading one would be trimmed off the page with it.
const COPY = localized({
  nothingYet: 'No transactions yet.',
  nothingYetRest: 'A statement is booked only if it reconciles against the totals printed on '
    + 'it; anything that did not is in the list below, archived and unbooked.',
  noMatch: 'No transaction matches this filter.',
  noMatchRest: 'Nothing has been deleted — changing or clearing a control above brings the '
    + 'rows back.',
  refusedMonth: 'A statement that was refused has no transactions at all, so filtering to its '
    + 'month correctly shows none.',
  pastEnd: 'This page is past the end of the result. Previous goes back to rows that exist.',
  listFailed: 'The transactions could not be read.',
  legend: 'Measured on this account’s own leg: what the matched lines did to the balance, '
    + 'transfers included.',
});

// The three figures this table reports, named so that not one of them can be
// read as the same quantity as the In / Out / Net / Balance at the top. The
// names are looked up in `renderTotals`: translated here, in an array built at
// import time, they would stay English for the life of the page.
const FIGURES = [
  ['Bank leg in', 'bank_in_minor'],
  ['Bank leg out', 'bank_out_minor'],
  ['Bank leg net', 'bank_net_minor'],
];

// Sent explicitly rather than left to the server's default, so the pager can do
// its arithmetic before the first response arrives.
//
// It no longer spells the value `repo.DEFAULT_PAGE_SIZE` spells, and that is the
// point rather than a drift. Fifty rows is a page you scroll past rather than
// read; this page asks for twenty. The server's fifty is what a caller who names
// no limit gets, which is a different caller asking a different question, and
// not this page's to decide.
//
// Nothing has to be kept in step, because neither value is ever inferred from
// the other. This client always sends its own `limit`, so the server's default
// is never what it receives; the response echoes back the `limit` that was
// actually used, and `renderPager` disables Next against that echo rather than
// against the constant. The only place the number crosses the wire is the
// request, and the request carries it. `step()` reads the constant because it
// moves the offset *before* there is a response to read it off.
const PAGE_SIZE = 20;

function figureNode(label, value) {
  const box = el('div', 'txn-figure');
  box.appendChild(el('span', 'txn-figure__key', label));
  box.appendChild(el('span', 'txn-figure__value num money', value));
  return box;
}

/**
 * The transactions panel.
 *
 * `root` is the section; everything inside it is found by its `data-txn` name
 * rather than by a dozen ids passed in. `onChange` fires after a category is
 * recorded, so the figures at the top of the page and the row counts in the
 * diagnostics are re-read from the server — naming the `transfer` category
 * moves a line out of those figures, and naming any other moves it back in.
 */
export function createTransactionsPanel(options) {
  const root = options.root;
  const countsNode = options.countsNode;
  const onChange = options.onChange;
  // The page-wide window, read at the moment a request is built rather than
  // copied in, so this panel and the charts above it cannot be a beat apart.
  const span = options.span || (() => ({}));
  const rowsNode = root.querySelector('[data-txn="rows"]');
  const totalsNode = root.querySelector('[data-txn="totals"]');
  const statusNode = root.querySelector('[data-txn="status"]');
  const pagerNode = root.querySelector('[data-txn="pager"]');
  const noticeNode = root.querySelector('[data-txn="notice"]');

  let offset = 0;
  // What the last response said, so the bulk bar can ask how many the filter
  // matched without re-issuing the query the number came from.
  let last = null;
  // Which request the panel is waiting for. A response that is no longer the
  // latest is dropped rather than rendered: two controls changed in quick
  // succession would otherwise let the slower, older answer paint over the
  // newer one, and the figures over the table would then be describing a
  // filter nobody has selected.
  let inFlight = 0;

  function announce(message) {
    if (statusNode && statusNode.textContent !== message) {
      statusNode.textContent = message;
    }
  }

  const filters = createFilters({
    root,
    onChange: () => {
      offset = 0;
      // A selection made under one filter must not survive into another. The
      // ids would still be valid and the person would still be looking at a
      // count -- of rows they can no longer see. Changing the question clears
      // the answer.
      bulk.reset();
      load();
    },
  });

  /**
   * The bulk toolbar. It holds ids, never a filter — see its own header — and
   * `idsForFilter` is what turns "everything this matches" into a list, by
   * asking for the ids in one read the way the page asks for anything else.
   */
  const bulk = createBulkBar({
    groups: () => filters.groups(),
    matched: () => (last ? last.totals.matched : 0),
    async idsForFilter() {
      const query = filters.query();
      const window = span();
      query.since = window.since;
      query.until = window.until;
      query.limit = Math.min(last ? last.totals.matched : 0, 500);
      query.offset = 0;
      const page = await fetchTransactions(query);
      return page.items;
    },
    onSelectionChange: () => renderRows(last),
    onApplied(summary, kind) {
      notice(summary, kind);
      // Everything a bulk decision moves is re-read rather than adjusted: the
      // rows, the figures over them, and — through `onChange` — the four at the
      // top of the page and both pictures under them. A number the browser
      // adjusted is a number that drifts.
      load();
      if (onChange) {
        onChange();
      }
    },
  });

  function renderCounts(data) {
    if (!countsNode) {
      return;
    }
    clear(countsNode);
    if (!data) {
      return;
    }
    const matched = data.totals.matched;
    countsNode.appendChild(el('span', 'count', t('{count} line(s) match', { count: matched })));
    if (filters.isFiltered()) {
      countsNode.appendChild(el('span', '', t('filtered')));
    }
  }

  function renderTotals(data) {
    clear(totalsNode);
    const grid = el('div', 'txn-figures');
    for (const [label, key] of FIGURES) {
      grid.appendChild(figureNode(t(label), formatMinor(data.totals[key])));
    }
    totalsNode.appendChild(grid);
    totalsNode.appendChild(el('p', 'txn-legend muted', COPY.legend));
    // The server's own sentence over the table, verbatim: the service reporting
    // what it measured, not this page's wording to translate.
    if (data.summary) {
      totalsNode.appendChild(el('p', 'txn-summary', data.summary));
    }
  }

  // Three different facts, and they read differently: nothing has ever been
  // booked, this filter selects nothing, or the pager has run off the end.
  function emptyNode(data) {
    const box = el('p', 'empty');
    if (data.totals.matched > 0) {
      box.appendChild(el('strong', '', COPY.pastEnd));
      return box;
    }
    if (!filters.isFiltered()) {
      box.appendChild(el('strong', '', COPY.nothingYet));
      box.appendChild(el('span', '', ` ${COPY.nothingYetRest}`));
      return box;
    }
    box.appendChild(el('strong', '', COPY.noMatch));
    box.appendChild(el('span', '', ` ${COPY.noMatchRest}`));
    if (filters.monthChosen()) {
      box.appendChild(el('span', '', ` ${COPY.refusedMonth}`));
    }
    return box;
  }

  function renderRows(data) {
    if (!data) {
      return;
    }
    clear(rowsNode);
    if (data.items.length === 0) {
      rowsNode.appendChild(emptyNode(data));
      return;
    }
    const context = {
      bulk,
      // Whether the header switch starts pressed: every row on this page is
      // already selected. Derived rather than remembered, so a page arrived at
      // by "select all matching" or by paging shows the switch in the state the
      // rows are actually in.
      allPicked: data.items.every((txn) => bulk.has(txn.txn_id)),
      // **The table is not rebuilt for this.** The switch lives in the header
      // of the table it would rebuild, so re-rendering destroys the control
      // inside its own handler -- the element is detached mid-click, and the
      // fresh header comes back unchecked while every row under it is selected.
      // The boxes are set in place instead.
      onPickAll: (on) => {
        for (const txn of data.items) {
          bulk.toggle(txn, on);
        }
        for (const box of rowsNode.querySelectorAll('tbody .txn__pick')) {
          box.checked = on;
        }
      },
      groups: filters.groups(),
      // The palette step for a category id, from the same list the picker and
      // the filter are built out of. It is a lookup and not a rule: the one
      // place that decides which category takes which step is the module that
      // fetched the taxonomy.
      sliceOf: filters.sliceOf,
      selectsOnDecision: filters.selectsOnDecision,
      onReread: load,
      onChanged: onChange,
    };
    const table = el('table', 'txn-table');
    const head = el('thead');
    head.appendChild(headerRow(context));
    table.appendChild(head);
    const body = el('tbody');
    for (const txn of data.items) {
      for (const part of createRow(txn, context)) {
        body.appendChild(part);
      }
    }
    table.appendChild(body);
    // Its own scroller: a bank descriptor is long and third-party text, and a
    // page that scrolls sideways because of one line is a page one line breaks.
    const scroller = el('div', 'txn-scroll');
    scroller.appendChild(table);
    rowsNode.appendChild(scroller);
  }

  function renderPager(data) {
    clear(pagerNode);
    const matched = data.totals.matched;
    const shown = data.items.length;
    const first = data.offset + 1;
    const range = shown > 0
      ? t('Showing {first}–{last} of {matched}', { first, last: data.offset + shown, matched })
      : t('Showing none of {matched}', { matched });
    const previous = button('btn btn--quiet', t('Previous'), () => step(-1));
    const next = button('btn btn--quiet', t('Next'), () => step(1));
    previous.disabled = data.offset <= 0;
    next.disabled = data.offset + data.limit >= matched;
    pagerNode.appendChild(previous);
    pagerNode.appendChild(el('span', 'pager__range', range));
    pagerNode.appendChild(next);
  }

  function step(direction) {
    offset = Math.max(0, offset + direction * PAGE_SIZE);
    load();
  }

  /**
   * A panel-wide line that is not the table's own. Empty clears it.
   *
   * `kind` is `'fail'` for a problem and `'ok'` for the server's account of a
   * bulk decision — which is not a failure and must not be painted as one, but
   * belongs in the same place for the same reason: it is about the whole panel
   * rather than about a row.
   */
  function notice(message, kind) {
    if (!noticeNode) {
      return;
    }
    clear(noticeNode);
    noticeNode.hidden = !message;
    noticeNode.className = kind === 'ok' ? 'notice notice--ok' : 'notice notice--fail';
    if (message) {
      noticeNode.appendChild(el('p', 'notice__text', message));
    }
  }

  /** A dead server is the page's news, not this table's; anything else is. */
  function failed(error) {
    const offline = isOffline(error);
    const message = offline ? CONNECTION_COPY.panel : (error.message || COPY.listFailed);
    renderCounts(null);
    clear(totalsNode);
    clear(pagerNode);
    clear(rowsNode);
    rowsNode.appendChild(el('p', offline ? 'empty' : 'empty empty--fail', message));
    announce(offline
      ? t('Transaction results unavailable while ledgerbox is not answering.')
      : t('Transaction results could not be updated.'));
  }

  /** One page, from one request. Called by every control on the panel. */
  async function load() {
    inFlight += 1;
    const mine = inFlight;
    rowsNode.setAttribute('aria-busy', 'true');
    try {
      const query = filters.query();
      const window = span();
      query.since = window.since;
      query.until = window.until;
      query.limit = PAGE_SIZE;
      query.offset = offset;
      const data = await fetchTransactions(query);
      if (mine !== inFlight) {
        return;
      }
      last = data;
      renderCounts(data);
      renderTotals(data);
      renderRows(data);
      renderPager(data);
      announce(transactionResultStatus(data));
    } catch (error) {
      if (mine !== inFlight) {
        return;
      }
      // The server's sentence, not a friendlier one invented here.
      failed(error);
    } finally {
      if (mine === inFlight) {
        rowsNode.removeAttribute('aria-busy');
      }
    }
  }

  /**
   * Re-read everything this panel shows, option lists included. Called at boot
   * and whenever something outside it changed the ledger: an ingest adds months
   * and creates the category rows, a deletion takes a month away.
   */
  async function refresh() {
    const problems = [];
    await filters.loadOptions(problems);
    notice(problems.join(' '), 'fail');
    // The picker offers exactly the ids a row's own selector offers, because
    // both are built from the one list the filter panel fetched. Two modules
    // grouping one taxonomy their own way is the shape §5.29 exists to name.
    bulk.fill(filters.groups());
    return load();
  }

  // Between the figures and the rows: it is about the rows, and it appears only
  // when something is selected, so the table does not carry an empty strip on
  // every load.
  const bulkHost = el('div', 'bulk-host');
  rowsNode.parentNode.insertBefore(bulkHost, rowsNode);
  bulk.render(bulkHost);

  return { refresh, showUnclassified: filters.showUnclassified };
}
