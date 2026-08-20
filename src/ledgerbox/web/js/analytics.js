// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The two pictures, and the one request that fills them.
//
// This panel owns fetching, the empty states and the failure state; the two
// chart modules own drawing and are handed a parsed object each. The split is
// the same one `transactions.js` and `transaction-filters.js` make — "what did
// I ask for" against "what came back" — with the added reason that a chart
// module that could also fail a request would have two ways to say nothing, and
// the two would word it differently.
//
// **Nothing on this page is grouped in the browser.** Months and category
// buckets are grouped, ordered and totalled by the database, on the same view
// the four figures at the top of the page are read from. A page that fetched
// the transactions and pivoted them itself would be a second definition of
// every one of those figures, which is how the predecessor ended up with a
// table that disagreed with its own chart. One request, and it is re-issued
// whenever the ledger changes rather than adjusted in place.
//
// **Empty is not a failure and is not a blank.** A ledger with nothing booked
// yet, and a ledger that has booked lines but has spent nothing, are different
// facts and read differently. Neither of them is a chart drawn from zeroes.

import { clear, el, fetchAnalytics, formatMinor, isOffline } from './api.js';
import { CONNECTION_COPY } from './connection.js';
import { localized, t } from './i18n.js';
import { loadTones } from './category-tones.js';
import { createMonthlyChart } from './chart-monthly.js';
import { createCategoryChart } from './chart-categories.js';

// The four figure names, wrapped once and read from both places that use
// them: the cells themselves and the sentence under them about transfers.
// One name for one figure is a property of where the name lives, not of
// how carefully two dictionary entries were written.
const FIGURE = localized({ in: 'In', out: 'Out', net: 'Net', balance: 'Balance' });

// Every sentence below is looked up as it is read. **The space between a
// pair of them is not inside either one**: keys are whitespace-normalised,
// and English reads through the same lookup as every other language, so a
// leading or trailing space would be trimmed out of the key and off the page
// with it, welding two sentences together. The separators are at the reading
// sites, which is where `advice.js` had to move its own.
const COPY = localized({
  nothingYet: 'Nothing is booked yet, so there is nothing to break down.',
  nothingYetRest: 'These two pictures are drawn from booked lines only: a statement that failed a '
    + 'check printed on it is archived and never averaged in here, exactly as it is never counted '
    + 'in the four figures at the top of the page.',
  // **The third fact.** The header of this file names two — nothing booked, and
  // booked but nothing spent — and the empty branch was worded for the first
  // while its condition tests the third: no booked line falls in *this window*.
  // With a range set to the last seven days on a ledger whose newest line is
  // older than that, the panel said "Nothing is booked yet" directly under a
  // Balance of somebody's actual money. Two answers on one screen and the wrong
  // one on top, which is §5.25 with the roles swapped.
  nothingHere: 'No booked line falls in this date range.',
  nothingHereRest: 'The ledger is not empty — widen the range, or set it back to All time, to '
    + 'see what is in it. The figures above describe this range too, which is why they are zero.',
  failed: 'The breakdown could not be read.',
  nothingBooked: 'Nothing is booked yet.',
  nothingBookedRest: 'Totals appear once a statement has passed every check printed on it. '
    + 'A statement that fails one is archived and listed below, never averaged in.',
  months: (count) => t('{count} transaction month(s)', { count }),
  note: 'Two readings of the same booked lines, grouped by the database and not by this page. '
    + 'Both count booked lines only: a statement that failed a check printed on it is archived '
    + 'and never averaged in here, exactly as it is never counted in the four figures above. '
    + 'Marking a line as a transfer takes it out of both pictures.',
  // The window as the *server* reported it, not as the control believes it. If
  // those ever differ, the figures belong to the server's answer.
  // "to now" was wrong on the end the presets deliberately leave open: they set
  // only `since`, precisely so a line dated ahead of today is not hidden, and
  // "to now" describes a bound the request does not carry. An open end is said
  // as open.
  //
  // The three below are functions, which `localized()` passes through
  // untouched on purpose -- it looks up strings, and a function is not one.
  // They call `t()` themselves, which is also what keeps a date and a count
  // substituted into a sentence rather than looked up in it.
  windowed: (window) => {
    if (window.since && window.until) {
      return t('dated {since} to {until}', { since: window.since, until: window.until });
    }
    return window.since
      ? t('dated {since} onwards', { since: window.since })
      : t('dated up to {until}', { until: window.until });
  },
  buckets: (count) => t('{count} bucket(s)', { count }),
  // A balance is a position at the end of the window, not a sum over it, so a
  // window that ends before this ledger begins asks about a day the ledger has
  // no evidence for. The other three figures really are zero — nothing came in,
  // because nothing is in range — and only this one would be a claim.
  balanceUnknown: 'Balance is not shown for this range: nothing in this ledger is dated on or '
    + 'before its end, so there is no evidence of what the account held then.',
});

/**
 * The analytics panel.
 *
 * `root` is the section; every part inside it is found by its `data-chart`
 * name, the way the transactions panel finds its parts, rather than by a
 * handful of ids threaded in from `main.js`.
 */
export function createAnalyticsPanel(options) {
  const root = options.root;
  const bodyNode = root.querySelector('[data-chart="body"]');
  const emptyNode = root.querySelector('[data-chart="empty"]');
  const figuresNode = options.figuresNode;
  const span = options.span || (() => ({}));
  // Fires after every successful render, so panels that quote a figure this
  // request produced re-read it rather than keeping a copy that goes stale.
  const onData = options.onData || (() => {});
  // The last totals this panel actually rendered. Held so the planning notes
  // can quote the same window the figures above are showing, rather than
  // issuing a second request that could answer about a different one.
  let lastTotals = null;

  // Stated by the module that fills these two pictures, so the sentence and the
  // thing it describes cannot drift apart in separate files.
  const head = el('div', 'panel__head');
  const heading = el('h2', 'panel__title', t('Where it went'));
  heading.id = 'analytics-h';
  head.appendChild(heading);
  const countsNode = el('p', 'panel__meta');
  head.appendChild(countsNode);
  root.insertBefore(el('p', 'panel__note', COPY.note), root.firstChild);
  root.insertBefore(head, root.firstChild);
  const monthly = createMonthlyChart(root);
  const categories = createCategoryChart(root);

  // Which request this panel is waiting for. An upload settling while a delete
  // is still in flight would otherwise let the older answer paint last, and the
  // charts would then be describing a ledger that no longer exists.
  let inFlight = 0;

  function renderCounts(data) {
    if (!countsNode) {
      return;
    }
    clear(countsNode);
    if (!data) {
      return;
    }
    countsNode.appendChild(el('span', 'count', COPY.months(data.monthly.months.length)));
    countsNode.appendChild(el('span', 'count', COPY.buckets(data.categories.slices.length)));
  }

  function figureCell(key, value, modifier) {
    const box = el('div', 'ledger__cell');
    box.appendChild(el('span', 'ledger__key', key));
    box.appendChild(el('span', modifier ? `ledger__value ${modifier}` : 'ledger__value', value));
    return box;
  }

  /**
   * The four figures, from the same response the charts came from.
   *
   * They used to be read from `/api/health`, which has no date range and never
   * will: it answers "is this ledger sound", and that question is not narrowed
   * by a window. Once the range moved the charts it had to move these too, or
   * the page would state a relationship between a filtered picture and an
   * unfiltered figure and be wrong about it for every window but one.
   */
  function renderFigures(data) {
    if (!figuresNode) {
      return;
    }
    clear(figuresNode);
    const totals = data && data.totals;
    if (!totals) {
      const box = el('p', 'ledger__empty');
      // The separator between the two sentences, outside both of them.
      box.appendChild(el('strong', '', `${COPY.nothingBooked} `));
      box.appendChild(el('span', '', COPY.nothingBookedRest));
      figuresNode.appendChild(box);
      return;
    }

    const grid = el('div', 'ledger__grid');
    grid.appendChild(
      figureCell(FIGURE.in, formatMinor(totals.inflow_minor), 'ledger__value--in'),
    );
    grid.appendChild(
      figureCell(FIGURE.out, formatMinor(totals.outflow_minor), 'ledger__value--out'),
    );
    grid.appendChild(figureCell(FIGURE.net, formatMinor(totals.net_minor)));
    // An em dash, never $0.00, and the reason is said in the line below rather
    // than only to a screen reader: the glyph is what stops the figure being
    // read as an amount, and the sentence is what makes it mean something.
    const balanceKnown = typeof totals.balance_minor === 'number';
    const balance = figureCell(
      FIGURE.balance,
      balanceKnown ? formatMinor(totals.balance_minor) : '—',
    );
    if (!balanceKnown) {
      balance.appendChild(el('span', 'visually-hidden', COPY.balanceUnknown));
    }
    grid.appendChild(balance);
    figuresNode.appendChild(grid);

    const parts = [t('{count} transaction(s)', { count: totals.txn_count })];
    if (!balanceKnown) {
      parts.push(COPY.balanceUnknown);
    }
    // Only when the window actually narrows something. A line that says "all
    // time" on every load is a line nobody reads on the load where it does not.
    if (data.span && (data.span.since || data.span.until)) {
      parts.push(COPY.windowed(data.span));
    }
    // Flagging a line as a transfer subtracts money from the two figures above,
    // and a count never said how much. Absent entirely when nothing is flagged.
    if (totals.transfer_count) {
      // `In` and `Out` are the two cells directly above, substituted rather
      // than written again, so this sentence cannot end up naming them
      // something the grid does not.
      parts.push(
        t('{count} transfer(s) excluded: {inflow} from {in}, {outflow} from {out}', {
          count: totals.transfer_count,
          inflow: formatMinor(totals.transfer_excluded_in_minor),
          in: FIGURE.in,
          outflow: formatMinor(totals.transfer_excluded_out_minor),
          out: FIGURE.out,
        }),
      );
    }
    figuresNode.appendChild(el('p', 'ledger__foot', parts.join(' · ')));
  }

  /** Show the charts, or the sentence that stands in for them. Never both. */
  function setBody(hasAnything) {
    if (bodyNode) {
      bodyNode.hidden = !hasAnything;
    }
    if (emptyNode) {
      emptyNode.hidden = hasAnything;
    }
  }

  /**
   * The stand-in for the two pictures, in whichever of its two states is true.
   *
   * `booked` is whether the *ledger* has anything in it, which is a different
   * question from whether this *window* does — and it is the server that knows:
   * `totals` is null only when nothing has ever been booked, because the route
   * decides that from an unscoped count. Reading it off the empty month list
   * instead is what produced the sentence this replaces.
   */
  function renderEmpty(booked) {
    setBody(false);
    if (!emptyNode) {
      return;
    }
    clear(emptyNode);
    const box = el('p', 'empty');
    box.appendChild(el('strong', '', booked ? COPY.nothingHere : COPY.nothingYet));
    // Same separator rule as the figures above.
    box.appendChild(el('span', '', ` ${booked ? COPY.nothingHereRest : COPY.nothingYetRest}`));
    emptyNode.appendChild(box);
  }

  function render(data) {
    const months = data.monthly.months || [];
    const slices = data.categories.slices || [];
    if (months.length === 0 && slices.length === 0) {
      monthly.reset();
      categories.reset();
      renderEmpty(Boolean(data.totals));
      return;
    }
    setBody(true);
    monthly.render(data.monthly);
    categories.render(data.categories);
  }

  /**
   * What this panel shows when its request did not come back.
   *
   * **One message per panel, not one per node.** This panel owns two places a
   * sentence can go — the figures at the top and the stand-in for the charts —
   * and printing the failure in both made a single dead server say the same
   * thing twice here alone. It goes in the figures, which is the higher of the
   * two, and the chart area stays empty under it.
   *
   * A server that is not answering is not this panel's news to break: the
   * indicator in the masthead says it once, for the whole page, and this says
   * only that it is waiting. Anything else — a 500, a 422 — is specific to this
   * request and is shown in the server's own words.
   */
  function failed(error) {
    const offline = isOffline(error);
    const message = offline ? CONNECTION_COPY.panel : (error.message || COPY.failed);
    renderCounts(null);
    if (figuresNode) {
      clear(figuresNode);
      figuresNode.appendChild(el('p', 'ledger__empty', message));
    }
    monthly.reset();
    categories.reset();
    setBody(false);
    if (emptyNode) {
      clear(emptyNode);
      if (!offline) {
        emptyNode.appendChild(el('p', 'empty empty--fail', message));
      }
    }
  }

  /**
   * One request, and everything this panel shows comes out of it. Called at
   * boot, after an upload, after a statement is deleted, and after a category
   * is recorded — the last one because naming the `transfer` category takes a
   * line out of both pictures, and naming any other puts it back.
   */
  async function refresh() {
    inFlight += 1;
    const mine = inFlight;
    root.setAttribute('aria-busy', 'true');
    try {
      // The colour map before the first paint, so no wedge is drawn with a
      // step it would change on the next render. Cached after the first call.
      const [data] = await Promise.all([fetchAnalytics(span()), loadTones()]);
      if (mine !== inFlight) {
        return;
      }
      lastTotals = data.totals || null;
      renderCounts(data);
      renderFigures(data);
      render(data);
      onData();
    } catch (error) {
      if (mine !== inFlight) {
        return;
      }
      // The server's own sentence, not a friendlier one invented here.
      failed(error);
    } finally {
      if (mine === inFlight) {
        root.removeAttribute('aria-busy');
      }
    }
  }

  return {
    refresh,
    /** The selected window's net, or null when nothing is booked. */
    net: () => (lastTotals ? lastTotals.net_minor : null),
  };
}
