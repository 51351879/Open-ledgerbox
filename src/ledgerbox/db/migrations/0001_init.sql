-- ledgerbox schema, revision 0001
--
-- Transcribed column-for-column from docs/EXECUTION_PLAN.md §3.2.
-- Every table is STRICT (SQLite >= 3.37) and every money column is an INTEGER
-- count of minor units. There is no REAL column in this file and there never
-- will be one: only .00/.25/.50/.75 are exactly representable in binary
-- floating point, which is not enough for money.

-- ===== BRONZE: append-only, never updated ==================================

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

-- ===== SILVER ==============================================================

CREATE TABLE commodity (
  id     TEXT PRIMARY KEY,             -- 'USD' | 'VTSAX'
  kind   TEXT NOT NULL CHECK (kind IN
           ('currency','equity','fund','bond','option','crypto')),
  scale  INTEGER NOT NULL,             -- USD=2, equities=8
  cusip  TEXT, isin TEXT,              -- the real keys; tickers get reused
  ticker TEXT
) STRICT;

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
-- No `sign` column: the normal balance direction follows from `kind`.

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
-- Invariant: SUM(amount_minor) GROUP BY txn_id, currency == 0  (reconcile check 0)

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

-- ===== IDENTITY ============================================================

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

CREATE UNIQUE INDEX txn_identity_src
  ON txn_identity(account_id, source_system, source_id)
  WHERE source_id IS NOT NULL;

-- ===== RECONCILIATION / REVIEW =============================================

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

CREATE TABLE category (
  id       TEXT PRIMARY KEY,           -- stable id, not a display name
  parent_id TEXT REFERENCES category(id),
  kind     TEXT NOT NULL CHECK (kind IN ('income','expense','transfer'))
) STRICT;
-- Display names live in the i18n files, never in the key. The predecessor put
-- both languages in the key and split('/') them apart.

CREATE TABLE category_override (   -- a user's manual per-transaction fix must persist
  txn_id      TEXT PRIMARY KEY REFERENCES txn(id),
  category_id TEXT NOT NULL REFERENCES category(id),
  created_at  TEXT NOT NULL
) STRICT;

CREATE TABLE price (
  commodity_id   TEXT NOT NULL,
  quote_currency TEXT NOT NULL,
  date           TEXT NOT NULL,
  price_minor    INTEGER NOT NULL,
  source         TEXT NOT NULL,        -- statement | manual | yahoo
  PRIMARY KEY (commodity_id, quote_currency, date, source)
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
