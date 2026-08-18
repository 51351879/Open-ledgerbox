// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The dictionary layer, refuted one rule at a time.
//
// Every check here is about the same question: what does the page show when a
// translation is absent, empty, or wrong? A UI that answers "nothing" or
// "{count}" in those cases is worse than one that was never translated, and
// this is a project whose entire argument is that a number you cannot account
// for should not be displayed.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  applyStaticText,
  availableLocales,
  currentLocale,
  localized,
  missingKeys,
  registerLocale,
  resetI18n,
  setLocale,
  t,
} from '../../src/ledgerbox/web/js/i18n.js';

function fresh() {
  resetI18n();
}

test('English is the key, the default, and where an unconfigured page stays', () => {
  fresh();
  assert.equal(currentLocale(), 'en');
  assert.equal(t('Nothing claimed this'), 'Nothing claimed this');
  assert.deepEqual(availableLocales(), ['en']);
});

test('a registered locale translates the sentences it knows', () => {
  fresh();
  registerLocale('zh-CN', { 'Add statements': '添加账单' });
  setLocale('zh-CN');

  assert.equal(currentLocale(), 'zh-CN');
  assert.equal(t('Add statements'), '添加账单');
  assert.deepEqual(availableLocales(), ['en', 'zh-CN']);
});

test('a sentence the dictionary never learned falls back to English and is recorded', () => {
  fresh();
  registerLocale('zh-CN', { 'Add statements': '添加账单' });
  setLocale('zh-CN');

  assert.equal(t('Clear results'), 'Clear results');
  assert.deepEqual(missingKeys(), ['Clear results']);
});

test('an empty translation is a missing one', () => {
  // A blank label is the worst of the three outcomes: it neither says the
  // thing nor shows that it failed to.
  fresh();
  registerLocale('zh-CN', { 'Clear results': '   ' });
  setLocale('zh-CN');

  assert.equal(t('Clear results'), 'Clear results');
  assert.deepEqual(missingKeys(), ['Clear results']);
});

test('a non-string translation cannot reach the page', () => {
  fresh();
  registerLocale('zh-CN', { 'Clear results': 42 });
  setLocale('zh-CN');

  assert.equal(t('Clear results'), 'Clear results');
  assert.deepEqual(missingKeys(), ['Clear results']);
});

test('placeholders are filled from the values given', () => {
  fresh();
  assert.equal(
    t('{count} transactions need classification', { count: 14 }),
    '14 transactions need classification',
  );

  registerLocale('zh-CN', { '{count} transactions need classification': '{count} 笔待分类' });
  setLocale('zh-CN');
  assert.equal(t('{count} transactions need classification', { count: 14 }), '14 笔待分类');
});

test('a translation that loses a placeholder is refused, not rendered', () => {
  // This is the case that made the check necessary. A sentence whose number
  // has been translated away still reads like a sentence -- "transactions need
  // classification" -- and is a page stating a fact with the fact removed.
  fresh();
  registerLocale('zh-CN', { '{count} transactions need classification': '有交易待分类' });
  setLocale('zh-CN');

  assert.equal(
    t('{count} transactions need classification', { count: 14 }),
    '14 transactions need classification',
  );
  assert.deepEqual(missingKeys(), ['{count} transactions need classification']);
});

test('a translation that invents a placeholder is refused too', () => {
  fresh();
  registerLocale('zh-CN', { 'Clear results': '清除 {count} 条结果' });
  setLocale('zh-CN');

  assert.equal(t('Clear results'), 'Clear results');
  assert.deepEqual(missingKeys(), ['Clear results']);
});

test('a placeholder with no value left is left alone rather than blanked', () => {
  // `undefined` printed into a sentence about money is the shape of the defect
  // this project exists to refuse. The brace survives so the omission is
  // visible in the page and in a screenshot.
  fresh();
  assert.equal(
    t('{count} transactions need classification', {}),
    '{count} transactions need classification',
  );
});

test('selecting a locale nobody registered leaves the page in English', () => {
  fresh();
  assert.equal(setLocale('ja'), false);
  assert.equal(currentLocale(), 'en');
  assert.equal(t('Add statements'), 'Add statements');
});

test('going back to English clears nothing and translates nothing', () => {
  fresh();
  registerLocale('zh-CN', { 'Add statements': '添加账单' });
  setLocale('zh-CN');
  assert.equal(t('Add statements'), '添加账单');

  assert.equal(setLocale('en'), true);
  assert.equal(t('Add statements'), 'Add statements');
  assert.deepEqual(missingKeys(), []);
});

test('registering a locale twice merges rather than replaces', () => {
  // Locale files are split by whoever writes them; a second file must not
  // silently delete the first one's work.
  fresh();
  registerLocale('zh-CN', { 'Add statements': '添加账单' });
  registerLocale('zh-CN', { 'Clear results': '清除结果' });
  setLocale('zh-CN');

  assert.equal(t('Add statements'), '添加账单');
  assert.equal(t('Clear results'), '清除结果');
});

// ---------------------------------------------------------------------------
// The static sweep over markup already written in English
// ---------------------------------------------------------------------------

function textNode(value) {
  return { nodeType: 3, nodeValue: value };
}

function element(text, attributes = {}) {
  return {
    nodeType: 1,
    attributes,
    childNodes: [textNode(text)],
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attributes, name)
        ? this.attributes[name]
        : null;
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
}

test('the static sweep replaces English markup it has a translation for', () => {
  fresh();
  registerLocale('zh-CN', { 'Add statements': '添加账单' });
  setLocale('zh-CN');

  const heading = element('Add statements');
  const untouched = element('Clear results');
  applyStaticText([heading, untouched]);

  assert.equal(heading.childNodes[0].nodeValue, '添加账单');
  assert.equal(untouched.childNodes[0].nodeValue, 'Clear results');
});

test('the static sweep keeps the surrounding whitespace of the markup', () => {
  // Indented HTML puts newlines and spaces in the same text node as the
  // sentence. Replacing the node wholesale reflows the page; replacing only
  // the sentence does not.
  fresh();
  registerLocale('zh-CN', { 'Add statements': '添加账单' });
  setLocale('zh-CN');

  const spaced = element('\n      Add statements\n    ');
  applyStaticText([spaced]);

  assert.equal(spaced.childNodes[0].nodeValue, '\n      添加账单\n    ');
});

test('the static sweep translates the attributes a reader is read', () => {
  fresh();
  registerLocale('zh-CN', {
    'Filter transactions': '筛选交易',
    'Search descriptors': '搜索描述符',
  });
  setLocale('zh-CN');

  const input = element('', {
    'aria-label': 'Filter transactions',
    placeholder: 'Search descriptors',
    'data-category': 'transfer',
  });
  applyStaticText([input]);

  assert.equal(input.getAttribute('aria-label'), '筛选交易');
  assert.equal(input.getAttribute('placeholder'), '搜索描述符');
  assert.equal(input.getAttribute('data-category'), 'transfer', 'identifiers are not text');
});

test('the static sweep in English changes nothing at all', () => {
  fresh();
  registerLocale('zh-CN', { 'Add statements': '添加账单' });

  const heading = element('Add statements');
  applyStaticText([heading]);

  assert.equal(heading.childNodes[0].nodeValue, 'Add statements');
  assert.deepEqual(missingKeys(), [], 'English is not a language with gaps');
});

test('a paragraph indented by its markup is found under its one-line key', () => {
  // The reason keys are normalised. HTML wraps and indents prose, so the text
  // node carries newlines and leading spaces inside the sentence; a dictionary
  // keyed on that would be invalidated by re-wrapping the file, and a
  // translation a whitespace edit can break is one nobody maintains.
  fresh();
  registerLocale('zh-CN', {
    'One request per file, in order.': '每个文件一次请求，按顺序。',
  });
  setLocale('zh-CN');

  const paragraph = element('\n      One request per file,\n      in order.\n    ');
  applyStaticText([paragraph]);

  assert.equal(paragraph.childNodes[0].nodeValue, '\n      每个文件一次请求，按顺序。\n    ');
});

test('normalisation is the same rule for t() and for the sweep', () => {
  fresh();
  registerLocale('zh-CN', { 'Add statements': '添加账单' });
  setLocale('zh-CN');
  assert.equal(t('Add   statements'), '添加账单');
  assert.equal(t('Add statements'), '添加账单');
});

test('an element whose text is not text is left alone', () => {
  // Found by opening the page, not here. With scripting enabled a browser
  // parses `<noscript>` contents as one literal string, markup included, so
  // the sweep saw a whole `<p ...>` element as a sentence and reported it
  // missing -- while the dictionary entry written for it could never match.
  // The better reason is that `<noscript>` is shown exactly when this module
  // does not run.
  fresh();
  registerLocale('zh-CN', { 'Add statements': '添加账单' });
  setLocale('zh-CN');

  const hidden = element('Add statements');
  hidden.tagName = 'NOSCRIPT';
  const code = element('Add statements');
  code.tagName = 'CODE';
  const normal = element('Add statements');
  normal.tagName = 'H2';
  applyStaticText([hidden, code, normal]);

  assert.equal(hidden.childNodes[0].nodeValue, 'Add statements');
  assert.equal(code.childNodes[0].nodeValue, 'Add statements', 'a quoted label is a value');
  assert.equal(normal.childNodes[0].nodeValue, '添加账单');
  assert.deepEqual(missingKeys(), [], 'an element we skip is not a gap we report');
});

// ---------------------------------------------------------------------------
// Copy objects read through the dictionary
// ---------------------------------------------------------------------------

test('a wrapped copy object translates every sentence it holds', () => {
  fresh();
  const COPY = localized({ up: 'Ledgerbox online', retry: 'Try again now' });
  assert.equal(COPY.up, 'Ledgerbox online', 'English is unchanged by wrapping');

  registerLocale('zh-CN', { 'Ledgerbox online': 'Ledgerbox 在线' });
  setLocale('zh-CN');
  assert.equal(COPY.up, 'Ledgerbox 在线');
  assert.equal(COPY.retry, 'Try again now', 'an untranslated sentence still falls back');
  assert.deepEqual(missingKeys(), ['Try again now']);
});

test('a wrapped copy object is read live, not snapshotted', () => {
  // main.js picks the language after every module has been imported, so a copy
  // frozen at import time would be permanently English no matter what the
  // reader chose.
  fresh();
  const COPY = localized({ up: 'Ledgerbox online' });
  registerLocale('zh-CN', { 'Ledgerbox online': 'Ledgerbox 在线' });

  assert.equal(COPY.up, 'Ledgerbox online');
  setLocale('zh-CN');
  assert.equal(COPY.up, 'Ledgerbox 在线');
  setLocale('en');
  assert.equal(COPY.up, 'Ledgerbox online');
});

test('a wrapped copy object leaves everything that is not a sentence alone', () => {
  // Shallow deliberately: a `{ tone, label }` value must not be half-translated
  // by a rule nobody wrote down, and a number is not prose.
  fresh();
  const nested = { tone: 'ok', label: 'Imported' };
  const COPY = localized({ nested, threshold: 1000, describe: () => 'Imported' });
  registerLocale('zh-CN', { Imported: '已导入' });
  setLocale('zh-CN');

  assert.equal(COPY.nested, nested);
  assert.equal(COPY.nested.label, 'Imported');
  assert.equal(COPY.threshold, 1000);
  assert.equal(COPY.describe(), 'Imported');
  assert.deepEqual(missingKeys(), [], 'nothing that is not a sentence was even looked up');
});
