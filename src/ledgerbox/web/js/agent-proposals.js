// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Review UI for proposal audit rows produced outside Ledgerbox by a local tool.
// It invokes no Agent. Every write names checked transaction ids and delegates
// the state transition to the proposal API; a transport failure is reported as
// unknown rather than claimed to be a rollback.

import {
  button,
  clear,
  el,
  fetchCategories,
  fetchProposalRun,
  fetchProposalRuns,
  isOffline,
  option,
  reviewProposalRun,
  withdrawProposalRun,
} from './api.js';
import { CONNECTION_COPY } from './connection.js';
import { renderProposalGroups, renderProposalHistory } from './agent-proposal-groups.js';

const defaultServices = {
  fetchRuns: fetchProposalRuns,
  fetchRun: fetchProposalRun,
  fetchCategories,
  review: reviewProposalRun,
  withdraw: withdrawProposalRun,
};

function producerName(client) {
  if (client === 'codex') return 'Codex';
  if (client === 'claude-code') return 'Claude Code';
  return 'Other local tool';
}

function runLabel(run) {
  const short = run.run_id.startsWith('sha256:') ? run.run_id.slice(7, 19) : run.run_id.slice(0, 12);
  return `${run.pending} pending · ${run.state} · ${run.created_at} · ${short}`;
}

function failureCopy(error) {
  const message = error?.message || 'The local service reported an unexplained failure.';
  if (isOffline(error)) {
    return `${message} Reload current facts before retrying; this page cannot confirm whether `
      + 'the action finished.';
  }
  if (error?.status === 409) {
    return `${message} The proposal or ledger changed. Reload current facts before reviewing; `
      + 'this refused action changed nothing.';
  }
  if (error?.status >= 400 && error?.status < 500) {
    return `${message} This refused action changed nothing.`;
  }
  return `${message} Reload current facts before retrying.`;
}

export function createProposalPanel({ root, onChange = () => {}, services = defaultServices }) {
  clear(root);
  root.setAttribute('aria-labelledby', 'agent-proposals-h');

  const head = el('div', 'panel__head');
  head.appendChild(el('h2', 'panel__title', 'Agent proposals'));
  head.children[0].id = 'agent-proposals-h';
  const counts = el('p', 'panel__meta');
  head.appendChild(counts);
  root.appendChild(head);
  const note = el(
    'p',
    'panel__note',
    'This panel only lists suggestions the Agent submitted. A zero pending count does not mean '
      + 'every candidate was classified: suggestions the Agent omitted stay under Transactions '
      + 'with Category set to “Nothing claimed this”. Review-first runs wait here; automatic '
      + 'v2 runs are already applied atomically and remain inspectable and withdrawable here.',
  );
  root.appendChild(note);

  const controls = el('div', 'proposal-controls');
  root.appendChild(controls);
  const notice = el('div', 'notice');
  notice.hidden = true;
  root.appendChild(notice);
  const status = el('p', 'visually-hidden');
  status.setAttribute('aria-live', 'polite');
  status.setAttribute('aria-atomic', 'true');
  root.appendChild(status);
  const body = el('div', 'proposal-body');
  root.appendChild(body);

  let runs = [];
  let selectedRunId = null;
  let announcing = null;
  let runSelect = null;

  function announce(message) {
    if (message !== announcing) {
      announcing = message;
      status.textContent = message;
    }
  }

  function showNotice(message, tone = 'neutral') {
    notice.className = `notice notice--${tone}`;
    notice.textContent = message;
    notice.hidden = !message;
  }

  function showEmpty() {
    clear(controls);
    clear(body);
    body.className = 'proposal-body empty';
    body.textContent = 'No Agent proposal runs yet. You can keep classifying with the manual '
      + 'transaction controls, or submit a proposal with the local JSON command.';
    counts.textContent = '0 runs';
    showNotice('');
    announce('No Agent proposals to review.');
  }

  function renderControls() {
    clear(controls);
    const label = el('label', 'control proposal-controls__run');
    label.appendChild(el('span', 'control__key', 'Proposal run'));
    const select = el('select', 'control__field');
    runSelect = select;
    for (const run of runs) select.appendChild(option(run.run_id, runLabel(run)));
    select.value = selectedRunId;
    select.addEventListener('change', () => {
      selectedRunId = select.value;
      refresh();
    });
    label.appendChild(select);
    controls.appendChild(label);
    controls.appendChild(button('btn btn--quiet', 'Reload current facts', () => refresh()));
  }

  function renderRunMeta(run) {
    const meta = el('div', 'proposal-run');
    const top = el('div', 'proposal-run__head');
    top.appendChild(el('span', `badge badge--${run.state === 'open' ? 'pending' : 'quiet'}`, run.state));
    const producer = run.producer || {};
    top.appendChild(el('strong', '', producerName(producer.client)));
    top.appendChild(el('span', 'proposal-run__stamp', run.created_at));
    meta.appendChild(top);
    const details = [];
    if (producer.client_version) details.push(`client ${producer.client_version}`);
    if (producer.model_reported) details.push(`model label ${producer.model_reported} (self-reported)`);
    if (details.length) meta.appendChild(el('p', 'proposal-run__producer', details.join(' · ')));
    const id = el('code', 'proposal-run__id', run.run_id);
    id.setAttribute('title', run.run_id);
    meta.appendChild(id);
    return meta;
  }

  async function review(runId, request) {
    const body = { action: request.action, txn_ids: request.txnIds };
    if (request.action === 'accept') body.category_id = request.categoryId;
    try {
      const result = await services.review(runId, body);
      const changed = result.accepted + result.edited + result.rejected;
      const verb = request.action === 'reject' ? 'Rejected' : 'Applied';
      await refresh({ notice: `${verb} ${changed} proposal(s).`, tone: 'ok', focus: true });
      onChange();
      return true;
    } catch (error) {
      const message = failureCopy(error);
      showNotice(message, 'fail');
      announce('Proposal review failed. Current selection was kept.');
      return false;
    }
  }

  function addWithdraw(run, host) {
    const applied = run.proposals.filter((row) => (
      row.outcome === 'accepted' || row.outcome === 'edited'
    )).length;
    if (applied === 0) return;
    const action = button('btn btn--quiet proposal-withdraw__start', 'Withdraw applied decisions', () => {
      action.disabled = true;
      const confirm = el('div', 'notice notice--confirm proposal-withdraw');
      confirm.appendChild(
        el(
          'p',
          'notice__text',
          `${applied} applied decision(s) belong to this run. Withdrawal clears only categories `
            + 'that still match what this run applied; later manual edits are preserved.',
        ),
      );
      const actions = el('div', 'notice__actions');
      const withdraw = button('btn', 'Confirm withdrawal', async () => {
        withdraw.disabled = true;
        keep.disabled = true;
        try {
          const result = await services.withdraw(run.run_id);
          await refresh({
            notice: `Withdrew ${result.withdrawn}; already absent ${result.already_absent}; `
              + `changed later and preserved ${result.changed_later}.`,
            tone: 'ok',
            focus: true,
          });
          onChange();
        } catch (error) {
          showNotice(failureCopy(error), 'fail');
          announce('Proposal withdrawal could not be confirmed. Reload current facts.');
          withdraw.disabled = false;
          keep.disabled = false;
        }
      });
      const keep = button('btn btn--quiet', 'Keep applied decisions', () => {
        confirm.hidden = true;
        action.disabled = false;
      });
      actions.appendChild(withdraw);
      actions.appendChild(keep);
      confirm.appendChild(actions);
      host.appendChild(confirm);
      withdraw.focus?.();
    });
    host.appendChild(action);
  }

  function renderRun(run, categories) {
    clear(body);
    body.className = 'proposal-body';
    body.appendChild(renderRunMeta(run));
    const groups = el('div', 'proposal-groups');
    renderProposalGroups({
      host: groups,
      proposals: run.proposals,
      categories,
      onReview: (request) => review(run.run_id, request),
      onMessage: (message, tone) => {
        showNotice(message, tone);
        announce(message);
      },
    });
    if (groups.children.length > 0) body.appendChild(groups);
    else {
      body.appendChild(
        el(
          'p',
          'empty',
          'This run has no pending proposals. That only means every submitted suggestion was '
            + 'reviewed. Candidates the Agent omitted never appear in this run; find them under '
            + 'Transactions → Category → Nothing claimed this.',
        ),
      );
    }
    addWithdraw(run, body);
    renderProposalHistory(body, run.proposals);
  }

  async function refresh(options = {}) {
    body.setAttribute('aria-busy', 'true');
    try {
      const [freshRuns, categories] = await Promise.all([
        services.fetchRuns(50),
        services.fetchCategories(),
      ]);
      runs = freshRuns;
      if (runs.length === 0) {
        showEmpty();
        return;
      }
      if (!runs.some((run) => run.run_id === selectedRunId)) {
        selectedRunId = (runs.find((run) => run.state === 'open' && run.pending > 0) || runs[0]).run_id;
      }
      renderControls();
      const run = await services.fetchRun(selectedRunId);
      renderRun(run, categories);
      const pending = runs.reduce((total, item) => total + item.pending, 0);
      counts.textContent = `${pending} pending in ${runs.length} recent run(s)`;
      if (options.notice) showNotice(options.notice, options.tone || 'neutral');
      else showNotice('');
      announce(options.notice || `${pending} Agent proposal(s) pending.`);
      if (options.focus) runSelect?.focus?.();
    } catch (error) {
      clear(controls);
      clear(body);
      body.className = 'proposal-body empty';
      if (isOffline(error)) {
        // One sentence for one state, shared with the four panels that already
        // read it. This said `Waiting for the local service.` while they said
        // `Waiting for ledgerbox.` -- two wordings for the server not
        // answering, on one page, which translating the page made visible.
        body.textContent = CONNECTION_COPY.panel;
        showNotice('');
        announce('Agent proposal review is waiting.');
      } else {
        body.textContent = 'Agent proposal review could not load.';
        showNotice(error?.message || 'The local service reported an unexplained failure.', 'fail');
        announce('Agent proposal review could not load.');
      }
      counts.textContent = '';
    } finally {
      body.removeAttribute('aria-busy');
    }
  }

  return {
    refresh,
    services,
    nodes: { body, controls, counts, note, notice, status },
  };
}
