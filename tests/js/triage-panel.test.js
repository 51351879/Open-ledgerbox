// SPDX-License-Identifier: AGPL-3.0-or-later

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { ApiError } from '../../src/ledgerbox/web/js/api.js';
import { createTriagePanel } from '../../src/ledgerbox/web/js/triage.js';

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
    this.id = '';
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

test('empty and offline triage states preserve other classification paths', async () => {
  const restore = installDocument();
  try {
    const root = new FakeElement('section');
    const panel = createTriagePanel({
      root,
      services: {
        fetchRuns: async () => [],
        fetchRun: async () => { throw new Error('must not fetch an absent run'); },
        fetchCategories: async () => [],
        review: async () => {},
        dismiss: async () => {},
        withdraw: async () => {},
      },
    });
    await panel.refresh();
    assert.match(panel.nodes.body.textContent, /No remaining-coverage triage runs yet/);
    assert.match(panel.nodes.body.textContent, /Manual transaction classification/i);
    assert.equal(panel.nodes.status.textContent, 'No remaining coverage triage to review.');

    panel.services.fetchRuns = async () => {
      throw new ApiError(0, 'No answer from ledgerbox.', null);
    };
    await panel.refresh();
    assert.equal(panel.nodes.body.textContent, 'Waiting for the local service.');
    assert.equal(panel.nodes.notice.hidden, true);
    assert.equal(panel.nodes.status.textContent, 'Remaining coverage triage is waiting.');
  } finally {
    restore();
  }
});
