// SPDX-License-Identifier: AGPL-3.0-or-later

import { strict as assert } from 'node:assert';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';

import { tonesOf } from '../../src/ledgerbox/web/js/category-tones.js';
import { SLICE_STEPS, sliceClass } from '../../src/ledgerbox/web/js/charts.js';

test('the current twenty-four-category taxonomy has twenty-four distinct colour steps', () => {
  const categories = Array.from({ length: 24 }, (_, index) => ({ id: `category-${index + 1}` }));
  const tones = tonesOf(categories);

  assert.equal(SLICE_STEPS, 24);
  assert.equal(tones.size, 24);
  assert.equal(new Set(tones.values()).size, 24);
  assert.equal(tones.get('category-1'), 'slice-1');
  assert.equal(tones.get('category-24'), 'slice-24');
});

test('a future category uses the documented last-step fallback', () => {
  assert.equal(sliceClass(24), 'slice-24');
});

test('every colour step has light, dark, and wedge CSS', async () => {
  const tokens = await readFile(
    new URL('../../src/ledgerbox/web/css/tokens.css', import.meta.url),
    'utf8',
  );
  const charts = await readFile(
    new URL('../../src/ledgerbox/web/css/charts.css', import.meta.url),
    'utf8',
  );

  for (let step = 1; step <= SLICE_STEPS; step += 1) {
    const token = `--cat-${step}:`;
    assert.equal(tokens.split(token).length - 1, 2, `${token} must exist in both themes`);
    assert.match(charts, new RegExp(`\\.slice-${step}\\s*\\{`));
  }
});
