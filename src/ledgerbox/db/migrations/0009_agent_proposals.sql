-- Proposal-only BYOA classification.
--
-- These two tables are local audit data.  Ingest never writes them, a rebuild
-- from archive/ cannot reproduce them, and submitting a proposal never writes
-- category_override.  Only an explicit human review may bridge from a pending
-- proposal to the existing override writer, in the same transaction that
-- records the outcome.

CREATE TABLE agent_proposal_run (
  id               TEXT PRIMARY KEY
                     CHECK (length(id) = 71
                            AND substr(id, 1, 7) = 'sha256:'
                            AND substr(id, 8) NOT GLOB '*[^0-9a-f]*'),
  ledger_revision  TEXT NOT NULL
                     CHECK (length(ledger_revision) = 71
                            AND substr(ledger_revision, 1, 7) = 'sha256:'
                            AND substr(ledger_revision, 8) NOT GLOB '*[^0-9a-f]*'),
  schema_version   INTEGER NOT NULL CHECK (schema_version = 1),
  client           TEXT NOT NULL CHECK (client IN ('codex','claude-code','other')),
  client_version   TEXT CHECK (client_version IS NULL OR length(client_version) <= 200),
  model_reported   TEXT CHECK (model_reported IS NULL OR length(model_reported) <= 200),
  created_at       TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'open'
                     CHECK (state IN ('open','completed','dismissed'))
) STRICT;

CREATE TABLE agent_category_proposal (
  run_id                  TEXT NOT NULL
                            REFERENCES agent_proposal_run(id) ON DELETE CASCADE,
  txn_id                  TEXT NOT NULL REFERENCES txn(id),
  group_id                TEXT NOT NULL
                            CHECK (length(group_id) = 71
                                   AND substr(group_id, 1, 7) = 'sha256:'
                                   AND substr(group_id, 8) NOT GLOB '*[^0-9a-f]*'),
  suggested_category_id   TEXT NOT NULL REFERENCES category(id),
  outcome                 TEXT NOT NULL DEFAULT 'pending'
                            CHECK (outcome IN
                                   ('pending','accepted','edited','rejected','withdrawn')),
  applied_category_id     TEXT REFERENCES category(id),
  reviewed_at             TEXT,
  PRIMARY KEY (run_id, txn_id),
  CHECK (
    (outcome = 'pending' AND applied_category_id IS NULL AND reviewed_at IS NULL)
    OR
    (outcome = 'rejected' AND applied_category_id IS NULL AND reviewed_at IS NOT NULL)
    OR
    (outcome IN ('accepted','edited','withdrawn')
     AND applied_category_id IS NOT NULL AND reviewed_at IS NOT NULL)
  )
) STRICT;

CREATE INDEX agent_category_proposal_txn
  ON agent_category_proposal(txn_id);

CREATE INDEX agent_category_proposal_run_outcome
  ON agent_category_proposal(run_id, outcome);
