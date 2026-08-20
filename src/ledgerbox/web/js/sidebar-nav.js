// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The table of contents down the side of the page, and the pending counts that
// hang off four of its entries.
//
// Split out of `agent-center.js` when that file reached the 400-line signal
// EXECUTION_PLAN §1.3 sets. The seam is real rather than convenient: this is a
// fixed list of nine section names and how they are drawn, while everything
// left behind is about a local Agent's connection state. They were sharing a
// file, not a subject.
//
// The nine names are also the page's most-read strings, so this is where the
// dictionary layer is wired into a rendered surface rather than into static
// markup. `t()` returns the English key unchanged unless a locale is active
// and knows the sentence, which is why nothing else here had to change.

import { el } from './api.js';
import { t } from './i18n.js';

// `index.html` carries the same nine anchors as markup. They are not
// duplication in the sense that matters -- the page works with JavaScript off
// only as far as the anchors, and this render replaces them -- but they are two
// lists, and the English here is what a reader sees.
const NAV_ITEMS = [
  ['ledger', 'Overview', null],
  ['analytics', 'Charts', null],
  ['transactions', 'Transactions', 'needs'],
  ['large-flows', 'Large flows', null],
  ['agent-proposals', 'Agent proposals', 'proposals'],
  ['agent-triage', 'Coverage triage', 'triage'],
  ['statement-history', 'Statements', null],
  ['advice', 'Planning notes', null],
  ['review-queue', 'Review queue', 'review'],
];

/**
 * Draw the directory into `root` and hand back the badge nodes by name.
 */
export function addDirectory(root) {
  const nav = el('nav', 'sidebar-nav');
  nav.setAttribute('aria-label', t('On this page'));
  const badges = {};
  for (const [target, label, badgeName] of NAV_ITEMS) {
    const link = el('a', 'sidebar-nav__link');
    link.setAttribute('href', `#${target}`);
    link.appendChild(el('span', '', t(label)));
    if (badgeName) {
      const badge = el('span', 'sidebar-nav__badge', '0');
      badge.hidden = true;
      link.appendChild(badge);
      badges[badgeName] = badge;
    }
    nav.appendChild(link);
  }
  root.appendChild(nav);
  return badges;
}

/**
 * A count beside a section name, hidden at zero.
 *
 * Hidden rather than shown as `0`: a badge is there to say something needs
 * attention, and nine zeroes down the side of the page is nine claims that
 * nothing does, competing with the four that mean something.
 */
export function setBadge(node, count, label = t('pending')) {
  const value = Math.max(0, Number(count) || 0);
  node.textContent = String(value);
  node.hidden = value === 0;
  node.setAttribute('aria-label', `${value} ${label}`);
}
