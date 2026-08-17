// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Pure arithmetic for the category chart's display-only switches. Kept outside
// the DOM module so the reflow rule has direct Node coverage.

/**
 * Return the signed visible total and each slice's share of that total.
 *
 * Hidden slices get `null`, so chart geometry cannot accidentally advance
 * through a removed arc. A visible slice whose refunds exceed its spending is
 * clamped to a zero sweep because SVG cannot draw a backwards wedge; its signed
 * amount remains available in the legend.
 */
export function visibleSliceShares(slices, hidden = new Set()) {
  const total = slices.reduce(
    (sum, slice, index) => (hidden.has(index) ? sum : sum + (slice.spend_minor || 0)),
    0,
  );
  const shares = slices.map((slice, index) => {
    if (hidden.has(index) || total === 0) {
      return null;
    }
    return Math.max(0, (slice.spend_minor || 0) / total);
  });
  return { total, shares };
}
