// SPDX-License-Identifier: AGPL-3.0-or-later

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { createLargeFlowsPanel } from '../../src/ledgerbox/web/js/large-flows.js';

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
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
  removeAttribute(name) { this.attributes.delete(name); }
}

function installDocument() {
  const previous = globalThis.document;
  globalThis.document = {
    createElement: (tag) => new FakeElement(tag),
    createTextNode: (text) => ({ textContent: String(text) }),
  };
  return () => { globalThis.document = previous; };
}

function flows(items) {
  return { threshold_minor: 100000, items, total_count: items.length, truncated: false };
}

const AGENT_LINE = {
  txn_id: 'txn-large-1', date: '2026-08-01', amount_minor: -250000,
  raw_descriptor: 'SYNTHETIC WIRE OUT', category_id: 'housing',
  category_decided_by: 'agent',
};
const UNCLAIMED_LINE = {
  txn_id: 'txn-large-2', date: '2026-08-02', amount_minor: 180000,
  raw_descriptor: 'SYNTHETIC DEPOSIT', category_id: null,
  category_decided_by: 'none',
};

function texts(node, out = []) {
  if (node.textContent) out.push(node.textContent);
  for (const child of node.children) texts(child, out);
  return out;
}

test('every row says who answered, and only answered rows offer Confirm', async () => {
  const restore = installDocument();
  try {
    const panel = createLargeFlowsPanel({
      root: new FakeElement('section'),
      countsNode: new FakeElement('p'),
    });
    panel.services.fetchFlows = async () => flows([AGENT_LINE, UNCLAIMED_LINE]);
    await panel.refresh();

    const rows = panel.nodes.list.children;
    assert.equal(rows.length, 2);
    const first = texts(rows[0]).join(' ');
    assert.match(first, /set by Agent/);
    const confirm = rows[0].children.find((child) => child.tagName === 'BUTTON');
    assert.ok(confirm, 'an answered large line offers one-click confirmation');

    const second = texts(rows[1]).join(' ');
    assert.match(second, /nobody claimed this/);
    assert.equal(
      rows[1].children.find((child) => child.tagName === 'BUTTON'),
      undefined,
      'there is nothing to confirm on an unclassified line',
    );
    assert.match(
      texts(rows[1]).join(' '),
      /Classify in Transactions/,
    );
  } finally {
    restore();
  }
});

test('Confirm re-decides the same category and refreshes the board', async () => {
  const restore = installDocument();
  try {
    const confirmed = [];
    let served = [AGENT_LINE];
    let changed = 0;
    const panel = createLargeFlowsPanel({
      root: new FakeElement('section'),
      countsNode: new FakeElement('p'),
      onChange: () => { changed += 1; },
    });
    panel.services.fetchFlows = async () => flows(served);
    panel.services.confirmCategory = async (txnId, categoryId) => {
      confirmed.push([txnId, categoryId]);
      served = [];
    };
    await panel.refresh();

    const confirm = panel.nodes.list.children[0].children
      .find((child) => child.tagName === 'BUTTON');
    await confirm.listeners.get('click')();

    assert.deepEqual(confirmed, [['txn-large-1', 'housing']], (
      'confirmation is the same category, re-decided by the person'
    ));
    assert.equal(changed, 1);
    assert.equal(panel.nodes.list.children.length, 0, 'the confirmed line left the board');
  } finally {
    restore();
  }
});

test('a rejected confirmation stays on the board and says why', async () => {
  const restore = installDocument();
  try {
    const panel = createLargeFlowsPanel({
      root: new FakeElement('section'),
      countsNode: new FakeElement('p'),
    });
    panel.services.fetchFlows = async () => flows([AGENT_LINE]);
    panel.services.confirmCategory = async () => {
      throw new Error('Synthetic refusal.');
    };
    await panel.refresh();
    const confirm = panel.nodes.list.children[0].children
      .find((child) => child.tagName === 'BUTTON');
    await confirm.listeners.get('click')();

    assert.match(panel.nodes.status.textContent, /synthetic refusal/i);
    assert.equal(confirm.disabled, false, 'the person may try again');
    assert.equal(panel.nodes.list.children.length, 1);
  } finally {
    restore();
  }
});
