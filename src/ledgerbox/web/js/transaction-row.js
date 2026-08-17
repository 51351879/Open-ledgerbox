// SPDX-License-Identifier: AGPL-3.0-or-later
//
// One statement line, and the one decision a person can record about it.
//
// Split out of `transactions.js` at the 400-line signal `docs/EXECUTION_PLAN.md`
// §1.3 puts there, the same way `deletion-plan.js` came out of `statements.js`
// (§5.66). The seam was already in the file: that half answers "which lines am
// I looking at" — controls, paging, the figures over the whole match — and this
// half answers "what is this line, and what do I say it is". They share nothing
// but `api.js`.
//
// Three things here are load-bearing and none of them is decoration.
//
// **`category_id: null` is an em dash and never a named bucket.** The
// predecessor's worst defect was a wrong rule that doubled as a silent
// catch-all, so its "Other" pile held $33.78 and the breakdown looked perfect
// (§5.38). 285 of the author's 415 real lines are null. `category_decided_by`
// is what separates the three cases — `none` nothing claimed it, `rule` a rule
// did, `override` a person did — and all three are said in words, not colour.
//
// **The transfer badge and the category cell are not derived from each other.**
// A transfer the rules flagged carries a NULL category; one a person marked
// carries the `transfer` category. Computing either cell from the other would
// be wrong in both directions, so each reads its own field.
//
// **A control never claims a value the server refused.** If the PATCH fails the
// select is put back to the effective value that is actually stored, and the
// server's own sentence is shown underneath.
//
// **Colour is added to two cells and is the only signal in neither.** A category
// chip is tinted with that category's step of the palette the donut uses, and
// the id is printed inside the chip; an amount is green or red, and
// `formatMinor` has already put the sign in the glyph. The em dash takes no
// tint at all, because it is the absence of a category and not one — the same
// reason the donut hatches its unclaimed slice instead of giving it a hue.

import {
  button, clear, el, formatMinor, join, option, updateTransactionCategory,
} from './api.js';

// Every sentence a person reads in a row, in one place, because rule 11 binds
// them alike: none may read stronger than the evidence behind it.
const COPY = {
  letRules: 'Let the rules decide',
  noCategory: 'No category: nothing claimed this line.',
  byRule: 'set by a rule',
  byPerson: 'set by you',
  byAgent: 'set by Agent',
  // A learned answer is machine-applied from an earlier decision. It never
  // wears "set by you": the person decided the merchant once, not this line.
  byLearned: 'set by your earlier answer',
  transfer: 'Transfer',
  transferBy: 'marked by you',
  transferByAgent: 'marked by Agent',
  transferByLearned: 'marked by your earlier answer',
  patchFailed: 'The category could not be recorded.',
  // Short on purpose: it would otherwise be repeated on twenty rows. The
  // server's own sentence about why appears once, above the table.
  noCategories: 'No categories to choose from.',
  // Said only under a filter that selects on the thing just changed. The row
  // itself is re-read from the server's reply; the count and the figures over
  // the table were measured before this decision and nothing here recomputes
  // them, because a number the browser adjusted is a number that drifts.
  stale: 'This filter selects on category or transfer, so the count and the figures above the '
    + 'table were measured before this change.',
  reread: 'Re-read the table',
  pickAll: 'Select every line on this page',
  pick: (date, amount) => `Select the line dated ${date} for ${amount}`,
};

// Fixed here so the header and the body cannot drift apart: both are built in
// this module, from this list, and `colSpan` on the notice row reads its length.
const COLUMNS = [
  // First, and its header is a switch for the page rather than a word: a
  // checkbox column whose header is a label is a column you cannot use without
  // twenty clicks, which is the thing the bulk toolbar exists to remove.
  ['', 'txn__c-pick'],
  ['Date', 'txn__c-date'],
  ["Description, as the bank printed it", 'txn__c-desc'],
  ['Transfer', 'txn__c-transfer'],
  ['Category', 'txn__c-cat'],
  ['Amount, bank leg', 'txn__c-amount'],
  ['Change category', 'txn__c-set'],
];

export const COLUMN_COUNT = COLUMNS.length;

/**
 * The `<thead>` row. Column labels say which leg the amount is measured on.
 *
 * `context.onPickAll` makes the first header a checkbox that takes every row on
 * this page. It stays a `<th>` with an accessible name, because a control
 * whose only label is its position is a control a screen reader cannot offer.
 */
export function headerRow(context) {
  const row = el('tr');
  for (const [label, className] of COLUMNS) {
    const head = el('th', className, label);
    head.scope = 'col';
    row.appendChild(head);
  }
  if (context && context.onPickAll) {
    const box = el('input');
    box.type = 'checkbox';
    box.className = 'txn__pick';
    box.checked = Boolean(context.allPicked);
    box.setAttribute('aria-label', COPY.pickAll);
    box.addEventListener('change', () => context.onPickAll(box.checked));
    row.firstChild.appendChild(box);
  }
  return row;
}

/**
 * The chip's class list: the plain pill, plus a palette step when this ledger's
 * taxonomy has one for that id.
 *
 * The two extra classes go on together or not at all, exactly as `charts.css`
 * pairs `--paint` with `slice-n`: the step class only carries `--slice`, and
 * `--tint` is the only thing that spends it. Nothing here can therefore hand a
 * colour to a chip that has no step, which is what leaves the em dash — and any
 * id this taxonomy does not know — visually outside the palette rather than
 * accidentally inside it holding an unset custom property.
 */
function chipClass(txn, context) {
  const step = typeof context.sliceOf === 'function' ? context.sliceOf(txn.category_id) : '';
  return step ? `txn__catname txn__catname--tint ${step}` : 'txn__catname';
}

/**
 * The category cell. Three states and each one is spelled out: the id a rule
 * produced, the id a person chose with a marker saying so, or an em dash whose
 * meaning is in the text beside it rather than in the glyph.
 *
 * The chip is tinted with that category's palette step, and the em dash is not
 * tinted with anything — the donut leaves its hatched slice outside the palette
 * for the same reason, and this is the same fact drawn twice. Colour says
 * nothing here that the text does not: the id is printed inside the chip.
 */
export function categorySourceCopy(decidedBy) {
  if (decidedBy === 'override') return COPY.byPerson;
  if (decidedBy === 'agent') return COPY.byAgent;
  if (decidedBy === 'learned') return COPY.byLearned;
  return COPY.byRule;
}

export function transferSourceCopy(decidedBy) {
  if (decidedBy === 'override') return COPY.transferBy;
  if (decidedBy === 'agent') return COPY.transferByAgent;
  if (decidedBy === 'learned') return COPY.transferByLearned;
  return '';
}

function categoryCell(txn, context) {
  const cell = el('td', 'txn__cat');
  if (!txn.category_id) {
    cell.appendChild(el('span', 'txn__none muted', '—'));
    cell.appendChild(el('span', 'visually-hidden', COPY.noCategory));
    return cell;
  }
  cell.appendChild(el('span', chipClass(txn, context), txn.category_id));
  cell.appendChild(el(
    'span',
    txn.category_decided_by === 'override' ? 'txn__by txn__by--mine' : 'txn__by',
    categorySourceCopy(txn.category_decided_by),
  ));
  return cell;
}

/**
 * The transfer cell. Empty when the line is not one — a column reading "no" on
 * 415 rows is a column nobody reads on the row that says something else.
 */
function transferCell(txn) {
  const cell = el('td', 'txn__transfer');
  if (!txn.is_transfer) {
    return cell;
  }
  cell.appendChild(el('span', 'badge badge--neutral', COPY.transfer));
  const source = transferSourceCopy(txn.transfer_decided_by);
  if (source) {
    cell.appendChild(el(
      'span',
      txn.transfer_decided_by === 'override' ? 'txn__by txn__by--mine' : 'txn__by',
      source,
    ));
  }
  return cell;
}

/** Green for a deposit, red for a withdrawal, and neither for exactly nothing. */
function amountClass(minor) {
  if (typeof minor !== 'number' || minor === 0) {
    return 'txn__amount num money';
  }
  return minor < 0
    ? 'txn__amount num money txn__amount--out'
    : 'txn__amount num money txn__amount--in';
}

/**
 * The amount, right-aligned, with the sign carried by the glyph and not only
 * by the colour: `formatMinor` renders a debit as `-$12.44`, so a reader who
 * sees no colour still sees the minus.
 *
 * The two colours are `--ok` and `--fail`, which is what the four figures at
 * the top of the page already paint In and Out with. A third green invented
 * here would be a second vocabulary for one fact; there is one, and the table
 * reads it. Zero takes neither, because a line that moved nothing is not a
 * deposit drawn small — it is neither direction, and saying so costs nothing.
 */
function amountCell(txn) {
  const cell = el('td', amountClass(txn.amount_minor), formatMinor(txn.amount_minor));
  if (txn.currency && txn.currency !== 'USD') {
    // Only when it is not the currency `formatMinor` prints, because then the
    // rendered figure and the stored one would otherwise disagree in silence.
    cell.appendChild(el('span', 'txn__ccy', ` ${txn.currency}`));
  }
  return cell;
}

function describeCell(txn) {
  const cell = el('td', 'txn__desc');
  // Third-party text: a merchant name, a counterparty, a Zelle memo. It enters
  // as textContent and stays a string, whatever it contains.
  cell.appendChild(el('span', '', txn.raw_descriptor || ''));
  if (txn.statement_month) {
    cell.appendChild(el('span', 'txn__month muted', txn.statement_month));
  }
  return cell;
}

/**
 * The write entry point: a select carrying the effective category, with the
 * groups the server's `kind` gives and a first option that hands the line back
 * to the rules. Naming the `transfer` category is how a person says "this is a
 * transfer" — there is no second field for it, and so no second definition.
 */
function pickerCell(txn, context, apply) {
  const cell = el('td', 'txn__set');
  // The groups are built once, by the panel that fetched them, and handed to
  // every row. Two modules grouping one list their own way is the shape §5.29
  // exists to name.
  const groups = context.groups || [];
  if (groups.length === 0) {
    cell.appendChild(el('p', 'muted', COPY.noCategories));
    return { cell, select: null };
  }

  const select = el('select', 'picker');
  select.setAttribute(
    'aria-label',
    `Category for ${txn.date} ${formatMinor(txn.amount_minor)}`,
  );
  select.appendChild(option('', COPY.letRules));
  for (const group of groups) {
    const box = el('optgroup');
    box.label = group.label;
    for (const id of group.ids) {
      box.appendChild(option(id, id));
    }
    select.appendChild(box);
  }
  select.value = txn.category_id || '';
  select.addEventListener('change', () => apply(select.value));
  cell.appendChild(select);
  return { cell, select };
}

/**
 * One transaction as two `<tr>`s: the line, and a notice under it that stays
 * hidden until the server has said something about this line.
 *
 * `context` carries the category groups the panel built, `sliceOf` for the
 * palette step of one category id, whether the active filter selects on
 * category or transfer, `onReread` for the table, and `onChanged`, which the
 * panel uses to re-read the figures at the top of the page from the server
 * rather than adjusting any of them here.
 */
export function createRow(txn, context) {
  const row = el('tr', 'txn');
  const note = el('tr', 'txn-note');
  const noteCell = el('td', 'txn-note__cell');
  noteCell.colSpan = COLUMN_COUNT;
  note.appendChild(noteCell);
  note.hidden = true;

  let current = txn;
  let picker = null;

  function clearNote() {
    clear(noteCell);
    note.hidden = true;
    noteCell.className = 'txn-note__cell';
  }

  function showNote(kind, message, extra) {
    clear(noteCell);
    noteCell.className = `txn-note__cell txn-note__cell--${kind}`;
    noteCell.appendChild(el('p', 'notice__text', message));
    if (extra) {
      noteCell.appendChild(extra);
    }
    noteCell.appendChild(join(
      el('div', 'notice__actions'),
      button('btn btn--quiet', 'Dismiss', clearNote),
    ));
    note.hidden = false;
  }

  /**
   * The checkbox, and the only thing on a row that is not about that row.
   *
   * It carries a name of its own rather than leaning on the cells beside it: a
   * screen reader announcing twenty checkboxes called "checkbox" is a table
   * nobody can select from. The selection itself lives in the bulk controller,
   * which is why this reads `has()` on every paint instead of holding state —
   * a row rebuilt after a PATCH must come back checked if it still is.
   */
  function pickCell() {
    const cell = el('td', 'txn__pick-cell');
    if (!context.bulk) {
      return cell;
    }
    const box = el('input');
    box.type = 'checkbox';
    box.className = 'txn__pick';
    box.checked = context.bulk.has(current.txn_id);
    box.setAttribute('aria-label', COPY.pick(current.date, formatMinor(current.amount_minor)));
    box.addEventListener('change', () => context.bulk.toggle(current, box.checked));
    cell.appendChild(box);
    return cell;
  }

  function paint() {
    clear(row);
    row.appendChild(pickCell());
    row.appendChild(el('td', 'txn__date num', current.date || ''));
    row.appendChild(describeCell(current));
    row.appendChild(transferCell(current));
    row.appendChild(categoryCell(current, context));
    row.appendChild(amountCell(current));
    picker = pickerCell(current, context, apply);
    row.appendChild(picker.cell);
  }

  function setBusy(busy) {
    if (picker && picker.select) {
      picker.select.disabled = busy;
    }
  }

  async function apply(value) {
    setBusy(true);
    try {
      // '' is the first option, and it means "withdraw my decision"; the wire
      // field for that is null and the server requires it to be present.
      const result = await updateTransactionCategory(txn.txn_id, value === '' ? null : value);
      current = result.transaction || current;
      paint();
      // The server's own sentence. It is the only line that knows whether the
      // decision moved this row into or out of the figures at the top.
      showNote('ok', result.summary || '', staleNote());
      if (context.onChanged) {
        context.onChanged();
      }
    } catch (error) {
      setBusy(false);
      if (picker && picker.select) {
        // The control must not go on displaying a value the server refused.
        picker.select.value = current.category_id || '';
      }
      showNote('fail', error.message || COPY.patchFailed, null);
    }
  }

  function staleNote() {
    if (!context.selectsOnDecision || !context.selectsOnDecision()) {
      return null;
    }
    const box = el('div', 'txn-note__stale');
    box.appendChild(el('p', 'notice__text muted', COPY.stale));
    if (context.onReread) {
      box.appendChild(button('btn btn--quiet', COPY.reread, context.onReread));
    }
    return box;
  }

  paint();
  return [row, note];
}
