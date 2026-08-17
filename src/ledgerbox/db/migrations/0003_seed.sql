-- Seed rows every ledger needs before the first statement can be booked.
--
-- Real bank accounts are created at ingest time (their id derives from the
-- institution and the statement's account mask). What must exist up front is
-- the currency and the counter-accounts that make a single-entry statement
-- line into a balanced double-entry transaction.

INSERT INTO commodity (id, kind, scale, cusip, isin, ticker)
VALUES ('USD', 'currency', 2, NULL, NULL, NULL);

-- The other leg. P0 has no categorization engine, so every non-transfer leg
-- lands in one of these two; P2 only rewrites posting.category_id, never the
-- structure.
INSERT INTO account (id, parent_id, name, kind, subtype, currency,
                     booking_method, is_own_account, institution, mask,
                     opened_on, closed_on)
VALUES
  ('income:uncategorized',      NULL, 'Income:Uncategorized',
   'income',  NULL, 'USD', 'NONE', 0, NULL, NULL, NULL, NULL),
  ('expenses:uncategorized',    NULL, 'Expenses:Uncategorized',
   'expense', NULL, 'USD', 'NONE', 0, NULL, NULL, NULL, NULL),
  ('equity:opening-balances',   NULL, 'Equity:Opening-Balances',
   'equity',  NULL, 'USD', 'NONE', 0, NULL, NULL, NULL, NULL);
