-- One definition of "is this a transfer", and every reader takes it from here.
--
-- Two sources of truth feed one answer, which is the whole reason this view
-- exists rather than each aggregation deciding for itself:
--
--   txn.is_transfer      what the rules derived at ingest. A pure function of
--                        the descriptor, so re-ingesting archive/ reproduces
--                        it and the rebuild invariant is unaffected.
--   category_override    what a person decided. User data: it is NOT in
--                        archive/, cannot be recomputed, and survives because
--                        txn_id is a content hash. A person's answer wins.
--
-- "Not a transfer" is expressed by overriding to an income or expense
-- category, not by a sentinel. That keeps the override table meaning exactly
-- one thing -- "this transaction's category is X" -- and makes both directions
-- of correction reachable with no extra column.
--
-- docs/STATUS.md §5.29 is why this is a view and not a repeated expression:
-- the archive once carried two definitions of "what is a shard", they drifted,
-- and the resulting failure was one the documented remedy could not clear.
-- §5.43 is the same lesson learned again on these exact two aggregations,
-- where a paragraph claiming they could not disagree was refuted three times.
-- The block-level `cashflow_agreement` check added in M2.1 guards this
-- migration: it was written before the thing it protects changed.

CREATE VIEW v_txn_transfer AS
SELECT
  t.id AS txn_id,
  CASE
    WHEN co.category_id IS NULL  THEN t.is_transfer
    WHEN c.kind = 'transfer'     THEN 1
    ELSE 0
  END AS is_transfer,
  -- Which of the two sources answered. Not decoration: an operator looking at
  -- a number that excluded a transaction is entitled to know whether a rule or
  -- a person took it out, and it is the only way to tell a rule that fired
  -- from a rule that fired and was then overruled.
  CASE WHEN co.category_id IS NULL THEN 'rule' ELSE 'override' END AS decided_by
FROM txn t
LEFT JOIN category_override co ON co.txn_id = t.id
LEFT JOIN category c ON c.id = co.category_id;

-- v_transaction and v_cashflow_monthly are rebuilt on it. Dropped in
-- dependency order: v_cashflow_monthly selects from v_transaction.
DROP VIEW v_cashflow_monthly;
DROP VIEW v_transaction;

-- Unchanged from 0004 except that `is_transfer` is now the *effective* value.
-- That substitution is deliberate rather than additive: leaving the raw column
-- exposed here would leave a second, wronger answer within reach of every
-- future reader, and one of them would take it.
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
JOIN      txn            t  ON t.id  = ti.txn_id
JOIN      v_txn_transfer vt ON vt.txn_id = t.id
JOIN      posting        p  ON p.txn_id = t.id AND p.account_id = ti.account_id
LEFT JOIN raw_record     rr ON rr.id = ti.raw_record_id
LEFT JOIN source_file    sf ON sf.id = rr.source_file_id
WHERE t.superseded_by IS NULL;

-- Body unchanged. It reads v_transaction.is_transfer, which is now effective.
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

-- category_override is read on every aggregate query now, and it is looked up
-- by txn_id -- which is its PRIMARY KEY, so the join above already has an
-- index. No index is added here on purpose: an index nothing uses is a claim
-- that something does.
