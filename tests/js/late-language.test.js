// SPDX-License-Identifier: AGPL-3.0-or-later
//
// A label captured when the module was imported is a label that never changes
// language.
//
// `main.js` imports every module first and chooses the language afterwards, on
// purpose: the panels render once at boot, and re-running each of them in place
// would be a second definition of what the page shows. That ordering makes one
// mistake very easy to write and impossible to see in English. A module-level
// table -- `PRESETS` in the range control, `KINDS` in the filter bar -- holding
// already-translated strings takes its copy while the page is still English and
// keeps it for the rest of the page's life. Every check that reads only the
// dictionary passes, and the control is in English.
//
// So this builds the two controls that come from such tables *after* choosing a
// language, which is the order the page really has, and asks the dictionary
// itself which sentences should have moved.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { availableLocales, setLocale, t } from '../../src/ledgerbox/web/js/i18n.js';
import '../../src/ledgerbox/web/js/locales/all.js';
import { renderFilterControls } from '../../src/ledgerbox/web/js/transaction-filters.js';
import { createDateRange } from '../../src/ledgerbox/web/js/date-range.js';

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.className = '';
    this.textContent = '';
    this.value = '';
    this.type = '';
    this.hidden = false;
    this.label = '';
    this.placeholder = '';
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    return child;
  }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
}

function installDocument() {
  const previous = globalThis.document;
  globalThis.document = { createElement: (tag) => new FakeElement(tag) };
  return () => { globalThis.document = previous; };
}

function everything(node) {
  return [node, ...node.children.flatMap(everything)];
}

/**
 * Everything the built control puts in front of a reader: text, the `<optgroup>`
 * name, the placeholder, and the attributes a screen reader announces.
 */
function shownBy(node) {
  const said = new Set();
  for (const child of everything(node)) {
    for (const value of [child.textContent, child.label, child.placeholder]) {
      if (value) said.add(value);
    }
    for (const value of child.attributes.values()) said.add(value);
  }
  return said;
}

function buildFilterBar() {
  const host = new FakeElement('div');
  renderFilterControls({
    querySelector: (selector) => (selector === '[data-txn="controls"]' ? host : null),
  });
  return host;
}

function buildRangeControl() {
  const parts = {
    preset: new FakeElement('select'),
    custom: new FakeElement('div'),
    since: new FakeElement('input'),
    until: new FakeElement('input'),
    notice: new FakeElement('p'),
  };
  createDateRange({
    root: {
      querySelector(selector) {
        const match = selector.match(/^\[data-range="(.+)"\]$/);
        return match ? parts[match[1]] || null : null;
      },
    },
    onChange: () => {},
    today: new Date(2026, 7, 19),
  });
  return parts.preset;
}

/** The sentences these two controls are built from, as their modules write them. */
const FILTER_BAR = [
  'Filter and sort the transactions', "Search the bank's line", 'part of a description',
  'Month', 'Any month', 'Category', 'Any category', 'Nothing claimed this',
  'Transfers', 'Included', 'Only transfers', 'Excluding transfers',
  'Direction', 'Either way', 'Into the account', 'Out of the account',
  'Sort by', 'Date', 'Amount', 'Description', 'Statement month',
  'Order', 'Descending', 'Ascending', 'Clear filters',
];
const RANGE_CONTROL = [
  'All time', 'Last 7 days', 'Last month', 'Last 3 months', 'Last 6 months',
  'Last 12 months', 'Custom…',
];

const CONTROLS = [
  ['the filter bar', buildFilterBar, FILTER_BAR],
  ['the range control', buildRangeControl, RANGE_CONTROL],
];

for (const tag of availableLocales().filter((locale) => locale !== 'en')) {
  for (const [what, build, sentences] of CONTROLS) {
    test(`${what} speaks ${tag} when ${tag} was chosen after the imports`, () => {
      const restore = installDocument();
      try {
        assert.equal(setLocale(tag), true);
        const shown = shownBy(build());
        const stillEnglish = sentences.filter(
          (sentence) => t(sentence) !== sentence && shown.has(sentence),
        );
        assert.deepEqual(
          stillEnglish,
          [],
          `${what} shows these in English although ${tag} answers for them`,
        );
        // Not vacuous: the control has to have said something at all.
        assert.ok(shown.size > sentences.length / 2);
      } finally {
        setLocale('en');
        restore();
      }
    });
  }
}
