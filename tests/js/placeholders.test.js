// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Every `{name}` a sentence carries is given a value at the site that says it.
//
// `i18n.js` deliberately leaves an unfilled placeholder in the page as
// `{count}` rather than printing `undefined` into a sentence about money, so
// the mistake is visible -- but only to somebody looking at that panel in that
// state. It is written by getting one word wrong: `t('{count} line(s) match',
// { matched })` passes a value under the wrong name, reads correctly, and puts
// `{count} line(s) match` over the transaction table. That is how it was found
// here, by luck rather than by anything that would fail.
//
// So the call sites are read instead. This is a small scanner rather than a
// regular expression: the sentences are split across lines with `+`, the value
// objects contain nested `t()` calls of their own, and a pattern that could
// survive both would be less readable than the parser.

import { strict as assert } from 'node:assert';
import { readFile, readdir } from 'node:fs/promises';
import { test } from 'node:test';

const JS = new URL('../../src/ledgerbox/web/js/', import.meta.url);

/**
 * The `{name}`s in a sentence.
 *
 * A fresh expression every call, and that is not fussiness. A shared global
 * one carries `lastIndex` between calls -- `matchAll` copies it -- so a `test()`
 * somewhere else leaves the next search starting halfway through the sentence
 * and missing a placeholder at the front of it. This check was written with a
 * shared expression, passed against the real defect it was written for, and
 * was itself a check that could not fail.
 */
function placeholdersIn(sentence) {
  return Array.from(sentence.matchAll(/\{(\w+)\}/g), (match) => match[1]);
}

/**
 * From the character after `t(`, the text of the call's arguments.
 *
 * Quotes and template literals are tracked so that a bracket inside a sentence
 * does not close the call, which is the only thing that makes this harder than
 * counting parentheses.
 */
function argumentsAt(source, start) {
  let depth = 1;
  let quote = null;
  for (let index = start; index < source.length; index += 1) {
    const character = source[index];
    if (quote) {
      if (character === '\\') index += 1;
      else if (character === quote) quote = null;
      continue;
    }
    if (character === "'" || character === '"' || character === '`') {
      quote = character;
    } else if (character === '(' || character === '{' || character === '[') {
      depth += 1;
    } else if (character === ')' || character === '}' || character === ']') {
      depth -= 1;
      if (depth === 0) return source.slice(start, index);
    }
  }
  return null;
}

/** The call's arguments, split on the commas that belong to it. */
function topLevel(text) {
  const parts = [];
  let depth = 0;
  let quote = null;
  let from = 0;
  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quote) {
      if (character === '\\') index += 1;
      else if (character === quote) quote = null;
      continue;
    }
    if (character === "'" || character === '"' || character === '`') quote = character;
    else if ('({['.includes(character)) depth += 1;
    else if (')}]'.includes(character)) depth -= 1;
    else if (character === ',' && depth === 0) {
      parts.push(text.slice(from, index));
      from = index + 1;
    }
  }
  parts.push(text.slice(from));
  return parts;
}

/** The sentence a first argument spells, or null when it is not a literal. */
function sentenceOf(text) {
  const trimmed = text.trim();
  if (!trimmed.startsWith("'")) return null;
  const pieces = trimmed.match(/'(?:[^'\\]|\\.)*'/g) || [];
  // Only the literals joined by `+` are the sentence; anything else here means
  // this argument is an expression and not a sentence to check.
  if (!/^(\s*'(?:[^'\\]|\\.)*'\s*\+?)+$/.test(trimmed)) return null;
  return pieces.map((piece) => piece.slice(1, -1)).join('');
}

/** The names a value object supplies, including shorthand ones. */
function namesGiven(text) {
  const trimmed = text.trim();
  if (!trimmed.startsWith('{')) return null;
  const given = new Set();
  for (const entry of topLevel(trimmed.slice(1, -1))) {
    const name = entry.trim().match(/^(\w+)\s*(:|$)/);
    if (name) given.add(name[1]);
  }
  return given;
}

async function callSites() {
  const files = (await readdir(JS)).filter((name) => name.endsWith('.js'));
  const sites = [];
  for (const name of files) {
    const source = await readFile(new URL(name, JS), 'utf8');
    for (let index = source.indexOf('t('); index >= 0; index = source.indexOf('t(', index + 1)) {
      // `t(` and not `format(`, `.filter(` or `assert(`.
      if (index > 0 && /[\w.$]/.test(source[index - 1])) continue;
      const text = argumentsAt(source, index + 2);
      if (text === null) continue;
      const parts = topLevel(text);
      const sentence = sentenceOf(parts[0]);
      if (sentence === null) continue;
      const line = source.slice(0, index).split('\n').length;
      sites.push({ where: `${name}:${line}`, sentence, values: parts[1] });
    }
  }
  return sites;
}

test('the scanner finds the sentences it is supposed to be reading', async () => {
  // A scanner that matched nothing would pass the check below in silence,
  // which is the shape this whole suite exists to refuse.
  const sites = await callSites();
  assert.ok(sites.length > 100, `only ${sites.length} t() call sites found`);
  assert.ok(
    sites.filter((site) => placeholdersIn(site.sentence).length > 0).length > 20,
    'almost no sentence with a placeholder was found',
  );
});

test('every placeholder in a sentence is given a value where it is said', async () => {
  const unfilled = [];
  for (const site of await callSites()) {
    const wanted = placeholdersIn(site.sentence);
    if (wanted.length === 0) continue;
    const given = site.values === undefined ? new Set() : namesGiven(site.values);
    // A value object built somewhere else is not readable from here and is not
    // claimed to be wrong; only a literal one is checked.
    if (given === null) continue;
    for (const name of wanted) {
      if (!given.has(name)) unfilled.push(`${site.where}: {${name}} has no value`);
    }
  }
  assert.deepEqual(unfilled, []);
});
