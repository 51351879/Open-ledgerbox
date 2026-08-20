// SPDX-License-Identifier: AGPL-3.0-or-later
//
// What the category panel is entitled to claim about its own total.
//
// Split out of `chart-categories.js` at the 400-line signal
// `docs/EXECUTION_PLAN.md` §1.3 puts there, at the seam §5.66 cut
// `deletion-plan.js` along: that file answers "what is in this ring and how do
// I switch a bucket off", and this one answers "given what is switched off,
// what may the sentence underneath say". They share nothing but `api.js`.
//
// **Everything here is a pure function of one object**, which is the point
// rather than a side effect of the split. The sentence these produce is the
// third form of a claim this codebase has published as a falsehood twice
// (§5.69), and until now it was guarded by a comment and one manual browser
// session. Pure functions can be run by `node --test` without a DOM, and
// `tests/js/category-claim.test.js` does.
//
// The claim itself, stated once:
//
//   * With every bucket drawn, the total **is** the "Out" printed at the top of
//     the page, broken down. That is a checkable relationship — `verify`'s
//     `cashflow_agreement` asserts it against two independent expressions on
//     the operator's own ledger — and the sentence may say so.
//   * The moment any bucket is switched off, the ring is no longer that
//     decomposition, and the sentence **stops saying it is**. It states what is
//     drawn, states the whole beside it, and says how many buckets are missing.
//   * Visible shares are recomputed against visible spending, so the remaining
//     wedges close up into a complete ring. The sentence names that denominator
//     explicitly; the server total and classification coverage stay whole.

import { formatMinor } from './api.js';
import { percentText, sharePercent } from './charts.js';
import { localized, t } from './i18n.js';

// Every word this panel says, in one module. The claim functions below own the
// sentence under the ring and the label on the figure; the rest is the legend's
// vocabulary, which moved here when `chart-categories.js` crossed the 400-line
// line a second time. Splitting a panel's prose from its other prose would have
// been a split by size rather than by job, and the job here is "what this panel
// says".
// Looked up as each sentence is read. **The separator between a pair of them
// is at the reading site and not inside either one**: keys are normalised, and
// English reads through the same lookup as every other language, so a leading
// space or dash would be trimmed out of the key and off the page with it.
// `paintTotal` renders these as adjacent inline spans, so losing one would
// weld two sentences together in the one claim this file exists to keep true.
const COPY = localized({
  // The wording the transaction table and the month filter already use for the
  // same fact, deliberately: one absence, one name for it.
  unclaimed: 'Nothing claimed these',
  unclaimedAside: (lineShare) => t('not a category — no rule claimed these lines and '
    + 'nobody overruled that. This is {share} of spending lines; the percentage above is '
    + 'the share of spending amount, not the share of lines.', { share: lineShare }),
  unclaimedKey: 'The hatched slice is not a category. It is the lines no rule claimed and '
    + 'nobody overruled, drawn differently on purpose so it cannot be read as one more '
    + 'bucket beside the named ones. Each row’s main percentage is its share of spending '
    + 'amount — of the whole until a visual filter is active, then of visible spending; '
    + 'classification coverage by both amount and line count is stated below the chart.',
  // What this says has to be checkable by looking: a reader who narrows the
  // date range can see whether the colours moved. They do not, and saying they
  // are assigned by rank — which this said for the whole of M6, after the code
  // had stopped doing it — is a sentence a reader could catch the page out on.
  rampKey: 'One colour per category, fixed by the category and not by its size, so a bucket '
    + 'keeps its colour when you change the dates. Colour is a second way to find a slice '
    + 'and never the only one: the rows below name every slice, including the ones too '
    + 'small to see.',
  toggleKey: 'Each row is a switch. Switching a bucket off removes its wedge and rebalances '
    + 'the remaining visible buckets into a complete ring representing all visible '
    + 'spending. The crossed-out row keeps its original whole-spend figures for reference.',
  shareKey: 'Share of total spent',
  visibleShareKey: 'Share of visible spent',
  lineShareKey: 'Share of spending lines',
  // The count beside every legend row. It lives here rather than in the panel
  // for the reason the header gives: this module is what this panel says.
  lines: (count) => t('{count} line(s)', { count }),
  hiddenTag: 'switched off',
  totalLead: 'Total spent',
  // The functions carry their own separator and their sentence does not; see
  // the note above. `localized()` passes a function through untouched.
  totalLines: (count) => ` ${t('over {count} spending line(s).', { count })}`,
  // `Out` is the figure at the top of the page and is worded here exactly as
  // `analytics.js` names it.
  totalRest: 'The amount is the “Out” figure at the top of this page, broken down: the same '
    + 'measurement of the same money, not a second one.',
  filteredLead: (drawn, whole) => t('Showing {drawn} of {whole} spent', { drawn, whole }),
  filteredRest: (count, whole) => ` — ${t('{count} bucket(s) are switched off in the list '
    + 'below. Their wedges are removed and the remaining visible buckets are rebalanced '
    + 'into a complete ring; the ring represents all visible spending. This visual filter '
    + 'does not change the whole {whole}, the ledger, or the classification coverage '
    + 'below.', { count, whole })}`,
  filteredEmpty: 'Every bucket is switched off, so the ring is empty and there is no visible '
    + 'spending share to compute.',
  noTotal: 'Nothing has been spent yet, so there is no total to divide and no share to compute.',
  // Said when there is nothing to divide *and* somebody has switched a bucket
  // off anyway. Rare, and it has to be reachable rather than tidy: the legend
  // is built and clickable before this branch is chosen, so a ledger whose
  // buckets cancel to nothing still has nine working switches on it.
  noTotalHidden: (count) => ` ${t('{count} bucket(s) are switched off in the list below. '
    + 'Turning them back on will not change the figures, because there are none to '
    + 'change.', { count })}`,
  empty: 'No spending to break down yet.',
  restore: 'Show every bucket again',
});

export const CLAIM_COPY = COPY;

/**
 * What is classified, measured in the two ways a person can reasonably mean it.
 *
 * `view` carries the server's category total and transaction count plus the
 * one `category_id: null` slice, if present. This function never asks for a
 * second response and never reclassifies a row. It only states complements of
 * numbers already in the same category breakdown.
 *
 * Amount coverage uses the chart's signed **net spending** amounts. That keeps
 * it exactly aligned with the donut and the Out figure instead of quietly
 * introducing an absolute-value measure that neither of them uses.
 */
export function coverageClaim(view) {
  const totalLines = Number.isInteger(view.txnCount) && view.txnCount > 0
    ? view.txnCount : 0;
  if (totalLines === 0) {
    return t('Classification coverage: there are no spending lines in this view.');
  }

  const rawUnclaimedLines = Number.isInteger(view.unclaimedTxnCount)
    ? view.unclaimedTxnCount : 0;
  const unclaimedLines = Math.min(totalLines, Math.max(0, rawUnclaimedLines));
  const classifiedLines = totalLines - unclaimedLines;
  const classifiedLineShare = percentText(sharePercent(classifiedLines, totalLines));
  const unclaimedLineShare = percentText(sharePercent(unclaimedLines, totalLines));

  const lineSentence = t('Classification coverage: {classified} of {total} spending line(s) '
    + '({classifiedShare}) are classified. The remaining {unclassified} line(s) '
    + '({unclassifiedShare}) are unclassified.', {
    classified: classifiedLines,
    total: totalLines,
    classifiedShare: classifiedLineShare,
    unclassified: unclaimedLines,
    unclassifiedShare: unclaimedLineShare,
  });

  if (!view.divisible || typeof view.unclaimedSpend !== 'number') {
    return `${lineSentence} `
      + t('Amount coverage is not computable because net spending is zero.');
  }

  const classifiedSpend = view.total - view.unclaimedSpend;
  const classifiedAmountShare = percentText(sharePercent(classifiedSpend, view.total));
  const unclaimedAmountShare = percentText(sharePercent(view.unclaimedSpend, view.total));
  return `${lineSentence} `
    + t('By net spending amount, {classified} is classified and {unclassified} is '
      + 'unclassified. Line share and amount share answer different questions and neither '
      + 'is an Agent accuracy score.', {
      classified: classifiedAmountShare,
      unclassified: unclaimedAmountShare,
    });
}

/**
 * The sentence under the ring, as parts rather than as nodes.
 *
 * `view` carries `total`, `drawn`, `hidden`, `txnCount` and `divisible`.
 * Returns `{ filtered, lead, body }`: `lead` is the emphasised opening or null,
 * `body` is the rest in order, and `filtered` is what tells the caller to offer
 * the restore control. Nothing is added up here — `drawn` is computed by the
 * panel from the slices it was given, and `total` is the server's own figure.
 *
 * `filtered` is exactly `hidden > 0`. It is not `drawn !== total`: a bucket
 * that spent nothing can be switched off without moving the sum, and the
 * sentence must still stop claiming to be the unfiltered decomposition.
 *
 * **"Exactly" is load-bearing and was false for one branch.** The no-total case
 * used to return `filtered: false` whatever was hidden, so a ledger whose
 * buckets cancel to nothing lost its restore control the moment anybody
 * switched one off — the legend is built and clickable before this branch is
 * chosen, so that state is reachable rather than theoretical. A file whose
 * whole reason for existing is a sentence published as a falsehood twice had
 * its own predicate described wrongly in the line above the code.
 */
export function totalClaim(view) {
  const filtered = view.hidden > 0;
  // `paintTotal` renders every body part as an adjacent inline span. Keep the
  // separator in the part, as the older total fragments above do, so both the
  // visual sentence and copied/accessibility text retain a word boundary.
  const coverage = ` ${coverageClaim(view)}`;
  if (!view.divisible) {
    return {
      filtered,
      lead: null,
      body: filtered
        ? [COPY.noTotal, COPY.noTotalHidden(view.hidden), coverage]
        : [COPY.noTotal, coverage],
    };
  }
  const whole = formatMinor(view.total);
  if (!filtered) {
    return {
      filtered: false,
      lead: `${COPY.totalLead} ${whole}`,
      body: [COPY.totalLines(view.txnCount), ` ${COPY.totalRest}`, coverage],
    };
  }
  return {
    filtered: true,
    lead: COPY.filteredLead(formatMinor(view.drawn), whole),
    body: [
      COPY.filteredRest(view.hidden, whole),
      view.drawn === 0 ? ` ${COPY.filteredEmpty}` : '',
      coverage,
    ].filter(Boolean),
  };
}

/**
 * The whole figure in one sentence, for the `aria-label` on the `<svg>`.
 *
 * A picture nobody can read is not an accessible picture with a label on it, so
 * this carries the same facts the legend does: how much, into how many buckets,
 * how many of those are categories, and — when any are switched off — how much
 * of the total is actually drawn and what denominator the visible shares use.
 */
export function donutLabel(view) {
  if (!view.divisible) {
    return view.hidden ? COPY.noTotal + COPY.noTotalHidden(view.hidden) : COPY.noTotal;
  }
  const whole = t('Donut chart dividing {total} of spending into {buckets} bucket(s): '
    + '{named} category(ies) and the lines nothing claimed. Every bucket is named, with '
    + 'its share and its amount, in the list beside the chart.', {
    total: formatMinor(view.total),
    buckets: view.buckets,
    named: view.named,
  });
  if (!view.hidden) {
    return `${whole} ${coverageClaim(view)}`;
  }
  // One sentence per fact, joined by the separator rather than each carrying
  // one: a normalised key loses a trailing space as surely as a leading one.
  return [
    whole,
    t('{count} bucket(s) are switched off in that list and are not drawn,', {
      count: view.hidden,
    }),
    view.drawn === 0
      ? t('so the ring is empty and there is no visible spending share to compute.')
      : t('so the remaining wedges form a complete ring showing {drawn}; their shares are '
        + 'recomputed against that visible spending.', { drawn: formatMinor(view.drawn) }),
    t('The whole {total} and classification coverage are unchanged.', {
      total: formatMinor(view.total),
    }),
    coverageClaim(view),
  ].join(' ');
}
