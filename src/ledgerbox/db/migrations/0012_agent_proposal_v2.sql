-- Proposal schema v2 adds an explicit application mode.  Rebuild the three
-- connected tables because SQLite cannot widen the schema_version CHECK in
-- place.  Existing v1 audit and override provenance are copied unchanged.

DROP VIEW v_category_spend;
DROP VIEW v_cashflow_line;
DROP VIEW v_cashflow_monthly;
DROP VIEW v_transaction;
DROP VIEW v_txn_category;
DROP VIEW v_txn_transfer;

CREATE TABLE agent_proposal_run_v2 (
  id               TEXT PRIMARY KEY
                     CHECK (length(id) = 71
                            AND substr(id, 1, 7) = 'sha256:'
                            AND substr(id, 8) NOT GLOB '*[^0-9a-f]*'),
  ledger_revision  TEXT NOT NULL
                     CHECK (length(ledger_revision) = 71
                            AND substr(ledger_revision, 1, 7) = 'sha256:'
                            AND substr(ledger_revision, 8) NOT GLOB '*[^0-9a-f]*'),
  schema_version   INTEGER NOT NULL CHECK (schema_version IN (1, 2)),
  application_mode TEXT CHECK (
                     (schema_version = 1 AND application_mode IS NULL)
                     OR
                     (schema_version = 2
                      AND application_mode IN ('review_first','automatic'))
                   ),
  client           TEXT NOT NULL CHECK (client IN ('codex','claude-code','other')),
  client_version   TEXT CHECK (client_version IS NULL OR length(client_version) <= 200),
  model_reported   TEXT CHECK (model_reported IS NULL OR length(model_reported) <= 200),
  created_at       TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'open'
                     CHECK (state IN ('open','completed','dismissed'))
) STRICT;

CREATE TABLE agent_category_proposal_v2 (
  run_id                  TEXT NOT NULL
                            REFERENCES agent_proposal_run_v2(id) ON DELETE CASCADE,
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

CREATE TABLE category_override_v2 (
  txn_id      TEXT PRIMARY KEY REFERENCES txn(id),
  category_id TEXT NOT NULL REFERENCES category(id),
  created_at  TEXT NOT NULL,
  source      TEXT NOT NULL DEFAULT 'human'
                CHECK (source IN ('human', 'agent')),
  agent_run_id TEXT REFERENCES agent_proposal_run_v2(id)
                CHECK (
                  (source = 'human' AND agent_run_id IS NULL)
                  OR
                  (source = 'agent' AND agent_run_id IS NOT NULL)
                )
) STRICT;

INSERT INTO agent_proposal_run_v2
  (id, ledger_revision, schema_version, application_mode, client,
   client_version, model_reported, created_at, state)
SELECT id, ledger_revision, schema_version, NULL, client,
       client_version, model_reported, created_at, state
FROM agent_proposal_run;

INSERT INTO agent_category_proposal_v2
  (run_id, txn_id, group_id, suggested_category_id, outcome,
   applied_category_id, reviewed_at)
SELECT run_id, txn_id, group_id, suggested_category_id, outcome,
       applied_category_id, reviewed_at
FROM agent_category_proposal;

INSERT INTO category_override_v2
  (txn_id, category_id, created_at, source, agent_run_id)
SELECT txn_id, category_id, created_at, source, agent_run_id
FROM category_override;

DROP TABLE agent_category_proposal;
DROP TABLE category_override;
DROP TABLE agent_proposal_run;

ALTER TABLE agent_proposal_run_v2 RENAME TO agent_proposal_run;
ALTER TABLE agent_category_proposal_v2 RENAME TO agent_category_proposal;
ALTER TABLE category_override_v2 RENAME TO category_override;

CREATE INDEX agent_category_proposal_txn
  ON agent_category_proposal(txn_id);

CREATE INDEX agent_category_proposal_run_outcome
  ON agent_category_proposal(run_id, outcome);

CREATE VIEW v_txn_category AS
SELECT
  t.id AS txn_id,
  COALESCE(
    co.category_id,
    (SELECT p.category_id
       FROM posting p
      WHERE p.txn_id = t.id AND p.category_id IS NOT NULL
      ORDER BY p.seq
      LIMIT 1)
  ) AS category_id,
  CASE
    WHEN co.category_id IS NOT NULL AND co.source = 'agent' THEN 'agent'
    WHEN co.category_id IS NOT NULL THEN 'override'
    WHEN EXISTS (
      SELECT 1 FROM posting p WHERE p.txn_id = t.id AND p.category_id IS NOT NULL
    ) THEN 'rule'
    ELSE 'none'
  END AS decided_by
FROM txn t
LEFT JOIN category_override co ON co.txn_id = t.id;

CREATE VIEW v_txn_transfer AS
SELECT
  t.id AS txn_id,
  CASE
    WHEN co.category_id IS NULL  THEN t.is_transfer
    WHEN c.kind = 'transfer'     THEN 1
    ELSE 0
  END AS is_transfer,
  CASE
    WHEN co.category_id IS NULL THEN 'rule'
    WHEN co.source = 'agent' THEN 'agent'
    ELSE 'override'
  END AS decided_by
FROM txn t
LEFT JOIN category_override co ON co.txn_id = t.id
LEFT JOIN category c ON c.id = co.category_id;

CREATE VIEW v_transaction AS
SELECT
  t.id                         AS txn_id,
  t.date,
  t.payee,
  t.narration,
  t.flag,
  vt.is_transfer,
  vt.decided_by                AS transfer_decided_by,
  p.id                         AS posting_id,
  p.account_id,
  p.amount_minor,
  p.currency,
  vc.category_id,
  vc.decided_by                AS category_decided_by,
  ti.raw_descriptor,
  ti.occurrence_index,
  ti.natural_key,
  rr.source_file_id,
  rr.record_index,
  substr(sf.period_end, 1, 7)  AS statement_month,
  sf.period_start,
  sf.period_end
FROM txn_identity ti
JOIN      txn            t  ON t.id  = ti.txn_id
JOIN      v_txn_transfer vt ON vt.txn_id = t.id
JOIN      v_txn_category vc ON vc.txn_id = t.id
JOIN      posting        p  ON p.txn_id = t.id AND p.account_id = ti.account_id
LEFT JOIN raw_record     rr ON rr.id = ti.raw_record_id
LEFT JOIN source_file    sf ON sf.id = rr.source_file_id
WHERE t.superseded_by IS NULL;

CREATE VIEW v_cashflow_monthly AS
SELECT
  statement_month,
  COUNT(*)                                                     AS txn_count,
  SUM(CASE WHEN amount_minor > 0 THEN amount_minor ELSE 0 END) AS inflow_minor,
  SUM(CASE WHEN amount_minor < 0 THEN amount_minor ELSE 0 END) AS outflow_minor,
  SUM(amount_minor)                                            AS net_minor
FROM v_transaction
WHERE is_transfer = 0
GROUP BY statement_month;

CREATE VIEW v_cashflow_line AS
SELECT
  t.id            AS txn_id,
  t.date          AS date,
  a.kind          AS account_kind,
  vt.is_transfer  AS is_transfer,
  vc.category_id  AS category_id,
  p.amount_minor  AS amount_minor
FROM posting p
JOIN account        a  ON a.id = p.account_id
JOIN txn            t  ON t.id = p.txn_id
JOIN v_txn_transfer vt ON vt.txn_id = t.id
JOIN v_txn_category vc ON vc.txn_id = t.id
WHERE t.superseded_by IS NULL
  AND a.kind IN ('income', 'expense');

CREATE VIEW v_category_spend AS
SELECT
  category_id,
  -SUM(amount_minor)      AS spend_minor,
  COUNT(DISTINCT txn_id)  AS txn_count
FROM v_cashflow_line
WHERE is_transfer = 0
  AND account_kind = 'expense'
GROUP BY category_id;
