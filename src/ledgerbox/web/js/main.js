// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Entry point: finds the nodes, wires the panels together, and renders
// everything the server says about itself.
//
// All of it is read from the server rather than accumulated in the page. A
// count the browser incremented itself is a count that drifts, and drift in the
// one number that says "something is unbooked" is the failure this whole
// project exists to prevent.
//
// The page is ranked, not filtered. The status line comes first and says only
// what is wrong; the four figures come next because they are the answer; the
// two pictures follow because they are those same figures divided two ways, and
// the "Out" one of them breaks down is the figure directly above it; the
// transaction table is under both, because with a pager on it, it shows one page of
// lines of a filtered query -- a sample, where the figures and the charts are
// the whole booked ledger, and the complete statement outranks the partial one;
// the statement list is where each of those lines came from; the review queue
// follows it because the queue makes its claims about the rows in that list;
// row counts, schema version and the data directory are diagnostics and sit
// under a disclosure at the bottom. Nothing is dropped -- a status page that
// quietly stops mentioning something is a status page you cannot trust.
//
// Adding a statement is a disclosure in the header rather than the first panel
// on the page. It was the biggest thing here and it is the thing you do least
// often; the drop target it opens is unchanged, and dropping a file anywhere on
// the page still works whether it is open or shut.

import { clear, el, fetchHealth, isOffline } from './api.js';
import { heartbeat, watch } from './connection.js';
import {
  renderConnection,
  renderDiagnostics,
  renderStatus,
  statusKey,
} from './health-strip.js';
import { createAnalyticsPanel } from './analytics.js';
import { createAdvicePanel } from './advice.js';
import { createAgentSidebar } from './agent-center.js';
import { createLargeFlowsPanel } from './large-flows.js';
import { createProposalPanel } from './agent-proposals.js';
import { createTriagePanel } from './triage.js';
import { createDateRange } from './date-range.js';
import { createReviewQueue } from './review.js';
import { createStatementList } from './statements.js';
import { createTransactionsPanel } from './transactions.js';
import { createUploader } from './upload.js';
import { applyStoredLanguage, wireLanguageControl } from './language.js';

function node(id) {
  return document.getElementById(id);
}

// The four figures moved to the analytics panel, which owns the request that
// produces them. They are narrowed by the date range and `/api/health` is not:
// health answers "is this ledger sound", which no window narrows. Leaving them
// here would have put a filtered picture beside an unfiltered figure under one
// heading, and the page states a relationship between the two.
//
// Everything the page says about /api/health renders in health-strip.js.

function boot() {
  // Language first, before a single panel renders. The static markup is
  // English and the dictionary rewrites it in place; doing this after the
  // panels would translate the page in front of the reader. With nothing
  // stored this is a no-op and every string below stays exactly as written,
  // which is why the existing tests still pin English sentences.
  applyStoredLanguage();
  wireLanguageControl(node('locale'));

  const ledgerNode = node('ledger');
  const statusNode = node('status');
  const diagNode = node('diagnostics-body');
  const queueNode = node('queue');
  const resultsNode = node('results');
  const statementsNode = node('statements');
  const transactionsNode = node('transactions');
  const analyticsNode = node('analytics');
  const proposalNode = node('agent-proposals');
  const agentSidebarNode = node('workspace-sidebar');
  const triageNode = node('agent-triage');
  if (!ledgerNode || !statusNode || !diagNode || !queueNode || !resultsNode || !statementsNode
      || !transactionsNode || !analyticsNode || !agentSidebarNode || !proposalNode || !triageNode) {
    return;
  }

  // What the strip is showing, so a poll that changes nothing writes nothing.
  let statusShowing = null;

  async function refreshHealth() {
    try {
      const health = await fetchHealth();
      const key = statusKey(health);
      if (key !== statusShowing) {
        statusShowing = key;
        renderStatus(statusNode, health);
      }
      renderDiagnostics(diagNode, health);
    } catch (error) {
      statusShowing = null;
      clear(statusNode);
      // A server that is not answering is said once, by the light in the
      // header. This strip is for what is wrong with the *ledger*, and while
      // nothing is answering there is nothing known about the ledger to say.
      if (!isOffline(error)) {
        statusNode.appendChild(
          el('span', 'status__error', error.message || 'The local service did not answer.'),
        );
      }
    }
  }

  // One control, three readers, declared before all of them. The figures, both
  // charts and the table are re-read against the same window rather than one of
  // them being adjusted: the page states that the wedges add up to the Out and
  // that the months add up to the figures, and those sentences hold only while
  // every panel is answering about the same rows.
  //
  // `onRangeChange` is a function declaration and therefore hoisted, so it may
  // name panels built below it; nothing calls it until somebody moves the
  // control, which is long after boot.
  function onRangeChange() {
    analytics.refresh();
    transactions.refresh();
  }

  const range = createDateRange({ root: document, onChange: onRangeChange });

  const queue = createReviewQueue({
    container: queueNode,
    countsNode: node('queue-counts'),
    onChange: refreshHealth,
  });

  let transactions = null;
  const agentSidebar = createAgentSidebar({
    root: agentSidebarNode,
    onNeedsClassification: () => {
      window.location.hash = 'transactions';
      transactions?.showUnclassified();
    },
  });

  // Declared before the transactions panel because that panel's onChange has to
  // name it. It re-reads on its own request and derives nothing from the others.
  //
  // It owns the four figures as well as the two charts, because one request
  // produces all three and the date range narrows all three together.
  const analytics = createAnalyticsPanel({
    root: analyticsNode,
    figuresNode: ledgerNode,
    span: () => range.span(),
    // The planning notes quote the net of whatever window is showing. Handed
    // the figure this request produced rather than fetching their own, so the
    // two cannot describe different windows.
    onData: () => advice.refresh(),
  });

  // Recording a category moves three things outside this panel and none of them
  // is re-derived here. Naming the `transfer` category takes the line out of the
  // In and Out figures at the top (and putting it back returns it); the
  // `category_override` row count under Diagnostics changes either way; and both
  // pictures under the table move, because a transfer leaves the monthly bars
  // and the breakdown together, and any other name moves the line from one
  // wedge to another. All three are read again from the server rather than
  // adjusted in the browser.
  //
  // The statement list and the review queue are deliberately not re-read: a
  // statement's txn_count counts identity rows, which an override does not
  // touch, and no check in the queue is about a category. Refreshing them would
  // be motion standing in for correctness.
  transactions = createTransactionsPanel({
    root: transactionsNode,
    countsNode: node('txn-counts'),
    span: () => range.span(),
    onChange: () => {
      refreshHealth();
      analytics.refresh();
      proposals.refresh();
      triage.refresh();
      agentSidebar.refresh();
      largeFlows.refresh();
    },
  });

  // Confirming keeps the shown category but changes who decided it, which is
  // this board's own exit condition and the transactions table's provenance
  // column; the money itself does not move, so the figures are left alone.
  const largeFlowsNode = node('large-flows');
  const largeFlows = largeFlowsNode
    ? createLargeFlowsPanel({
      root: largeFlowsNode,
      countsNode: node('large-flows-counts'),
      onChange: () => {
        transactions.refresh();
        agentSidebar.refresh();
      },
    })
    : { refresh: () => {} };

  // Review-first submissions use this panel as the explicit human bridge.
  // Automatic submissions are already effective but stay visible here so the
  // user can inspect or withdraw the Agent-attributed run.
  const proposals = createProposalPanel({
    root: proposalNode,
    onChange: () => {
      transactions.refresh();
      analytics.refresh();
      triage.refresh();
      agentSidebar.refresh();
      largeFlows.refresh();
      refreshHealth();
    },
  });

  // Triage submission is audit-only. Only an explicit category choice here
  // reaches the existing override writer; gap and uncertain outcomes remain
  // unclassified and therefore do not move coverage.
  const triage = createTriagePanel({
    root: triageNode,
    onChange: () => {
      transactions.refresh();
      proposals.refresh();
      analytics.refresh();
      agentSidebar.refresh();
      refreshHealth();
    },
  });

  // Deleting a statement moves the four figures, the queue depth, the row
  // counts, the transactions it booked and both pictures under them all at
  // once — a whole statement month can leave the bar chart — so all of them are
  // re-read from the server rather than adjusted here. See the header of this
  // file for why.
  const statements = createStatementList({
    container: statementsNode,
    countsNode: node('statements-counts'),
    onChange: () => {
      queue.refresh();
      transactions.refresh();
      proposals.refresh();
      triage.refresh();
      analytics.refresh();
      agentSidebar.refresh();
      largeFlows.refresh();
      refreshHealth();
    },
  });

  const adviceNode = node('advice');
  const advice = adviceNode
    ? createAdvicePanel({
      root: adviceNode,
      net: () => analytics.net(),
    })
    : { refresh: () => {} };

  function refreshAll() {
    statements.refresh();
    transactions.refresh();
    proposals.refresh();
    triage.refresh();
    agentSidebar.refresh();
    analytics.refresh();
    largeFlows.refresh();
    queue.refresh();
    refreshHealth();
  }

  // The one line that makes the disclosure honest: files can arrive by a drop
  // anywhere on the page, and `upload.js` renders their cards into a panel that
  // is now inside a `<details>`. Opening it on the way in means a drop always
  // shows its result. Nothing here gates the upload -- if this node is missing
  // the files still go.
  const adder = node('adder');

  createUploader({
    results: resultsNode,
    fileInput: node('file-input'),
    chooseButton: node('choose'),
    clearButton: node('clear-results'),
    onFiles: () => {
      if (adder) {
        adder.open = true;
      }
    },
    onSettled: refreshAll,
  });

  const refreshButton = node('refresh');
  if (refreshButton) {
    refreshButton.addEventListener('click', refreshAll);
  }

  // The light, and the thing that lets it go red without anybody clicking.
  //
  // The heartbeat re-issues the health request rather than pinging something of
  // its own: one request, two readers. A second endpoint invented for a status
  // light would be a second answer to "is this service up", and the two would
  // eventually differ on the one question the light exists to settle.
  const linkNode = node('link');
  if (linkNode) {
    watch((up) => renderConnection(linkNode, up));
  }
  heartbeat(() => Promise.all([refreshHealth(), agentSidebar.refresh()]));

  refreshAll();
}

// A module script is deferred, so the document is parsed by the time this runs.
boot();
