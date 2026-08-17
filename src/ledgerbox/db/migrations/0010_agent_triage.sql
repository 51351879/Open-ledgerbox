-- Exhaustive remaining-coverage triage produced by a user-owned local Agent.
--
-- Submission writes these audit tables only.  A later, explicit human review
-- may write category_override in the same transaction as the item outcome.
-- Confirming a taxonomy gap or leaving a line uncertain changes no category.

CREATE TABLE agent_triage_run (
  id               TEXT PRIMARY KEY
                     CHECK (length(id) = 71
                            AND substr(id, 1, 7) = 'sha256:'
                            AND substr(id, 8) NOT GLOB '*[^0-9a-f]*'),
  ledger_revision  TEXT NOT NULL
                     CHECK (length(ledger_revision) = 71
                            AND substr(ledger_revision, 1, 7) = 'sha256:'
                            AND substr(ledger_revision, 8) NOT GLOB '*[^0-9a-f]*'),
  scope_revision   TEXT NOT NULL
                     CHECK (length(scope_revision) = 71
                            AND substr(scope_revision, 1, 7) = 'sha256:'
                            AND substr(scope_revision, 8) NOT GLOB '*[^0-9a-f]*'),
  schema_version   INTEGER NOT NULL CHECK (schema_version = 1),
  since            TEXT CHECK (since IS NULL OR length(since) = 10),
  until            TEXT CHECK (until IS NULL OR length(until) = 10),
  client           TEXT NOT NULL CHECK (client IN ('codex','claude-code','other')),
  client_version   TEXT CHECK (client_version IS NULL OR length(client_version) <= 200),
  model_reported   TEXT CHECK (model_reported IS NULL OR length(model_reported) <= 200),
  created_at       TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'open'
                     CHECK (state IN ('open','completed','dismissed'))
) STRICT;

CREATE TABLE agent_triage_item (
  run_id                  TEXT NOT NULL
                            REFERENCES agent_triage_run(id) ON DELETE CASCADE,
  txn_id                  TEXT NOT NULL REFERENCES txn(id),
  group_id                TEXT NOT NULL
                            CHECK (length(group_id) = 71
                                   AND substr(group_id, 1, 7) = 'sha256:'
                                   AND substr(group_id, 8) NOT GLOB '*[^0-9a-f]*'),
  route                   TEXT NOT NULL
                            CHECK (route IN
                                   ('possible_transfer','taxonomy_gap','uncertain')),
  reason_code             TEXT NOT NULL,
  outcome                 TEXT NOT NULL DEFAULT 'pending'
                            CHECK (outcome IN
                                   ('pending','confirmed_transfer','confirmed_taxonomy_gap',
                                    'left_uncertain','classified_existing','stale','withdrawn')),
  applied_category_id     TEXT REFERENCES category(id),
  reviewed_at             TEXT,
  PRIMARY KEY (run_id, txn_id),
  CHECK (
    (route = 'possible_transfer' AND reason_code IN
      ('payment_rail_ownership_unknown','account_movement_language',
       'debt_or_card_settlement','investment_platform_flow'))
    OR
    (route = 'taxonomy_gap' AND reason_code IN
      ('repeated_cluster_without_category','coherent_activity_missing',
       'current_category_too_broad'))
    OR
    (route = 'uncertain' AND reason_code IN
      ('descriptor_ambiguous','counterparty_role_unknown','mixed_signal',
       'insufficient_context','one_off_unresolved'))
  ),
  CHECK (
    (outcome = 'pending' AND applied_category_id IS NULL AND reviewed_at IS NULL)
    OR
    (outcome IN ('confirmed_taxonomy_gap','left_uncertain','stale')
     AND applied_category_id IS NULL AND reviewed_at IS NOT NULL)
    OR
    (outcome IN ('confirmed_transfer','classified_existing','withdrawn')
     AND applied_category_id IS NOT NULL AND reviewed_at IS NOT NULL)
  )
) STRICT;

CREATE INDEX agent_triage_item_txn
  ON agent_triage_item(txn_id);

CREATE INDEX agent_triage_item_run_outcome
  ON agent_triage_item(run_id, outcome);

CREATE INDEX agent_triage_item_run_route
  ON agent_triage_item(run_id, route);
