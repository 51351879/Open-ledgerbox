// SPDX-License-Identifier: AGPL-3.0-or-later
//
// Whether the local service is answering, decided in one place.
//
// The owner's words: "I cannot even see whether my backend is open." What the
// page did instead was say it six times. `api.js` throws one `ApiError` with
// status 0 when the fetch itself fails, and six catch blocks each printed that
// same sentence into their own panel — the figures, the two charts (two nodes
// of their own), the transaction table, its filter notice, the statement list
// and the review queue. Six copies of one fact, none of them labelled as *the*
// fact, all of them shaped like something wrong with that panel.
//
// **The claim is deliberately narrow.** This says whether the last request was
// answered — nothing about whether the ledger is sound, whether migrations are
// pending, or whether anything is queued. `/api/health` answers those and the
// status strip prints them. A 500 is an *answer*: the process is there and it
// failed at something, which is that panel's news to break and not this
// indicator's. So only `status === 0` — a transport failure, which on loopback
// means the process is gone — turns it red.
//
// **A green light has to be about now, not about the last time anybody asked.**
// A page nobody touches issues no requests, so an indicator fed only by traffic
// would sit on "connected" for as long as the tab stayed open, whatever
// happened to the server. It therefore polls, and the poll *is* the health
// refresh the page already did — one request, two readers, rather than a second
// endpoint invented for a light. The poll stops while the tab is hidden,
// because a claim about right now is worth nothing to a tab nobody is looking
// at, and it speeds up while disconnected, which is the state where somebody is
// waiting to see it come back.
//
// **Nothing here formats or renders.** The indicator is built by `main.js`
// beside the rest of the masthead; this module owns the state and the wording,
// so a panel deciding to shorten its own message and the light deciding what to
// say cannot drift apart.
//
// **It imports nothing.** `api.js` calls `report` here, and the test for which
// failure means "the process is gone" lives there, beside the `ApiError` it
// asks about. The other direction would be a cycle — this module importing the
// module that imports it — and while ES modules tolerate one, a cycle between
// the only two files every other module depends on is not a thing to tolerate
// for the sake of putting one predicate in the more comfortable place.

/** How often to re-ask while things are working, and while they are not. */
const HEARTBEAT_MS = 15000;
const RETRY_MS = 3000;

export const CONNECTION_COPY = {
  up: 'Ledgerbox online',
  down: 'Ledgerbox not answering',
  unknown: 'Checking Ledgerbox…',
  upAside: 'ledgerbox is running on this machine and answering.',
  downAside: 'The ledgerbox process on this machine is not answering. Start it again — the '
    + 'window that opened this page has the command — and this will go green by itself. '
    + 'Nothing has been lost: the ledger is a file on your disk.',
  // What a panel says instead of repeating the sentence above. Short on purpose:
  // it is a placeholder for a number, not an explanation, and the explanation is
  // one place up the page.
  panel: 'Waiting for ledgerbox.',
  retry: 'Try again now',
};

const listeners = new Set();
//: `null` until the first request settles: "we have not asked yet" is not the
//: same claim as either answer, and the light says so rather than guessing.
let up = null;

/** Called by `api.js` for every request that settles, either way. */
export function report(answered) {
  const next = Boolean(answered);
  if (up === next) {
    return;
  }
  up = next;
  for (const listener of listeners) {
    listener(up);
  }
}

/** The current answer, or `null` before anything has been asked. */
export function state() {
  return up;
}

/**
 * Subscribe. The listener fires on **transitions only**, never on every poll.
 *
 * That is what keeps the indicator out of a screen reader's way: it sits in a
 * live region so that losing the server is announced, and a poll that re-wrote
 * the same words every fifteen seconds would announce "Connected" forever. It
 * is called once immediately with the current state so a late subscriber is not
 * blank until something changes.
 */
export function watch(listener) {
  listeners.add(listener);
  listener(up);
  return () => listeners.delete(listener);
}

/**
 * Keep asking, so the light can go red on its own.
 *
 * `ask` is the page's existing health refresh; this module does not know what
 * it fetches, only that issuing it makes `report` fire. Returns a stop
 * function, which nothing calls today — the page lives as long as the tab does
 * — and exists so that this module cannot be the reason a test leaks a timer.
 */
export function heartbeat(ask) {
  let timer = null;
  let stopped = false;

  function schedule() {
    if (stopped) {
      return;
    }
    if (timer !== null) {
      clearTimeout(timer);
    }
    timer = setTimeout(tick, up === false ? RETRY_MS : HEARTBEAT_MS);
  }

  function askAndSchedule() {
    if (stopped) {
      return;
    }
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    // `ask` updates `up` through api.report().  The interval therefore has to
    // be chosen after that asynchronous request settles; choosing it here first
    // made the initial disconnection wait another connected 15-second cycle.
    Promise.resolve()
      .then(ask)
      .catch(() => {})
      .finally(schedule);
  }

  function tick() {
    timer = null;
    // A hidden tab is not looking at the light, and a browser throttles its
    // timers anyway; asking is deferred rather than skipped, so returning to
    // the tab does not wait out a full interval.
    if (document.visibilityState === 'visible') {
      askAndSchedule();
    } else {
      schedule();
    }
  }

  function onVisibilityChange() {
    if (document.visibilityState === 'visible') {
      askAndSchedule();
    }
  }

  document.addEventListener('visibilitychange', onVisibilityChange);

  schedule();
  return () => {
    stopped = true;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    document.removeEventListener('visibilitychange', onVisibilityChange);
  };
}
