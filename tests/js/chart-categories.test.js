// SPDX-License-Identifier: AGPL-3.0-or-later

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { visibleSliceShares } from '../../src/ledgerbox/web/js/category-filter.js';

const slices = [
  { spend_minor: -50 },
  { spend_minor: -30 },
  { spend_minor: -20 },
];

test('hidden slices disappear and the remaining ring rebalances to 100%', () => {
  const { total, shares } = visibleSliceShares(slices, new Set([1]));
  assert.equal(total, -70);
  assert.deepEqual(shares, [5 / 7, null, 2 / 7]);
  assert.equal(shares.filter((share) => share !== null).reduce((sum, share) => sum + share, 0), 1);
});

test('the unfiltered ring preserves the original proportions', () => {
  const { total, shares } = visibleSliceShares(slices);
  assert.equal(total, -100);
  assert.deepEqual(shares, [0.5, 0.3, 0.2]);
});

test('switching every slice off produces an empty ring', () => {
  const { total, shares } = visibleSliceShares(slices, new Set([0, 1, 2]));
  assert.equal(total, 0);
  assert.deepEqual(shares, [null, null, null]);
});
