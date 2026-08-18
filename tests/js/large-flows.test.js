// SPDX-License-Identifier: AGPL-3.0-or-later

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  missingKeys,
  registerLocale,
  resetI18n,
  setLocale,
} from '../../src/ledgerbox/web/js/i18n.js';
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

test('the board speaks the reader`s language and never translates the money', async () => {
  // The point of the whole dictionary layer, on the panel where getting it
  // wrong costs the most: a category id is an identifier the ledger stores and
  // an amount is a figure, so both are substituted into the sentence rather
  // than looked up in it.
  const restore = installDocument();
  resetI18n();
  registerLocale('zh-CN', {
    'set by Agent': '由 Agent 决定',
    'nobody claimed this': '没有任何规则认领',
    Confirm: '确认',
    'Classify in Transactions': '到 Transactions 分类',
    '{count} large line(s) awaiting one look': '{count} 笔大额待看一眼',
  });
  setLocale('zh-CN');
  try {
    const counts = new FakeElement('p');
    const panel = createLargeFlowsPanel({ root: new FakeElement('section'), countsNode: counts });
    panel.services.fetchFlows = async () => flows([AGENT_LINE, UNCLAIMED_LINE]);
    await panel.refresh();

    const rows = panel.nodes.list.children;
    const first = texts(rows[0]).join(' ');
    assert.match(first, /由 Agent 决定/);
    assert.match(first, /housing/, 'the category id is stored data, not a word');
    assert.match(texts(rows[1]).join(' '), /没有任何规则认领/);
    assert.match(texts(rows[1]).join(' '), /到 Transactions 分类/);
    assert.equal(counts.textContent, '2 笔大额待看一眼');
  } finally {
    setLocale('en');
    resetI18n();
    restore();
  }
});

test('a board with no dictionary behind it is the English board', async () => {
  // The fallback stated as a property of this panel and not only of the layer:
  // a half-translated locale leaves every unanswered sentence in English rather
  // than blank, and the row still says who answered.
  const restore = installDocument();
  resetI18n();
  registerLocale('zh-CN', { Confirm: '确认' });
  setLocale('zh-CN');
  try {
    const panel = createLargeFlowsPanel({
      root: new FakeElement('section'),
      countsNode: new FakeElement('p'),
    });
    panel.services.fetchFlows = async () => flows([AGENT_LINE]);
    await panel.refresh();

    const row = texts(panel.nodes.list.children[0]).join(' ');
    assert.match(row, /set by Agent/);
    assert.match(row, /确认/);
    assert.ok(missingKeys('zh-CN').includes('set by Agent'));
  } finally {
    setLocale('en');
    resetI18n();
    restore();
  }
});
