// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The repeated object inside proposal review: one suggested category, the
// current ledger rows it names, and an explicit subset to accept/edit/reject.
// The server remains the authority for eligibility, revision and atomicity;
// this module only names the ids a person currently has checked.

import { button, clear, el, formatMinor, option } from './api.js';
import { t } from './i18n.js';

export function pendingGroups(proposals) {
  const groups = new Map();
  for (const proposal of proposals || []) {
    if (proposal.outcome !== 'pending') continue;
    const key = proposal.group_id;
    if (!groups.has(key)) {
      groups.set(key, {
        groupId: key,
        categoryId: proposal.suggested_category_id,
        rows: [],
      });
    }
    groups.get(key).rows.push(proposal);
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      rows: group.rows.sort((left, right) => left.txn_id.localeCompare(right.txn_id)),
    }))
    .sort((left, right) => (
      left.categoryId.localeCompare(right.categoryId)
      || left.groupId.localeCompare(right.groupId)
    ));
}

export function impactCopy(categoryId, selected) {
  // Whole sentences per number rather than a noun swapped into one: a
  // language with no plural says both the same way, and a dictionary can
  // answer for a sentence but not for half of one.
  const lead = selected === 1
    ? t('1 selected transaction.')
    : t('{count} selected transactions.', { count: selected });
  if (categoryId === 'transfer') {
    return `${lead} ${t('Transfer remains manual approval only; accepting removes those '
      + 'amounts from the In and Out figures, not from the ledger.')}`;
  }
  // The category id is substituted, never looked up.
  return `${lead} ${t('Accepting sets the current category to {category}. Balances and '
    + 'statement lines do not change.', { category: categoryId })}`;
}

function decisionCopy(transaction) {
  if (!transaction) return t('Current ledger row is unavailable.');
  if (transaction.category_decided_by === 'none') return t('No current category.');
  const source = transaction.category_decided_by === 'override'
    ? t('set by you')
    : transaction.category_decided_by === 'agent' ? t('set by Agent')
      : transaction.category_decided_by === 'learned' ? t('set by your earlier answer')
        : t('set by a rule');
  return `${transaction.category_id || '—'} (${source})`;
}

function currentRow(proposal, onSelection) {
  const transaction = proposal.current_transaction;
  const row = el('label', 'proposal-row');
  const pick = el('input', 'proposal-row__pick');
  pick.type = 'checkbox';
  pick.checked = true;
  pick.dataset.txnId = proposal.txn_id;
  pick.setAttribute('aria-label', t('Include transaction {id}', { id: proposal.txn_id }));
  pick.addEventListener('change', onSelection);
  row.appendChild(pick);

  const facts = el('span', 'proposal-row__facts');
  if (transaction) {
    const first = el('span', 'proposal-row__line');
    first.appendChild(el('time', 'proposal-row__date', transaction.date));
    first.appendChild(el('span', 'proposal-row__desc', transaction.raw_descriptor));
    first.appendChild(
      el(
        'span',
        `proposal-row__amount${transaction.amount_minor < 0 ? ' proposal-row__amount--out' : ''}`,
        formatMinor(transaction.amount_minor),
      ),
    );
    facts.appendChild(first);
  } else {
    facts.appendChild(el('span', 'proposal-row__line', t('Current ledger row unavailable')));
  }
  facts.appendChild(el('span', 'proposal-row__current', decisionCopy(transaction)));
  row.appendChild(facts);
  return { row, pick };
}

function categoryPicker(categories, suggested) {
  const select = el('select', 'control__field proposal-group__category');
  const sorted = [...(categories || [])].sort((left, right) => (
    left.kind.localeCompare(right.kind) || left.id.localeCompare(right.id)
  ));
  if (!sorted.some((category) => category.id === suggested)) {
    select.appendChild(option(suggested, suggested));
  }
  for (const category of sorted) {
    select.appendChild(option(category.id, `${category.id} · ${category.kind}`));
  }
  select.value = suggested;
  select.setAttribute('aria-label', t('Category to apply to selected transactions'));
  return select;
}

function setDisabled(nodes, disabled) {
  for (const node of nodes) node.disabled = disabled;
}

function renderGroup({ group, categories, onReview, onMessage }) {
  const card = el('article', 'proposal-group');
  const head = el('div', 'proposal-group__head');
  const title = el('h3', 'proposal-group__title', group.categoryId);
  head.appendChild(title);
  head.appendChild(
    el('span', 'badge badge--pending', t('{count} pending', { count: group.rows.length })),
  );
  card.appendChild(head);

  const controls = el('div', 'proposal-group__controls');
  const allLabel = el('label', 'proposal-group__all');
  const all = el('input', 'proposal-group__check');
  all.type = 'checkbox';
  all.checked = true;
  allLabel.appendChild(all);
  // The separator stays outside the sentence; keys are whitespace-normalised.
  allLabel.appendChild(document.createTextNode(` ${t('Include all in this group')}`));
  controls.appendChild(allLabel);

  const picker = categoryPicker(categories, group.categoryId);
  controls.appendChild(picker);
  const accept = button('btn', t('Accept selected'), () => apply('accept'));
  accept.dataset.action = 'accept';
  const reject = button('btn btn--quiet', t('Reject selected'), () => apply('reject'));
  reject.dataset.action = 'reject';
  controls.appendChild(accept);
  controls.appendChild(reject);
  card.appendChild(controls);

  const impact = el('p', 'proposal-group__impact');
  card.appendChild(impact);
  const rows = el('div', 'proposal-group__rows');
  const picks = [];

  function selectedIds() {
    return picks.filter((pick) => pick.checked).map((pick) => pick.dataset.txnId);
  }

  function sync() {
    const selected = selectedIds().length;
    all.checked = selected === picks.length;
    all.indeterminate = selected > 0 && selected < picks.length;
    impact.textContent = impactCopy(picker.value, selected);
  }

  for (const proposal of group.rows) {
    const rendered = currentRow(proposal, sync);
    picks.push(rendered.pick);
    rows.appendChild(rendered.row);
  }
  card.appendChild(rows);

  all.addEventListener('change', () => {
    for (const pick of picks) pick.checked = all.checked;
    sync();
  });
  picker.addEventListener('change', sync);

  const busyNodes = [all, picker, accept, reject, ...picks];
  async function apply(action) {
    const txnIds = selectedIds();
    if (txnIds.length === 0) {
      onMessage(t('Select at least one transaction in this group.'), 'fail');
      return;
    }
    setDisabled(busyNodes, true);
    const completed = await onReview({
      action,
      txnIds,
      categoryId: action === 'accept' ? picker.value : null,
    });
    if (!completed) setDisabled(busyNodes, false);
  }

  sync();
  return card;
}

export function renderProposalGroups({ host, proposals, categories, onReview, onMessage }) {
  clear(host);
  for (const group of pendingGroups(proposals)) {
    host.appendChild(renderGroup({ group, categories, onReview, onMessage }));
  }
}

export function renderProposalHistory(host, proposals) {
  const reviewed = (proposals || []).filter((proposal) => proposal.outcome !== 'pending');
  if (reviewed.length === 0) return;
  const details = el('details', 'proposal-history');
  details.appendChild(
    el(
      'summary',
      'proposal-history__summary',
      t('Reviewed decisions ({count})', { count: reviewed.length }),
    ),
  );
  const rows = el('div', 'proposal-history__rows');
  for (const proposal of reviewed) {
    const transaction = proposal.current_transaction;
    const row = el('div', 'proposal-history__row');
    row.appendChild(
      el('span', `badge badge--${proposal.outcome === 'rejected' ? 'quiet' : 'ok'}`, proposal.outcome),
    );
    row.appendChild(
      el('span', 'proposal-history__desc', transaction?.raw_descriptor || proposal.txn_id),
    );
    row.appendChild(
      el('span', 'proposal-history__amount', transaction ? formatMinor(transaction.amount_minor) : '—'),
    );
    row.appendChild(
      el(
        'span',
        'proposal-history__category',
        proposal.applied_category_id || t('No category applied'),
      ),
    );
    rows.appendChild(row);
  }
  details.appendChild(rows);
  host.appendChild(details);
}
