// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The bulk selector's failure path.  A real-browser acceptance round found that
// losing the service while "Select all matching" fetched its ids produced no
// message and one unhandled rejection: the click looked like it did nothing.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { createBulkBar } from '../../src/ledgerbox/web/js/transaction-bulk.js';

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.className = '';
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
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

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  async click() {
    const listener = this.listeners.get('click');
    if (listener) {
      await listener();
    }
  }
}

function installDocument() {
  const previous = globalThis.document;
  globalThis.document = {
    createElement: (tag) => new FakeElement(tag),
  };
  return () => {
    globalThis.document = previous;
  };
}

test('selecting all reports an id-fetch failure and keeps the existing selection', async () => {
  const restore = installDocument();
  try {
    const notices = [];
    const bulk = createBulkBar({
      matched: () => 2,
      idsForFilter: async () => {
        throw new Error('service stopped');
      },
      onApplied: (message, kind) => notices.push({ message, kind }),
      onSelectionChange: () => {},
    });
    const host = new FakeElement('div');
    bulk.render(host);
    bulk.toggle({ txn_id: 'one', category_decided_by: 'none', category_id: null }, true);

    const actions = bulk.node.children[2];
    const selectAll = actions.children[1];
    await selectAll.click();

    assert.equal(bulk.has('one'), true, 'the selection a person already made survives');
    assert.deepEqual(notices, [{
      message: 'service stopped Nothing was changed.',
      kind: 'fail',
    }]);
    assert.equal(actions.children[0].disabled, false, 'the apply control is usable again');
  } finally {
    restore();
  }
});
