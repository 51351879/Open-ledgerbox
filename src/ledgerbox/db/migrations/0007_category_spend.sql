-- One definition of "what did this category cost", and every reader takes it
-- from here.
--
-- EXECUTION_PLAN §3.2 named `v_category_spend` as a gold-layer view and 0004
-- deliberately did not build it, on the grounds that "an empty view that looks
-- queryable is worse than an absent one" -- the categorisation engine did not
-- exist yet. It exists now, and P2 M5's second chart is exactly this question,
-- so the view arrives with the reader that makes it meaningful.
--
-- It lands *before* that chart rather than beside it, which is the same order
-- M4 used for `v_txn_category` (STATUS §5.67) and M2.1 used for
-- `cashflow_agreement` (§5.45/§5.47): the definition goes in first, so that
-- when a figure on the page first looks wrong there is no question of whether
-- the definition or the drawing introduced it.
--
--
-- WHAT IT MEASURES, AND WHY THAT EXACT ROW SET
--
-- The expense leg of every non-superseded, non-transfer transaction, grouped by
-- the **effective** category. That is not one choice among several -- it is the
-- only row set for which this holds:
--
--     SUM(spend_minor) over this view  ==  repo.ledger_totals()['outflow_minor']
--
-- and that equality is the whole point. A breakdown chart claims to be some
-- total taken apart. If its slices sum to a number that is merely *near* the
-- figure printed at the top of the same page, the page has grown a fourth
-- cashflow measurement -- and STATUS §5.45 records what the third one cost:
-- a paragraph rewritten four times, refuted by construction three times, and
-- finally a block-level check written to settle it. So the slices here add up
-- to the headline Out by construction, and `tests/test_analytics.py` pins it.
--
-- Hence three things that each look like they could have gone the other way:
--
--   * the **expense leg**, not the bank leg. `ledger_totals` measures income and
--     spending on the income/expense legs (§5.6), and the transaction table's
--     figures measure the bank leg (§5.69). Summing the bank leg here would
--     produce a total that is close to Out and not equal to it -- the exact
--     shape §5.69 spent two acceptance rounds getting a sentence right about.
--     Note the category itself is written on the *bank* leg (§5.36); this view
--     reads the category per **transaction**, through `v_txn_category`, so
--     which leg carries the column is not the same question as which leg
--     carries the amount.
--
--   * `vt.is_transfer = 0`, from `v_txn_transfer` and never from `txn`
--     directly, so a line a *person* marked leaves this breakdown exactly as it
--     leaves the headline figures. "Transfers do not appear in the spending
--     pie" is an acceptance item from EXECUTION_PLAN §7 that has never been
--     testable: the rules claim none of the author's 415 real lines (§5.52), so
--     until M4 gave a person a way to mark one, the condition could not be
--     reached at all. It can now, and it is reached deliberately in the tests.
--
--   * NO FILTER ON THE CATEGORY'S `kind`. An override may put an income
--     category on a withdrawal, and `repo.list_categories` allows that on
--     purpose -- a refunded restaurant charge really is dining. Dropping such a
--     row because its category reads `income` would break the sum above and
--     silently shrink somebody's spending, which is this project's own failure
--     mode aimed at itself. The row is grouped under whatever category is
--     effective, and the total stays whole.
--
--
-- NULL IS A GROUP, NOT A GAP
--
-- `category_id IS NULL` -- nothing claimed the line -- is a row in this view
-- like any other. On the author's 13 statements the rules claim 130 of 415
-- lines and the remaining 285 are stored NULL on purpose (§5.38: there is no
-- catch-all to fall into), and on the author's own live ledger the unclaimed
-- share of spending is the overwhelming majority of it.
--
-- The predecessor's worst defect was not that a rule was wrong. It was that the
-- wrong rule *was also the silent catch-all*, so "other" came to $33.78 and the
-- pie looked complete (§5.38, PROJECT_SUMMARY §2.3). A view that dropped the
-- NULL group would let 130 claimed lines render as 100% coverage -- worse than
-- having no chart, because it stops people looking. So the group is emitted,
-- and every caller is expected to give it area and to call it something like
-- "nothing claimed this" -- never "other", which is the word that made the
-- predecessor's chart look finished.
--
--
-- ONE DIMENSION, ON PURPOSE
--
-- There is no `statement_month` here, and adding one would not be a small
-- extension. `statement_month` reaches the ledger through
-- `txn_identity -> raw_record -> source_file`, and `repo._TOTALS_SQL` joins
-- none of those. A transaction with an expense leg and **no identity row**
-- would therefore be counted by `outflow_minor` and dropped by a month-grouped
-- version of this view -- which is, exactly, the first of the two negative
-- cases `cashflow_agreement` was built around (§5.45). It is unreachable while
-- `build_entries`/`insert_entries` are the only writers, and the equality above
-- is the foundation every M5 figure stands on: it should not rest on something
-- being unreachable. A monthly breakdown by category is a different view, with
-- its own weaker guarantee stated out loud, on the day something needs one.
--
--
-- WHY THE GROUPING CANNOT FAN OUT OR DROP A ROW
--
-- Stated as the guarantee rather than as a list of conditions, because §5.43 is
-- the standing record of what happens when a claim of this shape is written as
-- a condition list: three versions, three counter-examples.
--
--     `v_txn_category` emits exactly one row per `txn` by construction: it
--     selects FROM `txn` and LEFT JOINs `category_override`, whose `txn_id` is
--     its PRIMARY KEY. Joining it to a posting set therefore neither multiplies
--     that set nor removes anything from it.
--
-- That is a property of the two schemas and not of what has been ingested, and
-- it is the same device 0006 used for the same reason: a guarantee the grammar
-- enforces beats an invariant somebody has to keep true. What it does NOT buy
-- is that this view and `_TOTALS_SQL` will still agree after somebody edits one
-- of them; nothing at runtime checks that, and `docs/STATUS.md` §7 carries that
-- gap openly rather than leaving it implied.

CREATE VIEW v_category_spend AS
SELECT
  vc.category_id,
  -- Negative, in the same sign convention as `outflow_minor` and
  -- `transfer_excluded_out_minor` (§5.50). Flipping the sign to make a chart
  -- more convenient would put a second convention into the one place whose
  -- reason for existing is that its numbers add up to a figure printed
  -- elsewhere.
  -SUM(p.amount_minor) AS spend_minor,
  -- How many transactions are behind the amount. Not comparable with
  -- `ledger_totals`' `txn_count`, which counts income and expense together;
  -- this one describes a slice.
  COUNT(DISTINCT p.txn_id) AS txn_count
FROM posting p
JOIN account        a  ON a.id = p.account_id
JOIN txn            t  ON t.id = p.txn_id
JOIN v_txn_transfer vt ON vt.txn_id = t.id
JOIN v_txn_category vc ON vc.txn_id = t.id
WHERE t.superseded_by IS NULL
  AND vt.is_transfer = 0
  AND a.kind = 'expense'
GROUP BY vc.category_id;

-- No index. The joins are on `txn.id`, `account.id` and `category_override`'s
-- primary key, and the posting scan is the same one `ledger_totals` already
-- does on every page load. An index nothing uses is a claim that something
-- does.
