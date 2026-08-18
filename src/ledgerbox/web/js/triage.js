// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Local review UI for exhaustive remaining-coverage triage submitted by a
// user-owned Agent. This module invokes no Agent and has no automatic write.

import {
  button,
  clear,
  el,
  fetchCategories,
  isOffline,
  option,
} from './api.js';
import {
  dismissTriageRun,
  fetchTriageRun,
  fetchTriageRuns,
  reviewTriageRun,
  withdrawTriageRun,
} from './triage-api.js';
import { CONNECTION_COPY } from './connection.js';
import { renderTriageGroups, renderTriageHistory } from './triage-groups.js';

const defaultServices = {
  fetchRuns: fetchTriageRuns,
  fetchRun: fetchTriageRun,
  fetchCategories,
  review: reviewTriageRun,
  dismiss: dismissTriageRun,
  withdraw: withdrawTriageRun,
};

function producerName(client) {
  if (client === 'codex') return 'Codex';
  if (client === 'claude-code') return 'Claude Code';
  return 'Other local tool';
}

function runLabel(run) {
  const short = run.run_id.startsWith('sha256:') ? run.run_id.slice(7, 19) : run.run_id.slice(0, 12);
  const range = run.scope?.since || run.scope?.until
    ? `${run.scope.since || 'start'}..${run.scope.until || 'end'}` : 'all dates';
  return `${run.pending} pending · ${run.state} · ${range} · ${short}`;
}

function failureCopy(error) {
  const message = error?.message || 'The local service reported an unexplained failure.';
  if (isOffline(error)) {
    return `${message} Reload current facts before retrying; this page cannot confirm whether `
      + 'the action finished.';
  }
  if (error?.status === 409) {
    return `${message} The triage or ledger changed. Reload current facts before reviewing; `
      + 'this refused action changed nothing.';
  }
  if (error?.status >= 400 && error?.status < 500) {
    return `${message} This refused action changed nothing.`;
  }
  return `${message} Reload current facts before retrying.`;
}

export function createTriagePanel({ root, onChange = () => {}, services = defaultServices }) {
  clear(root);
  root.setAttribute('aria-labelledby', 'agent-triage-h');

  const head = el('div', 'panel__head');
  const heading = el('h2', 'panel__title', 'Remaining coverage triage');
  heading.id = 'agent-triage-h';
  head.appendChild(heading);
  const counts = el('p', 'panel__meta');
  head.appendChild(counts);
  root.appendChild(head);
  root.appendChild(
    el(
      'p',
      'panel__note',
      'A tool you ran locally sorted every currently unanswered row into three review routes. '
        + 'Possible transfer is not a transfer decision. Confirmed gaps and uncertain rows stay '
        + 'unclassified; only choosing an existing category changes coverage.',
    ),
  );

  const controls = el('div', 'triage-controls');
  root.appendChild(controls);
  const notice = el('div', 'notice');
  notice.hidden = true;
  root.appendChild(notice);
  const status = el('p', 'visually-hidden');
  status.setAttribute('aria-live', 'polite');
  status.setAttribute('aria-atomic', 'true');
  root.appendChild(status);
  const body = el('div', 'triage-body');
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
    body.className = 'triage-body empty';
    body.textContent = 'No remaining-coverage triage runs yet. Manual transaction classification '
      + 'and Agent proposal review remain available.';
    counts.textContent = '0 runs';
    showNotice('');
    announce('No remaining coverage triage to review.');
  }

  function renderControls() {
    clear(controls);
    const label = el('label', 'control triage-controls__run');
    label.appendChild(el('span', 'control__key', 'Triage run'));
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
    const meta = el('div', 'triage-run');
    const top = el('div', 'triage-run__head');
    top.appendChild(el('span', `badge badge--${run.state === 'open' ? 'pending' : 'quiet'}`, run.state));
    top.appendChild(el('strong', '', producerName(run.producer?.client)));
    top.appendChild(el('span', 'triage-run__stamp', run.created_at));
    meta.appendChild(top);
    const range = run.scope?.since || run.scope?.until
      ? `${run.scope.since || 'start'} through ${run.scope.until || 'end'}` : 'All transaction dates';
    meta.appendChild(el('p', 'triage-run__scope', range));
    const id = el('code', 'triage-run__id', run.run_id);
    id.setAttribute('title', run.run_id);
    meta.appendChild(id);
    return meta;
  }

  async function review(runId, request) {
    const payload = { action: request.action, txn_ids: request.txnIds };
    if (request.action === 'classify') payload.category_id = request.categoryId;
    try {
      const result = await services.review(runId, payload);
      const changed = result.confirmed_transfer + result.confirmed_taxonomy_gap
        + result.left_uncertain + result.classified_existing;
      await refresh({ notice: `Recorded ${changed} explicit triage decision(s).`, tone: 'ok', focus: true });
      onChange();
      return true;
    } catch (error) {
      showNotice(failureCopy(error), 'fail');
      announce('Triage review failed. Current selection was kept.');
      return false;
    }
  }

  function addDismiss(run, host) {
    const pending = run.items.filter((item) => item.outcome === 'pending').length;
    if (pending === 0) return;
    const action = button('btn btn--quiet triage-dismiss__start', 'Dismiss remaining as uncertain', () => {
      action.disabled = true;
      const confirm = el('div', 'notice notice--confirm triage-dismiss');
      confirm.appendChild(
        el(
          'p',
          'notice__text',
          `${pending} pending item(s) will remain unclassified. This changes no category or money figure.`,
        ),
      );
      const actions = el('div', 'notice__actions');
      const dismiss = button('btn', 'Confirm leave unclassified', async () => {
        dismiss.disabled = true;
        keep.disabled = true;
        try {
          const result = await services.dismiss(run.run_id);
          await refresh({
            notice: `Left ${result.left_uncertain} remaining item(s) unclassified.`,
            tone: 'ok',
            focus: true,
          });
          onChange();
        } catch (error) {
          showNotice(failureCopy(error), 'fail');
          announce('Triage dismissal could not be confirmed. Reload current facts.');
          dismiss.disabled = false;
          keep.disabled = false;
        }
      });
      const keep = button('btn btn--quiet', 'Keep reviewing', () => {
        confirm.hidden = true;
        action.disabled = false;
      });
      actions.appendChild(dismiss);
      actions.appendChild(keep);
      confirm.appendChild(actions);
      host.appendChild(confirm);
      dismiss.focus?.();
    });
    host.appendChild(action);
  }

  function addWithdraw(run, host) {
    const applied = run.items.filter((item) => (
      item.outcome === 'confirmed_transfer' || item.outcome === 'classified_existing'
    )).length;
    if (applied === 0) return;
    const action = button('btn btn--quiet triage-withdraw__start', 'Withdraw applied categories', () => {
      action.disabled = true;
      const confirm = el('div', 'notice notice--confirm triage-withdraw');
      confirm.appendChild(
        el(
          'p',
          'notice__text',
          `${applied} category decision(s) came from your review of this run. Withdrawal clears `
            + 'only values that still match; later changes are preserved.',
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
          announce('Triage withdrawal could not be confirmed. Reload current facts.');
          withdraw.disabled = false;
          keep.disabled = false;
        }
      });
      const keep = button('btn btn--quiet', 'Keep applied categories', () => {
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
    body.className = 'triage-body';
    body.appendChild(renderRunMeta(run));
    const groups = el('div', 'triage-routes');
    renderTriageGroups({
      host: groups,
      run,
      categories,
      onReview: (request) => review(run.run_id, request),
      onMessage: (message, tone) => {
        showNotice(message, tone);
        announce(message);
      },
    });
    if (groups.children.length > 0) body.appendChild(groups);
    else body.appendChild(el('p', 'empty', 'This run has no pending triage items.'));
    addDismiss(run, body);
    addWithdraw(run, body);
    renderTriageHistory(body, run.items);
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
      announce(options.notice || `${pending} remaining coverage item(s) pending.`);
      if (options.focus) runSelect?.focus?.();
    } catch (error) {
      clear(controls);
      clear(body);
      body.className = 'triage-body empty';
      if (isOffline(error)) {
        // One sentence for one state, shared with the four panels that already
        // read it. This said `Waiting for the local service.` while they said
        // `Waiting for ledgerbox.` -- two wordings for the server not
        // answering, on one page, which translating the page made visible.
        body.textContent = CONNECTION_COPY.panel;
        showNotice('');
        announce('Remaining coverage triage is waiting.');
      } else {
        body.textContent = 'Remaining coverage triage could not load.';
        showNotice(error?.message || 'The local service reported an unexplained failure.', 'fail');
        announce('Remaining coverage triage could not load.');
      }
      counts.textContent = '';
    } finally {
      body.removeAttribute('aria-busy');
    }
  }

  return {
    refresh,
    services,
    nodes: { body, controls, counts, notice, status },
  };
}
