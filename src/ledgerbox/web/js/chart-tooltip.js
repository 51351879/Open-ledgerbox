// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The one hover mechanism both charts use, and the reason it is an HTML box and
// not anything drawn inside an `<svg>`.
//
// **Why not in the SVG.** SVG has no z-index and nothing painted in it can
// escape the viewBox: a `<text>` or `<foreignObject>` tooltip is clipped the
// moment it reaches past the plot, and the columns that reach past it are the
// leftmost and the rightmost — the first two anybody hovers. So the tooltip is
// an ordinary absolutely positioned `<div>` in the chart card, measured against
// that card's own box and clamped inside it, and the figure stays a figure.
// Clamping is against the card rather than the window on purpose: a box that
// stayed inside the window but hung out of a card would push the page sideways,
// which is the failure `.chart-scroll` and `.charts__grid > *` already carry
// notes about.
//
// **It never becomes the chart's accessible name.** The box is `aria-hidden`,
// so a screen reader gets one copy of the figures and a pointer gets the other
// rather than both getting two.
//
// **Which targets get `focus` is a decision, not a default.** A tooltip only a
// mouse can reach is a tooltip half the people on this page do not have, so
// anything already in the tab order is bound for focus as well: the month
// columns' hit rects, which carry the same figures on their own `aria-label`,
// and the donut's legend rows, which are `<button>`s carrying them as text.
//
// The donut's **wedges** are bound `pointerOnly`, and that was measured rather
// than reasoned. Adding a `focus` listener to a `<path>` makes it focusable in
// Chromium — `tabIndex` still reports -1, which is how it went unnoticed — and
// a focusable element cannot be presentational, so the wedges came out of the
// `role="img"` subtree and became nine tab stops with no accessible name
// between them. The figures were never lost: the legend beside the ring carries
// every slice by name, share, amount and line count, in text, in the default
// view. What the wedges added to a keyboard was nine silent stops on the way
// past it.
//
// **It states nothing of its own.** Every string in it is passed in already
// formatted by the caller, which means through `api.js`'s `formatMinor` for
// money. Nothing here adds up, divides, or rounds.

import { clear, el } from './api.js';

/** Between the tooltip and the thing it describes, and the card's edge. */
const GAP = 8;
const EDGE = 6;

/**
 * A tooltip that lives inside `host` and is positioned over it.
 *
 * `host` must be a positioned element — `charts.css` gives `.chart` its
 * `position: relative` for exactly this. A null host makes every method a
 * no-op, so a panel whose markup is missing still renders its figures.
 */
export function createChartTooltip(host) {
  let box = null;

  function ensure() {
    if (!box) {
      box = el('div', 'tip');
      box.setAttribute('aria-hidden', 'true');
      box.hidden = true;
      host.appendChild(box);
    }
    return box;
  }

  /** Title, key/value lines, and an optional sentence under them. */
  function fill(said) {
    const node = ensure();
    clear(node);
    node.appendChild(el('p', 'tip__title', said.title));
    for (const [key, value] of said.rows || []) {
      const line = el('p', 'tip__row');
      line.appendChild(el('span', 'tip__key', key));
      line.appendChild(el('span', 'tip__value num money', value));
      node.appendChild(line);
    }
    if (said.note) {
      node.appendChild(el('p', 'tip__note', said.note));
    }
    return node;
  }

  /**
   * Centre on the anchor, then clamp to the card.
   *
   * The tooltip is measured after being shown, because a hidden box has no
   * size. `atTop` puts it just inside the anchor's top edge rather than above
   * it, which is what the month columns want: their hit target is the whole
   * height of the plot, so "above it" would be a long way from the column.
   */
  function place(node, anchor, atTop) {
    const card = host.getBoundingClientRect();
    const mark = anchor.getBoundingClientRect();
    const tip = node.getBoundingClientRect();
    const centred = mark.left + mark.width / 2 - card.left - tip.width / 2;
    const rightmost = card.width - tip.width - EDGE;
    node.style.left = `${Math.max(EDGE, Math.min(centred, rightmost))}px`;
    const above = mark.top - card.top - tip.height - GAP;
    const within = mark.top - card.top + GAP;
    const wanted = atTop || above < EDGE ? within : above;
    node.style.top = `${Math.max(EDGE, Math.min(wanted, card.height - tip.height - EDGE))}px`;
  }

  function hide() {
    if (box) {
      box.hidden = true;
    }
  }

  /**
   * Open on hover and on focus, close on leave and on blur.
   *
   * `describe` is called each time rather than once, so a target whose figures
   * or whose anchor changed since it was bound says the current thing. It may
   * return null, which closes the tooltip instead of opening it — that is how a
   * legend row whose slice is switched off declines to point at a wedge that is
   * not drawn.
   *
   * `options.pointerOnly` leaves `focus` and `blur` off. It is for a target
   * that is **not** a control and must not become one — see this module's
   * header for what binding focus to an SVG `<path>` did to the tab order.
   */
  function bind(target, describe, options) {
    if (!host) {
      return;
    }
    const atTop = Boolean(options && options.atTop);
    const pointerOnly = Boolean(options && options.pointerOnly);
    const open = () => {
      const said = describe();
      if (!said) {
        hide();
        return;
      }
      const node = fill(said);
      node.hidden = false;
      place(node, said.anchor || target, atTop);
    };
    target.addEventListener('mouseenter', open);
    target.addEventListener('mouseleave', hide);
    if (!pointerOnly) {
      target.addEventListener('focus', open);
      target.addEventListener('blur', hide);
    }
  }

  return { bind, hide: () => (host ? hide() : undefined) };
}
