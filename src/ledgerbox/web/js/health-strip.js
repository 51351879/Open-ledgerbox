// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The connection light, the status strip and the diagnostics block: everything
// the page says about /api/health. Split from main.js at the 400-line rule;
// these five functions share one input and no page state, which is what makes
// this a seam and not a shuffle.

import { clear, el, humanizeKey } from './api.js';
import { CONNECTION_COPY } from './connection.js';
import { t } from './i18n.js';

function flag(text, tone) {
  return el('span', tone ? `status__flag status__flag--${tone}` : 'status__flag', text);
}

/**
 * The connection light: a dot, the words beside it, and one sentence.
 *
 * **Colour is never the only thing that says it.** The words change with the
 * state and the dot is a second reading of them, which is the rule the
 * statement list follows for a refused statement and the donut follows for its
 * unclaimed slice.
 *
 * The sentence under it is the *only* place on the page that explains a server
 * that is not answering. Six panels used to print that explanation into
 * themselves; they now say they are waiting and leave the reason here.
 */
export function renderConnection(target, up) {
  clear(target);
  const state = up === null ? 'unknown' : (up ? 'up' : 'down');
  target.className = `link link--${state}`;
  target.appendChild(el('span', 'link__dot'));
  target.appendChild(el('span', 'link__label', CONNECTION_COPY[state]));
  if (up === false) {
    target.appendChild(el('span', 'link__aside', CONNECTION_COPY.downAside));
  } else if (up === true) {
    // Said to a screen reader and not shown: the words and the dot already
    // carry it visually, and a line of reassurance printed on every load is a
    // line nobody reads on the load where it says something else.
    target.appendChild(el('span', 'visually-hidden', CONNECTION_COPY.upAside));
  }
}

/**
 * What the status strip is currently saying, as one string.
 *
 * The heartbeat re-reads `/api/health` every few seconds, and this region is
 * `aria-live`. Rebuilding it on every poll would announce "Queue clear" to a
 * screen reader for as long as the tab stayed open — the defect §7 records for
 * the totals strip, arriving here on a timer instead of on a keystroke. So the
 * DOM is only touched when what it would say has changed.
 */
export function statusKey(health) {
  return [
    health.open_block || 0,
    health.open_warn || 0,
    health.integrity_ok ? 1 : 0,
    health.schema_version,
    health.schema_latest,
    health.database_present ? 1 : 0,
  ].join('|');
}

export function renderStatus(target, health) {
  clear(target);

  const blocking = health.open_block || 0;
  const warning = health.open_warn || 0;
  if (blocking > 0) {
    target.appendChild(
      flag(t('{count} statement(s) refused and unbooked', { count: blocking }), 'fail'),
    );
  } else if (warning > 0) {
    target.appendChild(flag(t('{count} warning(s) to look at', { count: warning }), 'warn'));
  } else {
    target.appendChild(flag(t('Queue clear'), 'ok'));
  }

  // Only ever mentioned when it is a problem. An integrity line that reads "ok"
  // on every load is a line nobody reads on the load where it does not.
  if (!health.integrity_ok) {
    target.appendChild(flag(t('Database integrity check FAILED'), 'fail'));
  }
  if (health.schema_version !== health.schema_latest) {
    target.appendChild(
      flag(
        t('Schema {version} of {latest}: migrations pending', {
          version: health.schema_version,
          latest: health.schema_latest,
        }),
        'warn',
      ),
    );
  }
  if (!health.database_present) {
    target.appendChild(
      el(
        'span',
        '',
        t('No ledger file yet. It is created the first time a statement is booked.'),
      ),
    );
  }
}

export function renderDiagnostics(target, health) {
  clear(target);

  const rows = health.rows || {};
  const names = Object.keys(rows).sort();
  if (names.length > 0) {
    const grid = el('div', 'diag__rows');
    for (const name of names) {
      const row = el('div', 'diag__row');
      row.appendChild(el('span', 'diag__key', humanizeKey(name)));
      row.appendChild(el('span', 'diag__value', String(rows[name])));
      grid.appendChild(row);
    }
    target.appendChild(grid);
  }

  const facts = el('div', 'diag__facts');
  const version = el('p');
  version.appendChild(document.createTextNode('ledgerbox '));
  version.appendChild(el('span', 'diag__value', health.version || 'unknown'));
  version.appendChild(
    document.createTextNode(
      `, schema ${health.schema_version} of ${health.schema_latest}`
      + `, integrity ${health.integrity_ok ? 'ok' : 'FAILED'}`,
    ),
  );
  facts.appendChild(version);

  // The operator's own path, on their own machine, in their own browser. It is
  // also the answer to "where did my statement actually go".
  const where = el('p');
  // The separating space stays outside the sentence: keys are normalised, so a
  // trailing space would be trimmed out of the key and out of the layout with
  // it, leaving the label welded to the path.
  where.appendChild(document.createTextNode(`${t('Data directory')} `));
  where.appendChild(el('code', 'diag__path', health.data_dir || 'unknown'));
  facts.appendChild(where);
  target.appendChild(facts);
}
