// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The review queue: what was archived, what was refused, and why. Also the
// renderer for a single queued item, which `upload.js` reuses so that an item
// looks the same the moment it is created as it does an hour later.
//
// Resolving books nothing. The buttons here write a status and a timestamp; the
// only way a refused statement enters the ledger is to fix the parser and
// re-ingest the archived bytes. The copy says so because the button cannot.

import {
  ApiError,
  button,
  clear,
  el,
  fetchReview,
  formatMinor,
  humanizeKey,
  isMinorField,
  isOffline,
  resolveReviewItem,
} from './api.js';
import { CONNECTION_COPY } from './connection.js';

// Deep enough for the nested payloads the reconciler emits, shallow enough that
// a pathological object cannot lock the page up building nodes.
const MAX_DETAIL_DEPTH = 3;

const SEVERITY_LABEL = { block: 'Blocking', warn: 'Warning' };

function valueNode(key, value, depth) {
  if (value === null || value === undefined) {
    return el('span', 'muted', '—');
  }
  if (isMinorField(key) && typeof value === 'number') {
    return el('span', 'num money', formatMinor(value));
  }
  if (typeof value === 'number') {
    return el('span', 'num', String(value));
  }
  if (typeof value === 'boolean') {
    return el('span', '', value ? 'yes' : 'no');
  }
  if (Array.isArray(value)) {
    if (depth >= MAX_DETAIL_DEPTH) {
      return el('span', 'num', JSON.stringify(value));
    }
    const list = el('ul', 'detail__items');
    for (const entry of value) {
      const row = el('li');
      // The key travels down so that `amounts_minor: [...]` formats each member
      // as currency rather than as a bare integer.
      row.appendChild(valueNode(key, entry, depth + 1));
      list.appendChild(row);
    }
    return list;
  }
  if (typeof value === 'object') {
    if (depth >= MAX_DETAIL_DEPTH) {
      return el('span', 'num', JSON.stringify(value));
    }
    return detailList(value, depth + 1) || el('span', 'muted', '—');
  }
  // Strings print as-is. They are third-party text — statement descriptions,
  // payee names, transfer memos — and textContent is the whole defence.
  return el('span', '', String(value));
}

/** The `detail` object as a definition list, or null when it is empty. */
export function detailList(detail, depth) {
  const level = depth || 0;
  const entries = Object.entries(detail || {});
  if (entries.length === 0) {
    return null;
  }
  const list = el('dl', level > 0 ? 'detail detail--nested' : 'detail');
  for (const [key, value] of entries) {
    list.appendChild(el('dt', 'detail__key', humanizeKey(key)));
    const cell = el('dd', 'detail__value');
    cell.appendChild(valueNode(key, value, level));
    list.appendChild(cell);
  }
  return list;
}

function itemHead(item, severity) {
  const head = el('div', 'item__head');
  head.appendChild(el('span', `badge badge--${severity}`, SEVERITY_LABEL[severity] || severity));
  head.appendChild(el('code', 'item__check', item.check_id || 'unknown check'));
  if (item.statement_month) {
    head.appendChild(el('span', 'item__month', item.statement_month));
  } else {
    // No month means the layout was refused before a period could be read.
    head.appendChild(el('span', 'item__month muted', 'period unread'));
  }
  if (item.status && item.status !== 'open') {
    head.appendChild(el('span', 'badge badge--quiet', item.status));
  }
  return head;
}

function itemStamps(item) {
  const parts = [];
  if (item.created_at) {
    parts.push(`Queued ${item.created_at}`);
  }
  if (item.resolved_at) {
    parts.push(`Closed ${item.resolved_at}`);
  }
  return parts.length > 0 ? el('p', 'item__stamp', parts.join(' · ')) : null;
}

/**
 * One queued item as a card. Pass `onDone` to get Resolve / Dismiss buttons;
 * omit it for the read-only copies shown inside an upload result.
 */
export function reviewItemNode(item, options) {
  const settings = options || {};
  const severity = item.severity === 'block' ? 'block' : 'warn';
  const card = el('article', `item item--${severity}`);

  card.appendChild(itemHead(item, severity));
  card.appendChild(el('p', 'item__message', item.message || ''));

  const detail = detailList(item.detail);
  if (detail) {
    card.appendChild(detail);
  }

  const stamps = itemStamps(item);
  if (stamps) {
    card.appendChild(stamps);
  }

  if (settings.onDone && (!item.status || item.status === 'open')) {
    card.appendChild(actionsNode(item, settings.onDone));
  }
  return card;
}

function actionsNode(item, onDone) {
  const wrap = el('div', 'item__foot');
  const actions = el('div', 'item__actions');
  const notice = el('div', 'notice');
  notice.hidden = true;

  const ui = {
    setBusy(busy) {
      for (const node of actions.querySelectorAll('button')) {
        node.disabled = busy;
      }
    },
    reset() {
      clear(notice);
      notice.hidden = true;
      notice.className = 'notice';
    },
    fail(message) {
      clear(notice);
      notice.className = 'notice notice--fail';
      notice.appendChild(el('p', 'notice__text', message));
      notice.hidden = false;
    },
    confirm(message) {
      clear(notice);
      notice.className = 'notice notice--confirm';
      notice.appendChild(el('p', 'notice__text', message));
      notice.appendChild(
        el(
          'p',
          'notice__text muted',
          'The statement stays archived. Fixing the parser and re-ingesting the kept '
            + 'bytes is the only route into the ledger.',
        ),
      );
      const row = el('div', 'notice__actions');
      row.appendChild(
        button('btn btn--danger', 'Dismiss anyway', () => {
          send({ action: 'dismiss', acknowledge_unbooked: true });
        }),
      );
      row.appendChild(button('btn btn--quiet', 'Keep it open', () => ui.reset()));
      notice.appendChild(row);
      notice.hidden = false;
    },
  };

  async function send(body) {
    ui.setBusy(true);
    ui.reset();
    try {
      const updated = await resolveReviewItem(item.id, body);
      onDone(updated);
    } catch (error) {
      ui.setBusy(false);
      // A 409 on an un-acknowledged block dismissal is the server asking the
      // question again, not a failure. Anything else — unknown id, already
      // closed — is final and gets shown as it arrived.
      const askAgain =
        error instanceof ApiError
        && error.status === 409
        && body.action === 'dismiss'
        && item.severity === 'block'
        && body.acknowledge_unbooked !== true;
      if (askAgain) {
        ui.confirm(error.message);
      } else {
        ui.fail(error.message || 'The item could not be updated.');
      }
    }
  }

  actions.appendChild(button('btn', 'Resolve', () => send({ action: 'resolve' })));
  actions.appendChild(
    button('btn btn--quiet', 'Dismiss', () => send({ action: 'dismiss' })),
  );
  wrap.appendChild(actions);
  wrap.appendChild(notice);
  return wrap;
}

function groupNode(title, items, onDone) {
  const group = el('section', 'group');
  group.appendChild(el('h3', 'group__title', `${title} — ${items.length}`));
  for (const item of items) {
    group.appendChild(reviewItemNode(item, { onDone }));
  }
  return group;
}

/**
 * The queue panel. `onChange` fires after any successful resolve so the health
 * strip can re-read its counts from the server rather than guess at them.
 */
//: The panel's own claim about what is in it, built here rather than in the
//: markup: "resolving never books a transaction" is this module's promise, and
//: a promise is easiest to keep beside the code that keeps it.
const PANEL_NOTE = 'Everything here was archived and not booked. Resolving records that a '
  + 'person looked at it; it never books a transaction. The way a refused statement gets into '
  + 'the ledger is to fix the parser and re-ingest the kept bytes.';

export function createReviewQueue(options) {
  const container = options.container;
  const panel = container.closest('.panel');
  const panelHead = panel ? panel.querySelector('.panel__head') : null;
  if (panelHead && panelHead.parentNode) {
    panelHead.parentNode.insertBefore(el('p', 'panel__note', PANEL_NOTE), panelHead.nextSibling);
  }
  const countsNode = options.countsNode;
  const onChange = options.onChange;

  function renderCounts(data) {
    if (!countsNode) {
      return;
    }
    clear(countsNode);
    if (!data) {
      return;
    }
    const blocking = data.open_block || 0;
    const warning = data.open_warn || 0;
    countsNode.appendChild(
      el('span', blocking > 0 ? 'count count--block' : 'count', `${blocking} blocking`),
    );
    countsNode.appendChild(
      el('span', warning > 0 ? 'count count--warn' : 'count', `${warning} warning`),
    );
  }

  function done() {
    refresh();
    if (onChange) {
      onChange();
    }
  }

  function render(data) {
    clear(container);
    const items = data.items || [];
    if (items.length === 0) {
      const empty = el('p', 'empty');
      empty.appendChild(el('strong', '', 'Nothing is waiting on you.'));
      empty.appendChild(
        el(
          'span',
          '',
          ' Every statement in the ledger passed its own printed totals. Anything that '
            + 'did not would be listed here, unbooked.',
        ),
      );
      container.appendChild(empty);
      return;
    }
    // Blocking first: an unbooked statement is a hole in the ledger, a warning
    // is a note about one that is already in it.
    const blocking = items.filter((item) => item.severity === 'block');
    const warning = items.filter((item) => item.severity !== 'block');
    if (blocking.length > 0) {
      container.appendChild(groupNode('Blocking — nothing was booked', blocking, done));
    }
    if (warning.length > 0) {
      container.appendChild(groupNode('Warnings', warning, done));
    }
  }

  async function refresh() {
    container.setAttribute('aria-busy', 'true');
    try {
      const data = await fetchReview({ status: 'open' });
      renderCounts(data);
      render(data);
    } catch (error) {
      renderCounts(null);
      clear(container);
      const offline = isOffline(error);
      container.appendChild(el(
        'p',
        offline ? 'empty' : 'empty empty--fail',
        offline ? CONNECTION_COPY.panel : (error.message || 'The queue could not be read.'),
      ));
    } finally {
      container.removeAttribute('aria-busy');
    }
  }

  return { refresh };
}
