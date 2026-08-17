// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Agent Center calls. Kept out of api.js so neither file grows past the split
// line, and so the one route that starts local work is easy to find: queueing a
// round is the whole of its durable effect, and no call here reaches a model.

import { request } from './api.js';

/** Current policy, per-client readiness, and the latest stretch of work. */
export function fetchAgentCenter() {
  return request('/api/agent-center');
}

/** Ask the selected local Agent for one more classification round. */
export function startClassificationRound() {
  return request('/api/agent-center/classify', { method: 'POST' });
}

export function replaceAgentPolicy(policy) {
  return request('/api/agent-center/policy', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(policy),
  });
}
