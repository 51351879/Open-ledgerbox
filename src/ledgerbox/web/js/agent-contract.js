// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Agent Center wire contract. A response that omits either separate Skill fact,
// names an unknown client, or offers setup steps that could still register MCP
// after a failed install is rejected here instead of rendered as something safer.

export const LABELS = { codex: 'Codex', 'claude-code': 'Claude Code' };
const PERSONAL_SKILL_STATES = new Set(['missing', 'current', 'outdated', 'custom']);

// One pasted line cannot be split by a console, so registration must stay inside the
// success branch of the install guard. Anything else could register after a failure.
function guardsRegistration(command) {
  if (typeof command !== 'string' || command.includes('\n') || /--force|--yes/.test(command)) {
    return false;
  }
  const install = command.indexOf('agent install-skill');
  return install >= 0 && command.indexOf('if ($?) { ') > install;
}

export const JOB_STATES = new Set(['queued', 'running', 'completed', 'partial', 'failed']);
export const IN_FLIGHT = new Set(['queued', 'running']);

// A batch is the whole stretch of work. Rendering the newest round alone is how
// a run that classified 152 of 270 came to be shown as "2 submitted", so a
// payload that cannot describe the stretch is refused rather than summarised.
function validBatch(batch) {
  if (batch === null || batch === undefined) return true;
  if (!JOB_STATES.has(batch.state) || !Number.isInteger(batch.job_count) || batch.job_count < 1) {
    return false;
  }
  if (!Number.isInteger(batch.submitted_count) || !Number.isInteger(batch.applied_count)) {
    return false;
  }
  // Nothing is left over until the stretch stops moving, and once it stops the
  // leftover is a number. Neither may be guessed from the other.
  return IN_FLIGHT.has(batch.state)
    ? batch.omitted_count === null && batch.finished_at === null
    : Number.isInteger(batch.omitted_count);
}

export function validatedCenter(payload) {
  if (!payload || payload.schema_version !== 3 || !Array.isArray(payload.clients)) {
    throw new Error('Unsupported Agent Center response.');
  }
  if (!Object.hasOwn(payload, 'latest_batch') || !validBatch(payload.latest_batch)) {
    throw new Error('Unsupported Agent classification summary.');
  }
  const seen = new Set();
  for (const item of payload.clients) {
    if (!item || !LABELS[item.client] || seen.has(item.client)) {
      throw new Error('Unsupported Agent client in Agent Center response.');
    }
    if (typeof item.runner_skill_compatible !== 'boolean'
        || !PERSONAL_SKILL_STATES.has(item.personal_skill_state)
        || Object.hasOwn(item, 'skill_compatible')) {
      throw new Error('Unsupported Agent Skill status response.');
    }
    if (!guardsRegistration(payload.setup_commands?.[item.client])) {
      throw new Error('Unsafe or missing Agent setup steps.');
    }
    seen.add(item.client);
  }
  if (seen.size !== Object.keys(LABELS).length
      || (payload.policy?.selected_client !== null
        && !LABELS[payload.policy?.selected_client])) {
    throw new Error('Incomplete Agent Center response.');
  }
  return payload;
}
