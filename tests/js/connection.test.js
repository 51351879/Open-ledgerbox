// SPDX-License-Identifier: AGPL-3.0-or-later
//
// The heartbeat's interval is chosen after the request settles.  A browser
// acceptance round caught the opposite ordering: the first failed poll turned
// the light red asynchronously, but the next poll had already been scheduled
// with the old 15-second connected interval instead of the 3-second retry.

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import { heartbeat, report } from '../../src/ledgerbox/web/js/connection.js';

test('a failed asynchronous heartbeat schedules the disconnected retry interval', async () => {
  const oldDocument = globalThis.document;
  const oldSetTimeout = globalThis.setTimeout;
  const oldClearTimeout = globalThis.clearTimeout;
  const timers = new Map();
  let nextId = 1;

  globalThis.document = {
    visibilityState: 'visible',
    addEventListener: () => {},
    removeEventListener: () => {},
  };
  globalThis.setTimeout = (callback, delay) => {
    const id = nextId;
    nextId += 1;
    timers.set(id, { callback, delay });
    return id;
  };
  globalThis.clearTimeout = (id) => timers.delete(id);

  let stop;
  try {
    report(true);
    stop = heartbeat(async () => {
      await Promise.resolve();
      report(false);
    });

    const first = [...timers.entries()][0];
    assert.equal(first[1].delay, 15000);
    timers.delete(first[0]);
    first[1].callback();
    // Let the async ask, its catch, and its finally all drain. `setImmediate`
    // is deliberately left real while only timeout scheduling is under test.
    await new Promise((resolve) => setImmediate(resolve));

    assert.equal(timers.size, 1);
    assert.equal([...timers.values()][0].delay, 3000);
  } finally {
    if (stop) {
      stop();
    }
    globalThis.document = oldDocument;
    globalThis.setTimeout = oldSetTimeout;
    globalThis.clearTimeout = oldClearTimeout;
  }
});
