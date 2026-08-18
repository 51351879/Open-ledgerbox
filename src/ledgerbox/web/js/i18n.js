// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The dictionary layer. **The English sentence is the key and the default.**
//
// That choice is the whole design. A key like `upload.dropzone.subtitle` needs
// a second file open to know what the page says, goes stale without going
// wrong, and lets a sentence be edited in one place and translated in another
// until they mean different things. With the sentence as the key, an English
// page is the source of truth by construction: every existing test in this
// repository still asserts English strings and none of them had to change,
// because `t('Add statements')` in an unconfigured page returns exactly
// `Add statements`.
//
// The price is that editing English wording orphans its translations. That is
// the correct price to pay here -- an orphan falls back to English, which is
// true, where a stale key silently keeps showing the old sentence, which is
// not.
//
// **Nothing is guessed.** A translation that is absent, blank, not a string,
// or whose placeholders do not match the English is not used; the English is
// shown instead and the key is recorded in `missingKeys()`. A page that says
// `{count} transactions` or an empty button is worse than an untranslated one,
// and this project's argument is that a value you cannot account for does not
// get displayed.
//
// No DOM and no storage is touched here, so `node --test` can refute all of it
// without a browser. Reading `localStorage` and choosing a locale on load is
// main.js's job.

/** The locale everything falls back to. Its dictionary is the keys themselves. */
const DEFAULT_LOCALE = 'en';

/** Attributes a screen reader or a browser shows to a person, and so translates. */
const TRANSLATED_ATTRIBUTES = ['aria-label', 'placeholder', 'title'];

const TEXT_NODE = 3;

/**
 * Elements whose text is not text.
 *
 * `<noscript>` is the one that matters, and it was found by opening the page
 * rather than by any test here. With scripting enabled a browser parses that
 * element's contents as a single literal string -- markup and all -- so the
 * sweep saw `<p class="container noscript"> ledgerbox needs...` as one
 * sentence, missed it, and reported it. The dictionary entry written for it
 * could never have matched.
 *
 * The deeper reason is better than the parsing one: `<noscript>` is displayed
 * exactly when JavaScript is off, and this module is JavaScript. A sentence
 * that only appears when nothing here runs cannot be translated from here, and
 * pretending otherwise would leave a dead entry looking like coverage.
 */
const OPAQUE_TAGS = new Set(['NOSCRIPT', 'SCRIPT', 'STYLE', 'TEMPLATE', 'CODE', 'PRE']);

/** `{name}` -- deliberately not `{{name}}`; one shape, stated once. */
const PLACEHOLDER = /\{(\w+)\}/g;

/** tag -> Map(english sentence -> translated sentence) */
const dictionaries = new Map();

/** tag -> Set(english sentence) that this locale could not answer for. */
const gaps = new Map();

let active = DEFAULT_LOCALE;

/**
 * One sentence, one key.
 *
 * Markup indents its prose, so a paragraph in `index.html` arrives as a text
 * node with newlines and leading spaces inside the sentence. Without this, the
 * dictionary key for such a paragraph would have to reproduce the file's
 * indentation exactly and would break the next time anybody re-wrapped the
 * HTML -- a translation invalidated by a whitespace edit is a translation
 * nobody will maintain.
 */
export function normalize(text) {
  return text.replace(/\s+/g, ' ').trim();
}

function placeholderNames(text) {
  return new Set(Array.from(text.matchAll(PLACEHOLDER), (match) => match[1]));
}

function sameNames(left, right) {
  if (left.size !== right.size) return false;
  for (const name of left) {
    if (!right.has(name)) return false;
  }
  return true;
}

function record(english) {
  if (!gaps.has(active)) gaps.set(active, new Set());
  gaps.get(active).add(english);
}

// One place decides whether a translation may be shown, so `t` and the static
// sweep can never disagree about it.
function lookup(sentence) {
  const english = normalize(sentence);
  if (active === DEFAULT_LOCALE) return english;
  const translated = dictionaries.get(active)?.get(english);
  if (typeof translated !== 'string' || translated.trim() === '') {
    record(english);
    return english;
  }
  if (!sameNames(placeholderNames(english), placeholderNames(translated))) {
    record(english);
    return english;
  }
  return translated;
}

function fill(text, values) {
  // A name with no value keeps its braces rather than becoming `undefined`.
  // The omission then shows up in the page and in a screenshot of it, which is
  // the difference between a bug you can see and one you cannot.
  return text.replace(PLACEHOLDER, (whole, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : whole,
  );
}

/**
 * The English sentence, translated if this locale can be trusted with it.
 *
 * @param {string} english the sentence as the page would say it in English
 * @param {Record<string, unknown>} [values] `{name}` substitutions
 */
export function t(english, values) {
  const text = lookup(english);
  return values ? fill(text, values) : fill(text, {});
}

/**
 * A copy object read through the dictionary at the moment it is read.
 *
 * Most modules here keep their prose in one `const COPY = {...}` at the top,
 * which is already the right shape: the English sentence is the value, and the
 * value is the key. Wrapping the object translates every one of its sentences
 * without touching a single reading site -- and there are a lot of reading
 * sites. `CONNECTION_COPY.panel` alone is read by six modules.
 *
 * **Shallow on purpose.** Only string values are looked up; objects, numbers
 * and functions pass through untouched, so a nested `{ tone, label }` keeps its
 * `label` in English rather than being half-translated by a rule nobody wrote
 * down. A map with prose one level down gets `t()` at its reading site instead.
 *
 * Reading is live rather than snapshot, so a module imported before the locale
 * was chosen still speaks it afterwards -- which is the ordering the page has,
 * since `main.js` picks the language after every module is imported.
 */
export function localized(copy) {
  return new Proxy(copy, {
    get(target, key) {
      const value = Reflect.get(target, key);
      return typeof value === 'string' ? t(value) : value;
    },
  });
}

/**
 * Add or extend a dictionary. Merging rather than replacing: a language may
 * arrive in more than one file, and the second must not delete the first.
 */
export function registerLocale(tag, entries) {
  if (tag === DEFAULT_LOCALE) return;
  if (!dictionaries.has(tag)) dictionaries.set(tag, new Map());
  const dictionary = dictionaries.get(tag);
  for (const [english, translated] of Object.entries(entries ?? {})) {
    dictionary.set(english, translated);
  }
}

/**
 * Switch languages. Returns whether the tag was one this page actually has --
 * an unknown tag leaves the page in English rather than pretending.
 */
export function setLocale(tag) {
  if (tag === DEFAULT_LOCALE) {
    active = DEFAULT_LOCALE;
    return true;
  }
  if (!dictionaries.has(tag)) return false;
  active = tag;
  return true;
}

export function currentLocale() {
  return active;
}

/** English first, then every registered language, so a control can list them. */
export function availableLocales() {
  return [DEFAULT_LOCALE, ...Array.from(dictionaries.keys()).sort()];
}

/**
 * Every sentence this locale was asked for and could not answer.
 *
 * Exported because it is the honest report a translator needs: run the page,
 * read the list, fill the gaps. It is also what stops the fallback from being
 * invisible -- a silent fallback is a page that looks finished.
 */
export function missingKeys(tag = active) {
  return Array.from(gaps.get(tag) ?? []).sort();
}

/**
 * Translate markup that was written in English.
 *
 * `elements` is any iterable of elements -- in the page, `document.querySelectorAll('*')`.
 * Passing the collection rather than the document keeps this module free of
 * the DOM and lets the counterexamples hand it plain objects.
 *
 * Only whole text nodes are matched, trimmed, so the indentation of the HTML
 * survives; the sentence is replaced inside its own whitespace rather than the
 * node being rewritten. Nothing here parses or assigns markup.
 */
export function applyStaticText(elements) {
  if (active === DEFAULT_LOCALE) return 0;
  let replaced = 0;
  for (const element of elements) {
    if (OPAQUE_TAGS.has(element.tagName)) continue;
    for (const child of element.childNodes ?? []) {
      if (child.nodeType !== TEXT_NODE) continue;
      const raw = child.nodeValue;
      if (typeof raw !== 'string') continue;
      const sentence = raw.trim();
      if (sentence === '') continue;
      const translated = lookup(sentence);
      if (translated === normalize(sentence)) continue;
      // Replace the sentence inside its own whitespace, so the indentation of
      // the markup is exactly where it was.
      child.nodeValue = raw.replace(sentence, translated);
      replaced += 1;
    }
    for (const name of TRANSLATED_ATTRIBUTES) {
      const value = element.getAttribute?.(name);
      if (typeof value !== 'string' || value.trim() === '') continue;
      const translated = lookup(value);
      if (translated === normalize(value)) continue;
      element.setAttribute(name, translated);
      replaced += 1;
    }
  }
  return replaced;
}

/**
 * Forget every dictionary and go back to English.
 *
 * For the counterexamples. Module state shared between tests is how a suite
 * starts passing for the wrong reason, and a reset a test can call is cheaper
 * than a module every test has to re-import.
 */
export function resetI18n() {
  dictionaries.clear();
  gaps.clear();
  active = DEFAULT_LOCALE;
}
