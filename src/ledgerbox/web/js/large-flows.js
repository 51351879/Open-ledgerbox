// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Large money answered by anything other than a person waits here for one
// look. Confirming re-decides the line with its own current category through
// the normal override path -- so a confirmation is a real human decision: it
// outranks every rule, it teaches the merchant's template, and the line leaves
// this board because decided-by-a-person is the board's exit condition.

import {
  button,
  clear,
  el,
  fetchLargeFlows,
  formatMinor,
  isOffline,
  updateTransactionCategory,
} from './api.js';

const WHO = {
  agent: 'set by Agent',
  learned: 'set by your earlier answer',
  rule: 'set by a shipped rule',
  none: 'nobody claimed this',
};

export function createLargeFlowsPanel({ root, countsNode, onChange } = {}) {
  const api = { fetchFlows: fetchLargeFlows, confirmCategory: updateTransactionCategory };
  const body = el('div', 'large-flows');
  const intro = el(
    'p',
    'large-flows__intro',
    'Lines of at least $1,000 whose category no person has directly confirmed. '
      + 'Confirm keeps the shown category as your own decision; anything wrong, '
      + 'change it in Transactions instead.',
  );
  const list = el('div', 'large-flows__list');
  const status = el('p', 'large-flows__status');
  status.setAttribute('aria-live', 'polite');
  clear(root);
  root.appendChild(intro);
  root.appendChild(list);
  root.appendChild(status);

  function row(item) {
    const line = el('div', 'large-flows__row');
    const amount = el('span', 'large-flows__amount num money', formatMinor(item.amount_minor));
    const date = el('span', 'large-flows__date', item.date);
    const descriptor = el('span', 'large-flows__descriptor', item.raw_descriptor);
    const answer = el(
      'span',
      'large-flows__answer',
      item.category_id === null
        ? WHO.none
        : `${item.category_id} · ${WHO[item.category_decided_by] || item.category_decided_by}`,
    );
    line.appendChild(date);
    line.appendChild(amount);
    line.appendChild(descriptor);
    line.appendChild(answer);
    if (item.category_id !== null) {
      const confirm = button('btn btn--quiet btn--compact', 'Confirm', async () => {
        confirm.disabled = true;
        try {
          await api.confirmCategory(item.txn_id, item.category_id);
          status.textContent = `Confirmed ${item.category_id} for the ${formatMinor(item.amount_minor)} line.`;
          if (onChange) onChange();
          await refresh();
        } catch (error) {
          confirm.disabled = false;
          status.textContent = error.message || 'Could not confirm the category.';
        }
      });
      confirm.setAttribute(
        'aria-label',
        `Confirm ${item.category_id} for ${formatMinor(item.amount_minor)} on ${item.date}`,
      );
      line.appendChild(confirm);
    } else {
      const link = el('a', 'large-flows__classify');
      link.setAttribute('href', '#transactions');
      link.textContent = 'Classify in Transactions';
      line.appendChild(link);
    }
    return line;
  }

  async function refresh() {
    root.setAttribute('aria-busy', 'true');
    try {
      const data = await api.fetchFlows();
      clear(list);
      for (const item of data.items) {
        list.appendChild(row(item));
      }
      if (countsNode) {
        countsNode.textContent = data.items.length
          ? `${data.items.length} large line(s) awaiting one look`
            + (data.truncated ? ' (more beyond the first 200)' : '')
          : 'Every large line has a person-confirmed answer.';
      }
    } catch (error) {
      if (countsNode) {
        countsNode.textContent = isOffline(error)
          ? 'Waiting for the local Ledgerbox service.'
          : (error.message || 'Could not read large flows.');
      }
    } finally {
      root.removeAttribute('aria-busy');
    }
  }

  return { refresh, services: api, nodes: { list, status, intro } };
}
