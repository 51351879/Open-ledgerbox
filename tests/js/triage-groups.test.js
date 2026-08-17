// SPDX-License-Identifier: AGPL-3.0-or-later

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  ROUTE_COPY,
  pendingTriageGroups,
  renderTriageGroups,
  triageImpactCopy,
} from '../../src/ledgerbox/web/js/triage-groups.js';

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.dataset = {};
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.indeterminate = false;
    this.value = '';
    this.type = '';
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    return child;
  }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  async click() {
    const listener = this.listeners.get('click');
    if (listener) await listener({ preventDefault() {} });
  }
  change() {
    const listener = this.listeners.get('change');
    if (listener) listener();
  }
}

function installDocument() {
  const previous = globalThis.document;
  globalThis.document = {
    createElement: (tag) => new FakeElement(tag),
    createTextNode: (text) => ({ textContent: String(text) }),
  };
  return () => { globalThis.document = previous; };
}

function find(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children || []) {
    const found = find(child, predicate);
    if (found) return found;
  }
  return null;
}

function item(txnId, route = 'uncertain', outcome = 'pending') {
  return {
    txn_id: txnId,
    group_id: `group-${route}`,
    route,
    reason_code: route === 'possible_transfer' ? 'account_movement_language'
      : route === 'taxonomy_gap' ? 'coherent_activity_missing' : 'descriptor_ambiguous',
    outcome,
    applied_category_id: null,
    reviewed_at: outcome === 'pending' ? null : '2026-08-09T12:00:00Z',
    current_transaction: {
      txn_id: txnId,
      date: '2026-08-01',
      amount_minor: -1234,
      currency: 'USD',
      raw_descriptor: `synthetic ${txnId}`,
      category_id: null,
      category_decided_by: 'none',
      is_transfer: false,
      transfer_decided_by: 'rule',
    },
  };
}

test('routes say what can change and reviewed rows leave pending groups', () => {
  const groups = pendingTriageGroups([
    item('one', 'possible_transfer'),
    item('two', 'uncertain', 'left_uncertain'),
  ]);
  assert.deepEqual(groups.map((group) => group.route), ['possible_transfer']);
  assert.match(ROUTE_COPY.possible_transfer.note, /not a transfer decision/i);
  assert.match(ROUTE_COPY.taxonomy_gap.note, /does not invent a category/i);
  assert.match(ROUTE_COPY.uncertain.note, /keeps it unclassified/i);
  assert.match(triageImpactCopy('investment', 1), /In and Out figures/);
  assert.match(triageImpactCopy('dining', 2), /Balances and statement lines do not change/);
  assert.match(triageImpactCopy('', 2), /Choose a category/);
});

test('classification requires an explicit category instead of defaulting to cash', () => {
  const restore = installDocument();
  try {
    const host = new FakeElement('div');
    renderTriageGroups({
      host,
      run: {
        items: [item('one')],
        route_summaries: [{
          route: 'uncertain', item_count: 1, pending: 1, bank_amount_minor: -1234,
        }],
      },
      categories: [
        { id: 'cash', kind: 'expense' },
        { id: 'transfer', kind: 'transfer' },
      ],
      onReview: async () => true,
      onMessage: () => {},
    });

    const picker = find(host, (node) => node.tagName === 'SELECT');
    const classify = find(host, (node) => node.dataset?.action === 'classify');
    const impact = find(host, (node) => node.className === 'triage-group__impact');
    assert.equal(picker.value, '');
    assert.equal(classify.disabled, true);
    assert.match(impact.textContent, /Choose a category/);

    picker.value = 'transfer';
    picker.change();
    assert.equal(classify.disabled, false);
    assert.match(impact.textContent, /In and Out figures/);
  } finally {
    restore();
  }
});

test('failed triage write keeps exclusions and controls retryable', async () => {
  const restore = installDocument();
  try {
    const host = new FakeElement('div');
    const calls = [];
    renderTriageGroups({
      host,
      run: {
        items: [item('one'), item('two')],
        route_summaries: [{
          route: 'uncertain', item_count: 2, pending: 2, bank_amount_minor: -2468,
        }],
      },
      categories: [
        { id: 'dining', kind: 'expense' },
        { id: 'transfer', kind: 'transfer' },
      ],
      onReview: async (request) => { calls.push(request); return false; },
      onMessage: () => {},
    });

    const picker = find(host, (node) => node.tagName === 'SELECT');
    picker.value = 'dining';
    picker.change();
    const second = find(host, (node) => node.dataset?.txnId === 'two');
    second.checked = false;
    second.change();
    const classify = find(host, (node) => node.dataset?.action === 'classify');
    await classify.click();

    assert.deepEqual(calls[0], {
      action: 'classify', txnIds: ['one'], categoryId: 'dining',
    });
    assert.equal(second.checked, false);
    assert.equal(classify.disabled, false);

    const uncertain = find(host, (node) => node.dataset?.action === 'leave_uncertain');
    await uncertain.click();
    assert.deepEqual(calls[1], {
      action: 'leave_uncertain', txnIds: ['one'], categoryId: null,
    });
    assert.equal(uncertain.disabled, false);
  } finally {
    restore();
  }
});
