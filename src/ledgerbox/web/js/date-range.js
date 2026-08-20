// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The window every figure on this page is measured over.
//
// One control, read by three panels: the four figures, the two charts and the
// transaction table all send the same `since`/`until` and therefore describe
// the same window. That is not tidiness. The equalities this page states in
// prose — the wedges add up to the Out, the months add up to the four figures —
// are only checkable while every one of them is answering about the same rows,
// and a range that moved the charts but not the headline would make the page's
// own sentences false for every window but one.
//
// **These are transaction dates**: when the money moved. The table's Month
// control is a different question — which statement a line is printed on — and
// the two disagree for any line near a period boundary, because a Chase period
// does not begin on the 1st. Both are offered and both are labelled. The
// predecessor had both and labelled neither, and 83 of its 415 rows fell in
// different months depending on which chart was asking.
//
// **The relative presets leave the far end open.** "The last 7 days" sets only
// `since`. Closing it at today would silently hide a line dated ahead of today
// — rare, but the whole argument of this project is against a filter that
// quietly answers a question nobody asked.
//
// **Dates are built from the local calendar, never from `toISOString()`.** That
// returns UTC, and west of Greenwich it names yesterday for most of the day.
// The predecessor shipped exactly that bug: it parsed dates as UTC and read the
// weekday locally, so its whole day-of-week distribution was off by one.

import { el, option } from './api.js';
import { localized, t } from './i18n.js';

// `label`, `from` and `to` used to sit here as well. Nothing has ever read
// them -- `index.html` carries those three labels as markup -- and an unread
// string is the exact shape that becomes a dictionary entry which can never
// appear on the page. `all` and `custom` moved into `PRESETS` below, where
// they are looked up at render time instead of at import time.
const COPY = localized({
  // **It has to say what the page is showing instead.** Refusing to issue a
  // request for an impossible range is right -- it would paint an error over
  // four figures that are fine. But the controls then read one window while
  // every number below reads another, with nothing on screen connecting them,
  // which is the shape §5.25 records: one screen carrying two answers, and the
  // wrong one on top. So the refusal names the window still in force.
  //
  // A function, which `localized()` passes through untouched: it looks up
  // strings and a function is not one. It calls `t()` itself, and the window
  // still in force is substituted into the sentence rather than looked up.
  invalid: (showing) => t(
    'The start of the range is after its end, so it selects nothing. Swap the two dates, '
      + 'or clear one of them. Nothing below has changed: the figures, both charts and '
      + 'the table are still showing {showing}.',
    { showing },
  ),
  whole: 'the whole ledger',
});

/** The window in force, in words, for the sentence above.
 *
 * An open far end is said as open rather than as "to now". Every relative
 * preset leaves it open on purpose — see the header — so "to now" would name a
 * bound the request does not carry, on the one control whose whole job is to
 * say which rows are being described. */
function describe(span) {
  if (!span.since && !span.until) {
    return COPY.whole;
  }
  if (span.since && span.until) {
    return t('{since} to {until}', { since: span.since, until: span.until });
  }
  return span.since
    ? t('{since} onwards', { since: span.since })
    : t('everything up to {until}', { until: span.until });
}

/** `YYYY-MM-DD` for a local date, with no timezone anywhere near it. */
function iso(date) {
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${date.getFullYear()}-${month}-${day}`;
}

/**
 * `today` shifted back by whole days, months or years, in the local calendar.
 *
 * **The day of the month is clamped, because `setMonth` overflows instead.**
 * `setMonth` keeps the day number and lets the date run into the next month:
 * on 31 May, one month back is 31 April, which JavaScript resolves to 1 May.
 * So "Last month" selected *this* month and left the whole of April outside the
 * window, and on 31 March it silently dropped 28 February to 2 March. Both are
 * exactly the filter this file's header argues against — one that quietly
 * answers a question nobody asked — and neither is visible on a day whose date
 * number every month has.
 *
 * The month is resolved first, with a day of 1 so nothing can overflow while it
 * is being resolved; that also carries the year for free, since `new Date(y, -4,
 * 1)` is September of `y - 1`. Then the day is clamped to that month's last day.
 * `days` is applied afterwards through `setDate`, whose underflow is the wanted
 * behaviour: it walks back into the previous month rather than past it.
 */
function ago({ days = 0, months = 0, years = 0 }, today) {
  const probe = new Date(today.getFullYear() - years, today.getMonth() - months, 1);
  // Day 0 of the following month is the last day of this one.
  const lastDay = new Date(probe.getFullYear(), probe.getMonth() + 1, 0).getDate();
  const moved = new Date(
    probe.getFullYear(),
    probe.getMonth(),
    Math.min(today.getDate(), lastDay),
  );
  moved.setDate(moved.getDate() - days);
  return moved;
}

// Each preset is a function of *today*, so "this year" names the current year
// rather than a year somebody typed into this file. `since: null` is unbounded.
//
// The labels are the English sentences themselves and are looked up in `fill`,
// not here: this array is built when the module is imported, and `main.js`
// chooses the language after every module is imported. A label translated at
// import time would be English for the rest of the page's life.
const PRESETS = [
  ['all', 'All time', () => ({ since: null, until: null })],
  // Seven days inclusive of today, which is what "the last week" means to a
  // person: six days back, plus today.
  ['7d', 'Last 7 days', (today) => ({ since: iso(ago({ days: 6 }, today)), until: null })],
  ['1m', 'Last month', (today) => ({ since: iso(ago({ months: 1 }, today)), until: null })],
  ['3m', 'Last 3 months', (today) => ({ since: iso(ago({ months: 3 }, today)), until: null })],
  ['6m', 'Last 6 months', (today) => ({ since: iso(ago({ months: 6 }, today)), until: null })],
  ['1y', 'Last 12 months', (today) => ({ since: iso(ago({ years: 1 }, today)), until: null })],
  [
    'ytd',
    // Labelled with the year itself, so the option says what it will do rather
    // than requiring the reader to know what "this year" resolves to. This
    // string is therefore never displayed and has no dictionary entry.
    'This year',
    (today) => ({ since: `${today.getFullYear()}-01-01`, until: null }),
  ],
  ['custom', 'Custom…', () => ({ since: null, until: null })],
];

const UNBOUNDED = { since: null, until: null };

/**
 * The window one preset names on a given day. Exported because it is the whole
 * of this module's arithmetic and the only part of it that has ever been wrong:
 * `tests/js/date-range.test.js` runs it under `node --test` against the end of
 * every month, which is the only place the bug it now clamps for was visible.
 *
 * An unknown id is the unbounded window rather than an error. The `<select>` is
 * filled from `PRESETS`, so the only way here with something else is a caller
 * that is not this file, and answering "the whole ledger" is the reading that
 * cannot hide a row.
 */
export function presetSpan(value, today) {
  const preset = PRESETS.find(([id]) => id === value);
  return preset ? preset[2](today) : { ...UNBOUNDED };
}

/**
 * The range control.
 *
 * `onChange` fires only when the window actually changes, so re-picking the
 * preset already in force issues no request. Returns `{ span }`, which every
 * panel reads at the moment it builds a request rather than being handed a copy
 * — one source, so two panels cannot be a beat apart.
 *
 * `today` is injectable for the tests; nothing else passes it.
 */
export function createDateRange(options) {
  const root = options.root;
  const onChange = options.onChange;
  const today = options.today || new Date();

  const select = root.querySelector('[data-range="preset"]');
  const customBox = root.querySelector('[data-range="custom"]');
  const sinceInput = root.querySelector('[data-range="since"]');
  const untilInput = root.querySelector('[data-range="until"]');
  const noticeNode = root.querySelector('[data-range="notice"]');

  let current = { ...UNBOUNDED };

  function fill() {
    if (!select) {
      return;
    }
    for (const [value, label] of PRESETS.map(([v, l]) => [v, l])) {
      select.appendChild(
        option(value, value === 'ytd' ? String(today.getFullYear()) : t(label)),
      );
    }
    select.value = 'all';
  }

  function notice(message) {
    if (!noticeNode) {
      return;
    }
    noticeNode.textContent = '';
    noticeNode.hidden = !message;
    if (message) {
      noticeNode.appendChild(el('span', '', message));
    }
  }

  /** The window the controls currently describe, or null if it selects nothing. */
  function read() {
    const chosen = select ? select.value : 'all';
    if (chosen !== 'custom') {
      return presetSpan(chosen, today);
    }
    const since = sinceInput && sinceInput.value ? sinceInput.value : null;
    const until = untilInput && untilInput.value ? untilInput.value : null;
    // Refused here as well as by the server. The server's 422 is the guarantee;
    // this is so the person sees why before a request goes out and a panel
    // paints an error over its own numbers.
    if (since && until && since > until) {
      return null;
    }
    return { since, until };
  }

  function apply() {
    if (customBox) {
      customBox.hidden = !(select && select.value === 'custom');
    }
    const next = read();
    if (next === null) {
      notice(COPY.invalid(describe(current)));
      return;
    }
    notice('');
    if (next.since === current.since && next.until === current.until) {
      return;
    }
    current = next;
    onChange();
  }

  fill();
  for (const node of [select, sinceInput, untilInput]) {
    if (node) {
      node.addEventListener('change', apply);
    }
  }
  if (customBox) {
    customBox.hidden = true;
  }

  return {
    /** The window to send. A fresh object, so no caller can edit the state. */
    span: () => ({ since: current.since, until: current.until }),
  };
}
