// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Which colour a category takes, decided once for the whole page.
//
// Two places paint a category: the donut with its legend, and the chips in the
// transaction table. They must agree, and until this file existed they did not
// — measured, on the same ledger, at the same moment: **none** of the eight
// categories present took the same step in both.
//
// The cause was a rule that looked reasonable in isolation. The donut took its
// step from the slice's **index in the breakdown**, and the breakdown is ranked
// by spend. So over there a colour was a fact about the current window rather
// than about the category, and the moment P2 M6 put a date range on the page
// that stopped being a subtlety: narrowing the range reorders the ranking, and
// the wedges trade hues under a reader who has not changed anything but the
// dates. A legend you cannot return to is not a legend.
//
// So the step comes from the **taxonomy**: the order `/api/categories` returns,
// which is `ORDER BY c.kind, c.id` and depends on nothing but the shipped rules
// file. A category is the same colour on every load, in every window, whatever
// it did or did not spend — and in both places, because both read this.
//
// The list is fetched once and kept. It changes only when the rules file does,
// which cannot happen while the page is open: `ensure_categories` mirrors that
// file at ingest, and an ingest that added one would be a new category with no
// history, not a reordering of the ones already drawn.

import { fetchCategories } from './api.js';
import { sliceClass } from './charts.js';

/**
 * `id -> palette step class`, in the order the server returned them.
 *
 * The palette itself is `charts.css`'s twenty-four categorical steps and the class
 * names are `charts.js`'s, imported rather than restated — a second copy of
 * that palette is the two-definitions shape §5.29 exists to name, and the copy
 * is always the one that goes stale.
 *
 * The shipped rules file defines twenty-four categories and there are twenty-four
 * steps, so no two share one. A twenty-fifth would share step 24 with the
 * twenty-fourth,
 * which is `sliceClass`'s documented fallback rather than a new rule invented
 * here.
 */
export function tonesOf(categories) {
  const tones = new Map();
  (categories || []).forEach((row, index) => {
    tones.set(row.id, sliceClass(index));
  });
  return tones;
}

let cached = null;
let inFlight = null;

/**
 * The map, fetched at most once per page load.
 *
 * A failed fetch resolves to an empty map rather than rejecting: a category
 * with no step renders as the plain uncoloured pill, which is a page that has
 * lost its colours and not a page that has lost its numbers. The caller that
 * needs to *tell somebody* about a failed category list is
 * `transaction-filters.js`, which cannot fill its filter without one and says
 * so; nothing here should raise a second time for the same request.
 */
export function loadTones() {
  if (cached) {
    return Promise.resolve(cached);
  }
  if (!inFlight) {
    inFlight = fetchCategories()
      .then((rows) => {
        cached = tonesOf(rows);
        return cached;
      })
      .catch(() => new Map())
      .finally(() => {
        inFlight = null;
      });
  }
  return inFlight;
}

/**
 * The step for one category, or `''` before the list has arrived.
 *
 * Synchronous on purpose: it is called from inside a render, and a render that
 * awaited would paint the chart in two passes. Callers that care about the
 * first paint await :func:`loadTones` before rendering — `analytics.js` does.
 */
export function toneFor(categoryId) {
  if (!cached || categoryId === null || categoryId === undefined) {
    return '';
  }
  return cached.get(categoryId) || '';
}
