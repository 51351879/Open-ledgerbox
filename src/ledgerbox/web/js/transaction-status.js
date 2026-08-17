// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The one short sentence the transaction result live region announces.
// Keeping this pure lets Node pin the accessible contract without a browser,
// while the visible totals remain richer, navigable content outside aria-live.

export function transactionResultStatus(data) {
  const matched = Number(data.totals.matched || 0);
  const shown = data.items.length;
  if (matched === 0) {
    return 'Transaction results updated: no lines match.';
  }
  const noun = matched === 1 ? 'line matches' : 'lines match';
  if (shown === 0) {
    return `Transaction results updated: ${matched} ${noun}; this page shows none.`;
  }
  const first = data.offset + 1;
  const last = data.offset + shown;
  const range = first === last ? String(first) : `${first}–${last}`;
  return `Transaction results updated: ${matched} ${noun}; showing ${range}.`;
}
