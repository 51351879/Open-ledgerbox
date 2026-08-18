// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Every language this build ships, registered in one place.
//
// The locale files export dictionaries and register nothing, so a language
// cannot half-arrive by being imported from somewhere unexpected: it exists on
// the page exactly when it is listed here. Adding one is two lines -- an import
// and a row in the table below -- and forgetting either is what
// `tests/js/locales.test.js` fails on, because a dictionary nobody imports is
// indistinguishable from a missing one when you are reading the directory.

import { registerLocale } from '../i18n.js';
import { zhCN } from './zh-CN.js';

/** tag -> the dictionary as written. Exported so tests can read what shipped. */
export const DICTIONARIES = {
  'zh-CN': zhCN,
};

for (const [tag, entries] of Object.entries(DICTIONARIES)) {
  registerLocale(tag, entries);
}
