// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The planning notes panel, checked for the two things translating it can
// break.
//
// **The space before the figure.** This panel writes one sentence in three
// nodes so the amount can carry the money styling, and the English sentence
// used to end in a space to separate itself from it. Dictionary keys are
// whitespace-normalised and English reads through the same lookup as every
// other language, so that trailing space would have been trimmed out of the
// key and off the page with it, welding `net` to `$1,234.56`. The separator
// belongs at the reading site; this refuses the version where it does not.
//
// **The table one level down.** `localized()` is shallow on purpose, so the
// headings and notes inside `RANGES` are not reached by wrapping the copy
// object. They take `t()` where they are read, and a locale that has them must
// actually show them.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { availableLocales, setLocale } from '../../src/ledgerbox/web/js/i18n.js';
import '../../src/ledgerbox/web/js/locales/all.js';
import { createAdvicePanel } from '../../src/ledgerbox/web/js/advice.js';

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.className = '';
    this.textContent = '';
    this.hidden = false;
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
  getAttribute(name) { return this.attributes.get(name) ?? null; }
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

function flatten(node) {
  if (!node.children || node.children.length === 0) return node.textContent || '';
  return node.children.map(flatten).join('');
}

/** Open the first range and hand back everything the panel then says. */
function openFirstRange(root) {
  const choices = root.children.find((child) => child.className === 'advice__choices');
  const first = choices.children[0];
  first.listeners.get('click')();
  return flatten(root);
}

test('the measured net keeps its separator from the figure', () => {
  const restore = installDocument();
  try {
    const root = new FakeElement('section');
    const panel = createAdvicePanel({ root, net: () => 123456 });
    panel.refresh();
    const said = openFirstRange(root);
    assert.match(
      said,
      /these statements net \$1,234\.56\. That is what the documents say/,
      'the sentence, the amount and the sentence after it are one readable line',
    );
  } finally {
    restore();
  }
});

for (const tag of availableLocales().filter((locale) => locale !== 'en')) {
  test(`${tag} reaches the notes one level inside the range table`, () => {
    const restore = installDocument();
    try {
      setLocale('en');
      const english = new FakeElement('section');
      createAdvicePanel({ root: english, net: () => 123456 });
      const englishSaid = openFirstRange(english);

      assert.equal(setLocale(tag), true);
      const root = new FakeElement('section');
      createAdvicePanel({ root, net: () => 123456 });
      const said = openFirstRange(root);

      // The heading and every note of that range moved, not just the prose the
      // copy object holds: a wrapper that stops at the top level would leave
      // this half in English and look finished.
      assert.notEqual(said, englishSaid);
      for (const sentence of englishSaid.split('. ')) {
        if (sentence.length < 40) continue;
        assert.ok(
          !said.includes(sentence),
          `${tag} still shows this in English: ${sentence}`,
        );
      }
      // The amount is substituted, never looked up.
      assert.ok(said.includes('$1,234.56'));
    } finally {
      setLocale('en');
      restore();
    }
  });
}
