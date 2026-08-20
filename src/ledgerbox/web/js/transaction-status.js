// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The one short sentence the transaction result live region announces.
// Keeping this pure lets Node pin the accessible contract without a browser,
// while the visible totals remain richer, navigable content outside aria-live.
//
// **Singular and plural are whole sentences here, not a noun swapped into
// one.** English picks between `line matches` and `lines match`; a language
// with no plural has one sentence for both, and one with more than two
// number forms has more. A dictionary can answer for a sentence and cannot
// answer for half of one, so each case is written out.

import { t } from './i18n.js';

export function transactionResultStatus(data) {
  const matched = Number(data.totals.matched || 0);
  const shown = data.items.length;
  if (matched === 0) {
    return t('Transaction results updated: no lines match.');
  }
  if (shown === 0) {
    return matched === 1
      ? t('Transaction results updated: 1 line matches; this page shows none.')
      : t('Transaction results updated: {count} lines match; this page shows none.', {
        count: matched,
      });
  }
  const first = data.offset + 1;
  const last = data.offset + shown;
  const range = first === last ? String(first) : `${first}–${last}`;
  return matched === 1
    ? t('Transaction results updated: 1 line matches; showing {range}.', { range })
    : t('Transaction results updated: {count} lines match; showing {range}.', {
      count: matched,
      range,
    });
}
