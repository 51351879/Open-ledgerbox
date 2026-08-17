// SPDX-License-Identifier: AGPL-3.0-or-later

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  impactCopy,
  pendingGroups,
  renderProposalGroups,
} from '../../src/ledgerbox/web/js/agent-proposal-groups.js';

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

function proposal(txnId, outcome = 'pending') {
  return {
    txn_id: txnId,
    group_id: 'group-one',
    suggested_category_id: 'dining',
    outcome,
    applied_category_id: outcome === 'pending' ? null : 'dining',
    reviewed_at: outcome === 'pending' ? null : '2026-08-08T12:00:00Z',
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

test('pending groups exclude reviewed rows and transfer impact always says approval', () => {
  const groups = pendingGroups([proposal('one'), proposal('two', 'accepted')]);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].rows.map((row) => row.txn_id), ['one']);
  assert.match(impactCopy('transfer', 1), /manual approval/i);
  assert.match(impactCopy('dining', 2), /Balances and statement lines do not change/);
});

test('row exclusion sends only checked ids and a failed write keeps the selection usable', async () => {
  const restore = installDocument();
  try {
    const host = new FakeElement('div');
    const calls = [];
    const messages = [];
    renderProposalGroups({
      host,
      proposals: [proposal('one'), proposal('two')],
      categories: [
        { id: 'dining', kind: 'expense' },
        { id: 'groceries', kind: 'expense' },
        { id: 'transfer', kind: 'transfer' },
      ],
      onReview: async (request) => { calls.push(request); return false; },
      onMessage: (message, tone) => messages.push({ message, tone }),
    });

    const second = find(host, (node) => node.dataset?.txnId === 'two');
    second.checked = false;
    second.change();
    const apply = find(host, (node) => node.dataset?.action === 'accept');
    await apply.click();

    assert.deepEqual(calls, [{
      action: 'accept',
      txnIds: ['one'],
      categoryId: 'dining',
    }]);
    assert.equal(second.checked, false, 'failed write preserves the explicit exclusion');
    assert.equal(apply.disabled, false, 'the action is usable for a retry');

    const reject = find(host, (node) => node.dataset?.action === 'reject');
    await reject.click();
    assert.deepEqual(calls.at(-1), {
      action: 'reject',
      txnIds: ['one'],
      categoryId: null,
    });

    const first = find(host, (node) => node.dataset?.txnId === 'one');
    first.checked = false;
    first.change();
    await apply.click();
    assert.deepEqual(messages.at(-1), {
      message: 'Select at least one transaction in this group.',
      tone: 'fail',
    });
    assert.equal(calls.length, 2, 'an empty selection never reaches the write API');
  } finally {
    restore();
  }
});
