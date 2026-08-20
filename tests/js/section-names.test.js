// SPDX-License-Identifier: AGPL-3.0-or-later
//
// One thing, one name.
//
// The directory down the side of the page renders through `t()`. Three of the
// panels it points at built their own headings out of English literals, so a
// translated page listed `Agent 提案` in the directory and headed the section
// it scrolls to `Agent proposals`. That is worse than leaving the page in
// English: an untranslated page at least agrees with itself, while this one
// sends a reader looking for a name that is not on it.
//
// The check is a comparison rather than a table of expected strings. Each
// section's name is rendered twice -- once in English, once in the locale --
// and a locale that moved the directory entry while leaving the heading where
// it was fails. Nothing here needs editing when a name is reworded, and it
// covers every locale this build ships rather than the one that exposed it.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { availableLocales, setLocale } from '../../src/ledgerbox/web/js/i18n.js';
import '../../src/ledgerbox/web/js/locales/all.js';
import { addDirectory } from '../../src/ledgerbox/web/js/sidebar-nav.js';
import { createProposalPanel } from '../../src/ledgerbox/web/js/agent-proposals.js';
import { createTriagePanel } from '../../src/ledgerbox/web/js/triage.js';
import { createAdvicePanel } from '../../src/ledgerbox/web/js/advice.js';

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

/** Panels that draw their own heading, by the anchor the directory links to. */
const PANELS = {
  'agent-proposals': (root) => createProposalPanel({
    root,
    services: {
      fetchRuns: async () => [],
      fetchRun: async () => { throw new Error('unused'); },
      fetchCategories: async () => [],
      review: async () => {},
      withdraw: async () => {},
    },
  }),
  'agent-triage': (root) => createTriagePanel({
    root,
    services: {
      fetchRuns: async () => [],
      fetchRun: async () => { throw new Error('unused'); },
      fetchCategories: async () => [],
      review: async () => {},
      dismiss: async () => {},
      withdraw: async () => {},
    },
  }),
  advice: (root) => createAdvicePanel({ root }),
};

function findTitle(node) {
  if (node.className === 'panel__title') return node.textContent;
  for (const child of node.children ?? []) {
    const found = findTitle(child);
    if (found !== null) return found;
  }
  return null;
}

/** anchor -> the name the directory shows, in whatever locale is active. */
function directoryNames() {
  const root = new FakeElement('div');
  addDirectory(root);
  const nav = root.children[0];
  const names = {};
  for (const link of nav.children) {
    names[link.getAttribute('href').slice(1)] = link.children[0].textContent;
  }
  return names;
}

/** anchor -> the name the panel puts at the top of its own section. */
function headingNames() {
  const names = {};
  for (const [anchor, create] of Object.entries(PANELS)) {
    const root = new FakeElement('section');
    create(root);
    const title = findTitle(root);
    assert.ok(title, `${anchor} draws no panel__title`);
    names[anchor] = title;
  }
  return names;
}

for (const tag of availableLocales().filter((locale) => locale !== 'en')) {
  test(`${tag} never gives one section two names`, () => {
    const restore = installDocument();
    try {
      setLocale('en');
      const englishDirectory = directoryNames();
      const englishHeadings = headingNames();

      assert.equal(setLocale(tag), true);
      const directory = directoryNames();
      const headings = headingNames();

      const disagreeing = Object.keys(PANELS).filter((anchor) => (
        directory[anchor] !== englishDirectory[anchor]
        && headings[anchor] === englishHeadings[anchor]
      ));
      assert.deepEqual(
        disagreeing,
        [],
        `these sections are named in ${tag} in the directory and in English at their own top`,
      );
    } finally {
      setLocale('en');
      restore();
    }
  });
}
