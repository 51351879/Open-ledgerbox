// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Route-specific human review controls for exhaustive remaining-coverage
// triage. The browser names checked ids; the server owns eligibility,
// atomicity, current category state and every amount shown in route summaries.

import { button, clear, el, formatMinor, option } from './api.js';
import { t } from './i18n.js';

const ROUTE_ORDER = ['possible_transfer', 'taxonomy_gap', 'uncertain'];

// A nested table, which `localized()` deliberately does not reach: it looks
// up strings one level down and would half-translate this one. The titles and
// notes are looked up where they are read.
export const ROUTE_COPY = {
  possible_transfer: {
    title: 'Possible transfer',
    note: 'Possible transfer is not a transfer decision. Choose an existing category before anything changes.',
  },
  taxonomy_gap: {
    title: 'Possible taxonomy gap',
    note: 'Confirming a gap records audit evidence only. It does not invent a category or increase coverage.',
  },
  uncertain: {
    title: 'Uncertain',
    note: 'Leaving a row uncertain keeps it unclassified. No catch-all category is applied.',
  },
};

export function pendingTriageGroups(items) {
  const groups = new Map();
  for (const item of items || []) {
    if (item.outcome !== 'pending') continue;
    if (!groups.has(item.group_id)) {
      groups.set(item.group_id, {
        groupId: item.group_id,
        route: item.route,
        reasonCode: item.reason_code,
        rows: [],
      });
    }
    groups.get(item.group_id).rows.push(item);
  }
  return [...groups.values()]
    .map((group) => ({
      ...group,
      rows: group.rows.sort((left, right) => left.txn_id.localeCompare(right.txn_id)),
    }))
    .sort((left, right) => (
      ROUTE_ORDER.indexOf(left.route) - ROUTE_ORDER.indexOf(right.route)
      || left.reasonCode.localeCompare(right.reasonCode)
      || left.groupId.localeCompare(right.groupId)
    ));
}

export function triageImpactCopy(categoryId, selected) {
  // Whole sentences per number rather than a noun swapped into one: a
  // language with no plural says both the same way, and a dictionary can
  // answer for a sentence but not for half of one.
  const lead = selected === 1
    ? t('1 selected transaction.')
    : t('{count} selected transactions.', { count: selected });
  if (!categoryId) {
    return `${lead} ${t('Choose a category before classifying.')}`;
  }
  // The category id is substituted, never looked up.
  if (categoryId === 'transfer' || categoryId === 'investment') {
    return `${lead} ${t('Applying {category} removes those amounts from the In and Out '
      + 'figures, not from the ledger.', { category: categoryId })}`;
  }
  return `${lead} ${t('Classifying sets the current category to {category}. Balances and '
    + 'statement lines do not change.', { category: categoryId })}`;
}

function categoryPicker(categories) {
  const select = el('select', 'control__field triage-group__category');
  const prompt = option('', t('Choose a category…'));
  prompt.disabled = true;
  prompt.selected = true;
  select.appendChild(prompt);
  for (const category of [...(categories || [])].sort((left, right) => (
    left.kind.localeCompare(right.kind) || left.id.localeCompare(right.id)
  ))) {
    select.appendChild(option(category.id, `${category.id} · ${category.kind}`));
  }
  select.value = '';
  select.setAttribute('aria-label', t('Category to apply to selected transactions'));
  return select;
}

function currentDecision(transaction) {
  if (!transaction) return t('Current ledger row is unavailable.');
  if (transaction.category_decided_by === 'none') return t('Still unclassified.');
  const source = transaction.category_decided_by === 'override'
    ? t('set by you')
    : transaction.category_decided_by === 'agent' ? t('set by Agent')
      : transaction.category_decided_by === 'learned' ? t('set by your earlier answer')
        : t('set by a rule');
  return `${transaction.category_id || '—'} (${source})`;
}

function itemRow(item, onSelection) {
  const transaction = item.current_transaction;
  const row = el('label', 'triage-row');
  const pick = el('input', 'triage-row__pick');
  pick.type = 'checkbox';
  pick.checked = true;
  pick.dataset.txnId = item.txn_id;
  pick.setAttribute('aria-label', t('Include transaction {id}', { id: item.txn_id }));
  pick.addEventListener('change', onSelection);
  row.appendChild(pick);

  const facts = el('span', 'triage-row__facts');
  if (transaction) {
    const line = el('span', 'triage-row__line');
    line.appendChild(el('time', 'triage-row__date', transaction.date));
    line.appendChild(el('span', 'triage-row__desc', transaction.raw_descriptor));
    line.appendChild(
      el(
        'span',
        `triage-row__amount${transaction.amount_minor < 0 ? ' triage-row__amount--out' : ''}`,
        formatMinor(transaction.amount_minor),
      ),
    );
    facts.appendChild(line);
  } else {
    facts.appendChild(el('span', 'triage-row__line', t('Current ledger row unavailable')));
  }
  facts.appendChild(el('span', 'triage-row__current', currentDecision(transaction)));
  row.appendChild(facts);
  return { row, pick };
}

function setDisabled(nodes, disabled) {
  for (const node of nodes) node.disabled = disabled;
}

function renderReasonGroup({ group, categories, onReview, onMessage }) {
  const card = el('article', 'triage-group');
  const heading = el('div', 'triage-group__head');
  heading.appendChild(el('h4', 'triage-group__reason', group.reasonCode.replaceAll('_', ' ')));
  heading.appendChild(
    el('span', 'badge badge--pending', t('{count} pending', { count: group.rows.length })),
  );
  card.appendChild(heading);

  const controls = el('div', 'triage-group__controls');
  const allLabel = el('label', 'triage-group__all');
  const all = el('input', 'triage-group__check');
  all.type = 'checkbox';
  all.checked = true;
  allLabel.appendChild(all);
  // The separator stays outside the sentence; keys are whitespace-normalised.
  allLabel.appendChild(document.createTextNode(` ${t('Include all in this reason group')}`));
  controls.appendChild(allLabel);

  const picker = categoryPicker(categories);
  controls.appendChild(picker);
  const classify = button('btn', t('Classify selected'), () => apply('classify'));
  classify.dataset.action = 'classify';
  controls.appendChild(classify);
  let routeAction = null;
  if (group.route === 'taxonomy_gap') {
    routeAction = button('btn btn--quiet', t('Confirm gap'), () => apply('confirm_gap'));
    routeAction.dataset.action = 'confirm_gap';
    controls.appendChild(routeAction);
  } else if (group.route === 'uncertain') {
    routeAction = button(
      'btn btn--quiet',
      t('Leave uncertain'),
      () => apply('leave_uncertain'),
    );
    routeAction.dataset.action = 'leave_uncertain';
    controls.appendChild(routeAction);
  }
  card.appendChild(controls);

  const impact = el('p', 'triage-group__impact');
  card.appendChild(impact);
  const rows = el('div', 'triage-group__rows');
  const picks = [];

  function selectedIds() {
    return picks.filter((pick) => pick.checked).map((pick) => pick.dataset.txnId);
  }

  function sync() {
    const selected = selectedIds().length;
    all.checked = selected === picks.length;
    all.indeterminate = selected > 0 && selected < picks.length;
    impact.textContent = triageImpactCopy(picker.value, selected);
    classify.disabled = selected === 0 || !picker.value;
    if (routeAction) routeAction.disabled = selected === 0;
  }

  for (const item of group.rows) {
    const rendered = itemRow(item, sync);
    picks.push(rendered.pick);
    rows.appendChild(rendered.row);
  }
  card.appendChild(rows);

  all.addEventListener('change', () => {
    for (const pick of picks) pick.checked = all.checked;
    sync();
  });
  picker.addEventListener('change', sync);

  const busyNodes = [all, picker, classify, ...picks];
  if (routeAction) busyNodes.push(routeAction);
  async function apply(action) {
    const txnIds = selectedIds();
    if (txnIds.length === 0) {
      onMessage(t('Select at least one transaction in this reason group.'), 'fail');
      return;
    }
    if (action === 'classify' && !picker.value) {
      onMessage(t('Choose a category before classifying selected transactions.'), 'fail');
      return;
    }
    setDisabled(busyNodes, true);
    const completed = await onReview({
      action,
      txnIds,
      categoryId: action === 'classify' ? picker.value : null,
    });
    if (!completed) {
      setDisabled(busyNodes, false);
      sync();
    }
  }

  sync();
  return card;
}

export function renderTriageGroups({ host, run, categories, onReview, onMessage }) {
  clear(host);
  const groups = pendingTriageGroups(run.items);
  const summaries = new Map((run.route_summaries || []).map((row) => [row.route, row]));
  for (const routeName of ROUTE_ORDER) {
    const routeGroups = groups.filter((group) => group.route === routeName);
    if (routeGroups.length === 0) continue;
    const copy = ROUTE_COPY[routeName];
    const summary = summaries.get(routeName) || {};
    const section = el('section', `triage-route triage-route--${routeName}`);
    const head = el('div', 'triage-route__head');
    head.appendChild(el('h3', 'triage-route__title', t(copy.title)));
    head.appendChild(
      el('span', 'badge badge--pending', t('{count} pending', { count: summary.pending || 0 })),
    );
    section.appendChild(head);
    section.appendChild(el('p', 'triage-route__note', t(copy.note)));
    section.appendChild(
      el(
        'p',
        'triage-route__impact',
        t('{count} item(s) · current bank-line total {amount} (server-derived)', {
          count: summary.item_count || 0,
          amount: formatMinor(summary.bank_amount_minor || 0),
        }),
      ),
    );
    for (const group of routeGroups) {
      section.appendChild(renderReasonGroup({ group, categories, onReview, onMessage }));
    }
    host.appendChild(section);
  }
}

export function renderTriageHistory(host, items) {
  const reviewed = (items || []).filter((item) => item.outcome !== 'pending');
  if (reviewed.length === 0) return;
  const details = el('details', 'triage-history');
  details.appendChild(
    el(
      'summary',
      'triage-history__summary',
      t('Reviewed triage decisions ({count})', { count: reviewed.length }),
    ),
  );
  const rows = el('div', 'triage-history__rows');
  for (const item of reviewed) {
    const transaction = item.current_transaction;
    const row = el('div', 'triage-history__row');
    row.appendChild(el('span', 'badge badge--quiet', item.outcome.replaceAll('_', ' ')));
    row.appendChild(el('span', 'triage-history__desc', transaction?.raw_descriptor || item.txn_id));
    row.appendChild(
      el('span', 'triage-history__amount', transaction ? formatMinor(transaction.amount_minor) : '—'),
    );
    row.appendChild(
      el(
        'span',
        'triage-history__category',
        item.applied_category_id || (
          item.outcome === 'confirmed_taxonomy_gap' ? t('Gap recorded; still unclassified')
            : t('Still unclassified')
        ),
      ),
    );
    rows.appendChild(row);
  }
  details.appendChild(rows);
  host.appendChild(details);
}
