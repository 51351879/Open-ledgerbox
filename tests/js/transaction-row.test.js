// SPDX-License-Identifier: AGPL-3.0-or-later

import { strict as assert } from 'node:assert';
import { test } from 'node:test';

import {
  categorySourceCopy,
  transferSourceCopy,
} from '../../src/ledgerbox/web/js/transaction-row.js';

test('human, Agent, and rule category sources are not conflated', () => {
  assert.equal(categorySourceCopy('override'), 'set by you');
  assert.equal(categorySourceCopy('agent'), 'set by Agent');
  assert.equal(categorySourceCopy('rule'), 'set by a rule');
});

test('an Agent transfer is labelled without claiming the user marked it', () => {
  assert.equal(transferSourceCopy('override'), 'marked by you');
  assert.equal(transferSourceCopy('agent'), 'marked by Agent');
  assert.equal(transferSourceCopy('rule'), '');
});
