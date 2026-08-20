// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Money in and money out, one column per transaction month.
//
// **Diverging around a zero line, not grouped bars.** The question a person
// brings to this chart is 收支 — did this month take in more than it let out —
// and a zero line answers that by position: a column whose upper half is taller
// than its lower half is a month that ended up. Grouped bars can carry the same
// two numbers but the comparison is then between two neighbouring heights with
// no baseline between them, and the sign of the difference has to be worked out
// rather than seen. The trade is that the two halves of one column share a
// scale, which is what makes the zero line mean anything: one pixel is the same
// number of cents above the line and below it.
//
// **The axis is the transaction month, and it is not the table's month.**
// `CashflowMonthOut` renamed the field from `statement_month` to `month` when
// its meaning changed: it now answers *when did this happen*, keyed on
// `txn.date`, so the bars are the four figures at the top decomposed by month
// exactly as the pie is the Out decomposed by category. The transaction table's
// Month filter still means *which statement is this printed on*, derived from
// the period's end day, and the two differ for any line near a period boundary.
// Both are labelled wherever they appear; the predecessor had both, labelled
// neither, and put 83 of 415 rows in different months with nothing on screen
// saying so.
//
// This module read `statement_month` for one commit after the rename, which
// made every column on the chart draw its label as "no month" — the fallback
// below doing exactly what it was built to do, on a field that had moved.
//
// **A column with no month still gets drawn, at the end, labelled.** `month` is
// documented as never null, so that fallback should now be unreachable; it
// stays because dropping a row would make the columns sum to less than the
// figures at the top of the page while looking complete, and a bucket nobody
// can see is how that goes unnoticed.
//
// **The `<details>` under the chart is the chart.** Every figure drawn above is
// in it as text, plus the server's own totals — which are the server's, not a
// sum taken here. A picture nobody can read is not an accessible picture with
// an alt attribute on it; it is a picture, and the numbers have to be somewhere
// a screen reader, a printer and a person who does not read charts can all get
// at them.

import { clear, el, formatMinor } from './api.js';
import { localized, t } from './i18n.js';
import { attr, niceStep, svgFactory } from './charts.js';
import { createChartTooltip } from './chart-tooltip.js';

// `keyIn` and `keyOut` used to sit here and nothing has ever read them:
// `index.html` carries those two legend labels as markup. An unread string is
// the exact shape that becomes a dictionary entry which can never appear.
const COPY = localized({
  noMonth: 'no month',
  noMonthNote: '“no month” is a booked line the server returned with no month on it. It is drawn '
    + 'as its own column at the end rather than dropped, because a column that is not there is a '
    + 'column nobody can question.',
  monthKey: 'Columns are transaction months — when the money moved. The Month filter on the table '
    + 'below is the statement month, which is the statement a line was printed on, and the two '
    + 'differ for a line near a period boundary.',
  // A function, passed through by `localized()` untouched and calling `t()`
  // itself: the two counts are substituted, never looked up.
  thinned: (shown, total) => t('Only {shown} of {total} month labels are drawn, so they '
    + 'do not overlap. Every month is in the table below, labelled.', { shown, total }),
  gridNote: 'Gridlines are money, the middle line is zero. In is drawn above it and out below, on '
    + 'one shared scale.',
  totals: 'All months',
  empty: 'No month has a booked line yet.',
});

// Looked up where the header is drawn, not here: this array is built at import
// time and `main.js` chooses the language after every module is imported.
const COLUMNS = [
  ['Transaction month', 'chart-table__month'],
  ['In', 'chart-table__num'],
  ['Out', 'chart-table__num'],
  ['Net', 'chart-table__num'],
  ['Lines', 'chart-table__num'],
];

// Drawing units, which are CSS pixels here: the chart is given explicit width
// and height attributes and scrolls inside `.chart-scroll` rather than being
// scaled to fit. A viewBox that shrinks to a phone shrinks its own axis labels
// with it, and a 5px month label is a label that is not there.
const GEO = {
  // `plotH` fills the 300px chart well `charts.css` gives this card, which is
  // the height the operator's own dashboard uses for the same chart. It is the
  // drawing height and not a scale: every column is still its own figure times
  // the one shared scale, and making the well taller made no bar taller
  // relative to another.
  padLeft: 84, padRight: 16, padTop: 14, plotH: 240, barFrac: 0.54, maxGridlines: 6,
  // The plot is this wide whenever the months can share it out between them, so
  // the gridlines end where the data does rather than running on past the last
  // column. Outside that range the columns take their floor or their ceiling
  // and the chart's own width follows: one month does not become one enormous
  // bar, and sixty do not become invisible ones. Widened with the card: this is
  // the 2fr cell of the charts row now, not a full-width region under a table.
  plotW: 580, maxBand: 96, minBand: 12,
  // How wide `2025-06` is at the axis font size, and where the one or two rows
  // of month labels sit under the plot. This number is a measurement and not a
  // taste: `.chart__tick` went from 10px to 11px in the restyle and this stayed
  // at 44, so thirteen months read as `2025-012025-022025-03` — the plan said
  // one row would fit and the glyphs did not. Seven mono characters at 11px is
  // 46px, plus a gap.
  labelWidth: 52, labelDrop: 18, labelRow: 14,
};

/** Dated months in the order the server sent, then any undated bucket. */
function ordered(months) {
  const rows = months || [];
  return rows.filter((row) => row.month).concat(rows.filter((row) => !row.month));
}

function label(row) {
  return row.month || COPY.noMonth;
}

/** The widest of a field over the rows, without spreading an array into a call. */
function peak(rows, read) {
  return rows.reduce((best, row) => Math.max(best, read(row) || 0), 0);
}

/** How wide one month's column is: an even share of the plot, within bounds. */
function bandWidth(count) {
  const even = GEO.plotW / Math.max(1, count);
  return Math.min(GEO.maxBand, Math.max(GEO.minBand, even));
}

/**
 * How the month labels fit under the columns: on one row, or staggered over two
 * so that each has twice the width, and past that every nth one.
 *
 * Staggering before thinning is deliberate. A year of statements is the ordinary
 * case and its bands are narrower than a `2025-06`; dropping every second label
 * there would leave the commonest ledger on this page half-labelled. Thinning is
 * still not thinning data — every column is drawn at its true height either way,
 * and every month has its own row in the table under the chart.
 */
function labelPlan(band) {
  if (band >= GEO.labelWidth) {
    return { stride: 1, rows: 1 };
  }
  return { stride: Math.max(1, Math.ceil(GEO.labelWidth / (band * 2))), rows: 2 };
}

function numberCell(value) {
  return el('td', 'chart-table__num num money', formatMinor(value));
}

/** One month's four figures, in the order the table under the chart prints them. */
function figures(row) {
  return {
    title: label(row),
    rows: [
      [t('In'), formatMinor(row.inflow_minor)],
      [t('Out'), formatMinor(row.outflow_minor)],
      [t('Net'), formatMinor(row.net_minor)],
      [t('Lines'), String(row.txn_count)],
    ],
  };
}

/** The same four as one sentence, for whoever has the column focused. */
function spoken(row) {
  return t('{month}: in {inflow}, out {outflow}, net {net}, {count} line(s).', {
    month: label(row),
    inflow: formatMinor(row.inflow_minor),
    outflow: formatMinor(row.outflow_minor),
    net: formatMinor(row.net_minor),
    count: row.txn_count,
  });
}

/** The figures as a table: the same rows, the server's own totals underneath.
 *
 * Returned inside its own horizontal scroller, exactly as the transaction table
 * is (`.txn-scroll`). Five columns of `white-space: nowrap` money do not fit a
 * phone, and `width: 100%` does not stop them trying: measured at a 380px
 * viewport this table was 435px wide with nothing clipping it, so the **whole
 * page** scrolled sideways and every other panel moved with it. A page that
 * scrolls sideways because of one table is a page one table breaks.
 */
function tableNode(rows, monthly) {
  const table = el('table', 'chart-table');
  const head = el('thead');
  const headRow = el('tr');
  for (const [text, className] of COLUMNS) {
    const cell = el('th', className, t(text));
    cell.scope = 'col';
    headRow.appendChild(cell);
  }
  head.appendChild(headRow);
  table.appendChild(head);

  const body = el('tbody');
  for (const row of rows) {
    const line = el('tr');
    const month = el('th', 'chart-table__month num', label(row));
    month.scope = 'row';
    line.appendChild(month);
    line.appendChild(numberCell(row.inflow_minor));
    line.appendChild(numberCell(row.outflow_minor));
    line.appendChild(numberCell(row.net_minor));
    line.appendChild(el('td', 'chart-table__num num', String(row.txn_count)));
    body.appendChild(line);
  }
  table.appendChild(body);

  // Read from the server's own fields rather than summed over the rows above.
  // A total the browser added up is a second definition of the total, and the
  // two disagree on the day one of them is wrong.
  const foot = el('tfoot');
  const totals = el('tr');
  const key = el('th', 'chart-table__month', COPY.totals);
  key.scope = 'row';
  totals.appendChild(key);
  totals.appendChild(numberCell(monthly.inflow_minor));
  totals.appendChild(numberCell(monthly.outflow_minor));
  totals.appendChild(numberCell(monthly.net_minor));
  totals.appendChild(el('td', 'chart-table__num num', String(monthly.txn_count)));
  foot.appendChild(totals);
  table.appendChild(foot);

  const scroller = el('div', 'chart-scroll');
  scroller.appendChild(table);
  return scroller;
}

/**
 * The bar chart. `root` is the analytics section; the `<svg>` shell and the
 * disclosure body are found by their `data-chart` names, the same way the
 * transactions panel finds its parts.
 */
export function createMonthlyChart(root) {
  const shell = root.querySelector('[data-chart="monthly"]');
  const tableBox = root.querySelector('[data-chart="monthly-table"]');
  const noteBox = root.querySelector('[data-chart="monthly-note"]');
  const make = shell ? svgFactory(shell) : null;
  const tip = createChartTooltip(shell ? shell.closest('.chart') : null);

  function reset() {
    tip.hide();
    if (shell) {
      clear(shell);
    }
    if (tableBox) {
      clear(tableBox);
    }
    if (noteBox) {
      clear(noteBox);
    }
  }

  function gridlines(scale, zeroY, width, step, up, down) {
    const parts = [];
    for (let k = 1; k <= GEO.maxGridlines; k += 1) {
      const value = k * step;
      if (value <= up) {
        parts.push([zeroY - value * scale, value]);
      }
      if (value <= down) {
        parts.push([zeroY + value * scale, -value]);
      }
    }
    const group = make('g', 'chart__grid');
    for (const [y, value] of parts) {
      group.appendChild(attr(make('line', 'chart__gridline'), {
        x1: GEO.padLeft, y1: y, x2: width - GEO.padRight, y2: y,
      }));
      group.appendChild(attr(make('text', 'chart__gridlabel', formatMinor(value)), {
        x: GEO.padLeft - 8, y: y + 3, 'text-anchor': 'end',
      }));
    }
    group.appendChild(attr(make('line', 'chart__zero'), {
      x1: GEO.padLeft, y1: zeroY, x2: width - GEO.padRight, y2: zeroY,
    }));
    group.appendChild(attr(make('text', 'chart__gridlabel', formatMinor(0)), {
      x: GEO.padLeft - 8, y: zeroY + 3, 'text-anchor': 'end',
    }));
    return group;
  }

  function bar(className, x, top, width, height) {
    return attr(make('rect', className), {
      x, y: top, width, height: Math.max(0, height),
    });
  }

  function render(monthly) {
    reset();
    if (!shell || !make) {
      return;
    }
    const rows = ordered(monthly.months);
    if (rows.length === 0) {
      if (noteBox) {
        noteBox.appendChild(el('p', 'chart__note', COPY.empty));
      }
      shell.setAttribute('aria-label', COPY.empty);
      return;
    }

    const up = peak(rows, (row) => row.inflow_minor);
    const down = peak(rows, (row) => -row.outflow_minor);
    const span = up + down;
    const band = bandWidth(rows.length);
    const plan = labelPlan(band);
    const width = GEO.padLeft + rows.length * band + GEO.padRight;
    const height = GEO.padTop + GEO.plotH + GEO.labelDrop
      + (plan.rows === 2 ? GEO.labelRow : 0) + 8;
    // Both halves share one scale, which is the whole point of the zero line.
    const scale = span > 0 ? GEO.plotH / span : 0;
    const zeroY = span > 0 ? GEO.padTop + up * scale : GEO.padTop + GEO.plotH / 2;

    attr(shell, { viewBox: `0 0 ${width} ${height}`, width, height });
    shell.appendChild(gridlines(scale, zeroY, width, niceStep(Math.max(up, down), 3), up, down));

    const bars = make('g', 'chart__bars');
    const hits = make('g', 'chart__hits');
    const ticks = make('g', 'chart__ticks');
    const barW = band * GEO.barFrac;
    const baseline = GEO.padTop + GEO.plotH + GEO.labelDrop;
    rows.forEach((row, index) => {
      const centre = GEO.padLeft + index * band + band / 2;
      const left = centre - barW / 2;
      const inflow = (row.inflow_minor || 0) * scale;
      const outflow = -(row.outflow_minor || 0) * scale;
      bars.appendChild(bar('chart__bar chart__bar--in', left, zeroY - inflow, barW, inflow));
      bars.appendChild(bar('chart__bar chart__bar--out', left, zeroY, barW, outflow));
      // One hit target per month, the whole band wide and the whole plot tall,
      // so a month of two short bars is as easy to reach as a month of two tall
      // ones — and a month of two *zero* bars, which has nothing to aim at, is
      // reachable at all. It is focusable, so the four figures arrive on Tab
      // exactly as they arrive on hover; `charts.css` takes pointer events off
      // the bars, which is what lets one rectangle stand for a whole column.
      const hit = attr(make('rect', 'chart__hit'), {
        x: GEO.padLeft + index * band, y: GEO.padTop, width: band, height: GEO.plotH,
        tabindex: 0, role: 'img', 'aria-label': spoken(row),
      });
      hits.appendChild(hit);
      tip.bind(hit, () => figures(row), { atTop: true });
      if (index % plan.stride === 0) {
        // The stagger counts drawn labels, not months: with a stride of two,
        // `index % 2` would put every one of them on the same row.
        const slot = (index / plan.stride) % 2;
        ticks.appendChild(attr(make('text', 'chart__tick', label(row)), {
          x: centre,
          y: baseline + (plan.rows === 2 ? slot * GEO.labelRow : 0),
          'text-anchor': 'middle',
        }));
      }
    });
    // Hit targets under the bars, so the wash a hovered column gets never
    // paints over the two figures it is there to help you read.
    shell.appendChild(hits);
    shell.appendChild(bars);
    shell.appendChild(ticks);

    const first = label(rows[0]);
    const last = label(rows[rows.length - 1]);
    // `role="group"`, not the `role="img"` the empty shell carries in the
    // markup. Everything inside an element with `role="img"` is presentational
    // by definition, which would have hidden the focusable columns added above
    // from the accessibility tree while leaving them in the tab order — a stop
    // that announces nothing. The label below becomes the group's name and is
    // still read; each column then carries its own four figures.
    attr(shell, {
      role: 'group',
      'aria-label': t('Bar chart of money in and out for {count} transaction month(s), '
        + '{first} to {last}. In is drawn above a zero line and out below it, on one '
        + 'shared scale. Each column can be focused for its own figures, and every figure '
        + 'in it is in the table under the chart.', { count: rows.length, first, last }),
    });

    if (noteBox) {
      noteBox.appendChild(el('p', 'chart__note', COPY.gridNote));
      noteBox.appendChild(el('p', 'chart__note', COPY.monthKey));
      if (plan.stride > 1) {
        const shown = Math.ceil(rows.length / plan.stride);
        noteBox.appendChild(el('p', 'chart__note', COPY.thinned(shown, rows.length)));
      }
      if (rows.some((row) => !row.month)) {
        noteBox.appendChild(el('p', 'chart__note', COPY.noMonthNote));
      }
    }
    if (tableBox) {
      tableBox.appendChild(tableNode(rows, monthly));
    }
  }

  return { render, reset };
}
