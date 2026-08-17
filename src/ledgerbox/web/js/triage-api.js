// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The small HTTP seam for the independent remaining-coverage triage workflow.

import { request } from './api.js';

/** Newest exhaustive remaining-coverage triage runs; this never invokes an Agent. */
export function fetchTriageRuns(limit = 50) {
  const params = new URLSearchParams({ limit: String(limit) });
  return request(`/api/agent-triage?${params.toString()}`);
}

/** One triage run with current ledger rows and server-derived route impacts. */
export function fetchTriageRun(runId) {
  return request(`/api/agent-triage/${encodeURIComponent(runId)}`);
}

/** Apply one explicit human decision to checked triage rows. */
export function reviewTriageRun(runId, body) {
  return request(`/api/agent-triage/${encodeURIComponent(runId)}/review`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** Explicitly leave every remaining item unclassified and dismiss the run. */
export function dismissTriageRun(runId) {
  return request(`/api/agent-triage/${encodeURIComponent(runId)}/dismiss`, {
    method: 'POST',
  });
}

/** Compare-and-clear still-matching categories applied while reviewing this run. */
export function withdrawTriageRun(runId) {
  return request(`/api/agent-triage/${encodeURIComponent(runId)}/withdraw`, {
    method: 'POST',
  });
}
