// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Taking one statement back out: what it would cost, and the two steps that do
// it. Split out of `statements.js` when that file crossed the 400-line signal
// `docs/EXECUTION_PLAN.md` §1.3 puts there — and the seam was already in the
// file: one half answers "which statements do I have", this half answers "what
// happens if this one goes". They share nothing but the DOM helpers.
//
// The button and its two-step prompt moved here on the second pass, when the
// list grew a search box and a pager and crossed the line again. They belong on
// this side of the same seam and always did: `propose` renders `planBody` three
// lines below where `planBody` is defined, the refusal wording is the plan's
// wording, and the only thing `statements.js` needed back was one node to hang
// on a row. Its `COPY` said as much before the move — "the ones the confirmation
// prompt uses live next to the code that renders it" — while the prompt itself
// lived elsewhere.
//
// Every figure here was measured rather than predicted. The server performed the
// deletion inside a transaction, ran the checks against the result and rolled
// back, so `checks_after` holds real answers from the real code — six of the
// nine, with `checks_note` naming the three that were not simulated and why.
// **That note is rendered verbatim.** Rewording it would be this page making a
// claim about checks nobody ran.

import {
  ApiError, button, clear, deleteStatement, el,
  fetchDeletionPlan, formatMinor, join,
} from './api.js';

export const PLAN_COPY = {
  losses: 'Decisions, not documents. `archive/` never held them, so re-ingesting the same '
    + 'bytes brings the transactions back and not these.',
  // Said, not left out. The CLI and the 409 both state this in both directions;
  // this screen did not, and this screen is the only one of the three a person
  // using the browser ever sees — `api.js` always sends the acknowledgement, so
  // the 409 sentence is unreachable here. The argument for saying it was made in
  // a commit message and had not landed where the button is.
  noLosses: 'Nothing here is a decision or review history — no hand-set category, Agent '
    + 'proposal, resolved or dismissed review item — so re-ingesting the same file would '
    + 'restore all of it.',
  noFailure: 'No simulated check fails afterwards.',
  balanceGone: ' — afterwards no posting of an account you own is left, so the ledger has no '
    + 'balance to report rather than a balance of zero.',
  midRun: 'A month taken out of the middle of a run leaves the balances printed after it with '
    + 'nothing to replay from. That is the ledger reporting a real hole, not a fault in the '
    + 'deletion.',
  archiveGone: 'The archived PDF is already missing from disk.',
  refused: 'This statement cannot be deleted.',
  vanished: 'That statement is no longer in the ledger.',
  planFailed: 'The deletion could not be prepared.',
  deleteFailed: 'The statement could not be deleted.',
};

// The decision/audit fields are deliberately absent: all
// are reported on their own, above the rest, because they are the row types no
// rebuild brings back.
const IMPACT_ROWS = [
  ['postings', 'posting(s)'],
  ['txn_identities', 'transaction identity row(s)'],
  ['raw_records', 'raw record(s)'],
  ['review_items', 'review queue item(s)'],
  ['balance_assertions_removed', 'balance assertion(s) removed'],
  ['balance_assertions_reassigned', 'balance assertion(s) kept, provenance moved'],
];

// The counts a rebuild does not return. They are decisions or audit history;
// `archive/` holds documents. This listed only the first, as "the only" one, until an acceptance
// run dismissed a review item, deleted the statement, re-ingested the same bytes
// and saw the dismissal come back open.
const LOSSES = [
  ['category_overrides', 'hand-made category decision(s)'],
  ['review_items_decided', 'resolved or dismissed review item(s)'],
  ['agent_proposals', 'Agent proposal outcome(s)'],
  ['agent_proposal_runs', 'Agent proposal run(s) becoming empty'],
  ['agent_triage_items', 'Agent triage outcome(s)'],
  ['agent_triage_runs', 'Agent triage run(s) becoming empty'],
];

/** The non-zero rows of `impact`, or null when every one of them is zero. */
function impactNode(impact) {
  const list = el('ul', 'plan__items');
  for (const [key, label] of IMPACT_ROWS) {
    if ((impact[key] || 0) > 0) {
      list.appendChild(join(el('li'), el('span', 'num', String(impact[key])), ` ${label}`));
    }
  }
  return list.firstChild ? list : null;
}

function lossesNode(impact) {
  const parts = LOSSES
    .filter(([key]) => (impact[key] || 0) > 0)
    .map(([key, label]) => `${impact[key]} ${label}`);
  if (parts.length === 0) {
    return el('p', 'notice__text muted', PLAN_COPY.noLosses);
  }
  const lead = el('strong', '', `${parts.join(' and ')} go with it. `);
  return join(el('p', 'plan__loss'), lead, PLAN_COPY.losses);
}

function checksNode(plan) {
  const wrap = el('div', 'plan__checks');
  const failing = (plan.checks_after || []).filter((check) => check.status === 'fail');
  if (failing.length === 0) {
    wrap.appendChild(el('p', 'notice__text', PLAN_COPY.noFailure));
  } else {
    wrap.appendChild(el('p', 'notice__text', `${failing.length} check(s) fail afterwards:`));
    const list = el('ul', 'plan__items');
    for (const check of failing) {
      // Check messages are built from statement text: third-party influenced, and
      // textContent is the whole defence.
      const name = el('code', 'plan__check', check.check_id || 'unknown check');
      list.appendChild(join(el('li'), name, ' — ', el('span', '', check.message || '')));
    }
    wrap.appendChild(list);
    // Removing a month from the middle of a run really does make the balances printed after
    // it irreproducible. The operator meets that here, not in a red `verify` an hour later.
    wrap.appendChild(el('p', 'notice__text muted', PLAN_COPY.midRun));
  }
  if (plan.checks_note) {
    // Verbatim: it says which checks were not simulated and why, and any rewording
    // would be a claim about checks nobody ran.
    wrap.appendChild(el('p', 'notice__text muted', plan.checks_note));
  }
  return wrap;
}

/**
 * A balance the server sent as `null`, said rather than printed as zero.
 *
 * Reachable here in one step: forgetting the last statement leaves no posting
 * of an account you own, `sync_opening_entry` takes the opening entry away with
 * the assertion it was derived from, and the ledger then has nothing to say
 * about what the account holds. "$0.00" would be this panel answering that
 * question on its behalf, in the middle of a confirmation whose whole job is to
 * say what is about to be lost.
 */
function balanceNode(minor) {
  // `num money` is the class that sets tabular figures and the monospace face.
  // Applying it to words rendered "not known" as though it were an amount, in
  // the middle of a sentence whose other half is one. The words are plain text
  // and take the muted class instead, so the two cannot be misread for each
  // other at a glance.
  return typeof minor === 'number'
    ? el('span', 'num money', formatMinor(minor))
    : el('span', 'muted', 'not known');
}

function totalsNode(plan) {
  const before = plan.totals_before;
  const after = plan.totals_after;
  if (!before || !after) {
    return null;
  }
  const node = join(el('p', 'plan__totals'),
    'Balance ', balanceNode(before.balance_minor),
    ' → ', balanceNode(after.balance_minor),
    `, ledger transactions ${before.txn_count} → ${after.txn_count}`);
  // Said rather than left as two words. Deleting the last statement takes the
  // last own-account posting with it, and "not known" on its own reads like a
  // failure to look rather than like the absence of anything to look at.
  if (typeof after.balance_minor !== 'number') {
    node.appendChild(el('span', '', PLAN_COPY.balanceGone));
  }
  return node;
}

/**
 * The body of the confirmation: what goes, what cannot come back, what the
 * figures become, and which checks would fail. Returns an array of nodes for
 * the caller to place, so this module never decides where the prompt lives or
 * what buttons sit under it.
 */
export function planBody(plan) {
  const lead = `Delete ${plan.statement_month || 'this statement'}? `
    + `${plan.impact.txns} transaction(s) leave the ledger.`;
  const nodes = [el('p', 'notice__text', lead)];
  for (const part of [impactNode(plan.impact), lossesNode(plan.impact), totalsNode(plan)]) {
    if (part) {
      nodes.push(part);
    }
  }
  nodes.push(checksNode(plan));
  return nodes;
}

/**
 * The Delete button for one row, and everything it opens.
 *
 * Two steps and it stays two steps: the first asks the server what the deletion
 * would remove and which checks would fail afterwards, only the second writes.
 * Nothing is counted here — after a delete the caller re-reads every panel,
 * because a count the browser maintained is a count that drifts.
 *
 * `hooks.gone(message)` is called when the row turned out not to exist, and
 * `hooks.done(result)` after a successful write. Both destroy the node this
 * returns, which is why neither message is rendered into it.
 */
export function deleteControl(statement, hooks) {
  const wrap = el('div', 'stmt__foot');
  const actions = el('div', 'stmt__actions');
  const notice = el('div', 'notice');
  notice.hidden = true;

  function setBusy(busy) {
    for (const node of wrap.querySelectorAll('button')) {
      node.disabled = busy;
    }
  }

  function reset() {
    clear(notice);
    notice.hidden = true;
    notice.className = 'notice';
  }

  function open(kind) {
    clear(notice);
    notice.className = `notice notice--${kind}`;
    notice.hidden = false;
    return notice;
  }

  function fail(message) {
    open('fail').appendChild(el('p', 'notice__text', message));
  }

  function buttonRow(box, ...nodes) {
    box.appendChild(join(el('div', 'notice__actions'), ...nodes));
  }

  // A plan that came back `allowed: false`, or a 422 from the write. No confirm
  // button is offered for either: the answer does not change with anything the
  // person could send next, and a button that cannot work is a lie.
  function refuse(refusals, message) {
    const box = open('fail');
    box.appendChild(el('p', 'notice__text', PLAN_COPY.refused));
    const reasons = refusals || [];
    if (reasons.length > 0) {
      const list = el('ul', 'plan__items');
      for (const reason of reasons) {
        list.appendChild(el('li', '', reason));
      }
      box.appendChild(list);
    } else if (message) {
      box.appendChild(el('p', 'notice__text', message));
    }
    buttonRow(box, button('btn btn--quiet', 'Close', reset));
  }

  function propose(plan, serverNote) {
    const box = open('confirm');
    if (serverNote) {
      box.appendChild(el('p', 'notice__text', serverNote));
    }
    for (const node of planBody(plan)) {
      box.appendChild(node);
    }
    if (plan.archive_file_present === false) {
      box.appendChild(el('p', 'notice__text muted', PLAN_COPY.archiveGone));
    }
    buttonRow(box, button('btn btn--danger', 'Delete now', remove),
      button('btn btn--quiet', 'Keep it', reset));
  }

  function handle(error, fallback) {
    const status = error instanceof ApiError ? error.status : 0;
    if (status === 404) {
      // Gone already — another tab, or the CLI. The list is re-read, so the row
      // this notice is attached to is about to be destroyed; the message goes to
      // the standing note above the list instead of vanishing with it.
      hooks.gone(error.message || PLAN_COPY.vanished);
    } else if (status === 422) {
      refuse(null, error.message);
    } else {
      fail(error.message || fallback);
    }
  }

  /** Step one. Reads what would happen. Writes nothing. */
  async function ask(serverNote) {
    setBusy(true);
    try {
      const plan = await fetchDeletionPlan(statement.source_file_id);
      setBusy(false);
      if (plan.allowed === false) {
        refuse(plan.refusals, null);
      } else {
        propose(plan, serverNote);
      }
    } catch (error) {
      setBusy(false);
      handle(error, PLAN_COPY.planFailed);
    }
  }

  /** Step two, and the only step that writes. */
  async function remove() {
    setBusy(true);
    try {
      const result = await deleteStatement(statement.source_file_id);
      reset();
      hooks.done(result);
    } catch (error) {
      setBusy(false);
      // A 409 is the server asking the question again, not a failure: the plan is
      // re-read and the confirmation shown again with the server's own sentence at the
      // top of it. The page never retries the write by itself.
      if (error instanceof ApiError && error.status === 409) {
        ask(error.message);
      } else {
        handle(error, PLAN_COPY.deleteFailed);
      }
    }
  }

  actions.appendChild(button('btn btn--quiet', 'Delete…', () => ask(null)));
  wrap.appendChild(actions);
  wrap.appendChild(notice);
  return wrap;
}
