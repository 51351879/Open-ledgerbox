-- One definition of "what category is this", and every reader takes it from here.
--
-- The same two sources 0005 folded for `is_transfer`, folded again for the
-- category -- and for the same reason. Without this view, the write endpoint
-- P2 M4 adds would produce a ledger in which somebody sets a line to `dining`,
-- `category_override` gains a row, and the transaction table keeps showing the
-- rule's old answer. `v_txn_transfer` folds an override into exactly one
-- column, `is_transfer`; nothing folded it into the category itself.
--
--   posting.category_id   what the rules derived at ingest. A pure function of
--                         the descriptor, so re-ingesting archive/ reproduces
--                         it and the rebuild invariant is unaffected.
--   category_override     what a person decided. NOT in archive/, cannot be
--                         recomputed, survives because txn_id is a content
--                         hash. A person's answer wins.
--
-- Two things here are deliberately *not* shaped like 0005, because the column
-- underneath is not shaped like `txn.is_transfer`.
--
-- 1. THE RULE'S ANSWER IS READ WITH A SCALAR SUBQUERY, NOT A JOIN.
--
--    `txn.is_transfer` is a column on `txn`, so 0005 could reach it with a
--    plain join and stay one row per transaction. The category the rules
--    derived is a column on `posting` -- written on the bank leg only (see
--    `ledgerbox.db.repo.set_posting_categories`, and the test asserting no row
--    with `seq <> 0` carries one). Reaching it the way `v_transaction` reaches
--    the bank leg, by joining `txn_identity` and then `posting`, would make
--    this view emit one row per identity row; joining *that* into
--    `v_transaction` on `txn_id` alone would then cross-multiply, and a
--    transaction with two identity rows would render four times.
--
--    That is unreachable today -- `build_entries` emits one identity row per
--    statement line and transfer pairing does not exist. Which is precisely why
--    it is closed now rather than argued about later: STATUS §5.45 is the rule
--    that a definition goes in before the thing it defines becomes writable,
--    and a scalar subquery returns exactly one value by the grammar rather than
--    by an invariant somebody has to keep true. `ORDER BY seq LIMIT 1` makes
--    the choice deterministic even if that invariant ever breaks.
--
-- 2. `decided_by` HAS THREE VALUES HERE AND TWO IN 0005.
--
--    `txn.is_transfer` is NOT NULL DEFAULT 0, so the rules always have an
--    answer and "no override" can safely be reported as 'rule'.
--    `posting.category_id` is nullable and **most of this ledger is null** --
--    on the 13 real statements the rules claim 130 of 415 lines and the other
--    285 are stored as NULL on purpose (STATUS §5.38: there is no catch-all to
--    fall into). Reporting "no rule claimed this" as 'rule' would put the
--    project's own failure shape -- a line that reads stronger than its
--    evidence -- on the largest single block of data it has.
--
-- Note what this view does NOT expose: the category's `kind`. "Is this a
-- transfer" has one answer and it is `v_txn_transfer`, never `kind =
-- 'transfer'` derived from here. The two genuinely differ: `classify()` never
-- returns a transfer category however well its patterns fit, so a line the
-- *rules* flagged has `is_transfer = 1` and a NULL category, while a line a
-- *person* moved to the transfer category has both. A reader who derived the
-- flag from the kind would silently miss the first kind -- which is the only
-- kind the rules can produce.

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
    WHEN co.category_id IS NOT NULL THEN 'override'
    WHEN EXISTS (
      SELECT 1 FROM posting p WHERE p.txn_id = t.id AND p.category_id IS NOT NULL
    ) THEN 'rule'
    ELSE 'none'
  END AS decided_by
FROM txn t
LEFT JOIN category_override co ON co.txn_id = t.id;

-- v_transaction is rebuilt on it. Dropped in dependency order, as in 0005:
-- v_cashflow_monthly selects from v_transaction.
DROP VIEW v_cashflow_monthly;
DROP VIEW v_transaction;

-- Unchanged from 0005 except that `category_id` is now the *effective* value
-- and `category_decided_by` says which source produced it. The substitution is
-- deliberate rather than additive, for the reason 0005 gives for `is_transfer`:
-- leaving the raw column exposed here would leave a second, wronger answer
-- within reach of every future reader, and one of them would take it.
--
-- There is exactly one reader in `src/` that still wants the raw column, and it
-- reaches past this view for it on purpose: `repo.categorized_rows` selects
-- `posting.category_id AS rule_category_id` so that `reapply-rules --dry-run`
-- can ask "would the rules change their own previous answer" without counting
-- a person's override as a row the rules want to move. That is the same
-- exception `rule_is_transfer` already is, for the same reason, and the two are
-- now the only two.
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

-- Body unchanged. It reads v_transaction.is_transfer, which 0005 made
-- effective; nothing here consults a category, and nothing should -- categories
-- are a heuristic and this view feeds the cashflow figures.
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

-- No index is added. The override lookup is by `category_override.txn_id`,
-- which is its PRIMARY KEY, and the scalar subquery is covered by
-- `posting_txn`. An index nothing uses is a claim that something does.
