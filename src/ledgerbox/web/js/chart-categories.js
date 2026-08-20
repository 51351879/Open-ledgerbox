// SPDX-License-Identifier: AGPL-3.0-or-later
//
// What was spent, divided up: one donut, and a legend beside it that names
// every bucket and can switch one off.
//
// **One colour per category, and the category decides which one.** The step
// comes from `category-tones.js`, keyed on the taxonomy `/api/categories`
// returns, so a category is the same colour in every window and in both places
// that paint one. It used to be keyed on the slice's rank by spend, which made
// a colour a fact about the current window — harmless until M6 put a date range
// on the page, at which point changing the dates traded the hues around under a
// reader who had changed nothing else. That was fixed; these lines said
// otherwise for the whole of M6, in four places, one of them on the page.
//
// The eight steps the operator's own dashboard uses come first, then thirteen more
// that `charts.css` derives and measures; `charts.js` explains why twenty-four is
// every step this chart can need. Colour is a second way to find a slice — the
// legend names every one of them and is never behind a disclosure — so nothing
// here depends on a reader holding a swatch-to-name mapping in their head.
//
// **`category_id: null` is "Nothing claimed these" and is never called
// "Other".** It is not a leftover and not a long tail. It is the set of lines
// no rule claimed and nobody overruled — the absence of a decision — and the
// predecessor to this project shipped exactly the opposite: a catch-all bucket
// that swept those lines up under a category-shaped name, so a breakdown with
// 31% coverage rendered as a complete one. A chart that renames or drops the
// unclaimed lines is worse than no chart, because it is confidently wrong at a
// glance. Three separate things here stop that happening:
//
//   1. the slice is always drawn, at its true share, with its own row;
//   2. it is filled with a hatch and takes no step of the categorical palette,
//      so it does not read as one more category in a list of categories;
//   3. the legend row says, in words, that it is not a category.
//
// **No minimum wedge.** On a real ledger one bucket can hold nearly all of the
// spending and leave the rest as wedges of a few degrees each. They draw at a
// few degrees each. Nudging a small slice up to a visible size is a false claim
// about a magnitude, and this project is an argument against making those. What
// stops them being invisible is not geometry, it is the legend: persistent, in
// the default view, carrying every slice by name with its share, amount and
// line count whether or not the wedge can be seen.
//
// **Legend rows are view filters.** Hidden arcs disappear; visible slices close
// into a complete ring and divide visible spending. Crossed-out rows retain
// whole-ledger figures; totals and classification coverage do not change.
//
// The switched-off set is display only. It is never sent, never read back, and
// `reset()` clears it on every render, so a reload or an upload always returns
// to the whole ledger rather than to somebody else's filter.
//
// **`total_minor` is the "Out" at the top of the page**, and nothing is re-added
// up here except the sum of the slices a reader has switched on — a fact about
// their selection, which cannot contradict any figure the server states.

import { button, clear, el, formatMinor } from './api.js';
import { attr, percentText, ringPath, sharePercent, svgFactory } from './charts.js';
import { CLAIM_COPY, donutLabel, totalClaim } from './category-claim.js';
import { visibleSliceShares } from './category-filter.js';
import { toneFor } from './category-tones.js';
import { createChartTooltip } from './chart-tooltip.js';

// Every word this panel says lives in `category-claim.js`, beside the two pure
// functions that decide which of them is true — including the sentence under
// the ring, which `node --test` covers. The amount that sentence quotes is the
// one the top of the page already printed; the count beside it is not, because
// `CategoryBreakdownOut.txn_count` counts the lines behind the spending while
// the count at the top counts income and expense together.
const COPY = CLAIM_COPY;

const DONUT = { cx: 110, cy: 110, outer: 100, inner: 58 };

/** True for the one bucket that is the absence of a decision, not a category. */
function isUnclaimed(slice) {
  return slice.category_id === null || slice.category_id === undefined;
}

function sliceLabel(slice) {
  return isUnclaimed(slice) ? COPY.unclaimed : String(slice.category_id);
}

/**
 * The category panel. `root` is the analytics section; the donut shell, the
 * legend and the total sentence are found by their `data-chart` names.
 */
export function createCategoryChart(root) {
  const shell = root.querySelector('[data-chart="donut"]');
  const wedgeBox = root.querySelector('[data-chart="wedges"]');
  const legendBox = root.querySelector('[data-chart="legend"]');
  const totalBox = root.querySelector('[data-chart="cat-total"]');
  const make = shell ? svgFactory(shell) : null;
  const tip = createChartTooltip(shell ? shell.closest('.chart') : null);

  // Display only, and cleared by `reset`. Holds slice indices.
  const off = new Set();
  let view = null;
  let rows = [];

  function reset() {
    off.clear();
    view = null;
    rows = [];
    tip.hide();
    for (const box of [wedgeBox, legendBox, totalBox]) {
      if (box) {
        clear(box);
      }
    }
    if (totalBox) {
      totalBox.className = 'chart__total';
    }
    // The label goes with the sentence. It described the *previous* window's
    // spending until now -- harmless only because `[data-chart="body"]` is
    // hidden on every path that resets, so nothing reads it. A stale accessible
    // name kept alive by somebody else's `hidden` is a bug waiting for the
    // layout to change.
    if (shell) {
      shell.removeAttribute('aria-label');
    }
  }

  /** What the ring currently draws: the slices still switched on, added up. A
   *  fact about a selection a reader just made, not a second definition of a
   *  figure the server states. `CategoryBreakdownOut.total_minor` is documented
   *  and tested as the sum of every slice, so with nothing off this equals it. */
  function drawnTotal() {
    return visibleSliceShares(view.slices, off).total;
  }

  function shareTotal() {
    return off.size ? drawnTotal() : view.total;
  }

  /** What one slice says on hover, on focus, and to a screen reader. */
  function said(slice, index) {
    return {
      title: sliceLabel(slice),
      rows: [
        ['Spent', formatMinor(slice.spend_minor)],
        [off.size ? COPY.visibleShareKey : COPY.shareKey,
          percentText(sharePercent(slice.spend_minor, shareTotal()))],
        [COPY.lineShareKey, percentText(sharePercent(slice.txn_count, view.txnCount))],
        ['Lines', `${slice.txn_count}`],
      ],
      note: isUnclaimed(slice)
        ? COPY.unclaimedAside(percentText(sharePercent(slice.txn_count, view.txnCount)))
        : null,
      anchor: rows[index] ? rows[index].wedge : null,
    };
  }

  /** Pointing at one slice dims the others. No geometry moves. */
  function lit(index) {
    if (wedgeBox) {
      wedgeBox.classList.toggle('donut__wedges--lit', index !== null);
    }
    rows.forEach((row, at) => {
      if (row.wedge) {
        row.wedge.classList.toggle('chart__wedge--lit', at === index);
      }
    });
  }

  /** A switched-off row lights nothing: dimming the ring to point at a wedge
   *  that is not on it is the panel gesturing at something that is not there.
   *
   *  `pointerOnly` for the wedges, for the reason `chart-tooltip.js`'s header
   *  gives at length: a `focus` listener turns a `<path>` into a tab stop with
   *  no accessible name. The legend rows are the keyboard path. */
  function watch(node, index, pointerOnly) {
    const on = () => lit(off.has(index) ? null : index);
    node.addEventListener('mouseenter', on);
    node.addEventListener('mouseleave', () => lit(null));
    if (!pointerOnly) {
      node.addEventListener('focus', on);
      node.addEventListener('blur', () => lit(null));
    }
  }

  /**
   * The wedges, in slice order, clockwise from twelve. **Order and colour are
   * two different things here**: a wedge's position follows its rank by spend,
   * which is what makes the ring readable, while its colour follows the
   * category's place in the taxonomy, which is what makes the colour mean
   * something across two windows. The unclaimed bucket takes the hatch and no
   * palette step at all, and cannot shift anybody else's, because nothing about
   * the ordering feeds the colour.
   *
   * Hidden slices never advance `turn`. The remaining sweeps divide the visible
   * total, so they close up into one complete ring in the same proportions the
   * visible legend states.
   */
  function drawWedges() {
    let turn = 0;
    const { shares } = visibleSliceShares(view.slices, off);
    view.slices.forEach((slice, index) => {
      // Clamped at zero because a wedge cannot sweep backwards. A bucket whose
      // refunds outran its spending is a real possibility and it gets no arc;
      // its row in the legend still carries the signed amount and share.
      const share = shares[index] || 0;
      // A bucket that spent nothing -- or whose refunds outran its spending --
      // gets no path at all rather than an empty one. `d=""` draws the same
      // nothing, and it made the count of `.chart__wedge` nodes disagree with
      // the count of wedges anybody can see, which is a number tests reach for.
      if (share > 0) {
        const path = make('path', isUnclaimed(slice)
          ? 'chart__wedge chart__wedge--unclaimed'
          : `chart__wedge chart__wedge--paint ${toneFor(slice.category_id)}`);
        path.setAttribute('d', ringPath(
          DONUT.cx, DONUT.cy, DONUT.outer, DONUT.inner, turn, turn + share,
        ));
        if (isUnclaimed(slice)) {
          // A paint server, referenced by document fragment. Set as an attribute
          // rather than by a CSS rule so nothing in `charts.css` has to know the
          // id, and so no stylesheet rule can win over it and hand this bucket a
          // flat colour that would make it look like a category again.
          path.setAttribute('fill', 'url(#chart-unclaimed)');
        }
        wedgeBox.appendChild(path);
        if (rows[index]) {
          rows[index].wedge = path;
        }
        tip.bind(path, () => said(slice, index), { pointerOnly: true });
        watch(path, index, true);
      }
      if (!off.has(index)) {
        turn += share;
      }
    });
    // Two hairlines that say where the ring is, drawn once over the whole
    // figure rather than around each wedge: a per-wedge stroke is centred on
    // its own boundary, so on a two-degree slice it would be most of what you
    // see and the slice would read as larger than it is. They also stay put
    // when a wedge is switched off, so the reflowed ring retains a clean edge.
    for (const radius of [DONUT.outer, DONUT.inner]) {
      wedgeBox.appendChild(attr(make('circle', 'donut__edge'), {
        cx: DONUT.cx, cy: DONUT.cy, r: radius,
      }));
    }
  }

  /**
   * One legend row: a real `<button>` carrying `aria-pressed`.
   *
   * Pressed means *switched on and drawn*, the state every row starts in, so
   * the greyed rows are the unpressed ones. The row also carries the words
   * "switched off" in text, because greying is colour and this page never lets
   * colour be the only thing that says something.
   */
  function legendRow(slice, index) {
    const unclaimed = isUnclaimed(slice);
    const row = el('li', unclaimed ? 'legend__row legend__row--unclaimed' : 'legend__row');
    const toggle = button('legend__toggle', '', () => flip(index));
    toggle.setAttribute('aria-pressed', 'true');
    toggle.appendChild(el('span', unclaimed
      ? 'legend__swatch legend__swatch--unclaimed'
      : `legend__swatch legend__swatch--paint ${toneFor(slice.category_id)}`));
    toggle.appendChild(el('span', 'legend__label', sliceLabel(slice)));
    const state = el('span', 'legend__state', COPY.hiddenTag);
    state.hidden = true;
    toggle.appendChild(state);
    const pct = el(
      'span',
      'legend__pct num',
      percentText(sharePercent(slice.spend_minor, view.total)),
    );
    toggle.appendChild(pct);
    toggle.appendChild(el('span', 'legend__amount num money', formatMinor(slice.spend_minor)));
    toggle.appendChild(el('span', 'legend__count num', CLAIM_COPY.lines(slice.txn_count)));
    row.appendChild(toggle);
    if (unclaimed) {
      row.appendChild(el(
        'span',
        'legend__aside',
        COPY.unclaimedAside(percentText(sharePercent(slice.txn_count, view.txnCount))),
      ));
    }
    rows[index] = { toggle, state, pct, wedge: null };
    // Pointing at a row lights its wedge and repeats its figures over the ring.
    // A switched-off row describes nothing: no wedge to point at, and the row
    // itself already says every number the tooltip would have shown.
    tip.bind(toggle, () => (off.has(index) ? null : said(slice, index)));
    watch(toggle, index);
    return row;
  }

  function flip(index) {
    if (off.has(index)) {
      off.delete(index);
    } else {
      off.add(index);
    }
    repaint();
  }

  function restoreAll() {
    off.clear();
    repaint();
  }

  /** Everything the two claim functions need, gathered in one place. */
  function claimView() {
    const unclaimed = view.slices.find(isUnclaimed);
    return {
      total: view.total,
      drawn: drawnTotal(),
      hidden: off.size,
      txnCount: view.txnCount,
      divisible: view.divisible,
      buckets: view.slices.length,
      named: view.slices.filter((slice) => !isUnclaimed(slice)).length,
      unclaimedSpend: unclaimed ? unclaimed.spend_minor : 0,
      unclaimedTxnCount: unclaimed ? unclaimed.txn_count : 0,
    };
  }

  /** The sentence under the chart, in whichever of its states is true. */
  function paintTotal() {
    if (!totalBox) {
      return;
    }
    clear(totalBox);
    const claim = totalClaim(claimView());
    totalBox.className = claim.filtered ? 'chart__total chart__total--filtered' : 'chart__total';
    if (claim.lead) {
      totalBox.appendChild(el('strong', '', claim.lead));
    }
    for (const part of claim.body) {
      totalBox.appendChild(el('span', '', part));
    }
    if (claim.filtered) {
      totalBox.appendChild(button('chart__restore', CLAIM_COPY.restore, restoreAll));
    }
  }

  /** Everything that depends on which slices are switched on. */
  function repaint() {
    clear(wedgeBox);
    const visibleTotal = shareTotal();
    rows.forEach((row, index) => {
      row.wedge = null;
      const on = !off.has(index);
      row.toggle.setAttribute('aria-pressed', on ? 'true' : 'false');
      row.state.hidden = on;
      // Visible rows describe the reflowed ring. Hidden rows retain their
      // crossed-out share of the whole ledger as a stable reference.
      row.pct.textContent = percentText(sharePercent(
        view.slices[index].spend_minor,
        on ? visibleTotal : view.total,
      ));
    });
    if (view.divisible) {
      drawWedges();
    }
    lit(null);
    tip.hide();
    paintTotal();
    shell.setAttribute('aria-label', donutLabel(claimView()));
  }

  function render(categories) {
    reset();
    if (!shell || !make || !wedgeBox) {
      return;
    }
    const slices = categories.slices || [];
    if (slices.length === 0) {
      shell.setAttribute('aria-label', CLAIM_COPY.empty);
      if (totalBox) {
        totalBox.appendChild(el('span', '', CLAIM_COPY.empty));
      }
      return;
    }

    // Every share on this panel is a division by this figure. Without it there
    // is no share to state, and stating one anyway is the failure mode the rest
    // of this file exists to avoid: the rows still list every bucket and its
    // amount, and say plainly that the shares could not be computed.
    const total = categories.total_minor;
    view = {
      slices,
      total,
      txnCount: categories.txn_count,
      divisible: typeof total === 'number' && total !== 0,
    };

    if (legendBox) {
      const list = el('ul', 'legend__list');
      slices.forEach((slice, index) => {
        list.appendChild(legendRow(slice, index));
      });
      legendBox.appendChild(list);
      legendBox.appendChild(el('p', 'chart__note', COPY.toggleKey));
      legendBox.appendChild(el('p', 'chart__note', COPY.unclaimedKey));
      legendBox.appendChild(el('p', 'chart__note', COPY.rampKey));
    }
    repaint();
  }

  return { render, reset };
}
