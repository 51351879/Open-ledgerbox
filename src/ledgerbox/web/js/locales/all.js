// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Every language this build ships, registered in one place.
//
// The locale files export dictionaries and register nothing, so a language
// cannot half-arrive by being imported from somewhere unexpected: it exists on
// the page exactly when it is listed here. Adding one is an import and a row in
// the table below, and forgetting either is what `tests/js/locales.test.js`
// fails on, because a dictionary nobody imports is indistinguishable from a
// missing one when you are reading the directory.
//
// **A language may arrive in more than one file**, named `<tag>.<region>.js`.
// A dictionary is the one file here that grows with every sentence the page
// gains, and `zh-CN` met the 400-line split signal that every module answers
// to; it was split along the seam the page already has rather than exempted.
// `registerLocale` merges rather than replaces, which is what makes that safe,
// and the counterexamples refuse a sentence two of a language's files both
// answer for -- two answers to one question, decided by import order.

import { registerLocale } from '../i18n.js';
import { zhCN } from './zh-CN.js';
import { zhCNAgent } from './zh-CN.agent.js';
import { zhCNPanels } from './zh-CN.panels.js';
import { zhCNTable } from './zh-CN.table.js';

/** tag -> the files it arrives in, as written. Exported so tests can read them apart. */
export const PARTS = {
  'zh-CN': [zhCN, zhCNAgent, zhCNPanels, zhCNTable],
};

/** tag -> every sentence that language answers for. */
export const DICTIONARIES = Object.fromEntries(
  Object.entries(PARTS).map(([tag, parts]) => [tag, Object.assign({}, ...parts)]),
);

for (const [tag, entries] of Object.entries(DICTIONARIES)) {
  registerLocale(tag, entries);
}
