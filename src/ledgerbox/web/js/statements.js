// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Every file that reached the archive: the list, its search, and its pager.
// What deleting one would cost, and the two-step confirm that does it, live in
// `deletion-plan.js`.
//
// The panel is folded shut and ranked above the review queue. Shut, because it
// is provenance rather than an answer — where the lines in the table above came
// from, consulted when a figure looks wrong or when a file has to come back out.
// Above the queue, because the queue makes its claims *about* these rows: you
// read the object, then its problems. The status strip at the top already shouts
// when the queue is not empty, so nothing is lost by putting the object first.
// §5.33's principle is ranked, not filtered.
//
// `txn_count: 0` is the load-bearing value. It means the bytes are in `archive/`
// and the transactions are **not** in the ledger, which is the state the
// reconciliation gate exists to produce. That row gets a different left edge
// *and* a sentence, because a colour on its own is not a message — and its count
// is in the `<summary>`, which is the only line a reader sees while the panel is
// shut. See `renderCounts`: paging state is deliberately not in there.
//
// **Searching and paging happen in the browser here, and nowhere else on this
// page.** The transactions table sends every control to the database and says
// why in its own header: a browser that holds the ledger in order to slice it
// grows a second definition of every question it slices by. Three things make
// this list the exception rather than a relapse. `GET /api/statements` takes no
// parameters and returns the whole list, so it is already fetched whole — the
// alternative is not a narrower request, it is the same request plus work on the
// server nobody asked for. The list is bounded by how many files a person has
// uploaded (13 on the author's ledger, and it grows by one a month per account).
// And no figure anywhere on this page is derived from it: nothing below counts
// money, and the two counts in the summary are counts of rows the server just
// sent, not a second reading of anything. The moment something here starts
// adding up minor units, that argument is void and the arithmetic belongs in
// SQL.

import { button, clear, el, fetchStatements, isOffline, join } from './api.js';
import { CONNECTION_COPY } from './connection.js';
import { deleteControl } from './deletion-plan.js';

// Ten, and the panel is shut, because this list is a place you go to find one
// row — not a report you read down. A pager only appears past this; buttons that
// can only ever be disabled are furniture.
const PAGE_SIZE = 10;

// Every sentence a person reads, in one place, because rule 11 binds all of them
// alike: the line must not be stronger than the evidence behind it. The ones the
// confirmation prompt uses live next to the code that renders it, in
// `deletion-plan.js`.
const COPY = {
  unbooked: 'In the archive, not in the ledger. None of its transactions were booked, so '
    + 'nothing on this page counts them. Fixing the parser and re-ingesting these same bytes '
    + 'is the way in; deleting is the way out.',
  deleted: 'The statement was deleted.',
  listFailed: 'The statement list could not be read.',
  emptyLead: 'No statements yet.',
  emptyRest: ' Anything dropped above is listed here, archived either way — and booked only if '
    + 'it reconciles against the totals printed on it.',
  noMatch: 'No statement matches this search.',
  // An empty list with no explanation reads as data loss, and this one is one
  // keystroke away from a list of a person's own bank statements. It says what
  // undoes it.
  noMatchRest: ' Nothing has been deleted and nothing has changed — emptying the search box '
    + 'above brings the whole list back.',
};

function headNode(statement, unbooked) {
  const head = el('div', 'stmt__head');
  head.appendChild(el('span', 'stmt__month', statement.statement_month || 'month unread'));
  // No period means the layout was refused before one could be read.
  head.appendChild(el('span', 'stmt__period', statement.period_start && statement.period_end
    ? `${statement.period_start} → ${statement.period_end}` : 'period unread'));
  if (unbooked) {
    head.appendChild(el('span', 'badge badge--block', 'Not booked'));
  }
  return head;
}

function factsText(statement) {
  const parts = [statement.institution || 'institution not stated'];
  parts.push(`${statement.txn_count || 0} transaction(s)`);
  // Queue depth appears only when it is not zero: a line reading "0 blocking" on every row is
  // a line nobody reads on the row that says something else.
  const depth = [[statement.open_block, 'blocking'], [statement.open_warn, 'warning(s)']];
  for (const [count, label] of depth) {
    if (count > 0) {
      parts.push(`${count} ${label} in the queue`);
    }
  }
  parts.push(`${(statement.byte_len || 0).toLocaleString('en-US')} bytes`);
  if (statement.ingested_at) {
    parts.push(`ingested ${statement.ingested_at}`);
  }
  return parts.join(' · ');
}

function rowNode(statement, hooks) {
  const unbooked = (statement.txn_count || 0) === 0;
  const card = el('article', unbooked ? 'stmt stmt--unbooked' : 'stmt');
  card.appendChild(headNode(statement, unbooked));
  card.appendChild(el('p', 'stmt__facts', factsText(statement)));
  if (unbooked) {
    card.appendChild(el('p', 'stmt__unbooked', COPY.unbooked));
  }
  card.appendChild(deleteControl(statement, hooks));
  return card;
}

// A sentence that outlives the row it was about: what a completed deletion
// removed, or why a row just disappeared from under someone. `unremoved_files`
// is why the first case needs more than one line — the rows are gone and those
// bytes are not, and saying which file and why is what makes that a task rather
// than a mystery.
//
// It says `doctor` and not `verify`, and that correction is the point: `verify`
// only ever notices a leftover file in `archive/`. A leftover extraction cache
// left all nine checks green, and that is the file holding the whole text layer.
function receiptNode(result, dismiss) {
  const left = result.unremoved_files || [];
  const box = el('div', left.length > 0 ? 'notice notice--fail' : 'notice');
  box.appendChild(el('p', 'notice__text', result.summary || COPY.deleted));
  if (left.length > 0) {
    const lead = `${left.length} file(s) could not be removed from disk. `
      + '`ledgerbox doctor` reports them, and exits non-zero, until they are gone:';
    box.appendChild(el('p', 'notice__text', lead));
    const list = el('ul', 'plan__items');
    for (const entry of left) {
      list.appendChild(el('li', '', `${entry[0]} — ${entry[1]}`));
    }
    box.appendChild(list);
  }
  box.appendChild(join(el('div', 'notice__actions'), button('btn btn--quiet', 'Dismiss', dismiss)));
  return box;
}

/**
 * The three things a person can read off a row, lowercased and joined.
 *
 * The id is in here and is not on the row, deliberately: `find_statement`
 * accepts eight leading hex characters and the CLI prints them, so a prefix
 * pasted from a terminal has to land. A prefix is a substring, so `includes`
 * covers both it and the full sha-256 without a second branch.
 */
function haystack(statement) {
  return [statement.statement_month, statement.institution, statement.source_file_id]
    .map((part) => String(part || ''))
    .join(' ')
    .toLowerCase();
}

// The statement panel. `onChange` fires after a successful deletion so the queue
// and the health strip re-read their counts from the server; this module never
// adjusts a number the server owns.
export function createStatementList(options) {
  const container = options.container;
  const countsNode = options.countsNode;
  const onChange = options.onChange;
  // The rest of the panel is found by its `data-stmt` name, the way the
  // transactions panel finds its parts, rather than by three more ids passed
  // through `main.js`. Every one of them is optional: a missing search box means
  // no filter, a missing pager means one long list, and neither is a reason for
  // the archive to stop being readable.
  const root = container.closest('.panel') || document;
  const searchNode = root.querySelector('[data-stmt="q"]');
  const pagerNode = root.querySelector('[data-stmt="pager"]');
  const noticeNode = root.querySelector('[data-stmt="notice"]');

  // The whole list as the server last sent it. `matching()` and the pager are
  // views of this; nothing writes back into it.
  let all = [];
  let failure = null;
  let receipt = null;
  let page = 0;

  const hooks = {
    gone(message) {
      receipt = { summary: message };
      refresh();
    },
    done(result) {
      receipt = result;
      refresh();
      if (onChange) {
        onChange();
      }
    },
  };

  function needle() {
    return searchNode ? searchNode.value.trim().toLowerCase() : '';
  }

  function matching() {
    const wanted = needle();
    return wanted ? all.filter((row) => haystack(row).includes(wanted)) : all;
  }

  /**
   * The `<summary>` line, and the only thing a reader sees while the panel is
   * shut. It says how many statements there are and how many booked nothing,
   * and it is measured on the whole list rather than the page — a statement that
   * booked nothing is the one thing in this panel a person has to be told
   * without opening it, and a search or a pager must not be able to hide it.
   * Paging state belongs in the pager, where it is about the pager.
   */
  function renderCounts(rows) {
    if (!countsNode) {
      return;
    }
    clear(countsNode);
    if (!rows) {
      return;
    }
    const unbooked = rows.filter((row) => (row.txn_count || 0) === 0).length;
    countsNode.appendChild(el('span', 'count', `${rows.length} statement(s)`));
    if (unbooked > 0) {
      countsNode.appendChild(el('span', 'count count--block', `${unbooked} not booked`));
    }
  }

  function renderNotice(matched) {
    if (!noticeNode) {
      return;
    }
    clear(noticeNode);
    // Only for a search that selected nothing. An archive that is genuinely
    // empty is not a notice, it is the list saying so in the list's own place.
    const missed = !failure && all.length > 0 && matched === 0;
    noticeNode.hidden = !missed;
    if (missed) {
      join(noticeNode, el('strong', '', COPY.noMatch), COPY.noMatchRest);
    }
  }

  function renderPager(matched, start, shown) {
    if (!pagerNode) {
      return;
    }
    clear(pagerNode);
    if (failure || matched <= PAGE_SIZE) {
      return;
    }
    const previous = button('btn btn--quiet', 'Previous', () => step(-1));
    const next = button('btn btn--quiet', 'Next', () => step(1));
    previous.disabled = start <= 0;
    next.disabled = start + shown >= matched;
    // "of 4 matched, 13 in all" rather than a bare "of 4": while a search is on,
    // the number beside the arrows is not the number in the summary above, and
    // saying both is cheaper than letting a reader discover that.
    const range = `Showing ${start + 1}–${start + shown} of ${matched}`;
    pagerNode.appendChild(previous);
    pagerNode.appendChild(el('span', 'pager__range',
      needle() ? `${range} matched, ${all.length} in all` : range));
    pagerNode.appendChild(next);
  }

  function step(direction) {
    page = Math.max(0, page + direction);
    render();
  }

  function render() {
    const rows = matching();
    // Clamped rather than reset. A delete can shorten the result under the page
    // a person is standing on, and the last page that exists is nearer to where
    // they were than page one is; when the page they were on survives, they stay
    // on it. Empty results clamp to zero and render the notice instead.
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    page = Math.min(Math.max(page, 0), pages - 1);
    const start = page * PAGE_SIZE;
    const shown = rows.slice(start, start + PAGE_SIZE);

    clear(container);
    // Above everything, including a failure: it is about a row that no longer
    // exists, and the reason the list under it changed.
    if (receipt) {
      container.appendChild(receiptNode(receipt, () => {
        receipt = null;
        render();
      }));
    }
    renderNotice(rows.length);
    renderPager(rows.length, start, shown.length);
    if (failure) {
      container.appendChild(el('p', 'empty empty--fail', failure));
      return;
    }
    if (all.length === 0) {
      // An absence, not an error. On a fresh install this is the ordinary state.
      const empty = el('p', 'empty');
      container.appendChild(join(empty, el('strong', '', COPY.emptyLead), COPY.emptyRest));
      return;
    }
    for (const row of shown) {
      container.appendChild(rowNode(row, hooks));
    }
  }

  if (searchNode) {
    // Back to the first page on every change, or the offset is measured against
    // a result that no longer exists and the pager points past the end. No
    // request is issued and none is debounced: the rows are already here.
    searchNode.addEventListener('input', () => {
      page = 0;
      render();
    });
  }

  async function refresh() {
    container.setAttribute('aria-busy', 'true');
    try {
      const rows = await fetchStatements();
      all = rows;
      failure = null;
      renderCounts(rows);
      render();
    } catch (error) {
      // The list is dropped rather than left stale: a search typed after a failed
      // read would otherwise filter rows that may no longer be in the archive.
      all = [];
      // The panel's own placeholder when the process is gone; the masthead
      // indicator carries the explanation for the whole page.
      failure = isOffline(error) ? CONNECTION_COPY.panel : (error.message || COPY.listFailed);
      renderCounts(null);
      render();
    } finally {
      container.removeAttribute('aria-busy');
    }
  }

  return { refresh };
}
