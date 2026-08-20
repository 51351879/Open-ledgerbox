// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Compact A7.3 Agent evidence and page directory. This module never starts a
// model: setup and classification actions copy literal text for the user's own
// client, while the green state is reserved for a currently active MCP session.

import { button, clear, el, isOffline, option } from './api.js';
import {
  fetchAgentCenter,
  replaceAgentPolicy,
  startClassificationRound,
} from './api-agent.js';

// Long enough that a fifteen-minute run is not polled hundreds of times, short
// enough that a finished round is not stale on screen while somebody waits.
const WATCH_MS = 4000;
import { IN_FLIGHT, LABELS, validatedCenter } from './agent-contract.js';
import { createJobPanel } from './agent-job-panel.js';
import { t } from './i18n.js';
import { addDirectory, setBadge } from './sidebar-nav.js';

async function defaultCopyText(text) {
  if (!globalThis.navigator?.clipboard?.writeText) {
    throw new Error(t('Clipboard access is unavailable in this browser.'));
  }
  await navigator.clipboard.writeText(text);
}

function labelledControl(label, control) {
  const wrapper = el('label', 'agent-setup__control');
  wrapper.appendChild(el('span', 'control__key', label));
  wrapper.appendChild(control);
  return wrapper;
}

function checkbox(label, className = '') {
  const wrapper = el('label', `agent-setup__check ${className}`.trim());
  const input = el('input');
  input.type = 'checkbox';
  wrapper.appendChild(input);
  wrapper.appendChild(el('span', '', label));
  return { wrapper, input };
}

function clientSelect() {
  const select = el('select', 'control__field');
  select.appendChild(option('codex', 'Codex'));
  select.appendChild(option('claude-code', 'Claude Code'));
  return select;
}

export function createAgentSidebar({ root, services, onNeedsClassification } = {}) {
  const api = {
    fetchCenter: fetchAgentCenter,
    updatePolicy: replaceAgentPolicy,
    startRound: startClassificationRound,
    copyText: defaultCopyText,
    setTimer: (fn, ms) => setTimeout(fn, ms),
    clearTimer: (handle) => clearTimeout(handle),
    now: () => Date.now(),
    ...(services || {}),
  };
  clear(root);
  const panel = el('div', 'sidebar__panel');
  // Through `t()` because this render replaces the identical line of markup
  // in index.html: without it the static sweep translates the eyebrow and
  // this line puts it straight back into English, in front of the reader.
  panel.appendChild(el('p', 'sidebar__eyebrow', t('This ledger')));
  const ledgerName = el('strong', 'sidebar__ledger-name', t('Reading…'));
  const ledgerPath = el('code', 'sidebar__ledger-path');
  ledgerPath.setAttribute(
    'title',
    t('The data directory this page and copied commands use'),
  );
  panel.appendChild(ledgerName);
  panel.appendChild(ledgerPath);

  const agentState = el('p', 'agent-status agent-status--checking', t('Checking Agent MCP…'));
  agentState.setAttribute('aria-live', 'polite');
  panel.appendChild(agentState);
  const agentEvidence = el('p', 'sidebar__evidence');
  panel.appendChild(agentEvidence);

  const jobPanel = createJobPanel({
    onNeedsClassification,
    onClassifyNow: () => startRound(),
    now: api.now,
  });
  panel.appendChild(jobPanel.node);

  const badges = addDirectory(panel);

  const setup = el('details', 'agent-setup');
  setup.appendChild(el('summary', 'agent-setup__summary', t('Connect or change Agent')));
  const setupBody = el('div', 'agent-setup__body');
  const setupClient = clientSelect();
  setupBody.appendChild(labelledControl(t('Client'), setupClient));
  const runnerSkillState = el('p', 'agent-setup__aside');
  const personalSkillState = el('p', 'agent-setup__aside');
  setupBody.appendChild(runnerSkillState);
  setupBody.appendChild(personalSkillState);
  const setupActions = el('div', 'agent-setup__actions');
  const copySetup = button('btn btn--quiet btn--compact', t('Copy safe setup steps'),
    () => copySetupCommand());
  const copyRun = button('btn btn--quiet btn--compact', t('Copy classification prompt'),
    () => copyRunPrompt());
  setupActions.appendChild(copySetup);
  setupActions.appendChild(copyRun);
  setupBody.appendChild(setupActions);
  const tutorial = el(
    'p',
    'agent-setup__tutorial',
    t(
      '1. Copy the safe setup step. 2. Paste that single line into PowerShell: it installs '
        + 'or safely upgrades the personal Skill first, then registers MCP only if that '
        + 'install succeeded. 3. Start or reopen the selected client. 4. Check its MCP list '
        + 'for “ledgerbox”. The light above turns green only after that client actually '
        + 'opens the bridge.',
    ),
  );
  setupBody.appendChild(tutorial);
  const guide = el('p', 'agent-setup__guide');
  // The separator stays outside the sentence; a normalised key loses it.
  guide.appendChild(el('span', '', `${t('Full human and Agent-readable guide:')} `));
  const guidePath = el('code', '', 'docs/AGENT_SETUP.md');
  guide.appendChild(guidePath);
  setupBody.appendChild(guide);

  const settings = el('details', 'agent-settings');
  settings.appendChild(el('summary', 'agent-settings__summary', t('Classification settings')));
  const settingsBody = el('div', 'agent-settings__body');
  const policyClient = clientSelect();
  const mode = el('select', 'control__field');
  // `automatic` and `review_first` are wire values; only their labels move.
  mode.appendChild(option('automatic', t('Apply answers automatically')));
  mode.appendChild(option('review_first', t('Review suggestions first')));
  const autoImports = checkbox(t('Auto classify new imports'));
  const acknowledge = checkbox(
    t('I understand returned transaction facts may be sent to this client’s model '
      + 'provider.'),
    'agent-setup__ack',
  );
  settingsBody.appendChild(labelledControl(t('Local client'), policyClient));
  settingsBody.appendChild(labelledControl(t('Application mode'), mode));
  settingsBody.appendChild(autoImports.wrapper);
  settingsBody.appendChild(acknowledge.wrapper);
  const settingsAside = el(
    'p',
    'agent-setup__aside',
    t('When enabled, a successful import starts one bounded classification run in the '
      + 'selected local client.'),
  );
  settingsBody.appendChild(settingsAside);
  const policyActions = el('div', 'agent-setup__actions');
  const save = button('btn btn--compact', t('Save and enable'), () => savePolicy());
  const disconnect = button('btn btn--quiet btn--compact', t('Disable'),
    () => disconnectPolicy());
  policyActions.appendChild(save);
  policyActions.appendChild(disconnect);
  settingsBody.appendChild(policyActions);
  const disclosure = el('p', 'agent-setup__disclosure');
  settingsBody.appendChild(disclosure);
  settings.appendChild(settingsBody);
  setupBody.appendChild(settings);
  const status = el('p', 'agent-setup__status');
  status.setAttribute('aria-live', 'polite');
  status.setAttribute('aria-atomic', 'true');
  setupBody.appendChild(status);
  setup.appendChild(setupBody);
  panel.appendChild(setup);
  root.appendChild(panel);

  let data = null;
  let followedClient = null;
  let timer = null;
  let stopped = false;

  function renderSkillStatus() {
    const selected = data?.clients.find((item) => item.client === setupClient.value);
    if (!selected) {
      runnerSkillState.textContent = t('Runner Skill status unavailable.');
      personalSkillState.textContent = t('Personal Skill status unavailable.');
      copySetup.disabled = true;
      return;
    }
    runnerSkillState.textContent = selected.runner_skill_compatible
      ? t('Runner Skill compatible with this Ledgerbox protocol.')
      : t('Runner Skill incompatible or unavailable in this Ledgerbox installation.');
    const clientArg = selected.client;
    // The command inside the last one is a command and is substituted, not
    // looked up; the sentence around it is this page's and is.
    const personalCopy = {
      current: t('Personal Skill current.'),
      missing: t('Personal Skill missing. Safe setup installs it before MCP registration.'),
      outdated: t('Personal Skill outdated. Safe setup upgrades only a recognised official '
        + 'copy.'),
      custom: t('Personal Skill custom. Stop and run ledgerbox agent doctor --client '
        + '{client}; decide manually.', { client: clientArg }),
    };
    personalSkillState.textContent = personalCopy[selected.personal_skill_state];
    copySetup.disabled = !selected.runner_skill_compatible
      || selected.personal_skill_state === 'custom';
  }

  function render() {
    const policy = data.policy;
    const active = data.clients.find((item) => item.session_active && item.mcp_session === 'active');
    ledgerName.textContent = data.ledgerbox.ledger_label;
    ledgerPath.textContent = data.ledgerbox.data_dir;
    if (active) {
      agentState.className = 'agent-status agent-status--up';
      agentState.textContent = t('{client} MCP connected', { client: LABELS[active.client] });
      agentEvidence.textContent = active.last_result === 'partial'
        ? t('Last run submitted {submitted} of {candidates} candidates.', {
          submitted: active.submitted_count,
          candidates: active.candidate_count,
        })
        : t('Live session observed by this ledger.');
    } else {
      agentState.className = 'agent-status agent-status--down';
      agentState.textContent = t('No Agent MCP connected');
      agentEvidence.textContent = t('Ledgerbox may still be online; no Agent bridge is '
        + 'active now.');
    }
    const selected = policy.selected_client || active?.client || 'codex';
    // The dropdowns follow the STORED policy only when it changes. Re-asserting
    // it on every poll reached over mid-switch and put the choice back, which
    // read as "it will not let me pick Claude Code".
    if (selected !== followedClient) {
      setupClient.value = selected;
      policyClient.value = selected;
      followedClient = selected;
    }
    renderSkillStatus();
    mode.value = policy.application_mode;
    autoImports.input.checked = policy.auto_classify_new_imports;
    acknowledge.input.checked = false;
    disconnect.disabled = !policy.enabled;
    copyRun.disabled = !policy.enabled || !policy.selected_client;
    disclosure.textContent = data.provider_disclosure;
    guidePath.textContent = data.setup_guide;
    const omitted = jobPanel.render(data.latest_batch, policy);
    setBadge(badges.needs, omitted, t('need classification'));
    setBadge(badges.proposals, data.ledgerbox.pending_review_count);
    setBadge(badges.triage, data.ledgerbox.pending_triage_count);
    setBadge(badges.review, data.ledgerbox.open_review_count);
  }

  async function refresh() {
    root.setAttribute('aria-busy', 'true');
    try {
      data = validatedCenter(await api.fetchCenter());
      render();
      status.textContent = '';
    } catch (error) {
      data = null;
      copySetup.disabled = true;
      copyRun.disabled = true;
      jobPanel.unavailable();
      agentState.className = 'agent-status agent-status--down';
      agentState.textContent = t('Agent status unavailable');
      agentEvidence.textContent = isOffline(error)
        ? t('Waiting for the local Ledgerbox service.')
        : (error.message || t('Could not read Agent status.'));
    } finally {
      root.removeAttribute('aria-busy');
      scheduleWatch();
    }
  }

  // A classification round takes minutes and used to leave the page frozen on
  // whatever it happened to show when it started. Polling stops the moment the
  // work does, so an idle ledger is not asked anything.
  function scheduleWatch() {
    if (timer !== null) {
      api.clearTimer(timer);
      timer = null;
    }
    if (stopped || !data?.latest_batch || !IN_FLIGHT.has(data.latest_batch.state)) return;
    timer = api.setTimer(() => {
      timer = null;
      refresh();
    }, WATCH_MS);
  }

  async function startRound() {
    if (!data || jobPanel.nodes.classifyNow.disabled) return;
    jobPanel.nodes.classifyNow.disabled = true;
    try {
      data = validatedCenter(await api.startRound());
      render();
      status.textContent = t('Classification round queued. This panel follows it.');
    } catch (error) {
      render();
      status.textContent = error.message || t('Could not start a classification round.');
    } finally {
      scheduleWatch();
    }
  }

  async function copySetupCommand() {
    if (!data) return;
    const selected = data.clients.find((item) => item.client === setupClient.value);
    if (!selected?.runner_skill_compatible || selected.personal_skill_state === 'custom') {
      status.textContent = selected?.personal_skill_state === 'custom'
        ? t('Stop and run ledgerbox agent doctor --client {client}; decide manually.', {
          client: setupClient.value,
        })
        : t('Safe setup is unavailable for this Ledgerbox installation.');
      return;
    }
    try {
      await api.copyText(data.setup_commands[setupClient.value]);
      status.textContent = t('{client} setup command copied. Paste the single line into '
        + 'PowerShell.', { client: LABELS[setupClient.value] });
    } catch (error) {
      status.textContent = error.message || t('Could not copy the setup command.');
    }
  }

  async function copyRunPrompt() {
    if (!data?.policy.enabled || !data.policy.selected_client) return;
    try {
      await api.copyText(data.run_prompts[data.policy.selected_client]);
      status.textContent = t('{client} classification prompt copied.', {
        client: LABELS[data.policy.selected_client],
      });
    } catch (error) {
      status.textContent = error.message || t('Could not copy the classification prompt.');
    }
  }

  async function savePolicy() {
    if (!acknowledge.input.checked) {
      status.textContent = t('Confirm the provider data boundary before enabling '
        + 'classification.');
      return;
    }
    try {
      data.policy = await api.updatePolicy({
        selected_client: policyClient.value,
        application_mode: mode.value,
        enabled: true,
        auto_classify_new_imports: autoImports.input.checked,
        acknowledge_provider_data_policy: true,
      });
      render();
      status.textContent = t('{client} policy saved.', {
        client: LABELS[data.policy.selected_client],
      });
    } catch (error) {
      status.textContent = error.message || t('Could not save Agent settings.');
    }
  }

  async function disconnectPolicy() {
    if (!data) return;
    try {
      data.policy = await api.updatePolicy({
        selected_client: policyClient.value,
        application_mode: mode.value,
        enabled: false,
        auto_classify_new_imports: autoImports.input.checked,
        acknowledge_provider_data_policy: false,
      });
      render();
      status.textContent = t('Automatic Agent classification disabled. The MCP '
        + 'registration is unchanged.');
    } catch (error) {
      status.textContent = error.message || t('Could not disable Agent classification.');
    }
  }

  setupClient.addEventListener('change', renderSkillStatus);

  return {
    refresh,
    services: api,
    stop() {
      stopped = true;
      if (timer !== null) {
        api.clearTimer(timer);
        timer = null;
      }
    },
    nodes: {
      agentState, agentEvidence, ledgerName, ledgerPath, badges,
      ...jobPanel.nodes,
      setupClient, runnerSkillState, personalSkillState,
      copySetup, copyRun, tutorial, guidePath,
      policyClient, mode, autoImports: autoImports.input, acknowledge: acknowledge.input,
      save, disconnect, disclosure, settingsAside, status,
    },
  };
}
