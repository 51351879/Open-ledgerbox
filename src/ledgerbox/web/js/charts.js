// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The drawing primitives the two charts share, and nothing that knows what is
// being drawn. Geometry, a colour rank, and the two number-to-string rules that
// would otherwise be written twice and drift once.
//
// **Why the SVG shells are in `index.html` and not created here.**
// `document.createElementNS` needs the SVG namespace, and that namespace is
// written as a w3.org web address. `tests/test_api.py::
// test_the_frontend_requests_nothing_off_origin` fails on either URL scheme
// appearing in any line of any file under `web/` — comments included, as this
// paragraph found out — with no exclusions, and it is right to: the claim this
// page makes is that it names no off-origin address at all, and a guard with an
// exemption list has stopped being checkable. Splitting the literal to slip
// past a substring match would be worse still — it would leave the guard
// passing while the claim it stands for was false.
//
// So the namespace is never written down here. `index.html` carries the empty
// `<svg>` shells; the HTML parser puts them in the SVG namespace by itself,
// with no `xmlns` attribute needed, because `<svg>` is foreign content. This
// module reads `shell.namespaceURI` back off one of those parser-made nodes and
// builds every child from it. The string exists at runtime and appears in no
// shipped file, which is exactly what the guard is asking for.
//
// Nothing here computes a total, a share or a currency string. Shares are
// divided out of figures the server sent, and every amount goes through
// `api.js`'s `formatMinor`.
//
// The `<details>` disclosures the two charts open into are markup in
// `index.html`, in the shape the Diagnostics disclosure at the bottom of the
// page established, rather than assembled here: the sentence on a `<summary>`
// is a sentence a person reads, and this codebase keeps those where they can be
// read whole.

/**
 * How many categorical steps `charts.css` actually defines.
 *
 * Twenty-four, because the shipped rules file defines twenty-four categories.
 * The taxonomy map assigns a step to all of them because transaction chips can
 * show both `transfer` and `investment`, even though both transfer-kind labels
 * are excluded from this spending breakdown. The donut can therefore draw at
 * most twenty-two named buckets; its unclaimed slice is hatched and consumes no
 * step. No category has to take a colour a previous category already has.
 *
 * The first eight steps are the operator's own dashboard palette in its own
 * order; the other sixteen fill unused hue regions or add visibly deeper shades
 * of hues already present. `charts.css` carries the values,
 * the reasoning and the measured contrast of each.
 *
 * If a future rules file ever adds a twenty-fifth category, index 24 and beyond
 * take the last step and the legend is the only thing telling them apart. The
 * test for the taxonomy map pins the current twenty-four distinct assignments so a
 * rules-file expansion cannot silently enter that fallback again.
 */
export const SLICE_STEPS = 24;

/** Round for a path string. Three places is below a device pixel at this size. */
function f(value) {
  return value.toFixed(3);
}

/**
 * An element maker bound to one parser-made SVG shell. See the header for why
 * the namespace is read off a node rather than written down.
 *
 * `class` goes on by `setAttribute` rather than `.className`, which on an SVG
 * element is a read-only `SVGAnimatedString` and silently does nothing.
 */
export function svgFactory(shell) {
  const ns = shell.namespaceURI;
  return function make(tag, className, text) {
    const node = document.createElementNS(ns, tag);
    if (className) {
      node.setAttribute('class', className);
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  };
}

/** Attributes from a plain object. Values are stringified, never parsed. */
export function attr(node, values) {
  for (const key of Object.keys(values)) {
    node.setAttribute(key, String(values[key]));
  }
  return node;
}

/** A point on a circle. Turn 0 is twelve o'clock and turns run clockwise. */
function polar(cx, cy, radius, turn) {
  const angle = (turn - 0.25) * 2 * Math.PI;
  return [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
}

/**
 * One annular sector, as a path `d`. `from`/`to` are turns in [0, 1].
 *
 * A sweep of a whole turn has identical endpoints, and an elliptical arc
 * between identical points draws nothing at all — which is how a ledger with
 * exactly one bucket ends up rendering an empty circle. Two half turns instead.
 */
export function ringPath(cx, cy, outer, inner, from, to) {
  const sweep = to - from;
  if (!(sweep > 0)) {
    // Zero draws zero. A bucket that spent nothing has no wedge, and nothing
    // here nudges it up to a visible minimum: an inflated wedge is a false
    // claim about a magnitude, which is the one thing this page must not make.
    return '';
  }
  if (sweep >= 1) {
    return `${ringPath(cx, cy, outer, inner, 0, 0.5)} ${ringPath(cx, cy, outer, inner, 0.5, 1)}`;
  }
  const large = sweep > 0.5 ? 1 : 0;
  const [ox1, oy1] = polar(cx, cy, outer, from);
  const [ox2, oy2] = polar(cx, cy, outer, to);
  const [ix1, iy1] = polar(cx, cy, inner, from);
  const [ix2, iy2] = polar(cx, cy, inner, to);
  return `M ${f(ox1)} ${f(oy1)} A ${outer} ${outer} 0 ${large} 1 ${f(ox2)} ${f(oy2)}`
    + ` L ${f(ix2)} ${f(iy2)} A ${inner} ${inner} 0 ${large} 0 ${f(ix1)} ${f(iy1)} Z`;
}

/**
 * A gridline interval that lands on a readable number: 1, 2, 2.5 or 5 times a
 * power of ten. `lines` is how many are wanted, not how many are produced —
 * the caller stops when it runs out of chart.
 */
export function niceStep(span, lines) {
  if (!(span > 0)) {
    return 1;
  }
  const raw = span / Math.max(1, lines);
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  for (const factor of [1, 2, 2.5, 5]) {
    if (raw <= magnitude * factor) {
      return Math.max(1, Math.round(magnitude * factor));
    }
  }
  return Math.max(1, Math.round(magnitude * 10));
}

/**
 * The palette step numbered `index`, as the class `charts.css` uses.
 *
 * `index` is a position in the **taxonomy**, not in a chart: `category-tones.js`
 * is the only caller that decides one, and it counts through the list
 * `/api/categories` returns. This said "the slice at `index`" while that was
 * still true and for the whole of M6 after it stopped being.
 *
 * The class only carries the colour: `charts.css` turns `--slice` into a fill
 * or a background through a separate `--paint` class that the unclaimed bucket
 * never gets. That separation is load-bearing — a rule that set `fill` on every
 * wedge would beat the `fill="url(#chart-unclaimed)"` attribute on the hatched
 * one and hand the absence of a decision a flat colour, which is the whole
 * failure this chart is built to avoid.
 */
export function sliceClass(index) {
  return `slice-${Math.min(index + 1, SLICE_STEPS)}`;
}

/** `part / whole` as a percentage, or null when the whole is nothing to divide by. */
export function sharePercent(part, whole) {
  if (typeof part !== 'number' || typeof whole !== 'number' || whole === 0) {
    return null;
  }
  return (part / whole) * 100;
}

/**
 * A share, printed with enough places that a small one is still a number.
 *
 * A real breakdown can put nearly everything in one bucket and leave a long
 * tail under one percent. A fixed single place prints most of that tail
 * readably but renders a 0.009% bucket as "0.0%" — a figure
 * that reads as nothing when it is a real amount of money, on a page whose
 * whole argument is that a small number and no number are different. Places are
 * added until the figure survives, and only under a thousandth of a percent
 * does it give up and say so as an inequality instead of rounding to zero.
 */
export function percentText(pct) {
  if (pct === null) {
    return 'share not computable';
  }
  const size = Math.abs(pct);
  if (size === 0) {
    return '0%';
  }
  for (const [floor, places] of [[0.1, 1], [0.01, 2], [0.001, 3]]) {
    if (size >= floor) {
      return `${pct.toFixed(places)}%`;
    }
  }
  return pct < 0 ? '>-0.001%' : '<0.001%';
}
