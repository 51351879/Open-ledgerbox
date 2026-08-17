// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The only module that speaks to the server, and the only place where a money
// value becomes a string. Every other module receives a parsed object or an
// ApiError and nothing else, so when a response shape changes there is exactly
// one file to read.
//
// The DOM helpers at the bottom live here for the same reason: they are what
// makes the never-assign-markup rule cheap to obey. A node is created, its text
// is assigned to textContent, and a payee string containing a tag stays a payee
// string containing a tag. Nothing in this page parses a string into nodes.

import { report } from './connection.js';

const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });

const MINOR_SUFFIX = '_minor';

/**
 * Money crosses the wire as an integer count of minor units, and every such
 * field says so in its name. The division by 100 below is the only float in
 * this page: it happens once, at the last moment, for display.
 *
 * `Intl` rather than string concatenation because concatenation produces
 * `$-12.44` for a debit — which is what the predecessor to this project shipped
 * — and `Intl` produces `-$12.44`.
 */
export function formatMinor(minor) {
  if (typeof minor !== 'number' || !Number.isFinite(minor)) {
    return String(minor);
  }
  return USD.format(minor / 100);
}

/** True for the wire fields whose values are integer minor units. */
export function isMinorField(key) {
  return typeof key === 'string' && key.endsWith(MINOR_SUFFIX);
}

/**
 * `ending_balance_minor` -> `Ending balance`. The `_minor` suffix is dropped
 * only because the value beside it has already been rendered as currency; on a
 * raw integer the suffix is the thing that tells you the units.
 */
export function humanizeKey(key) {
  const raw = String(key);
  const base = isMinorField(raw) ? raw.slice(0, -MINOR_SUFFIX.length) : raw;
  const words = base.replace(/[_-]+/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : raw;
}

/** A failed request. `status` is 0 when the server did not answer at all. */
export class ApiError extends Error {
  constructor(status, message, payload) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

/**
 * True for the one failure that means the process is not there.
 *
 * Every other `ApiError` carries a status the *server* chose, so the server is
 * running and that panel has something specific of its own to report. Only a
 * transport failure — which on loopback is not a flaky network — says the
 * process has stopped.
 *
 * It lives here rather than in `connection.js` because it is a question about
 * an `ApiError`, and this module owns that class. `connection.js` imports
 * nothing; that is what keeps the two files that everything else depends on
 * from depending on each other.
 */
export function isOffline(error) {
  return error instanceof ApiError && error.status === 0;
}

// FastAPI reports a failure as {"detail": ...}: a string for a raised
// HTTPException, a list of objects for a request-validation error. Both have to
// arrive in front of the operator as one readable sentence, because the 409
// re-confirm flow shows the server's own wording rather than inventing its own.
function detailToMessage(payload, status) {
  const detail = payload ? payload.detail : null;
  if (typeof detail === 'string' && detail.length > 0) {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    return detail
      .map((entry) => (entry && typeof entry.msg === 'string' ? entry.msg : String(entry)))
      .join('; ');
  }
  return `The server answered ${status} without explaining why.`;
}

export async function request(path, init) {
  let response;
  try {
    response = await fetch(path, init);
  } catch (cause) {
    // This server is on loopback. A transport failure here is not a flaky
    // network, it is the process having stopped.
    //
    // Reported once, here, because this is the only function in the page that
    // issues a request — so the connection indicator cannot miss one, and no
    // panel has to remember to tell it anything. A status the server chose is
    // an answer and counts as connected, however unwelcome it is.
    report(false);
    throw new ApiError(0, 'No answer from ledgerbox. Is the server still running?', null);
  }
  report(true);

  let payload = null;
  try {
    payload = await response.json();
  } catch (cause) {
    payload = null;
  }

  if (!response.ok) {
    throw new ApiError(response.status, detailToMessage(payload, response.status), payload);
  }
  return payload;
}

/**
 * One file, one request. Resolves for every pipeline outcome including
 * `needs_review` and `failed` — those are 200s carrying a refusal, not errors.
 * Rejects only for 400 / 413 / 415 and for a server that is not there.
 */
export function uploadStatement(file) {
  const body = new FormData();
  // The field name is fixed by the interface contract; the filename is passed
  // through as data only and is never used to build a path.
  body.append('file', file, file.name);
  return request('/api/upload', { method: 'POST', body });
}

export function fetchReview(options) {
  const settings = options || {};
  const params = new URLSearchParams();
  params.set('status', settings.status || 'open');
  if (settings.severity) {
    params.set('severity', settings.severity);
  }
  return request(`/api/review?${params.toString()}`);
}

/** Records that a person looked. It books nothing, by design. */
export function resolveReviewItem(itemId, body) {
  return request(`/api/review/${encodeURIComponent(itemId)}/resolve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export function fetchHealth() {
  return request('/api/health');
}
// The Agent Center calls live in api-agent.js, which imports request from here.

/** Every archived statement, newest period first. */
export function fetchStatements() {
  return request('/api/statements');
}

/**
 * What deleting one statement *would* do. Writes nothing: the server performs
 * the deletion inside a transaction, runs the checks against the result and
 * rolls back, so `checks_after` is measured rather than predicted.
 *
 * A refusal arrives two ways and both have to be handled — `allowed: false` on
 * a 200, or a 422 — because the second is what the delete route answers with
 * and this page must not offer a confirm button for either.
 */
export function fetchDeletionPlan(sourceFileId) {
  return request(`/api/statements/${encodeURIComponent(sourceFileId)}/deletion-plan`, {
    method: 'POST',
  });
}

/**
 * The write. The acknowledgement is in the URL rather than implied by the verb:
 * a DELETE without it answers 409, which is the server asking the question
 * again, and the page answers by showing the plan again rather than retrying.
 */
export function deleteStatement(sourceFileId) {
  const params = new URLSearchParams({ acknowledge_impact: 'true' });
  return request(
    `/api/statements/${encodeURIComponent(sourceFileId)}?${params.toString()}`,
    { method: 'DELETE' },
  );
}

/**
 * One page of transactions, plus what the whole filter matched.
 *
 * Every control on the transactions panel issues one of these. Filtering,
 * sorting and paging are the database's job and stay there: a browser that
 * holds the ledger in order to slice it grows a second definition of every
 * question it slices by, which is how the predecessor ended up with a table
 * that disagreed with its own chart.
 *
 * Absent and empty values are left off the query string rather than sent
 * blank, so the URL in the network log is the filter a person actually chose.
 */
export function fetchTransactions(query) {
  const settings = query || {};
  const params = new URLSearchParams();
  for (const key of ['q', 'month', 'category', 'direction', 'since', 'until']) {
    if (settings[key]) {
      params.set(key, settings[key]);
    }
  }
  // Only a real boolean filters; the panel's "either" is the absence of the
  // parameter, not `transfer=false`, which means something else entirely.
  if (typeof settings.transfer === 'boolean') {
    params.set('transfer', String(settings.transfer));
  }
  params.set('sort', settings.sort || 'date');
  params.set('descending', settings.descending === false ? 'false' : 'true');
  params.set('limit', String(settings.limit || 50));
  params.set('offset', String(settings.offset || 0));
  return request(`/api/transactions?${params.toString()}`);
}

/** The taxonomy the ledger actually uses, mirrored from the shipped rules file. */
export function fetchCategories() {
  return request('/api/categories');
}

/**
 * Two re-readings of the same booked lines: In and Out per transaction month,
 * and what was spent per category.
 *
 * The grouping is the database's job and stays there. A page that fetched the
 * transactions and pivoted them itself would hold a second definition of every
 * figure it pivoted — which is the shape that let the predecessor ship a table
 * disagreeing with its own chart.
 *
 * Two things in the reply are load-bearing for the caller. A month's key is
 * `month`, keyed on the transaction date, and is *not* the `statement_month`
 * the table's filter and `/api/statements` mean; the field was renamed when the
 * meaning changed and this page labels both wherever they show. And
 * `categories.total_minor` is the same figure as the "Out" at the top of the
 * page, so the breakdown ties back to something already on screen instead of
 * being a fifth measurement.
 *
 * `span` is the page-wide window: `since` and `until` on the transaction date,
 * absent rather than blank when unset so the URL in the network log is the
 * window a person actually chose. It narrows the totals, the months and the
 * breakdown together, and `/api/transactions` is sent the same one in the same
 * gesture — a range that moved some of them would leave the table's figures and
 * the page's four describing different rows, and the page states a relationship
 * between them.
 */
export function fetchAnalytics(span) {
  const window = span || {};
  const params = new URLSearchParams();
  // Absent rather than blank, so the URL in the network log is the window a
  // person actually chose. `since=` and no `since` at all mean the same thing
  // to the server and different things to somebody reading the log.
  for (const key of ['since', 'until']) {
    if (window[key]) {
      params.set(key, window[key]);
    }
  }
  const query = params.toString();
  return request(query ? `/api/analytics?${query}` : '/api/analytics');
}

/**
 * Record what a person says one transaction's category is.
 *
 * `categoryId` of `null` withdraws the decision and lets the rules answer
 * again. The field is sent either way because the server requires it either
 * way: an empty body must not be able to discard somebody's correction by
 * accident. There is no confirmation step and none is wanted — unlike a
 * deletion, this is reversible by sending `null`.
 */
/** Large lines whose current category no person has directly confirmed. */
export function fetchLargeFlows() {
  return request('/api/large-flows');
}
export function updateTransactionCategory(txnId, categoryId) {
  return request(`/api/transactions/${encodeURIComponent(txnId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ category_id: categoryId === undefined ? null : categoryId }),
  });
}

/**
 * Record one decision about many transactions, naming every one of them.
 *
 * The ids are explicit and there is no "apply to whatever my filter matches"
 * form. A filter is a query and the set it matches can change between the
 * moment somebody reads a count off the screen and the moment a write lands; a
 * list is a set they saw and counted, and it cannot quietly grow.
 *
 * `categoryId` of `null` withdraws all of those decisions and lets the rules
 * answer again, exactly as it does for one row. One unknown id refuses the
 * whole request with a 404 and writes nothing — the caller is holding a stale
 * list, and the answer to that is to read again.
 */
export function updateManyCategories(txnIds, categoryId) {
  return request('/api/transactions/category', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      txn_ids: txnIds,
      category_id: categoryId === undefined ? null : categoryId,
    }),
  });
}

/** Newest local Agent proposal audit runs; this never invokes an Agent. */
export function fetchProposalRuns(limit = 50) {
  const params = new URLSearchParams({ limit: String(limit) });
  return request(`/api/agent-proposals?${params.toString()}`);
}

/** One audit run plus each proposal row's facts re-read from the current ledger. */
export function fetchProposalRun(runId) {
  return request(`/api/agent-proposals/${encodeURIComponent(runId)}`);
}

/** Accept/edit or reject an explicit subset of one proposal run. */
export function reviewProposalRun(runId, body) {
  return request(`/api/agent-proposals/${encodeURIComponent(runId)}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** Compare-and-clear the still-matching category decisions applied by one run. */
export function withdrawProposalRun(runId) {
  return request(`/api/agent-proposals/${encodeURIComponent(runId)}/withdraw`, {
    method: 'POST',
  });
}

// --- DOM ---

/** Create an element. `text` is assigned to textContent, never parsed. */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined && text !== null) {
    node.textContent = String(text);
  }
  return node;
}

/**
 * Append parts to `node`; a string becomes a text node and never markup. Lives
 * beside `el` because it is the same rule wearing a different hat: text goes in
 * as text, always, whoever wrote it.
 */
export function join(node, ...parts) {
  for (const part of parts) {
    node.appendChild(typeof part === 'string' ? document.createTextNode(part) : part);
  }
  return node;
}

/** Empty a node by removing its children, not by assigning markup to it. */
export function clear(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

/** A `<button type="button">`; the default `submit` has no form to submit. */
export function button(className, label, onClick) {
  const node = el('button', className, label);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
}

/**
 * An `<option>`. Beside `button` for the same reason `el` and `join` sit
 * together: a category id and a statement month are data, and they enter the
 * document as text on a property, never as a string somebody assembled.
 */
export function option(value, label) {
  const node = el('option', '', label === undefined ? value : label);
  node.value = value;
  return node;
}
