// SPDX-License-Identifier: AGPL-3.0-or-later
//
// One decision, said once, about many rows.
//
// The rules ship deliberately conservative and claim **none** of the author's
// 415 real lines (§5.52). 86.9% of what they leave unclaimed is money moving
// between his own accounts (§5.79), and the only thing that makes the breakdown
// mean anything is marking those as transfers — 79 rows, and until this module
// existed, one click each. §7 listed that as a product gap rather than a
// convenience, and it is: the ability had a data layer since M2 and a single-row
// endpoint since M4, and neither of those is a way to do it.
//
// **The selection is a list of ids, never a filter.** The toolbar can fill
// itself from "everything this filter matches", and when it does it *fetches
// the ids* and holds them. A filter is a query; the set it matches can change
// between reading a count off the screen and a write landing, and a person who
// selected 79 rows must not be able to write 81. The server takes the same
// view and refuses the whole request if one id has gone stale.
//
// **What cannot be undone is said before the click, not after.** Naming a
// category over a line somebody already named by hand destroys a decision
// `archive/` cannot rebuild (§5.49) — withdrawing an override afterwards hands
// the line to the *rules*, not back to the category it used to carry. Every row
// on screen already reports `category_decided_by`, so the count of decisions
// about to be replaced is known here, and the button says it.
//
// There is no confirmation step and no 409, for the reason `routes/
// transactions.py` gives: giving a reversible act a ceremony is mistaking
// ritual for safety. The irreversible part is the sentence on the button.

import { button, clear, el, option, updateManyCategories } from './api.js';

const COPY = {
  lead: (count) => `${count} line(s) selected`,
  selectAll: (count) => `Select all ${count} matching`,
  selectPage: 'Select every line on this page',
  clear: 'Clear selection',
  pick: 'Say they are',
  letRules: 'Let the rules decide',
  apply: (count, what) => (what === null
    ? `Withdraw ${count} decision(s)`
    : `Mark ${count} line(s) as ${what}`),
  // Said on the control, before it is pressed. The count comes from what the
  // table already knows about each row, so this costs no request and cannot be
  // out of date with the rows a person is looking at.
  replaces: (count) => `${count} of these carry a category you set by hand. Applying replaces `
    + 'it, and withdrawing afterwards hands the line to the rules rather than back to that '
    + 'category.',
  tooMany: (count, cap) => `This filter matches ${count} line(s), which is more than the ${cap} `
    + 'one request may name. Narrow it — by month, by direction, or by searching — and the '
    + 'button will offer the rest.',
  failed: 'Nothing was changed.',
  working: 'Working…',
};

/** The ceiling the server enforces, spelled here because a wire limit has two
 *  sides. `schemas.MAX_BULK_TRANSACTIONS` is the same number and is itself
 *  `repo.MAX_PAGE_SIZE`; a request over it is a 422 rather than a truncation,
 *  and this is what lets the page say *why* instead of silently offering less. */
const MAX_SELECTION = 500;

/**
 * The bulk toolbar.
 *
 * `options.groups` are the category groups the filter panel already built, so
 * the ids a person can choose in bulk are the same ones they can choose on a
 * row. `options.matched` reports how many the current filter matched, and
 * `options.idsForFilter` fetches those ids when asked. `options.onApplied`
 * fires after a write so the panel and the figures above it re-read.
 */
export function createBulkBar(options) {
  const chosen = new Map();
  let target = '';
  let busy = false;

  const box = el('div', 'bulk');
  box.hidden = true;
  const lead = el('span', 'bulk__lead');
  const picker = el('select', 'bulk__pick control__field');
  const noteNode = el('p', 'bulk__note');
  const actions = el('div', 'bulk__actions');
  const label = el('label', 'bulk__field');
  label.appendChild(el('span', 'control__key', COPY.pick));
  label.appendChild(picker);

  /** How many of the selected rows carry a decision this would overwrite. */
  function replacing() {
    let count = 0;
    for (const row of chosen.values()) {
      if (
        (row.category_decided_by === 'override'
          || row.category_decided_by === 'agent'
          || row.category_decided_by === 'learned')
        && row.category_id !== (target || null)
      ) {
        count += 1;
      }
    }
    return count;
  }

  function paint() {
    box.hidden = chosen.size === 0;
    if (chosen.size === 0) {
      return;
    }
    clear(lead);
    lead.appendChild(el('strong', '', COPY.lead(chosen.size)));

    clear(actions);
    const what = target || null;
    const apply = button(
      'btn',
      busy ? COPY.working : COPY.apply(chosen.size, what === null ? null : target),
      () => run(what),
    );
    apply.disabled = busy;
    actions.appendChild(apply);

    const matched = options.matched();
    if (matched > chosen.size) {
      if (matched <= MAX_SELECTION) {
        actions.appendChild(button('btn btn--quiet', COPY.selectAll(matched), selectMatching));
      } else {
        // Said rather than silently offered as "select 500 of them", which is
        // the shape that makes a person think they have the whole set.
        actions.appendChild(el('span', 'bulk__cap muted', COPY.tooMany(matched, MAX_SELECTION)));
      }
    }
    actions.appendChild(button('btn btn--quiet', COPY.clear, reset));

    clear(noteNode);
    const replaced = replacing();
    noteNode.hidden = replaced === 0;
    if (replaced) {
      noteNode.appendChild(el('span', '', COPY.replaces(replaced)));
    }
  }

  function reset() {
    chosen.clear();
    if (options.onSelectionChange) {
      options.onSelectionChange();
    }
    paint();
  }

  async function selectMatching() {
    busy = true;
    paint();
    try {
      for (const row of await options.idsForFilter()) {
        chosen.set(row.txn_id, row);
      }
    } catch (error) {
      // Fetching the ids is still part of the operation the person asked for.
      // Leaving the existing selection in place is safe; leaving the failure
      // unreported makes the click look successful while doing nothing.
      options.onApplied(`${error.message || ''} ${COPY.failed}`.trim(), 'fail');
    } finally {
      busy = false;
    }
    if (options.onSelectionChange) {
      options.onSelectionChange();
    }
    paint();
  }

  async function run(what) {
    busy = true;
    paint();
    try {
      const result = await updateManyCategories([...chosen.keys()], what);
      chosen.clear();
      // The server's own sentence. It is the only line that knows how many
      // decisions were replaced and how many lines left the headline figures.
      options.onApplied(result.summary, 'ok');
    } catch (error) {
      options.onApplied(`${error.message || ''} ${COPY.failed}`.trim(), 'fail');
    } finally {
      busy = false;
      paint();
    }
  }

  return {
    node: box,
    /** Build the picker once the taxonomy has arrived. */
    fill(groups) {
      clear(picker);
      picker.appendChild(option('', COPY.letRules));
      for (const group of groups) {
        const holder = el('optgroup');
        holder.label = group.label;
        for (const id of group.ids) {
          holder.appendChild(option(id, id));
        }
        picker.appendChild(holder);
      }
      picker.value = target;
      // A transfer is what this exists for, so it is preselected when the
      // taxonomy offers it -- without becoming the only thing the control can
      // say, which would be a second definition of "bulk" meaning "transfer".
      if (!target && [...picker.options].some((option) => option.value === 'transfer')) {
        picker.value = 'transfer';
        target = 'transfer';
      }
      paint();
    },
    has: (txnId) => chosen.has(txnId),
    toggle(row, on) {
      if (on) {
        chosen.set(row.txn_id, row);
      } else {
        chosen.delete(row.txn_id);
      }
      paint();
    },
    reset,
    render(container) {
      clear(container);
      box.appendChild(lead);
      box.appendChild(label);
      box.appendChild(actions);
      box.appendChild(noteNode);
      container.appendChild(box);
      picker.addEventListener('change', () => {
        target = picker.value;
        paint();
      });
      paint();
    },
  };
}
