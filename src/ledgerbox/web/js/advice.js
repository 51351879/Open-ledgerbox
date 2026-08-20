// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Planning notes: the one region of this page that is not a reading of the
// ledger, and the only one that has to say so.
//
// The owner's previous dashboard ended with a panel like this and it is the
// part they liked most, so it is here. What is deliberately different is what
// it claims.
//
// **Nothing here is computed from their transactions.** That is not modesty, it
// is arithmetic. Sorting spending into categories is a heuristic, and on a real
// ledger the shipped rules claim a small share of it — most of what is left is
// money moving between the person's own accounts, which the rules refuse to
// guess at because a Zelle to yourself and a Zelle to a friend are the same
// string. A panel that said "you spend too much on dining" would be reading a
// breakdown that does not cover enough of the ledger to support the sentence,
// and it would sound exactly as confident as one that did. That is the failure
// this whole project is built against, arriving at the last section of the page
// wearing a friendlier hat.
//
// So the ranges below select **general rules of thumb**, the kind printed in
// any introduction to personal finance. They are not advice, nobody licensed
// wrote them, and they do not know anything about this ledger. The panel says
// each of those things where a reader will see it rather than in a footnote.
//
// The one number it does use is the one the ledger can prove: the net of the
// window selected at the top of the page. It is quoted as what it is — what
// these statements did over that window — and never turned into a savings rate,
// because a savings rate needs income to mean take-home pay, and this ledger
// cannot tell pay from a transfer in.

import { clear, el, formatMinor } from './api.js';
import { localized, t } from './i18n.js';

// Read through the dictionary at the moment each sentence is read. The
// disclaimers below are the point of this panel rather than decoration on it,
// so a translation that softens one is a defect and not a style choice: the
// `ledgerbox-translate` Skill says so where a translator will see it.
const COPY = localized({
  title: 'Planning notes',
  intro: 'General information, not advice, and not from anyone licensed to give it. Nothing '
    + 'here is computed from your transactions: the rules that sort spending into categories '
    + 'claim a small share of this ledger, so a panel that told you what you spend too much on '
    + 'would be reading a breakdown that does not cover enough to say it. Pick a range to see '
    + 'the ordinary rules of thumb for it, and check them against the figures at the top of '
    + 'this page yourself.',
  disclaimer: 'General information only. Not advice, not personalised, and not written by '
    + 'anyone licensed to give it. Figures at the top of this page are measured; nothing in '
    + 'this section is.',
  blind: 'This section cannot see what you spend it on. The category breakdown above covers '
    + 'only the part of your spending the shipped rules claim, and on most ledgers that is a '
    + 'small share — so no note here is derived from it.',
  // The space before the figure is *not* in this sentence. Keys are
  // normalised, so a trailing space is trimmed out of the key -- and, since
  // English reads through the same lookup, off the page as well, welding the
  // amount to the last word. The separator lives at the reading site instead.
  netLead: 'Over the window selected at the top of this page, these statements net',
  netRest: '. That is what the documents say, and it is the only figure this section uses.',
  netUnknown: 'Once a statement is booked, this section will quote the net for the selected '
    + 'window — the one figure here that comes from your own documents.',
});

// Ordinary, widely published rules of thumb. No range is told what it spends,
// and none of these is a recommendation to buy anything.
//
// Not wrapped in `localized()`: its prose is a level down, inside objects and
// arrays, and that wrapper is shallow on purpose. Half-translating a table is
// worse than leaving it, so the sentences take `t()` where they are read. The
// `label` of each range is an amount and is never looked up.
const RANGES = [
  {
    id: '30-50',
    label: '$30k – $50k',
    heading: 'Cash buffer first',
    notes: [
      'The usual first target is a small emergency fund — often quoted as one month of '
        + 'essential costs to start with, then three — held somewhere boring and instant.',
      'High-interest debt is normally paid down before anything is invested, because its rate '
        + 'is certain and an investment return is not.',
      'Where an employer matches retirement contributions, the match is the part most guides '
        + 'say to capture before anything else.',
    ],
  },
  {
    id: '50-80',
    label: '$50k – $80k',
    heading: 'Buffer, then the tax-advantaged room',
    notes: [
      'Three to six months of essential costs is the range most often quoted for an emergency '
        + 'fund once income is steady.',
      'Tax-advantaged accounts have annual limits that do not carry over, which is why guides '
        + 'usually mention them before ordinary brokerage saving.',
      'The 50/30/20 split — needs, wants, saving — is a starting frame, not a rule. It is worth '
        + 'checking against your own figures rather than adopting.',
    ],
  },
  {
    id: '80-120',
    label: '$80k – $120k',
    heading: 'Automate, then look at fees',
    notes: [
      'Automatic transfers on payday are the mechanism most commonly recommended, on the '
        + 'grounds that it removes a monthly decision rather than because it earns anything.',
      'Fund fees compound the same way returns do, in the other direction. Comparing expense '
        + 'ratios is one of the few levers with a known sign.',
      'Insurance and estate basics — disability cover, beneficiaries — tend to be raised at '
        + 'this level because they are cheap to fix and expensive to have skipped.',
    ],
  },
  {
    id: '120-200',
    label: '$120k – $200k',
    heading: 'Tax treatment starts to dominate',
    notes: [
      'Which account a thing is held in starts to matter as much as what it is; asset location '
        + 'is the usual term.',
      'Concentration risk is worth naming if a large share of pay arrives as one company’s '
        + 'equity.',
      'Marginal rates and phase-outs make general rules less reliable here. This is the level '
        + 'at which most guides stop generalising and say to ask somebody licensed.',
    ],
  },
  {
    id: '200-plus',
    label: '$200k+',
    heading: 'General notes stop being useful',
    notes: [
      'Published rules of thumb are written for the middle of a distribution and get less '
        + 'applicable the further out you are.',
      'The questions at this level — entity structure, concentrated positions, estate planning '
        + '— have answers that depend on details no dashboard has.',
      'This panel is general information. For anything in that list, the honest suggestion is '
        + 'a licensed professional rather than a page on your own machine.',
    ],
  },
];

/**
 * The planning notes panel.
 *
 * `net()` returns the current window's net in minor units, or null when nothing
 * is booked. It is read at render time rather than passed in, so this panel
 * quotes the window that is actually selected rather than one it captured
 * earlier.
 */
export function createAdvicePanel(options) {
  const root = options.root;
  const net = options.net || (() => null);
  let chosen = null;

  // This panel builds its own head, note and controls. Everything it shows is
  // prose with no data behind it, and prose that has to stay inside a claim
  // this careful belongs next to the reasoning that constrains it rather than
  // in a markup file where the two can drift.
  const head = el('div', 'panel__head');
  const title = el('h2', 'panel__title', COPY.title);
  title.id = 'advice-h';
  head.appendChild(title);
  head.appendChild(el('p', 'panel__meta', COPY.disclaimer));
  root.appendChild(head);
  root.appendChild(el('p', 'panel__note', COPY.intro));

  const choicesNode = el('div', 'advice__choices');
  choicesNode.setAttribute('role', 'group');
  choicesNode.setAttribute('aria-label', t('Annual income range'));
  root.appendChild(choicesNode);

  const bodyNode = el('div', 'advice__body');
  bodyNode.setAttribute('aria-live', 'polite');
  bodyNode.hidden = true;
  root.appendChild(bodyNode);

  function renderBody() {
    clear(bodyNode);
    const range = RANGES.find((entry) => entry.id === chosen);
    bodyNode.hidden = !range;
    if (!range) {
      return;
    }

    bodyNode.appendChild(el('h3', 'advice__heading', t(range.heading)));

    const list = el('ul', 'advice__list');
    for (const note of range.notes) {
      list.appendChild(el('li', 'advice__note', t(note)));
    }
    bodyNode.appendChild(list);

    // The one measured figure, quoted as a measurement and left alone. Not
    // turned into a savings rate: that needs income to mean take-home pay, and
    // this ledger cannot tell pay from a transfer in.
    const measured = net();
    const line = el('p', 'advice__measured');
    if (measured === null || measured === undefined) {
      line.appendChild(el('span', '', COPY.netUnknown));
    } else {
      line.appendChild(el('span', '', `${COPY.netLead} `));
      line.appendChild(el('strong', 'num money', formatMinor(measured)));
      line.appendChild(el('span', '', COPY.netRest));
    }
    bodyNode.appendChild(line);

    bodyNode.appendChild(el('p', 'advice__blind muted', COPY.blind));
  }

  function renderChoices() {
    clear(choicesNode);
    for (const range of RANGES) {
      const node = el('button', 'btn btn--quiet advice__choice', range.label);
      node.type = 'button';
      // A real pressed state rather than a class a screen reader cannot see.
      node.setAttribute('aria-pressed', String(range.id === chosen));
      node.addEventListener('click', () => {
        chosen = range.id === chosen ? null : range.id;
        renderChoices();
        renderBody();
      });
      choicesNode.appendChild(node);
    }
  }

  renderChoices();
  renderBody();

  /** Re-read the measured figure. Called when the window or the ledger moves. */
  return { refresh: renderBody };
}
