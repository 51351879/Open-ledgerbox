// SPDX-License-Identifier: AGPL-3.0-or-later
//
// A dictionary is a set of claims about what the interface says. These check
// the claims against the interface.
//
// The failure they exist for is specific and likely: a translation is written
// by an Agent following a Skill, and an Agent that cannot find a sentence will
// happily produce a plausible one. A key nobody's page contains is a
// translation that will never appear, sitting in the file looking like
// coverage -- the same shape as a test that cannot fail.

import { strict as assert } from 'node:assert';
import { readFile, readdir } from 'node:fs/promises';
import { test } from 'node:test';

import {
  availableLocales,
  currentLocale,
  missingKeys,
  setLocale,
  t,
} from '../../src/ledgerbox/web/js/i18n.js';
import { DICTIONARIES, PARTS } from '../../src/ledgerbox/web/js/locales/all.js';

const WEB = new URL('../../src/ledgerbox/web/', import.meta.url);

/** Every language this build actually ships, English aside. */
const SHIPPED = availableLocales().filter((tag) => tag !== 'en');

/** `{name}` -- the same shape i18n.js fills. */
const PLACEHOLDER = /\{(\w+)\}/g;

function normalize(text) {
  return text.replace(/\s+/g, ' ').trim();
}

function names(text) {
  return Array.from(text.matchAll(PLACEHOLDER), (match) => match[1]).sort().join('|');
}

function dictionaryOf(tag) {
  // The object the page registers, not a regex over its source. Parsing the
  // file here would be a second reading of it, and the two would disagree the
  // first time a long key was wrapped across lines -- which zh-CN already does.
  return Object.keys(DICTIONARIES[tag] ?? {});
}

async function interfaceText() {
  // The markup, plus every module that could hand a sentence to `t()`. Both,
  // because static chrome and rendered strings are the two places a key may
  // legitimately come from, and a check that knew only one would reject
  // correct work.
  const html = await readFile(new URL('index.html', WEB), 'utf8');
  const scripts = await readdir(new URL('js/', WEB));
  const sources = await Promise.all(
    scripts
      .filter((name) => name.endsWith('.js'))
      .map((name) => readFile(new URL(`js/${name}`, WEB), 'utf8')),
  );
  // Splice adjacent string literals together before searching. A sentence too
  // long for one line is written as `'first half ' + 'second half'`, in the
  // source and in the dictionary alike, and a haystack that kept the `' + '`
  // reports the two longest and most carefully worded sentences on the page as
  // inventions. Found by adding them.
  return normalize([html, ...sources].join('\n')).replace(/'\s*\+\s*'/g, '');
}

test('this build ships at least one language besides English', () => {
  assert.ok(SHIPPED.length > 0, 'every check below would be vacuous with none');
});

test('every locale file beside all.js is registered by it', async () => {
  // A dictionary nobody imports is unreachable, and unreachable is
  // indistinguishable from absent when you are reading the directory.
  const all = await readFile(new URL('js/locales/all.js', WEB), 'utf8');
  const files = (await readdir(new URL('js/locales/', WEB)))
    .filter((name) => name.endsWith('.js') && name !== 'all.js');

  assert.ok(files.length > 0);
  for (const name of files) {
    // `<tag>.<region>.js`: a language too long for one file arrives in
    // several, and the tag is the part in front of the first dot. No BCP-47
    // tag contains one, so this cannot swallow part of a language's name.
    const tag = name.split('.')[0];
    assert.ok(all.includes(`./${name}`), `locales/all.js does not import ${name}`);
    assert.ok(tag in DICTIONARIES, `locales/all.js imports ${name} but never registers ${tag}`);
  }
});

test('no sentence is answered twice for one language', () => {
  // Splitting a dictionary across files buys the 400-line rule and costs
  // this: the same English sentence in two of them is two answers to one
  // question, and which one the page shows is decided by the order
  // `all.js` happens to import in. The merge is silent about it, so this
  // is not.
  const twice = [];
  for (const [tag, parts] of Object.entries(PARTS)) {
    const seen = new Set();
    for (const part of parts) {
      for (const key of Object.keys(part)) {
        if (seen.has(key)) twice.push(`${tag}: ${key}`);
        seen.add(key);
      }
    }
  }
  assert.deepEqual(twice, []);
});

for (const tag of SHIPPED) {
  test(`${tag} translates only sentences the interface really contains`, async () => {
    const haystack = await interfaceText();
    const keys = dictionaryOf(tag);
    assert.ok(keys.length > 0, `the dictionary for ${tag} is empty`);

    const invented = keys.filter((key) => !haystack.includes(normalize(key)));
    assert.deepEqual(
      invented,
      [],
      `locales/${tag}.js translates sentences that appear nowhere in the interface`,
    );
  });

  test(`${tag} answers every key it claims`, () => {
    const keys = dictionaryOf(tag);
    assert.equal(setLocale(tag), true);
    assert.equal(currentLocale(), tag);

    const unanswered = keys.filter((key) => t(key) === normalize(key));
    setLocale('en');
    assert.deepEqual(
      unanswered,
      [],
      'these keys are in the dictionary and still come back English: an empty, '
        + 'non-string, or placeholder-breaking value',
    );
  });

  test(`${tag} keeps every placeholder of every sentence it translates`, () => {
    // Belt and braces over the fallback in i18n.js: that one keeps a broken
    // sentence off the page, this one keeps it out of the repository.
    setLocale(tag);
    const broken = dictionaryOf(tag).filter((key) => names(t(key)) !== names(normalize(key)));
    setLocale('en');
    assert.deepEqual(broken, []);
  });

  test(`asking ${tag} for something it never learned reports the gap`, () => {
    setLocale(tag);
    const invented = 'A sentence no dictionary in this repository contains.';
    assert.equal(t(invented), invented);
    assert.ok(missingKeys(tag).includes(invented));
    setLocale('en');
  });
}
