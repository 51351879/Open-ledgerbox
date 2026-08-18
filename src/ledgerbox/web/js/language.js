// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Where the page meets the dictionary: storage, the `<html lang>` attribute,
// and the one control that changes languages.
//
// It is a separate file from `i18n.js` so that the rules -- what a missing or
// broken translation does -- stay testable under `node --test` with no browser
// anywhere near them. Everything here touches `window`; everything there is a
// function of its arguments.
//
// A stored choice this build does not have is not an error and not a prompt:
// the page stays in English, which is true, and the control still offers what
// it really has. A locale that was removed, or a `localStorage` copied from
// another checkout, resolves that way rather than to a half-translated page.

import { applyStaticText, availableLocales, currentLocale, setLocale } from './i18n.js';
import './locales/all.js';

/** Namespaced, because this origin is `127.0.0.1` and it is not only ours. */
const STORAGE_KEY = 'ledgerbox.locale';

function readStored() {
  // Storage can be switched off entirely. A page that throws on boot because
  // somebody hardened their browser is a worse outcome than an English page.
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStored(tag) {
  try {
    window.localStorage.setItem(STORAGE_KEY, tag);
  } catch {
    // The choice is lost at the next load and the page is still correct now.
  }
}

/**
 * Put the page in the stored language, before anything renders.
 *
 * Returns the locale actually in force, which is `en` whenever the stored tag
 * is absent or unknown.
 */
export function applyStoredLanguage(doc = document) {
  const tag = readStored();
  if (!tag || !setLocale(tag)) return currentLocale();
  // Screen readers pick pronunciation from this attribute; a Chinese page
  // announced as English is the accessibility half of the same mistake.
  doc.documentElement.lang = tag;
  applyStaticText(doc.querySelectorAll('*'));
  return tag;
}

/**
 * Wire the language control, offering only languages that exist.
 *
 * An option for a dictionary nobody registered is a promise the page cannot
 * keep, so it is removed rather than left to fail on selection.
 *
 * Changing the language reloads. The panels below render once at boot from
 * server data, and re-running each of them in place would be a second
 * rendering path -- a second definition of what the page shows, on a server
 * that is on this machine.
 */
export function wireLanguageControl(select, reload = () => window.location.reload()) {
  if (!select) return;
  const available = new Set(availableLocales());
  for (const option of Array.from(select.options)) {
    if (!available.has(option.value)) option.remove();
  }
  select.value = currentLocale();
  select.addEventListener('change', () => {
    writeStored(select.value);
    reload();
  });
}
