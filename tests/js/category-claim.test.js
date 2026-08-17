// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The sentence under the donut, and the one thing it is not allowed to do.
//
// This claim has been published as a falsehood twice already in two other
// shapes (`docs/STATUS.md` §5.69): first as "these two figures will not agree",
// refuted by putting the two responses side by side; then as "they agree only
// with no transfer and no filter", refuted in both directions by a single
// click. Both times the wording was the thing that got fixed, and §5.43's
// conclusion was that a sentence refuted that often has stopped being a wording
// problem — state the guarantee and let an assertion carry it.
//
// §5.87 is the third shape: switching a legend row off leaves the ring showing
// part of the spend. The visible shares now rebalance against that visible
// selection, while the sentence keeps the whole and coverage explicit.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  coverageClaim,
  donutLabel,
  totalClaim,
} from '../../src/ledgerbox/web/js/category-claim.js';

/** A spend of -$58,937.52 divided into nine buckets, none hidden. */
function view(overrides) {
  return {
    total: -5893752,
    drawn: -5893752,
    hidden: 0,
    txnCount: 343,
    divisible: true,
    buckets: 9,
    named: 8,
    unclaimedSpend: -4000000,
    unclaimedTxnCount: 200,
    ...overrides,
  };
}

/** Everything the claim would put on the page, as one string. */
function said(claim) {
  return [claim.lead, ...claim.body].filter(Boolean).join(' ');
}

test('with every bucket drawn, the total claims to be the Out broken down', () => {
  const claim = totalClaim(view());
  assert.equal(claim.filtered, false);
  const text = said(claim);
  assert.match(text, /Total spent/);
  assert.match(text, /broken down/);
  assert.match(text, /not a second one/);
  assert.doesNotMatch(text, /no longer/);
  assert.match(text, /Classification coverage/);
  assert.match(text, /line\(s\)/);
  assert.match(text, /net spending amount/);
  assert.match(claim.body.at(-1), /^ Classification coverage/,
    'adjacent inline spans retain a word boundary');
});

test('coverage keeps line share separate from amount share', () => {
  const text = coverageClaim(view({
    total: -10000,
    txnCount: 10,
    unclaimedSpend: -8000,
    unclaimedTxnCount: 4,
  }));
  assert.match(text, /6 of 10 spending line\(s\) \(60\.0%\) are classified/);
  assert.match(text, /remaining 4 line\(s\) \(40\.0%\) are unclassified/);
  assert.match(text, /20\.0% is classified/);
  assert.match(text, /80\.0% is unclassified/);
  assert.match(text, /neither is an Agent accuracy score/);
});

test('coverage says when amount share has no denominator', () => {
  const text = coverageClaim(view({
    total: 0,
    divisible: false,
    txnCount: 5,
    unclaimedTxnCount: 2,
  }));
  assert.match(text, /3 of 5 spending line\(s\) \(60\.0%\) are classified/);
  assert.match(text, /Amount coverage is not computable/);
});

test('coverage handles a fully classified and an empty view', () => {
  assert.match(coverageClaim(view({
    total: -100,
    txnCount: 2,
    unclaimedSpend: 0,
    unclaimedTxnCount: 0,
  })), /2 of 2 spending line\(s\) \(100\.0%\)/);
  assert.match(coverageClaim(view({ txnCount: 0 })), /no spending lines/);
});

test('switching one bucket off states the filtered denominator in the same sentence', () => {
  const claim = totalClaim(view({ hidden: 1, drawn: -5429755 }));
  assert.equal(claim.filtered, true);
  const text = said(claim);
  assert.match(text, /1 bucket\(s\) are switched off/);
  assert.match(text, /rebalanced into a complete ring/);
  assert.match(text, /represents all visible spending/);
  assert.doesNotMatch(text, /not a second one/);
});

test('the withdrawn state still shows both figures, so nothing has to be inferred', () => {
  const claim = totalClaim(view({ hidden: 2, drawn: -5000000 }));
  const text = said(claim);
  assert.match(text, /-\$50,000\.00/, 'what is drawn');
  assert.match(text, /-\$58,937\.52/, 'and the whole it is part of');
});

test('visible shares are rebalanced while the whole remains explicit', () => {
  const text = said(totalClaim(view({ hidden: 3, drawn: -100 })));
  assert.match(text, /represents all visible spending/);
  assert.match(text, /whole -\$58,937\.52/);
  assert.match(text, /does not change/);
});

test('hiding a bucket that spent nothing still withdraws the claim', () => {
  // The subtle one. `drawn` is unchanged because the bucket held nothing, so a
  // check written as `drawn !== total` would decide the ring is still a full
  // decomposition. It is not: the row is excluded even though reflowing the
  // visible ring happens to leave the same amount.
  const claim = totalClaim(view({ hidden: 1, drawn: -5893752 }));
  assert.equal(claim.filtered, true);
  assert.match(said(claim), /rebalanced/);
});

test('every bucket off is a state, not an edge case', () => {
  const claim = totalClaim(view({ hidden: 9, drawn: 0 }));
  assert.equal(claim.filtered, true);
  const text = said(claim);
  assert.match(text, /\$0\.00/);
  assert.match(text, /9 bucket\(s\) are switched off/);
  assert.match(text, /ring is empty/);
});

test('nothing spent is said in words and never as a share of zero', () => {
  const claim = totalClaim(view({ total: 0, drawn: 0, divisible: false }));
  assert.equal(claim.filtered, false);
  assert.equal(claim.lead, null);
  const text = said(claim);
  assert.match(text, /no total to divide/);
  assert.match(text, /spending line\(s\).*%/s, 'line coverage still has a denominator');
  assert.doesNotMatch(text, /By net spending amount/);
  assert.doesNotMatch(text, /broken down/);
});

test('a bucket switched off still counts as switched off with nothing to divide', () => {
  // `filtered` is documented as *exactly* `hidden > 0`, and this branch used to
  // ignore `hidden` entirely. The consequence was not cosmetic: the restore
  // control is offered only when `filtered`, and the legend is built and
  // clickable before this branch is chosen, so a ledger whose buckets cancel to
  // nothing could be switched off with no way back.
  for (const hidden of [1, 2, 9]) {
    const claim = totalClaim(view({ total: 0, drawn: 0, divisible: false, hidden }));
    assert.equal(claim.filtered, true, `${hidden} hidden with nothing to divide`);
    assert.match(said(claim), new RegExp(`${hidden} bucket\\(s\\) are switched off`));
    assert.match(said(claim), /no total to divide/, 'and it still says there is no total');
    assert.match(said(claim), /spending line\(s\).*%/s, 'line coverage remains computable');
    assert.doesNotMatch(said(claim), /By net spending amount/);
  }
  assert.match(donutLabel(view({ total: 0, divisible: false, hidden: 3 })), /3 bucket\(s\)/);
});

test('the figure this claims to be is never recomputed here', () => {
  // `total` is the server's own figure and `drawn` is the panel's sum of what
  // is switched on. This module adds nothing up, so a total it was handed comes
  // back out unchanged however many buckets are hidden.
  for (const hidden of [0, 1, 5, 9]) {
    const text = said(totalClaim(view({ hidden, drawn: -1 })));
    assert.match(text, /-\$58,937\.52/, `the whole survived ${hidden} hidden bucket(s)`);
  }
});

test('the accessible label carries the same state as the sentence', () => {
  // One picture, one set of facts. A label that stayed confident while the
  // sentence hedged would hand a screen reader the version that was retired.
  assert.doesNotMatch(donutLabel(view()), /switched off/);

  const filtered = donutLabel(view({ hidden: 2, drawn: -5000000 }));
  assert.match(filtered, /2 bucket\(s\) are switched off/);
  assert.match(filtered, /shares are recomputed against that visible spending/);
  assert.match(filtered, /-\$50,000\.00/);
});

test('the accessible label says when every visible share is absent', () => {
  const filtered = donutLabel(view({ hidden: 9, drawn: 0 }));
  assert.match(filtered, /ring is empty/);
  assert.match(filtered, /no visible spending share to compute/);
  assert.match(filtered, /whole -\$58,937\.52/);
});

test('the accessible label counts the buckets and says which are categories', () => {
  const label = donutLabel(view());
  assert.match(label, /9 bucket\(s\)/);
  assert.match(label, /8 category\(ies\) and the lines nothing claimed/);
  // Never "other". The unclaimed group is the absence of a decision and the
  // predecessor's catch-all is why this file exists to say so.
  assert.doesNotMatch(label, /other/i);
  assert.match(label, /Classification coverage/);
  assert.match(label, /Line share and amount share answer different questions/);
});

test('an empty ledger gets the no-total sentence from the label too', () => {
  assert.match(donutLabel(view({ divisible: false })), /no total to divide/);
});
