// SPDX-License-Identifier: AGPL-3.0-or-later

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { ApiError } from '../../src/ledgerbox/web/js/api.js';
import { createProposalPanel } from '../../src/ledgerbox/web/js/agent-proposals.js';

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

test('empty and offline proposal states stay bounded and keep manual classification available', async () => {
  const restore = installDocument();
  try {
    const root = new FakeElement('section');
    const panel = createProposalPanel({
      root,
      services: {
        fetchRuns: async () => [],
        fetchRun: async () => { throw new Error('must not fetch an absent run'); },
        fetchCategories: async () => [],
        review: async () => {},
        withdraw: async () => {},
      },
    });
    assert.match(panel.nodes.note.textContent, /only lists suggestions the Agent submitted/i);
    assert.match(panel.nodes.note.textContent, /Nothing claimed this/i);
    await panel.refresh();
    assert.match(panel.nodes.body.textContent, /No Agent proposal runs yet/);
    assert.match(panel.nodes.body.textContent, /manual transaction controls/i);
    assert.equal(panel.nodes.status.textContent, 'No Agent proposals to review.');

    panel.services.fetchRuns = async () => {
      throw new ApiError(0, 'No answer from ledgerbox.', null);
    };
    await panel.refresh();
    assert.equal(panel.nodes.body.textContent, 'Waiting for the local service.');
    assert.equal(panel.nodes.notice.hidden, true, 'the header connection light owns the reason');
    assert.equal(panel.nodes.status.textContent, 'Agent proposal review is waiting.');
  } finally {
    restore();
  }
});

test('a completed run explains that zero pending does not include omitted candidates', async () => {
  const restore = installDocument();
  try {
    const run = {
      run_id: `sha256:${'a'.repeat(64)}`,
      created_at: '2026-08-10T12:00:00Z',
      state: 'completed',
      producer: { client: 'claude-code' },
      proposal_count: 2,
      pending: 0,
      accepted: 2,
      edited: 0,
      rejected: 0,
      withdrawn: 0,
      proposals: [],
    };
    const root = new FakeElement('section');
    const panel = createProposalPanel({
      root,
      services: {
        fetchRuns: async () => [run],
        fetchRun: async () => run,
        fetchCategories: async () => [],
        review: async () => {},
        withdraw: async () => {},
      },
    });
    await panel.refresh();
    const bodyCopy = panel.nodes.body.children.map((child) => child.textContent).join(' ');
    assert.match(bodyCopy, /only means every submitted suggestion/i);
    assert.match(bodyCopy, /Nothing claimed this/i);
  } finally {
    restore();
  }
});
