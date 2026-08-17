-- Indexes. No columns, constraints or semantics change here; §3.2 stays intact.

CREATE INDEX posting_txn        ON posting(txn_id);
CREATE INDEX posting_account_dt ON posting(account_id, date);
CREATE INDEX posting_category   ON posting(category_id) WHERE category_id IS NOT NULL;

CREATE INDEX txn_date           ON txn(date);
CREATE INDEX txn_open           ON txn(id) WHERE superseded_by IS NULL;

CREATE INDEX raw_record_file    ON raw_record(source_file_id);

CREATE INDEX txn_identity_txn   ON txn_identity(txn_id);
CREATE INDEX txn_identity_raw   ON txn_identity(raw_record_id);

CREATE INDEX review_open        ON review_item(status, severity) WHERE status = 'open';
CREATE INDEX review_file        ON review_item(source_file_id);

CREATE INDEX balance_acct_asof  ON balance_assertion(account_id, as_of);

CREATE INDEX account_parent     ON account(parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX lot_account        ON lot(account_id, commodity_id);
