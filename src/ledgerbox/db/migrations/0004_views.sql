-- Gold layer = views. A single-user tool does not need dbt.
--
-- EXECUTION_PLAN §3.2 names four: v_cashflow_monthly, v_networth_daily,
-- v_holdings_asof(date) and v_category_spend. Only the first is defined here.
-- The other three read models P0 does not populate — net worth needs more than
-- one account, holdings need the (deliberately unused) lot tables, and
-- category spend needs the P2 categorization engine. They arrive with the data
-- that makes them meaningful; an empty view that looks queryable is worse than
-- an absent one. v_statement, v_transaction, v_identity_without_source and
-- v_unbalanced_txn are additions P0 does need.
--
-- statement_month is derived here, from source_file.period_end — the period's
-- *end* day. Taking the start day is what made 2025-06, 2025-09 and 2025-12
-- vanish from the predecessor's output entirely.

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

-- One row per ingested statement line: the bank-side leg only, rendered
-- single-entry. Joining through txn_identity (rather than posting alone) is
-- what keeps the counter-leg from double-counting.
--
-- Every join to raw_record/source_file is a LEFT join, on purpose:
-- txn_identity.raw_record_id is nullable (a row may arrive from CSV or a
-- future fetcher with no PDF behind it), and an INNER join would drop those
-- rows *silently* — the cashflow view would then under-report with no error
-- anywhere. Under-reporting that looks self-consistent is precisely the class
-- of failure this project exists to prevent. v_identity_without_source below
-- makes the same condition visible instead.
CREATE VIEW v_transaction AS
SELECT
  t.id                         AS txn_id,
  t.date,
  t.payee,
  t.narration,
  t.flag,
  t.is_transfer,
  p.id                         AS posting_id,
  p.account_id,
  p.amount_minor,
  p.currency,
  p.category_id,
  ti.raw_descriptor,
  ti.occurrence_index,
  ti.natural_key,
  rr.source_file_id,
  rr.record_index,
  substr(sf.period_end, 1, 7)  AS statement_month,
  sf.period_start,
  sf.period_end
FROM txn_identity ti
JOIN      txn         t  ON t.id  = ti.txn_id
JOIN      posting     p  ON p.txn_id = t.id AND p.account_id = ti.account_id
LEFT JOIN raw_record  rr ON rr.id = ti.raw_record_id
LEFT JOIN source_file sf ON sf.id = rr.source_file_id
WHERE t.superseded_by IS NULL;

-- Identity rows whose provenance is missing. Must be empty for statement
-- ingests; a non-empty result means statement_month is NULL somewhere and the
-- monthly aggregates are incomplete.
CREATE VIEW v_identity_without_source AS
SELECT ti.txn_id, ti.account_id, ti.source_system, ti.raw_descriptor
FROM txn_identity ti
LEFT JOIN raw_record rr ON rr.id = ti.raw_record_id
WHERE ti.raw_record_id IS NULL OR rr.id IS NULL;

CREATE VIEW v_cashflow_monthly AS
SELECT
  statement_month,
  COUNT(*)                                                    AS txn_count,
  SUM(CASE WHEN amount_minor > 0 THEN amount_minor ELSE 0 END) AS inflow_minor,
  SUM(CASE WHEN amount_minor < 0 THEN amount_minor ELSE 0 END) AS outflow_minor,
  SUM(amount_minor)                                            AS net_minor
FROM v_transaction
WHERE is_transfer = 0
GROUP BY statement_month;

-- Double-entry zero-sum, exposed as data so check 0 can be a query rather than
-- a belief. Any row here is a bug.
CREATE VIEW v_unbalanced_txn AS
SELECT txn_id, currency, SUM(amount_minor) AS residual_minor
FROM posting
GROUP BY txn_id, currency
HAVING SUM(amount_minor) <> 0;
