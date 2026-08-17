-- The row set every money figure on this page is a sum of, exposed once.
--
-- 0007 defined `v_category_spend` as an aggregate, and an aggregate has thrown
-- away the one column a date filter needs. P2 M6 puts a date range on the page,
-- so the question became: where does "narrow this to the last month" go?
--
-- The wrong answer is a second query against the base tables that repeats the
-- joins and predicates `v_category_spend` already encodes. That is two
-- definitions of "which postings are spending", and STATUS §5.29 is this
-- project's standing record of what that costs -- an archive with two ideas of
-- what a shard was, and a failure the documented remedy could not clear.
--
-- So the primitive moves down one level. `v_cashflow_line` is the **row set**:
-- one row per income-or-expense leg of a live transaction, carrying the date,
-- the effective transfer flag and the effective category. Everything else is a
-- projection of it:
--
--   ledger_totals        CASE over the whole set          (repo, may be scoped)
--   v_category_spend     GROUP BY category, expense legs  (below, unscoped)
--   monthly_cashflow     GROUP BY month of the date       (repo, may be scoped)
--
-- Which makes the equalities the two charts rest on structural rather than
-- argued. The slices sum to the Out and the months sum to the four figures
-- because they are sums of the same rows under the same predicate, filtered or
-- not. Before this they were sums of separately-written queries that happened to
-- agree, and STATUS §5.43 is four rewrites of a paragraph explaining why two
-- such queries agreed, refuted three times.
--
-- WHY THE TRANSFER FLAG AND THE ACCOUNT KIND ARE COLUMNS RATHER THAN FILTERS
--
-- `ledger_totals` reports what the transfer flag *removed*
-- (`transfer_excluded_*`, STATUS §5.50) out of the same scan that reports what
-- it kept. A view that filtered transfers out could not answer that, and the
-- caller would need a second query -- which is the shape this migration exists
-- to remove. Both are carried; every reader states its own predicate.
--
-- WHY `txn.date` AND NOT THE STATEMENT MONTH
--
-- The full argument is on `ledgerbox.db.repo.DateSpan`. In short: this column is
-- on `txn`, which every one of the readers above already joins, so a bound on it
-- adds no join and can therefore neither drop a row nor duplicate one. Reaching
-- the statement month would mean joining `txn_identity -> raw_record ->
-- source_file`, which silently drops a transaction that has no identity row --
-- the first of the two shapes `cashflow_agreement` was built to catch. And a
-- person asking for "the last week" is not asking for a number of statement
-- months.
--
-- The two are genuinely different questions and the product keeps both, each
-- labelled. The predecessor kept both and labelled neither: its chart bucketed
-- by transaction month, its table by statement month, and 83 of its 415 rows
-- fell in different buckets with nothing on screen saying so.

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

-- Rebuilt as a projection of the row set rather than as its own set of joins.
-- The body is the same question 0007 asked and the same answer it gave; what
-- changed is that it no longer states the predicates itself, so it cannot come
-- to state them differently from the queries beside it.
--
-- It stays because `verify`'s `cashflow_agreement` reads it unscoped, and
-- because EXECUTION_PLAN §3.2 named it. The sign convention is unchanged:
-- `spend_minor` is negative, matching `outflow_minor`.
DROP VIEW v_category_spend;

CREATE VIEW v_category_spend AS
SELECT
  category_id,
  -SUM(amount_minor)      AS spend_minor,
  COUNT(DISTINCT txn_id)  AS txn_count
FROM v_cashflow_line
WHERE is_transfer = 0
  AND account_kind = 'expense'
GROUP BY category_id;

-- No index. Every join underneath is on a primary key or on `posting_txn`, and
-- the date bound lands on `txn_date`, which 0002 already created. An index
-- nothing uses is a claim that something does.
