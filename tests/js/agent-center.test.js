// SPDX-License-Identifier: AGPL-3.0-or-later

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { createAgentSidebar } from '../../src/ledgerbox/web/js/agent-center.js';

class FakeElement {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = new Map();
    this.className = '';
    this.style = {};
    this.textContent = '';
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = '';
    this.type = '';
    this.open = false;
  }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    return child;
  }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
}

function installDocument() {
  const previous = globalThis.document;
  globalThis.document = {
    createElement: (tag) => new FakeElement(tag),
    createTextNode: (text) => ({ textContent: String(text) }),
  };
  return () => { globalThis.document = previous; };
}

function center(overrides = {}) {
  return {
    schema_version: 3,
    ledgerbox: {
      ready_for_proposals: true,
      passed_checks: 9,
      total_checks: 9,
      proposal_schema_version: 2,
      uncategorized_count: 3,
      pending_review_count: 2,
      pending_triage_count: 1,
      open_review_count: 4,
      ledger_label: 'Synthetic-Isolated',
      data_dir: 'D:\\Ledgerbox-A73-Acceptance\\Synthetic-Isolated',
    },
    policy: {
      selected_client: 'codex',
      application_mode: 'automatic',
      enabled: true,
      auto_classify_new_imports: true,
    },
    clients: [
      {
        client: 'codex', installed: true, runner_skill_compatible: true,
        personal_skill_state: 'current',
        mcp_bridge_available: true, mcp_session: 'active', session_active: true,
        last_seen_at: null, last_result: 'partial', result_at: null,
        candidate_count: 5, submitted_count: 3, error_code: null,
      },
      {
        client: 'claude-code', installed: false, runner_skill_compatible: true,
        personal_skill_state: 'missing',
        mcp_bridge_available: true, mcp_session: 'not_seen', session_active: false,
        last_seen_at: null, last_result: null, result_at: null,
        candidate_count: null, submitted_count: null, error_code: null,
      },
    ],
    provider_disclosure: 'The selected client may send facts to its configured provider.',
    run_prompts: { codex: 'Use $ledgerbox.', 'claude-code': '/ledgerbox classify' },
    setup_commands: {
      codex: "& 'ledgerbox' agent install-skill --client codex; if ($?) { codex mcp add ledgerbox -- 'ledgerbox-mcp' --client codex --data-dir 'D:\\safe' } else { Write-Error 'stop' }",
      'claude-code': "& 'ledgerbox' agent install-skill --client claude-code; if ($?) { claude mcp add --scope local ledgerbox 'ledgerbox-mcp' -e LEDGERBOX_MCP_CLIENT=claude-code -e 'LEDGERBOX_DATA_DIR=D:\\safe' } else { Write-Error 'stop' }",
    },
    setup_guide: 'docs/AGENT_SETUP.md',
    latest_batch: {
      job_count: 1, state: 'partial', candidate_count: 5,
      submitted_count: 3, applied_count: 3, omitted_count: 2,
      error_code: null, client_outcome: 'exited', rounds_capped: false,
      failed_rounds: 0, max_rounds: 25,
      queued_at: '2026-08-10T12:00:01+00:00', started_at: '2026-08-10T12:00:02+00:00',
      finished_at: '2026-08-10T12:00:03+00:00',
    },
    latest_job: {
      client: 'codex', application_mode: 'automatic', state: 'partial',
      candidate_count: 5, submitted_count: 3, applied_count: 3, omitted_count: 2,
      error_code: null, client_outcome: 'exited', client_exit_code: 0,
      queued_at: null, started_at: null, finished_at: null,
    },
    ...overrides,
  };
}

test('sidebar names the current ledger and only an active MCP session is green', async () => {
  const restore = installDocument();
  try {
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => center(),
        updatePolicy: async (body) => body,
        copyText: async () => {},
      },
    });
    await sidebar.refresh();
    assert.equal(sidebar.nodes.agentState.textContent, 'Codex MCP connected');
    assert.equal(sidebar.nodes.agentState.className, 'agent-status agent-status--up');
    assert.equal(sidebar.nodes.ledgerName.textContent, 'Synthetic-Isolated');
    assert.equal(sidebar.nodes.ledgerPath.textContent, 'D:\\Ledgerbox-A73-Acceptance\\Synthetic-Isolated');

    const disconnected = center({
      clients: center().clients.map((item) => ({ ...item, session_active: false, mcp_session: 'seen_before' })),
    });
    sidebar.services.fetchCenter = async () => disconnected;
    await sidebar.refresh();
    assert.equal(sidebar.nodes.agentState.textContent, 'No Agent MCP connected');
    assert.equal(sidebar.nodes.agentState.className, 'agent-status agent-status--down');
  } finally {
    restore();
  }
});

test('sidebar directory exposes only actionable badge counts', async () => {
  const restore = installDocument();
  try {
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => center(),
        updatePolicy: async (body) => body,
        copyText: async () => {},
      },
    });
    await sidebar.refresh();
    assert.equal(sidebar.nodes.badges.proposals.textContent, '2');
    assert.equal(sidebar.nodes.badges.needs.textContent, '2');
    assert.equal(sidebar.nodes.badges.triage.textContent, '1');
    assert.equal(sidebar.nodes.badges.review.textContent, '4');
    assert.equal(sidebar.nodes.badges.proposals.hidden, false);
    assert.equal(sidebar.nodes.badges.review.attributes.get('aria-label'), '4 pending');
  } finally {
    restore();
  }
});

test('a multi-round stretch is reported as its whole self, not as its last round', async () => {
  // The shape of a real run: thirteen jobs, 270 candidates, 152 classified. The
  // sidebar reported the newest job alone and said "2 submitted", which read as
  // a failed import and got a working run thrown away.
  const restore = installDocument();
  try {
    const payload = center({
      latest_batch: {
        job_count: 13, state: 'partial', candidate_count: 270,
        submitted_count: 152, applied_count: 152, omitted_count: 118,
        error_code: null, client_outcome: 'exited', rounds_capped: false,
        failed_rounds: 0, max_rounds: 25,
        queued_at: '2026-08-11T03:11:22+00:00', started_at: '2026-08-11T03:11:22+00:00',
        finished_at: '2026-08-11T03:26:33+00:00',
      },
      latest_job: {
        client: 'codex', application_mode: 'automatic', state: 'partial',
        candidate_count: 120, submitted_count: 2, applied_count: 2, omitted_count: 118,
        error_code: null, client_outcome: 'exited', client_exit_code: 0,
        queued_at: null, started_at: null, finished_at: null,
      },
    });
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => payload,
        updatePolicy: async (body) => body,
        copyText: async () => {},
      },
    });
    await sidebar.refresh();
    sidebar.stop();

    assert.match(sidebar.nodes.jobSummary.textContent, /270 candidates/);
    assert.match(sidebar.nodes.jobSummary.textContent, /152 submitted/);
    assert.match(sidebar.nodes.jobSummary.textContent, /152 applied/);
    assert.match(sidebar.nodes.jobSummary.textContent, /118 omitted/);
    assert.doesNotMatch(sidebar.nodes.jobSummary.textContent, /\b2 submitted\b/);
    assert.match(sidebar.nodes.jobSummary.textContent, /^Finished/);
    assert.match(sidebar.nodes.jobProgress.textContent, /13 rounds/);
    assert.match(sidebar.nodes.jobDetail.textContent, /Ended 2026-08-11T03:26:33/);
    assert.equal(sidebar.nodes.needsLink.textContent, 'Needs classification: 118');
  } finally {
    restore();
  }
});

test('a stretch in flight says so and the panel follows it until it stops', async () => {
  const restore = installDocument();
  try {
    const timers = [];
    const running = center({
      latest_batch: {
        job_count: 3, state: 'running', candidate_count: 270,
        submitted_count: 96, applied_count: 96, omitted_count: null,
        error_code: null, client_outcome: null, rounds_capped: false,
        failed_rounds: 0, max_rounds: 25,
        queued_at: '2026-08-11T03:11:22+00:00', started_at: '2026-08-11T03:11:22+00:00',
        finished_at: null,
      },
    });
    const done = center({
      latest_batch: { ...running.latest_batch, state: 'partial', omitted_count: 118,
        finished_at: '2026-08-11T03:26:33+00:00' },
    });
    let payload = running;
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => payload,
        updatePolicy: async (body) => body,
        copyText: async () => {},
        setTimer: (fn) => { timers.push(fn); return timers.length; },
        clearTimer: () => {},
      },
    });

    await sidebar.refresh();
    assert.match(sidebar.nodes.jobSummary.textContent, /Classifying now/);
    assert.match(sidebar.nodes.jobSummary.textContent, /96 submitted so far/);
    assert.equal(sidebar.nodes.needsLink.hidden, true, 'a moving number is not a leftover count');
    assert.equal(sidebar.nodes.classifyNow.disabled, true);
    assert.equal(timers.length, 1, 'a moving stretch is watched');

    payload = done;
    await timers[timers.length - 1]();
    assert.match(sidebar.nodes.jobSummary.textContent, /^Finished/);
    assert.equal(sidebar.nodes.needsLink.textContent, 'Needs classification: 118');
    assert.equal(timers.length, 1, 'a finished stretch is not polled again');
    assert.equal(sidebar.nodes.classifyNow.disabled, false);
  } finally {
    restore();
  }
});

test('a moving stretch shows how far along it is, how long it has run, and a named ceiling', async () => {
  const restore = installDocument();
  try {
    const payload = center({
      latest_batch: {
        job_count: 5, state: 'running', candidate_count: 118,
        submitted_count: 18, applied_count: 18, omitted_count: null,
        error_code: null, client_outcome: null, rounds_capped: false,
        failed_rounds: 0, max_rounds: 25,
        queued_at: '2026-08-11T23:44:01+00:00', started_at: '2026-08-11T23:44:01+00:00',
        finished_at: null,
      },
    });
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => payload,
        updatePolicy: async (body) => body,
        copyText: async () => {},
        setTimer: () => 1,
        clearTimer: () => {},
        // Five minutes into the run, so elapsed and rate are real numbers here.
        now: () => Date.parse('2026-08-11T23:49:01+00:00'),
      },
    });
    await sidebar.refresh();
    sidebar.stop();

    assert.equal(sidebar.nodes.jobMeter.hidden, false);
    assert.equal(sidebar.nodes.jobMeter.attributes.get('aria-valuenow'), '15');
    assert.match(
      sidebar.nodes.jobMeter.attributes.get('aria-label'),
      /18 of 118 candidates classified/,
    );
    assert.match(sidebar.nodes.jobProgress.textContent, /Round 5 of at most 25/);
    assert.match(sidebar.nodes.jobProgress.textContent, /running 5 min/);
    // 5 min over 5 rounds is a minute a round, and 20 rounds may remain.
    assert.match(sidebar.nodes.jobProgress.textContent, /up to 20 min left/);
    assert.match(sidebar.nodes.jobProgress.textContent, /if it uses every round/,
      'the remaining time is a ceiling, and has to read as one');
  } finally {
    restore();
  }
});

test('a stretch that classified is not called failed because its last round was empty', async () => {
  const restore = installDocument();
  try {
    const payload = center({
      latest_batch: {
        job_count: 5, state: 'partial', candidate_count: 118,
        submitted_count: 18, applied_count: 18, omitted_count: 100,
        error_code: null, client_outcome: 'exited', rounds_capped: false,
        failed_rounds: 1, max_rounds: 25,
        queued_at: '2026-08-11T23:44:01+00:00', started_at: '2026-08-11T23:44:01+00:00',
        finished_at: '2026-08-11T23:48:54+00:00',
      },
    });
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => payload,
        updatePolicy: async (body) => body,
        copyText: async () => {},
      },
    });
    await sidebar.refresh();
    sidebar.stop();

    assert.doesNotMatch(sidebar.nodes.jobSummary.textContent, /failed/i);
    assert.match(sidebar.nodes.jobSummary.textContent, /18 submitted/);
    assert.match(sidebar.nodes.jobProgress.textContent, /5 rounds in 5 min/);
    assert.match(sidebar.nodes.jobProgress.textContent, /1 of them returned nothing/);
  } finally {
    restore();
  }
});

test('Classify now asks for one round and never starts a model from the page', async () => {
  const restore = installDocument();
  try {
    let asked = 0;
    const queued = center({
      latest_batch: {
        job_count: 1, state: 'queued', candidate_count: null,
        submitted_count: 0, applied_count: 0, omitted_count: null,
        error_code: null, client_outcome: null, rounds_capped: false,
        failed_rounds: 0, max_rounds: 25,
        queued_at: '2026-08-11T21:00:00+00:00', started_at: null, finished_at: null,
      },
    });
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => center(),
        updatePolicy: async (body) => body,
        copyText: async () => {},
        startRound: async () => { asked += 1; return queued; },
        setTimer: () => 1,
        clearTimer: () => {},
      },
    });
    await sidebar.refresh();
    assert.equal(sidebar.nodes.classifyNow.disabled, false);

    await sidebar.nodes.classifyNow.listeners.get('click')();

    assert.equal(asked, 1);
    assert.match(sidebar.nodes.jobSummary.textContent, /Classification queued/);
    assert.match(sidebar.nodes.status.textContent, /queued/i);
    assert.equal(sidebar.nodes.classifyNow.disabled, true, 'one round at a time');
  } finally {
    restore();
  }
});

test('a refused round is reported and leaves the panel readable', async () => {
  const restore = installDocument();
  try {
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => center(),
        updatePolicy: async (body) => body,
        copyText: async () => {},
        startRound: async () => { throw new Error('Enable a local Agent first.'); },
        setTimer: () => 1,
        clearTimer: () => {},
      },
    });
    await sidebar.refresh();

    await sidebar.nodes.classifyNow.listeners.get('click')();

    assert.match(sidebar.nodes.status.textContent, /Enable a local Agent first/);
    assert.equal(sidebar.nodes.classifyNow.disabled, false, 'a refusal is not a lock-out');
  } finally {
    restore();
  }
});

test('a disconnected policy cannot ask for a round', async () => {
  const restore = installDocument();
  try {
    const payload = center({
      policy: {
        selected_client: null, application_mode: 'automatic',
        enabled: false, auto_classify_new_imports: false,
      },
    });
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => payload,
        updatePolicy: async (body) => body,
        copyText: async () => {},
      },
    });
    await sidebar.refresh();
    sidebar.stop();

    assert.equal(sidebar.nodes.classifyNow.disabled, true);
  } finally {
    restore();
  }
});

test('latest job separates counts and hands omissions to Transactions', async () => {
  const restore = installDocument();
  try {
    let handedOff = 0;
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      onNeedsClassification: () => { handedOff += 1; },
      services: {
        fetchCenter: async () => center(),
        updatePolicy: async (body) => body,
        copyText: async () => {},
      },
    });
    await sidebar.refresh();
    assert.match(sidebar.nodes.jobSummary.textContent, /5 candidates.*3 submitted.*3 applied.*2 omitted/i);
    assert.equal(sidebar.nodes.needsLink.textContent, 'Needs classification: 2');
    assert.equal(sidebar.nodes.needsLink.hidden, false);
    let prevented = false;
    sidebar.nodes.needsLink.listeners.get('click')({ preventDefault: () => { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(handedOff, 1);
  } finally {
    restore();
  }
});

test('polling never snatches the client choice back to the stored policy', async () => {
  const restore = installDocument();
  try {
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => center(),
        updatePolicy: async (body) => body,
        copyText: async () => {},
      },
    });
    await sidebar.refresh();
    assert.equal(sidebar.nodes.setupClient.value, 'codex');
    sidebar.nodes.setupClient.value = 'claude-code';
    sidebar.nodes.setupClient.listeners.get('change')();
    // The 4-second poll re-renders with the same stored policy. The person is
    // mid-switch; the page must not reach over and put the dropdown back.
    await sidebar.refresh();
    assert.equal(sidebar.nodes.setupClient.value, 'claude-code');
    assert.match(sidebar.nodes.personalSkillState.textContent, /missing/i);

    // A real policy change on the server is a different fact: follow it.
    const switched = center({
      policy: { ...center().policy, selected_client: 'claude-code' },
    });
    sidebar.services.fetchCenter = async () => switched;
    sidebar.nodes.setupClient.value = 'codex';
    await sidebar.refresh();
    assert.equal(sidebar.nodes.setupClient.value, 'claude-code');
  } finally {
    restore();
  }
});

test('an all-abstention batch is a finished examination, not a failure', async () => {
  const restore = installDocument();
  try {
    const payload = center({
      latest_batch: {
        ...center().latest_batch,
        state: 'partial',
        job_count: 1,
        candidate_count: 98,
        submitted_count: 0,
        applied_count: 0,
        omitted_count: 98,
        error_code: null,
        client_outcome: 'exited',
      },
    });
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => payload,
        updatePolicy: async (body) => body,
        copyText: async () => {},
      },
    });
    await sidebar.refresh();
    assert.match(sidebar.nodes.jobSummary.textContent, /98 candidates.*0 submitted.*98 omitted/i);
    assert.doesNotMatch(sidebar.nodes.jobSummary.textContent, /failed/i);
    assert.match(sidebar.nodes.jobDetail.textContent, /declined them all/i);
    assert.match(sidebar.nodes.jobDetail.textContent, /need a person/i);
  } finally {
    restore();
  }
});

test('setup copies non-forcing install before registration and never launches an Agent', async () => {
  const restore = installDocument();
  try {
    const copied = [];
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => center(),
        updatePolicy: async (body) => body,
        copyText: async (text) => { copied.push(text); },
      },
    });
    await sidebar.refresh();
    await sidebar.nodes.copySetup.listeners.get('click')();
    await sidebar.nodes.copyRun.listeners.get('click')();
    assert.deepEqual(copied, [center().setup_commands.codex, 'Use $ledgerbox.']);
    assert.ok(copied[0].indexOf('install-skill') < copied[0].indexOf('codex mcp add'));
    assert.match(copied[0], /if \(\$\?\) \{ /);
    assert.doesNotMatch(copied[0], /\n/);
    assert.doesNotMatch(copied[0], /--force|--yes/);
    assert.match(sidebar.nodes.status.textContent, /prompt copied/i);
    assert.match(sidebar.nodes.tutorial.textContent, /install.*then.*register/i);
    assert.match(sidebar.nodes.guidePath.textContent, /docs\/AGENT_SETUP\.md/);
  } finally {
    restore();
  }
});

test('runner compatibility never claims that a missing personal Skill is installed', async () => {
  const restore = installDocument();
  try {
    const payload = center({
      clients: center().clients.map((item) => item.client === 'codex'
        ? { ...item, runner_skill_compatible: true, personal_skill_state: 'missing' }
        : item),
    });
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => payload,
        updatePolicy: async (body) => body,
        copyText: async () => {},
      },
    });
    await sidebar.refresh();
    assert.match(sidebar.nodes.runnerSkillState.textContent, /runner Skill compatible/i);
    assert.match(sidebar.nodes.personalSkillState.textContent, /personal Skill missing/i);
    assert.doesNotMatch(sidebar.nodes.personalSkillState.textContent, /installed|current/i);
  } finally {
    restore();
  }
});

test('both clients render every personal Skill state as its own fact', async () => {
  const restore = installDocument();
  try {
    for (const client of ['codex', 'claude-code']) {
      for (const state of ['missing', 'current', 'outdated', 'custom']) {
        const payload = center({
          clients: center().clients.map((item) => item.client === client
            ? { ...item, personal_skill_state: state }
            : item),
        });
        const sidebar = createAgentSidebar({
          root: new FakeElement('aside'),
          services: {
            fetchCenter: async () => payload,
            updatePolicy: async (body) => body,
            copyText: async () => {},
          },
        });
        await sidebar.refresh();
        sidebar.nodes.setupClient.value = client;
        sidebar.nodes.setupClient.listeners.get('change')();
        assert.match(sidebar.nodes.personalSkillState.textContent, new RegExp(state, 'i'));
        assert.equal(sidebar.nodes.copySetup.disabled, state === 'custom');
      }
    }
  } finally {
    restore();
  }
});

test('custom personal Skill stops setup and points to doctor without force flags', async () => {
  const restore = installDocument();
  try {
    const copied = [];
    const payload = center({
      clients: center().clients.map((item) => item.client === 'codex'
        ? { ...item, personal_skill_state: 'custom' }
        : item),
    });
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => payload,
        updatePolicy: async (body) => body,
        copyText: async (text) => { copied.push(text); },
      },
    });
    await sidebar.refresh();
    assert.equal(sidebar.nodes.copySetup.disabled, true);
    assert.match(sidebar.nodes.personalSkillState.textContent, /custom/i);
    assert.match(sidebar.nodes.personalSkillState.textContent, /ledgerbox agent doctor --client codex/i);
    assert.doesNotMatch(sidebar.nodes.personalSkillState.textContent, /--force|--yes/);
    await sidebar.nodes.copySetup.listeners.get('click')();
    assert.deepEqual(copied, []);
  } finally {
    restore();
  }
});

test('clipboard rejection and a missing Clipboard API are visible failures', async () => {
  const restore = installDocument();
  const previousNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  try {
    const rejected = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => center(),
        updatePolicy: async (body) => body,
        copyText: async () => { throw new Error('Synthetic clipboard rejection.'); },
      },
    });
    await rejected.refresh();
    await rejected.nodes.copySetup.listeners.get('click')();
    assert.match(rejected.nodes.status.textContent, /synthetic clipboard rejection/i);

    Object.defineProperty(globalThis, 'navigator', {
      value: undefined,
      configurable: true,
      writable: true,
    });
    const missing = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => center(),
        updatePolicy: async (body) => body,
      },
    });
    await missing.refresh();
    await missing.nodes.copySetup.listeners.get('click')();
    assert.match(missing.nodes.status.textContent, /clipboard access is unavailable/i);
  } finally {
    if (previousNavigator) {
      Object.defineProperty(globalThis, 'navigator', previousNavigator);
    } else {
      delete globalThis.navigator;
    }
    restore();
  }
});

test('legacy payloads and unknown clients fail closed before setup can be copied', async () => {
  const restore = installDocument();
  try {
    for (const payload of [
      {
        ...center(),
        schema_version: 1,
        clients: center().clients.map((item) => ({
          ...item,
          skill_compatible: item.runner_skill_compatible,
          runner_skill_compatible: undefined,
          personal_skill_state: undefined,
        })),
      },
      { ...center(), clients: [{ ...center().clients[0], client: 'unknown-client' }] },
    ]) {
      const copied = [];
      const sidebar = createAgentSidebar({
        root: new FakeElement('aside'),
        services: {
          fetchCenter: async () => payload,
          updatePolicy: async (body) => body,
          copyText: async (text) => { copied.push(text); },
        },
      });
      await sidebar.refresh();
      assert.equal(sidebar.nodes.agentState.textContent, 'Agent status unavailable');
      assert.equal(sidebar.nodes.copySetup.disabled, true);
      await sidebar.nodes.copySetup.listeners.get('click')();
      assert.deepEqual(copied, []);
    }
  } finally {
    restore();
  }
});

test('setup steps that could register after a failed install fail closed', async () => {
  const restore = installDocument();
  try {
    // The first shape survives a failed install because a console runs each pasted
    // line separately; the second never checks the install at all.
    for (const unsafe of [
      'ledgerbox agent install-skill --client codex\nif (-not $?) { throw "stop" }\ncodex mcp add ledgerbox',
      "& 'ledgerbox' agent install-skill --client codex; codex mcp add ledgerbox",
    ]) {
      const copied = [];
      const payload = center();
      payload.setup_commands.codex = unsafe;
      const sidebar = createAgentSidebar({
        root: new FakeElement('aside'),
        services: {
          fetchCenter: async () => payload,
          updatePolicy: async (body) => body,
          copyText: async (text) => { copied.push(text); },
        },
      });
      await sidebar.refresh();
      assert.equal(sidebar.nodes.agentState.textContent, 'Agent status unavailable');
      assert.equal(sidebar.nodes.copySetup.disabled, true);
      await sidebar.nodes.copySetup.listeners.get('click')();
      assert.deepEqual(copied, []);
    }
  } finally {
    restore();
  }
});

test('collapsed classification settings keep the provider acknowledgement boundary', async () => {
  const restore = installDocument();
  try {
    const writes = [];
    const sidebar = createAgentSidebar({
      root: new FakeElement('aside'),
      services: {
        fetchCenter: async () => center(),
        updatePolicy: async (body) => { writes.push(body); return body; },
        copyText: async () => {},
      },
    });
    await sidebar.refresh();
    assert.match(sidebar.nodes.settingsAside.textContent, /successful import starts/i);
    sidebar.nodes.policyClient.value = 'claude-code';
    sidebar.nodes.mode.value = 'review_first';
    sidebar.nodes.autoImports.checked = false;
    await sidebar.nodes.save.listeners.get('click')();
    assert.equal(writes.length, 0);
    assert.match(sidebar.nodes.status.textContent, /provider data boundary/i);

    sidebar.nodes.acknowledge.checked = true;
    await sidebar.nodes.save.listeners.get('click')();
    assert.deepEqual(writes[0], {
      selected_client: 'claude-code',
      application_mode: 'review_first',
      enabled: true,
      auto_classify_new_imports: false,
      acknowledge_provider_data_policy: true,
    });
  } finally {
    restore();
  }
});
