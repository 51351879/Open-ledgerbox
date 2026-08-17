// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The date range's arithmetic, under `node --test`.
//
// This is the first behavioural test the frontend has ever had. Until now
// `web/` was covered by grep guards only — no `innerHTML`, nothing off-origin,
// no file over 400 lines, an SPDX header on each — which check that a file is
// not a certain shape and say nothing about what it computes.
//
// It exists because of a defect an acceptance round constructed and a person
// could not: on 31 May, "Last month" selected **this** month. `setMonth` does
// not clamp, so one month back from 31 May is 31 April, which JavaScript
// resolves to 1 May, and the whole of April fell outside a window labelled
// "Last month". On 31 March the same overflow silently dropped 28 February to
// 2 March. Every preset is correct on any day whose date number every month
// has, which is why the manual browser session that signed M6 off saw nothing:
// it ran on the 6th.
//
// That is this project's own failure mode aimed at its own filter — a control
// quietly answering a question nobody asked — and the reason the fix arrives
// with a test rather than only with a correction is discipline 7: a check
// nobody has seen fail has not been tested. Every case below fails against the
// previous implementation.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { presetSpan } from '../../src/ledgerbox/web/js/date-range.js';

/** A local-calendar date, built the way the module under test builds them. */
function day(year, month, date) {
  return new Date(year, month - 1, date);
}

// Written out rather than computed, so the expected value is a date somebody
// read off a calendar and not the output of the arithmetic being tested.
const MONTH_END = [
  // [today, preset, expected since]
  [day(2026, 5, 31), '1m', '2026-04-30'],
  [day(2026, 3, 31), '1m', '2026-02-28'],
  [day(2028, 3, 31), '1m', '2028-02-29'], // a leap February
  [day(2026, 5, 31), '3m', '2026-02-28'],
  [day(2026, 3, 31), '6m', '2025-09-30'],
  [day(2026, 1, 31), '1m', '2025-12-31'], // across a year boundary
  [day(2026, 1, 31), '3m', '2025-10-31'],
  [day(2026, 7, 31), '6m', '2026-01-31'],
  [day(2026, 8, 31), '6m', '2026-02-28'],
];

test('a month-end date does not overflow into the following month', () => {
  for (const [today, preset, expected] of MONTH_END) {
    const span = presetSpan(preset, today);
    assert.equal(
      span.since,
      expected,
      `${preset} on ${today.toDateString()} should start ${expected}, got ${span.since}`,
    );
  }
});

test('a month-end date still names a day inside the month it names', () => {
  // The property behind the table above, stated once: whatever "N months back"
  // resolves to, it must land in the month N before this one. The overflow bug
  // was exactly a violation of this and nothing else.
  for (const [months, preset] of [[1, '1m'], [3, '3m'], [6, '6m']]) {
    for (let date = 28; date <= 31; date += 1) {
      for (let month = 1; month <= 12; month += 1) {
        const today = day(2026, month, date);
        // Skip dates that do not exist: `new Date(2026, 1, 30)` is 2 March and
        // is not a day anybody can be on.
        if (today.getMonth() !== month - 1) {
          continue;
        }
        const span = presetSpan(preset, today);
        const wanted = new Date(2026, month - 1 - months, 1);
        const got = new Date(`${span.since}T00:00:00`);
        assert.equal(
          got.getFullYear() * 12 + got.getMonth(),
          wanted.getFullYear() * 12 + wanted.getMonth(),
          `${preset} on ${today.toDateString()} landed in ${span.since}`,
        );
      }
    }
  }
});

test('29 February minus a year is the 28th, not the 1st of March', () => {
  assert.equal(presetSpan('1y', day(2028, 2, 29)).since, '2027-02-28');
});

test('the last 7 days counts today as one of them', () => {
  // Six days back plus today. The header of the module states this reading, so
  // it is pinned rather than left to be re-derived by the next reader.
  assert.equal(presetSpan('7d', day(2026, 8, 6)).since, '2026-07-31');
  assert.equal(presetSpan('7d', day(2026, 3, 3)).since, '2026-02-25');
  assert.equal(presetSpan('7d', day(2026, 1, 1)).since, '2025-12-26');
});

test('a day count walks back across a month boundary rather than clamping', () => {
  // `days` is applied after the clamp and must keep `setDate`'s underflow,
  // which is the one place the overflowing behaviour is the wanted one.
  assert.equal(presetSpan('7d', day(2026, 3, 1)).since, '2026-02-23');
});

test('this year starts on the first of January of the current year', () => {
  assert.equal(presetSpan('ytd', day(2026, 8, 6)).since, '2026-01-01');
  assert.equal(presetSpan('ytd', day(2026, 1, 1)).since, '2026-01-01');
  assert.equal(presetSpan('ytd', day(2026, 12, 31)).since, '2026-01-01');
});

test('every relative preset leaves the far end open', () => {
  // Closing it at today would hide a line dated ahead of today. Asserted for
  // every preset rather than for the one that was being edited, because the
  // rule is about the set and a new preset should have to break this test to
  // arrive with a closed end.
  for (const preset of ['7d', '1m', '3m', '6m', '1y', 'ytd']) {
    const span = presetSpan(preset, day(2026, 8, 6));
    assert.equal(span.until, null, `${preset} closed the far end`);
    assert.ok(span.since, `${preset} produced no start`);
  }
});

test('all time and custom are both unbounded, and so is anything unknown', () => {
  for (const preset of ['all', 'custom', 'not-a-preset', '']) {
    assert.deepEqual(presetSpan(preset, day(2026, 8, 6)), { since: null, until: null });
  }
});

test('the window is built from the local calendar and never from UTC', () => {
  // `toISOString()` returns UTC, and west of Greenwich it names yesterday for
  // most of the day. The predecessor shipped exactly that and its whole
  // day-of-week distribution was off by one.
  //
  // Which probe separates the two implementations depends on the zone the run
  // is in — early local times diverge east of Greenwich, late ones west — so
  // both are tried and the divergent one is asserted on by name. In UTC itself
  // neither diverges and no assertion here can tell the implementations apart,
  // which is why `tests/test_web_behaviour.py` runs this suite under two
  // offset zones as well as under whatever the machine is set to.
  const probes = [new Date(2026, 7, 6, 0, 30, 0), new Date(2026, 7, 6, 23, 30, 0)];
  for (const probe of probes) {
    assert.equal(presetSpan('7d', probe).since, '2026-07-31');
    assert.equal(presetSpan('ytd', probe).since, '2026-01-01');
  }
  const divergent = probes.filter((probe) => probe.toISOString().slice(0, 10) !== '2026-08-06');
  for (const probe of divergent) {
    // This is the instant a `toISOString()` implementation would answer a
    // different day for, and it answered the local one.
    assert.equal(presetSpan('7d', probe).since, '2026-07-31');
  }
});
