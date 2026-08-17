// SPDX-License-Identifier: AGPL-3.0-or-later
//
// G2's two boundaries: the filter module owns its controls, and the live region
// receives one short result status rather than the whole figures/legend/summary.

import { strict as assert } from 'node:assert';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

import { createFilters, renderFilterControls } from '../../src/ledgerbox/web/js/transaction-filters.js';
import { transactionResultStatus } from '../../src/ledgerbox/web/js/transactions.js';

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.className = '';
    this.textContent = '';
    this.value = '';
  }

  get firstChild() {
    return this.children[0] || null;
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) {
      this.children.splice(index, 1);
    }
    return child;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }
}

function descendants(node) {
  return [node, ...node.children.flatMap(descendants)];
}

function filterRoot(host) {
  return {
    querySelector(selector) {
      if (selector === '[data-txn="controls"]') return host;
      const match = selector.match(/^\[data-txn="(.+)"\]$/);
      if (!match) return null;
      return descendants(host).find(
        (node) => node.attributes.get('data-txn') === match[1],
      ) || null;
    },
  };
}

test('the filter module builds all controls inside the shell it owns', () => {
  const previous = globalThis.document;
  const host = new FakeElement('div');
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  try {
    renderFilterControls({
      querySelector: (selector) => (selector === '[data-txn="controls"]' ? host : null),
    });
  } finally {
    globalThis.document = previous;
  }

  assert.equal(host.attributes.get('role'), 'group');
  assert.equal(host.attributes.get('aria-label'), 'Filter and sort the transactions');
  const names = descendants(host)
    .map((node) => node.attributes.get('data-txn'))
    .filter(Boolean);
  assert.deepEqual(names, [
    'q', 'month', 'category', 'transfer', 'direction', 'sort', 'order', 'reset',
  ]);
});

test('the omission handoff clears stale filters and selects only unclassified lines', () => {
  const previous = globalThis.document;
  const host = new FakeElement('div');
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  let changes = 0;
  try {
    const filters = createFilters({ root: filterRoot(host), onChange: () => { changes += 1; } });
    const controls = Object.fromEntries(
      descendants(host)
        .filter((node) => node.attributes.get('data-txn'))
        .map((node) => [node.attributes.get('data-txn'), node]),
    );
    controls.q.value = 'old merchant';
    controls.month.value = '2026-08';
    controls.transfer.value = 'false';
    filters.showUnclassified();
    assert.equal(changes, 1);
    assert.deepEqual(filters.query(), {
      q: '', month: '', category: '(none)', transfer: null,
      direction: '', sort: 'date', descending: true,
    });
  } finally {
    globalThis.document = previous;
  }
});

test('the transaction live status is short and says count plus visible range', () => {
  const status = transactionResultStatus({
    offset: 20,
    items: Array.from({ length: 17 }, () => ({})),
    totals: { matched: 37 },
  });
  assert.equal(status, 'Transaction results updated: 37 lines match; showing 21–37.');
  assert.ok(status.length < 100);
  assert.equal(transactionResultStatus({ offset: 0, items: [], totals: { matched: 0 } }),
    'Transaction results updated: no lines match.');
});

test('index keeps a control shell and a narrow live region, not live totals', () => {
  const html = readFileSync('src/ledgerbox/web/index.html', 'utf8');
  assert.match(html, /data-txn="controls"/);
  assert.doesNotMatch(html, /data-txn="q"/i, 'control markup belongs to transaction-filters.js');
  assert.match(html, /data-txn="status"[^>]*aria-live="polite"/i);
  assert.doesNotMatch(html, /data-txn="totals"[^>]*aria-live=/i);
});
