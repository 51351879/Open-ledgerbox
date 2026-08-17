-- GENERATED FILE — do not edit.
--
-- Snapshot of the schema produced by applying every migration in
-- src/ledgerbox/db/migrations/ in order. Regenerate with:
--
--     python tools/dump_schema.py
--
-- The authoritative definitions are the migrations; this file exists so the
-- current shape is reviewable in one place and diffable in a pull request.

CREATE TABLE account (
  id              TEXT PRIMARY KEY,
  parent_id       TEXT REFERENCES account(id),
  name            TEXT NOT NULL,       -- 'Assets:Chase:Checking'
  kind            TEXT NOT NULL CHECK (kind IN
                    ('asset','liability','equity','income','expense')),
  subtype         TEXT,                -- checking | credit_card | brokerage
  currency        TEXT NOT NULL REFERENCES commodity(id),
  booking_method  TEXT NOT NULL DEFAULT 'FIFO'
                    CHECK (booking_method IN
                      ('STRICT','FIFO','LIFO','AVERAGE','NONE')),
  is_own_account  INTEGER NOT NULL DEFAULT 1,  -- basis for internal-transfer detection
  institution     TEXT, mask TEXT,
  opened_on TEXT, closed_on TEXT
) STRICT;

CREATE TABLE "agent_category_proposal" (
  run_id                  TEXT NOT NULL
                            REFERENCES "agent_proposal_run"(id) ON DELETE CASCADE,
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

CREATE TABLE "agent_classification_job" (
  id                     TEXT PRIMARY KEY
                               CHECK (length(id) = 36 AND substr(id, 1, 4) = 'job-'),
  trigger_source_file_id TEXT REFERENCES source_file(id) ON DELETE CASCADE,
  trigger_kind           TEXT NOT NULL DEFAULT 'import'
                               CHECK (trigger_kind IN ('import','manual','followup')),
  round_index            INTEGER NOT NULL DEFAULT 1
                               CHECK (round_index BETWEEN 1 AND 99),
  client                 TEXT NOT NULL CHECK (client IN ('codex','claude-code')),
  application_mode       TEXT NOT NULL CHECK (application_mode IN ('review_first','automatic')),
  state                  TEXT NOT NULL DEFAULT 'queued'
                               CHECK (state IN ('queued','running','completed','partial','failed')),
  candidate_count        INTEGER CHECK (candidate_count IS NULL OR candidate_count >= 0),
  submitted_count        INTEGER CHECK (submitted_count IS NULL OR submitted_count >= 0),
  applied_count          INTEGER CHECK (applied_count IS NULL OR applied_count >= 0),
  omitted_count          INTEGER CHECK (omitted_count IS NULL OR omitted_count >= 0),
  error_code             TEXT CHECK (
                               error_code IS NULL
                               OR (length(error_code) BETWEEN 1 AND 64
                                   AND error_code NOT GLOB '*[^a-z0-9_]*')
                             ),
  client_outcome         TEXT CHECK (
                               client_outcome IS NULL
                               OR client_outcome IN
                                  ('exited','timeout','not_found','spawn_failed','workspace_missing')
                             ),
  client_exit_code       INTEGER,
  client_log_excerpt     TEXT,
  queued_at              TEXT NOT NULL,
  started_at             TEXT,
  finished_at            TEXT,
  session_id             TEXT REFERENCES agent_local_session(id) ON DELETE SET NULL,
  proposal_run_id        TEXT REFERENCES agent_proposal_run(id) ON DELETE SET NULL,
  -- An import job is the one kind that names a file, and it is the only kind
  -- that may: a job asked for by a person is not about any one statement.
  CHECK ((trigger_kind = 'import') = (trigger_source_file_id IS NOT NULL)),
  CHECK (
    (state = 'queued'
      AND started_at IS NULL AND finished_at IS NULL
      AND candidate_count IS NULL AND submitted_count IS NULL
      AND applied_count IS NULL AND omitted_count IS NULL AND error_code IS NULL)
    OR
    (state = 'running'
      AND started_at IS NOT NULL AND finished_at IS NULL
      AND candidate_count IS NULL AND submitted_count IS NULL
      AND applied_count IS NULL AND omitted_count IS NULL AND error_code IS NULL)
    OR
    (state IN ('completed','partial')
      AND started_at IS NOT NULL AND finished_at IS NOT NULL
      AND candidate_count IS NOT NULL AND submitted_count IS NOT NULL
      AND applied_count IS NOT NULL AND omitted_count IS NOT NULL
      AND submitted_count + omitted_count = candidate_count
      AND applied_count <= submitted_count
      AND error_code IS NULL
      AND ((state = 'completed' AND omitted_count = 0)
           OR (state = 'partial' AND omitted_count > 0)))
    OR
    (state = 'failed'
      AND started_at IS NOT NULL AND finished_at IS NOT NULL
      AND candidate_count IS NOT NULL AND submitted_count = 0
      AND applied_count = 0 AND omitted_count = candidate_count
      AND error_code IS NOT NULL)
  )
) STRICT;

CREATE TABLE agent_local_policy (
  id                         INTEGER PRIMARY KEY CHECK (id = 1),
  selected_client            TEXT CHECK (selected_client IN ('codex', 'claude-code')),
  application_mode           TEXT NOT NULL DEFAULT 'automatic'
                                   CHECK (application_mode IN ('review_first', 'automatic')),
  enabled                    INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
  auto_classify_new_imports  INTEGER NOT NULL DEFAULT 1 CHECK (auto_classify_new_imports IN (0, 1)),
  updated_at                 TEXT NOT NULL,
  CHECK (enabled = 0 OR selected_client IS NOT NULL)
) STRICT;

CREATE TABLE agent_local_session (
  id               TEXT PRIMARY KEY,
  client           TEXT NOT NULL CHECK (client IN ('codex', 'claude-code')),
  started_at       TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  ended_at         TEXT,
  result_state     TEXT NOT NULL DEFAULT 'none'
                        CHECK (result_state IN ('none', 'completed', 'partial', 'failed')),
  result_at        TEXT,
  candidate_count  INTEGER CHECK (candidate_count IS NULL OR candidate_count >= 0),
  submitted_count  INTEGER CHECK (submitted_count IS NULL OR submitted_count >= 0),
  error_code       TEXT,
  CHECK (
    (result_state = 'none'
      AND result_at IS NULL
      AND candidate_count IS NULL
      AND submitted_count IS NULL
      AND error_code IS NULL)
    OR
    (result_state = 'completed'
      AND result_at IS NOT NULL
      AND candidate_count IS NOT NULL
      AND submitted_count = candidate_count
      AND error_code IS NULL)
    OR
    (result_state = 'partial'
      AND result_at IS NOT NULL
      AND candidate_count IS NOT NULL
      AND submitted_count IS NOT NULL
      AND submitted_count < candidate_count
      AND error_code IS NULL)
    OR
    (result_state = 'failed'
      AND result_at IS NOT NULL
      AND candidate_count IS NULL
      AND submitted_count IS NULL
      AND error_code IS NOT NULL)
  )
) STRICT;

CREATE TABLE "agent_proposal_run" (
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

CREATE TABLE balance_assertion (
  id             TEXT PRIMARY KEY,
  account_id     TEXT NOT NULL REFERENCES account(id),
  as_of          TEXT NOT NULL,
  commodity_id   TEXT NOT NULL REFERENCES commodity(id),
  amount_minor   INTEGER,
  quantity_scaled INTEGER,
  source_file_id TEXT REFERENCES source_file(id),
  UNIQUE(account_id, as_of, commodity_id)
) STRICT;

CREATE TABLE category (
  id       TEXT PRIMARY KEY,           -- stable id, not a display name
  parent_id TEXT REFERENCES category(id),
  kind     TEXT NOT NULL CHECK (kind IN ('income','expense','transfer'))
) STRICT;

CREATE TABLE category_override (
  txn_id          TEXT PRIMARY KEY REFERENCES txn(id),
  category_id     TEXT NOT NULL REFERENCES category(id),
  created_at      TEXT NOT NULL,
  source          TEXT NOT NULL DEFAULT 'human'
                       CHECK (source IN ('human', 'agent', 'learned')),
  agent_run_id    TEXT REFERENCES "agent_proposal_run"(id),
  learned_rule_id TEXT REFERENCES learned_rule(id),
  CHECK (
    (source = 'human' AND agent_run_id IS NULL AND learned_rule_id IS NULL)
    OR
    (source = 'agent' AND agent_run_id IS NOT NULL AND learned_rule_id IS NULL)
    OR
    (source = 'learned' AND agent_run_id IS NULL AND learned_rule_id IS NOT NULL)
  )
) STRICT;

CREATE TABLE commodity (
  id     TEXT PRIMARY KEY,             -- 'USD' | 'VTSAX'
  kind   TEXT NOT NULL CHECK (kind IN
           ('currency','equity','fund','bond','option','crypto')),
  scale  INTEGER NOT NULL,             -- USD=2, equities=8
  cusip  TEXT, isin TEXT,              -- the real keys; tickers get reused
  ticker TEXT
) STRICT;

CREATE TABLE corporate_action (   -- without this table every split misfires holdings checks
  id           TEXT PRIMARY KEY,
  commodity_id TEXT NOT NULL,
  ex_date      TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN
                 ('split','reverse_split','dividend','drip',
                  'return_of_capital','spinoff','merger')),
  ratio_num INTEGER, ratio_den INTEGER,   -- exact rational, never a float
  cash_per_unit_minor INTEGER,
  resulting_commodity_id TEXT,
  applied_txn_id TEXT REFERENCES txn(id)
) STRICT;

CREATE TABLE learned_rule (
  id                  TEXT PRIMARY KEY
                           CHECK (length(id) = 35 AND substr(id, 1, 3) = 'lr-'),
  match_kind          TEXT NOT NULL DEFAULT 'template'
                           CHECK (match_kind IN ('template', 'prefix')),
  template            TEXT NOT NULL CHECK (length(template) > 0),
  template_version    INTEGER NOT NULL CHECK (template_version >= 1),
  category_id         TEXT NOT NULL REFERENCES category(id),
  source              TEXT NOT NULL CHECK (source IN ('human', 'agent')),
  agent_run_id        TEXT REFERENCES agent_proposal_run(id) ON DELETE CASCADE,
  learned_from_txn_id TEXT REFERENCES txn(id),
  created_at          TEXT NOT NULL,
  CHECK (
    (source = 'human' AND agent_run_id IS NULL)
    OR
    (source = 'agent' AND agent_run_id IS NOT NULL)
  ),
  -- A template rule is always learned from one concrete decision. A prefix
  -- rule is a standing human decree with no single teaching transaction.
  CHECK (match_kind = 'prefix' OR learned_from_txn_id IS NOT NULL),
  CHECK (
    match_kind = 'template'
    OR (source = 'human' AND learned_from_txn_id IS NULL AND length(template) >= 6)
  ),
  UNIQUE (match_kind, template, template_version)
) STRICT;

CREATE TABLE lot (
  id                  TEXT PRIMARY KEY,
  account_id          TEXT NOT NULL REFERENCES account(id),
  commodity_id        TEXT NOT NULL REFERENCES commodity(id),
  acquired_on         TEXT NOT NULL,
  cost_per_unit_minor INTEGER NOT NULL,
  cost_currency       TEXT NOT NULL REFERENCES commodity(id),
  label               TEXT,            -- the broker's lot number: a fact, not derivable
  opening_posting_id  TEXT REFERENCES posting(id),
  closed_on           TEXT
) STRICT;

CREATE TABLE posting (
  id          TEXT PRIMARY KEY,
  txn_id      TEXT NOT NULL REFERENCES txn(id),
  seq         INTEGER NOT NULL,
  account_id  TEXT NOT NULL REFERENCES account(id),
  date        TEXT,                    -- NULL => txn.date; used when legs settle on different days

  amount_minor    INTEGER NOT NULL,    -- signed, in the currency's minor units
  currency        TEXT NOT NULL REFERENCES commodity(id),
  quantity_scaled INTEGER,             -- kept separate from amount on purpose
  commodity_id    TEXT REFERENCES commodity(id),

  lot_id              TEXT REFERENCES lot(id),
  cost_per_unit_minor INTEGER,
  cost_currency       TEXT REFERENCES commodity(id),
  cost_date           TEXT,
  price_per_unit_minor INTEGER,

  category_id TEXT REFERENCES category(id),
  memo        TEXT,
  cleared     INTEGER NOT NULL DEFAULT 0,
  reconciled  INTEGER NOT NULL DEFAULT 0,
  UNIQUE(txn_id, seq)
) STRICT;

CREATE TABLE price (
  commodity_id   TEXT NOT NULL,
  quote_currency TEXT NOT NULL,
  date           TEXT NOT NULL,
  price_minor    INTEGER NOT NULL,
  source         TEXT NOT NULL,        -- statement | manual | yahoo
  PRIMARY KEY (commodity_id, quote_currency, date, source)
) STRICT;

CREATE TABLE raw_record (
  id             TEXT PRIMARY KEY,
  source_file_id TEXT NOT NULL REFERENCES source_file(id),
  record_index   INTEGER NOT NULL,     -- provenance only; never identity
  kind           TEXT NOT NULL,        -- stmttrn | invtran | invpos | balance
  payload        TEXT NOT NULL,        -- verbatim JSON, including page/bbox
  parser_id      TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  UNIQUE(source_file_id, record_index)
) STRICT;

CREATE TABLE review_item (
  id             TEXT PRIMARY KEY,
  source_file_id TEXT NOT NULL REFERENCES source_file(id),
  status         TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open','resolved','dismissed')),
  severity       TEXT NOT NULL CHECK (severity IN ('block','warn')),
  check_id       TEXT NOT NULL,        -- which assertion failed
  detail         TEXT NOT NULL,        -- human-readable + structured JSON
  created_at     TEXT NOT NULL,
  resolved_at    TEXT
) STRICT;

CREATE TABLE schema_migration (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  sha256     TEXT NOT NULL,
  applied_at TEXT NOT NULL
) STRICT;

CREATE TABLE source_file (
  id           TEXT PRIMARY KEY,
  sha256       TEXT NOT NULL UNIQUE,   -- content address: re-upload is a no-op by construction
  rel_path     TEXT NOT NULL,          -- path *inside* archive/, never the user's original path
  media_type   TEXT NOT NULL,
  byte_len     INTEGER NOT NULL,
  institution  TEXT,
  period_start TEXT,
  period_end   TEXT,
  ingested_at  TEXT NOT NULL,
  supersedes   TEXT REFERENCES source_file(id)   -- chain of corrected statements
) STRICT;

CREATE TABLE txn (
  id            TEXT PRIMARY KEY,
  date          TEXT NOT NULL,
  payee         TEXT,
  narration     TEXT,
  flag          TEXT NOT NULL DEFAULT '*' CHECK (flag IN ('*','!')),
  is_transfer   INTEGER NOT NULL DEFAULT 0,   -- excluded from income/expense aggregates
  superseded_by TEXT REFERENCES txn(id),
  created_at    TEXT NOT NULL
) STRICT;

CREATE TABLE txn_identity (
  txn_id              TEXT NOT NULL REFERENCES txn(id),
  account_id          TEXT NOT NULL REFERENCES account(id),
  source_system       TEXT NOT NULL,   -- pdf | csv | ofx | simplefin
  source_id           TEXT,            -- FITID: nullable and untrusted
  natural_key         TEXT NOT NULL,
  natural_key_version INTEGER NOT NULL,
  occurrence_index    INTEGER NOT NULL DEFAULT 0,
  raw_descriptor      TEXT NOT NULL,   -- verbatim; never normalized in place
  raw_record_id       TEXT REFERENCES raw_record(id),
  UNIQUE(account_id, source_system, natural_key, natural_key_version)
) STRICT;

CREATE INDEX account_parent     ON account(parent_id) WHERE parent_id IS NOT NULL;

CREATE INDEX agent_category_proposal_run_outcome
  ON agent_category_proposal(run_id, outcome);

CREATE INDEX agent_category_proposal_txn
  ON agent_category_proposal(txn_id);

CREATE UNIQUE INDEX agent_classification_job_import_trigger
  ON agent_classification_job(trigger_source_file_id)
  WHERE trigger_source_file_id IS NOT NULL;

CREATE UNIQUE INDEX agent_classification_job_one_running
  ON agent_classification_job(state) WHERE state = 'running';

CREATE UNIQUE INDEX agent_classification_job_proposal_run
  ON agent_classification_job(proposal_run_id) WHERE proposal_run_id IS NOT NULL;

CREATE INDEX agent_classification_job_queue
  ON agent_classification_job(state, queued_at, id);

CREATE UNIQUE INDEX agent_classification_job_session
  ON agent_classification_job(session_id) WHERE session_id IS NOT NULL;

CREATE INDEX agent_local_session_client_result
  ON agent_local_session(client, result_at DESC)
  WHERE result_state <> 'none';

CREATE INDEX agent_local_session_client_seen
  ON agent_local_session(client, last_seen_at DESC);

CREATE INDEX agent_triage_item_run_outcome
  ON agent_triage_item(run_id, outcome);

CREATE INDEX agent_triage_item_run_route
  ON agent_triage_item(run_id, route);

CREATE INDEX agent_triage_item_txn
  ON agent_triage_item(txn_id);

CREATE INDEX balance_acct_asof  ON balance_assertion(account_id, as_of);

CREATE INDEX learned_rule_run
  ON learned_rule(agent_run_id) WHERE agent_run_id IS NOT NULL;

CREATE INDEX lot_account        ON lot(account_id, commodity_id);

CREATE INDEX posting_account_dt ON posting(account_id, date);

CREATE INDEX posting_category   ON posting(category_id) WHERE category_id IS NOT NULL;

CREATE INDEX posting_txn        ON posting(txn_id);

CREATE INDEX raw_record_file    ON raw_record(source_file_id);

CREATE INDEX review_file        ON review_item(source_file_id);

CREATE INDEX review_open        ON review_item(status, severity) WHERE status = 'open';

CREATE INDEX txn_date           ON txn(date);

CREATE INDEX txn_identity_raw   ON txn_identity(raw_record_id);

CREATE UNIQUE INDEX txn_identity_src
  ON txn_identity(account_id, source_system, source_id)
  WHERE source_id IS NOT NULL;

CREATE INDEX txn_identity_txn   ON txn_identity(txn_id);

CREATE INDEX txn_open           ON txn(id) WHERE superseded_by IS NULL;

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

CREATE VIEW v_category_spend AS
SELECT
  category_id,
  -SUM(amount_minor)      AS spend_minor,
  COUNT(DISTINCT txn_id)  AS txn_count
FROM v_cashflow_line
WHERE is_transfer = 0
  AND account_kind = 'expense'
GROUP BY category_id;

CREATE VIEW v_identity_without_source AS
SELECT ti.txn_id, ti.account_id, ti.source_system, ti.raw_descriptor
FROM txn_identity ti
LEFT JOIN raw_record rr ON rr.id = ti.raw_record_id
WHERE ti.raw_record_id IS NULL OR rr.id IS NULL;

CREATE VIEW v_statement AS
SELECT
  sf.id                        AS source_file_id,
  sf.institution,
  sf.period_start,
  sf.period_end,
  substr(sf.period_end, 1, 7)  AS statement_month,
  sf.rel_path,
  sf.byte_len,
  sf.ingested_at
FROM source_file sf;

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
    WHEN co.category_id IS NOT NULL AND co.source = 'learned' THEN 'learned'
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
    WHEN co.source = 'learned' THEN 'learned'
    ELSE 'override'
  END AS decided_by
FROM txn t
LEFT JOIN category_override co ON co.txn_id = t.id
LEFT JOIN category c ON c.id = co.category_id;

CREATE VIEW v_unbalanced_txn AS
SELECT txn_id, currency, SUM(amount_minor) AS residual_minor
FROM posting
GROUP BY txn_id, currency
HAVING SUM(amount_minor) <> 0;

