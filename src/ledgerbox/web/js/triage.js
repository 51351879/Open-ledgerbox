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
import { t } from './i18n.js';
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
  return t('Other local tool');
}

function runLabel(run) {
  const short = run.run_id.startsWith('sha256:') ? run.run_id.slice(7, 19) : run.run_id.slice(0, 12);
  // `start` and `end` stand in for a bound the run did not have. They are
  // single words rather than sentences, which is as much context as a
  // dictionary gets for them; the long form beside the run says the same
  // thing in a sentence, and a translator reading both has what it needs.
  const range = run.scope?.since || run.scope?.until
    ? `${run.scope.since || t('start')}..${run.scope.until || t('end')}` : t('all dates');
  return `${t('{count} pending', { count: run.pending })} · ${run.state}`
    + ` · ${range} · ${short}`;
}

// The service's own sentence is quoted exactly as it arrived; only what this
// page adds to it is translated. Same split as the proposal panel, for the
// same reason: the first half is the local process reporting a fact, the
// second is this page saying what it means for the action you just tried.
function failureCopy(error) {
  const message = error?.message || t('The local service reported an unexplained failure.');
  if (isOffline(error)) {
    return `${message} ${t('Reload current facts before retrying; this page cannot confirm '
      + 'whether the action finished.')}`;
  }
  if (error?.status === 409) {
    return `${message} ${t('The triage or ledger changed. Reload current facts before '
      + 'reviewing; this refused action changed nothing.')}`;
  }
  if (error?.status >= 400 && error?.status < 500) {
    return `${message} ${t('This refused action changed nothing.')}`;
  }
  return `${message} ${t('Reload current facts before retrying.')}`;
}

export function createTriagePanel({ root, onChange = () => {}, services = defaultServices }) {
  clear(root);
  root.setAttribute('aria-labelledby', 'agent-triage-h');

  const head = el('div', 'panel__head');
  const heading = el('h2', 'panel__title', t('Remaining coverage triage'));
  heading.id = 'agent-triage-h';
  head.appendChild(heading);
  const counts = el('p', 'panel__meta');
  head.appendChild(counts);
  root.appendChild(head);
  root.appendChild(
    el(
      'p',
      'panel__note',
      // Quotes `Possible transfer`, a route heading `triage-groups.js` still
      // renders in English, so it has no dictionary entry yet: prose naming a
      // heading in the other language sends the reader looking for something
      // that is not on the page. `t()` is here so the entry is all that is left.
      t(
        'A tool you ran locally sorted every currently unanswered row into three review '
          + 'routes. Possible transfer is not a transfer decision. Confirmed gaps and '
          + 'uncertain rows stay unclassified; only choosing an existing category changes '
          + 'coverage.',
      ),
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
    body.textContent = t('No remaining-coverage triage runs yet. Manual transaction '
      + 'classification and Agent proposal review remain available.');
    counts.textContent = t('0 runs');
    showNotice('');
    announce(t('No remaining coverage triage to review.'));
  }

  function renderControls() {
    clear(controls);
    const label = el('label', 'control triage-controls__run');
    label.appendChild(el('span', 'control__key', t('Triage run')));
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
    controls.appendChild(button('btn btn--quiet', t('Reload current facts'), () => refresh()));
  }

  function renderRunMeta(run) {
    const meta = el('div', 'triage-run');
    const top = el('div', 'triage-run__head');
    top.appendChild(el('span', `badge badge--${run.state === 'open' ? 'pending' : 'quiet'}`, run.state));
    top.appendChild(el('strong', '', producerName(run.producer?.client)));
    top.appendChild(el('span', 'triage-run__stamp', run.created_at));
    meta.appendChild(top);
    const range = run.scope?.since || run.scope?.until
      ? t('{since} through {until}', {
        since: run.scope.since || t('start'),
        until: run.scope.until || t('end'),
      })
      : t('All transaction dates');
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
      await refresh({
        notice: t('Recorded {count} explicit triage decision(s).', { count: changed }),
        tone: 'ok',
        focus: true,
      });
      onChange();
      return true;
    } catch (error) {
      showNotice(failureCopy(error), 'fail');
      announce(t('Triage review failed. Current selection was kept.'));
      return false;
    }
  }

  function addDismiss(run, host) {
    const pending = run.items.filter((item) => item.outcome === 'pending').length;
    if (pending === 0) return;
    const startLabel = t('Dismiss remaining as uncertain');
    const action = button('btn btn--quiet triage-dismiss__start', startLabel, () => {
      action.disabled = true;
      const confirm = el('div', 'notice notice--confirm triage-dismiss');
      confirm.appendChild(
        el(
          'p',
          'notice__text',
          t(
            '{count} pending item(s) will remain unclassified. This changes no category or '
              + 'money figure.',
            { count: pending },
          ),
        ),
      );
      const actions = el('div', 'notice__actions');
      const dismiss = button('btn', t('Confirm leave unclassified'), async () => {
        dismiss.disabled = true;
        keep.disabled = true;
        try {
          const result = await services.dismiss(run.run_id);
          await refresh({
            notice: t('Left {count} remaining item(s) unclassified.', {
              count: result.left_uncertain,
            }),
            tone: 'ok',
            focus: true,
          });
          onChange();
        } catch (error) {
          showNotice(failureCopy(error), 'fail');
          announce(t('Triage dismissal could not be confirmed. Reload current facts.'));
          dismiss.disabled = false;
          keep.disabled = false;
        }
      });
      const keep = button('btn btn--quiet', t('Keep reviewing'), () => {
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
    const startLabel = t('Withdraw applied categories');
    const action = button('btn btn--quiet triage-withdraw__start', startLabel, () => {
      action.disabled = true;
      const confirm = el('div', 'notice notice--confirm triage-withdraw');
      confirm.appendChild(
        el(
          'p',
          'notice__text',
          t(
            '{count} category decision(s) came from your review of this run. Withdrawal '
              + 'clears only values that still match; later changes are preserved.',
            { count: applied },
          ),
        ),
      );
      const actions = el('div', 'notice__actions');
      const withdraw = button('btn', t('Confirm withdrawal'), async () => {
        withdraw.disabled = true;
        keep.disabled = true;
        try {
          const result = await services.withdraw(run.run_id);
          await refresh({
            notice: t(
              'Withdrew {withdrawn}; already absent {absent}; changed later and preserved '
                + '{preserved}.',
              {
                withdrawn: result.withdrawn,
                absent: result.already_absent,
                preserved: result.changed_later,
              },
            ),
            tone: 'ok',
            focus: true,
          });
          onChange();
        } catch (error) {
          showNotice(failureCopy(error), 'fail');
          announce(t('Triage withdrawal could not be confirmed. Reload current facts.'));
          withdraw.disabled = false;
          keep.disabled = false;
        }
      });
      const keep = button('btn btn--quiet', t('Keep applied categories'), () => {
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
    else body.appendChild(el('p', 'empty', t('This run has no pending triage items.')));
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
      counts.textContent = t('{pending} pending in {runs} recent run(s)', {
        pending,
        runs: runs.length,
      });
      if (options.notice) showNotice(options.notice, options.tone || 'neutral');
      else showNotice('');
      announce(
        options.notice
          || t('{count} remaining coverage item(s) pending.', { count: pending }),
      );
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
        announce(t('Remaining coverage triage is waiting.'));
      } else {
        body.textContent = t('Remaining coverage triage could not load.');
        showNotice(
          error?.message || t('The local service reported an unexplained failure.'),
          'fail',
        );
        announce(t('Remaining coverage triage could not load.'));
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
