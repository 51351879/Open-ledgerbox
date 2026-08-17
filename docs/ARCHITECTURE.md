# Architecture

How ledgerbox is put together, and why each piece is shaped the way it is.

This describes the code that exists through P2 and BYOA milestones G0–A3.
Where something is planned but absent, it says so and names the phase. If you
find a claim here that the source does not support, that is a bug — please
report it.

---

## The one-sentence version

A statement PDF is archived by content hash, identified by layout, extracted
into positioned words, **reconciled against the totals the statement prints on
itself**, and only then written into a local SQLite double-entry ledger.

Everything else is detail in service of the word "then".

---

## The five-layer pipeline

`src/ledgerbox/ingest/pipeline.py` orchestrates it. The order is not
negotiable.

```
1. ARCHIVE    SHA-256 the bytes → already known? return "duplicate", no side effects
              copy the original into archive/<YYYY>/<MM>/<sha256>.pdf, read-only

2. IDENTIFY   /Producer + document-wide text markers → exactly one parser, or refuse
              unknown layout → review queue, never a guess

3. EXTRACT    pdfplumber words → Span(text, x0, x1, top, bottom)
              every field keeps (page, x0, top, x1, bottom) provenance

4. RECONCILE  ── the gate ──
              0  postings sum to zero per (txn, currency)      block
              1  bal[n-1] + amt[n] == bal[n]                   block
              2  beginning + Σ amounts == ending               block
              3  the statement's own printed subtotals         block
              3b BUCKET_RULES reproduce the bank's buckets     warn
              4  transaction count vs declared count           warn
              5  dates in period; periods contiguous           warn
              6  page continuity                               warn

              ALL BLOCK CHECKS PASS → book.   ANY BLOCK FAILS → review queue.

5. BOOK       idempotency key → dedupe → single-entry becomes double-entry
              txn + posting + txn_identity + raw_record + balance_assertion
```

### Why reconciliation is a gate, not a report

This is the design decision the project exists to express.

The predecessor to this project ran for a year, rendering every chart without an
error, reporting a 78% savings rate. The real rate was about zero: 13 months of
statements netted **−$212.40** against a reported **+$193,209.52**. The parser
had been reading the *running balance* column as the *transaction amount* on
every deposit row, overstating income by **4.57×** — $268,391 reported against
$58,725 actual.

The failure was not that a parser had a bug. Every parser eventually has a bug.
The failure was that **nothing in the chain — PDF, CSV, JavaScript, HTML,
decision — ever compared the output against anything.** And the comparison was
sitting right there: Chase prints the beginning balance, the ending balance, the
deposit total and the withdrawal total on page one of every statement. A
fifteen-line assertion would have failed on **13 of 13** statements, with a total
error of $193,393.72.

So reconciliation is not a validation step that runs after the data is saved and
files a warning. It is positioned **between** parsing and writing, and when it
fails, `pipeline.ingest_file()` returns before a single row is inserted:

```python
if report.blocked:
    # Deliberately no rows: the gate is the product. The archived PDF
    # and the review items stay, so a fixed parser can be re-run over
    # exactly the same bytes.
    return IngestOutcome(status=NEEDS_REVIEW, ...)
```

Three properties follow, and all three are deliberate:

**A skipped block-level check blocks.** `ReconciliationReport.blocked` is true
if there are blocking failures **or** any block-level check with status `SKIP`.
"Unknown means refuse" applies to our own checks first: a report whose strongest
assertion could not run has established nothing, and letting it read `ok` is
precisely the shape of a statement that looks fine. `verdict()` renders that as
`UNVERIFIED`, never as `ok`.

**Every check always runs.** `run_statement_checks()` never short-circuits on
the first failure. An operator fixing one problem per ingest is how a statement
gets waved through on the third attempt.

**Check 0 runs on what is about to be written, not on what was written.** The
double-entry zero-sum test is fed the in-memory postings built by
`ledger.posting.build_entries()`, before the transaction opens. An unbalanced
transaction never reaches the database, so `v_unbalanced_txn` can be a
permanently-empty view rather than a cleanup task.

Check 3b uses `reconcile.checks.BUCKET_RULES`, which is **not** the user-facing
category engine in `analytics/`. The two exist for different questions and are
deliberately not shared: 3b reproduces the three buckets Chase prints on the
statement, so its rules must mirror *the bank's* taxonomy and it ends in a
catch-all on purpose — every withdrawal has to land somewhere for the subtotal
to be comparable. `analytics/categorize.py` answers "what did I spend this on"
and refuses a catch-all for exactly the opposite reason. Merging them would
force one of the two to lie.

Check 1 — the running balance chain — is the strongest and it is free, because
the bank already did the arithmetic on the page. It localises an error to a
**single row**, which checks 2 and 3 cannot. Check 2 alone is explicitly *not
sufficient*: two equal and opposite errors cancel and it passes. Check 3 is what
located the predecessor's defect precisely — the chain says *a* row is wrong;
the subtotals say the error is on the **income** side.

### Where failures go

A blocked statement produces `review_item` rows with a deterministic id
(`sha256` of `source_file_id`, `check_id` and `severity` joined by the same
`\x1f` separator used everywhere else), so re-ingesting the same broken
file **updates** one review item rather than breeding a new one each attempt.
The archived PDF stays. When the parser is fixed, the same bytes are re-processed
and a healed file leaves the queue —
`test_a_blocked_file_is_reprocessed_rather_than_treated_as_a_duplicate` covers
exactly that.

One thing the gate deliberately still does not do is take a statement back out.
That is `ingest/forget.py` (P2 M3) and it is a separate, confirmed act — see
"Deleting a statement" below. Resolving a review item remains what it always
was: a record that a person looked, with no path to `txn` at all.

`verify_ledger` is a different list, asked of the database after the fact.
**Nine block-level checks**: two that re-ask the gate's own questions of the
stored rows (the zero-sum invariant, the printed balances), and six the gate is
in no position to ask — whether a booked row still has provenance, whether a
blocking review item is still open, whether an archived statement was ever
booked, and the three comparing `archive/` against the database. The ninth,
added in P2 M2.1, asserts that the queries reporting income and expense
**agree with one another**. That one exists because the paragraph explaining why they agree was written four times
and refuted by construction three times: a property that hard to state in prose
belongs in an assertion instead.

P2 M5 and M6 gave that ninth check more parties rather than adding a tenth, and
it now **lists** what it compares instead of counting them — every count this
check was ever given went stale the next time it grew, twice, and the second
time the wrong number reached four files at once. `ledger_totals` is compared
against `v_cashflow_monthly`, against both expressions of the category
breakdown, and against the monthly split the bars are drawn from.

Only the first can be pulled apart by *data*: those two sum different postings
of different row sets, so a transaction shape can separate them, and two such
shapes are its negative test cases. The rest read `v_cashflow_line` under the
same predicates and differ only in how they group it, and a grouping does not
change a sum — so no data can separate them. What they catch is an edit, and
only one that changes what a query *sums to*: pointing every wedge at a single
category id, or collapsing thirteen month buckets into one, leaves every total
intact and passes here. The grouping keys are what the charts *are*, and
`tests/test_analytics.py` is what covers them.

One further comparison is made through a **date bound derived from the ledger**,
because everything above is unscoped and unscoped was the whole hole: a query
that ignores its `span` argument answers about the entire ledger while the
headline beside it answers about the window, and nothing was asking about any
window but one.

That both expressions of the breakdown are checked is the correction of a real
defect rather than thoroughness. Only the SQL view was compared, while the
donut is drawn from a Python query of the same name — an acceptance round
edited that one and watched the wedges sum to a twelfth of the figure printed
above them with all nine checks green. The argument for reading the view (a
check that calls the code it checks proves less) was sound about the view and
silently exempted everything else.

Narrow, and worth having, because the test suite does not run on the operator's
machine and every wedge of that chart claims to be part of a figure printed
elsewhere on the same page.

Failure detail is structured and money in it is **integer minor units with a
`_minor` suffix**, not decimals. `EXECUTION_PLAN.md` §4.3's example payload used
floats; using floats in the failure path would put binary floating point back
into the one place that exists to catch arithmetic errors. Human-readable
amounts live in `message`, which is a string.

---

## Module map

This is the tree that exists, not the one `EXECUTION_PLAN.md` §2 planned. The
plan is ahead of the code in several places and behind it in one.

```
src/ledgerbox/                     the tree below; mypy --strict clean
├── __init__.py                    __version__
├── __main__.py                    python -m ledgerbox → cli.main()
├── cli.py                         ledger commands plus versioned Agent JSON;
│                                  stable exits 0/1/2 and Agent refusals 3/4
├── agent.py                       verified minimum-data read contract, strict
│                                  proposal JSON parser; no model/network/write
├── agent_mcp.py                   optional seven-tool STDIO adapter over two
│                                  separated Agent workflows; no listener
├── content_ids.py                 shared canonical JSON content hashes
├── proposals.py                   proposal audit/review state machine; submit
│                                  never changes an effective category
├── triage.py                      exhaustive remaining-coverage validation,
│                                  audit and human-only review state machine
├── config.py                      DataPaths, data-dir resolution, the git guard
├── money.py                       integer minor units; strict amount parsing
├── dates.py                       statement periods, MM/DD → date, month-from-end
├── fsutil.py                      atomic writes, SHA-256, make_read_only
│
├── db/
│   ├── connection.py              pragmas, transaction(), connect_read_only()
│   ├── migrate.py                 forward-only migrations + checksum verification
│   ├── repo.py                    every write, as explicit SQL. No ORM
│   ├── schema.sql                 GENERATED snapshot (tools/dump_schema.py)
│   └── migrations/
│       ├── 0001_init.sql          14 STRICT tables
│       ├── 0002_indexes.sql       13 indexes, several partial
│       ├── 0003_seed.sql          USD + the counter-accounts
│       ├── 0004_views.sql         v_statement, v_transaction, v_cashflow_monthly,
│       │                          v_unbalanced_txn, v_identity_without_source
│       ├── 0005_transfer_predicate.sql
│       │                          v_txn_transfer: the ONE answer to "is this a
│       │                          transfer", rule folded under a person's
│       │                          override. v_transaction rebuilt on it
│       ├── 0006_category_predicate.sql
│       │                          v_txn_category: the same, for the category
│       │                          itself. Three decided_by values here, not
│       │                          two, because a category can be absent
│       ├── 0007_category_spend.sql
│       │                          v_category_spend: what each category cost.
│       │                          The expense legs, so the slices sum to the
│       │                          Out already on the page; NULL is a group
│       │                          and not a gap; no month dimension, because
│       │                          one would drop a row the total keeps
│       ├── 0008_cashflow_line.sql
│       │                          v_cashflow_line: the row set every money
│                                  figure is a sum of, one row per income or
│                                  expense leg, carrying the date. A date range
│                                  needs a column an aggregate has thrown away,
│                                  so the primitive moved down a level and
│                                  ledger_totals, v_category_spend and the
│                                  monthly split all became projections of it
│       ├── 0009_agent_proposals.sql
│       │                          proposal run + row audit; pending suggestions
│       │                          are separate from category_override
│       ├── 0010_agent_triage.sql  exhaustive triage run + row audit, separate
│       │                          from proposals and effective categories
│       └── 0011_agent_override_provenance.sql
│                                  human/Agent source + originating proposal run;
│                                  effective category and transfer views report it
│
├── ingest/
│   ├── pipeline.py                the five layers, orchestrated
│   ├── forget.py                  P2 M3. The inverse: measure a deletion by
│   │                              performing one and rolling back, then do it
│   ├── archive.py                 content-addressed bronze layer
│   ├── extract.py                 the ONLY module importing pdfplumber
│   ├── registry.py                PARSERS tuple, identify(), UnknownLayout
│   └── parsers/
│       ├── base.py                Parser protocol; ParsedStatement & friends
│       └── chase_checking.py      the only parser that exists
│
├── ledger/
│   ├── identity.py                natural_key, occurrence_index, all row ids
│   ├── posting.py                 single-entry rows → balanced double entry
│   └── beancount_export.py        plain-text escape hatch; `export beancount`
│
├── reconcile/
│   ├── checks.py                  the gate
│   └── report.py                  CheckResult → terminal text and review_item
│
├── analytics/                     P2. Computed *from* a ledger; gates nothing
│   ├── categorize.py              descriptor → category, and → transfer or not
│   └── rules/categories.json      the rules, as data. Priority is declared
│
├── api/                           P1. Imported only inside cli.cmd_serve
│   ├── schemas.py                 the ONE definition of every wire shape
│   ├── dependencies.py            one connection per request; writes serialised
│   ├── app.py                     create_app(paths); security headers; /static
│   └── routes/
│       ├── upload.py              POST /api/upload → the same ingest_file
│       ├── review.py              GET /api/review, POST /review/{id}/resolve
│       ├── health.py              GET /api/health
│       ├── statements.py          P2 M3. GET /api/statements, POST
│       │                          …/deletion-plan, DELETE …/{id}
│       ├── transactions.py        P2 M4. GET /api/transactions (filtered,
│       │                          sorted and paged in SQL), GET /api/categories,
│       │                          PATCH …/{txn_id} — the override's first caller
│       ├── analytics.py           P2 M5. GET /api/analytics — both charts out
│       │                          of one deferred read, so two pictures each
│       │                          captioned "this ledger" cannot describe two
│       ├── agent_proposals.py     A1. submit/read/review/dismiss/withdraw via
│       │                          the same proposal service; explicit IDs only
│       └── agent_triage.py        C2. list/read plus human review, dismiss and
│                                  compare-and-clear withdrawal
│
└── web/                           P1. No build step, no CDN, no framework
    ├── index.html                 ranked: status, the four figures, both
    │                              charts, transactions, statements, planning
    │                              notes, queue, diagnostics. Adding a statement
    │                              is a disclosure in the header
    ├── css/{tokens,app,records,transactions,charts,triage}
    │                              sheets cut by job; no build step
    └── js/{api,upload,review,statements,deletion-plan,main}.js
        js/{transactions,transaction-filters,transaction-row}.js   P2 M4
        js/{analytics,charts,chart-monthly,chart-categories}.js    P2 M5
        js/{chart-tooltip,category-tones,date-range,advice}.js     P2 M6
        js/{triage-api,triage-groups,triage}.js                    A6.5 C2
        js/category-claim.js       what the category panel may claim about its
                                   own total. Pure functions, and the only part
                                   of web/ with behavioural tests besides
                                   date-range.js -- `node --test tests/js`,
                                   reached from pytest
```

Two things about the P1 layers that are load-bearing rather than incidental:

* **`api/` is not a second way into the ledger.** `POST /api/upload` spools the
  bytes, checks the magic number, and then calls the same `ingest_file` the CLI
  calls. There is no code path that books a transaction without passing the gate,
  and resolving a review item deliberately has no path to `txn` at all.
* **`web/` never builds DOM from strings.** No `innerHTML`, no
  `insertAdjacentHTML`, no `document.write`, no `eval` — a test greps the shipped
  assets for all of them. Merchant names and counterparty memos are third-party
  text and they reach that page.

Differences from the plan worth knowing:

| Plan says | Reality |
|---|---|
| `ingest/identify.py` | Identification lives in `ingest/registry.py`. There is no `identify.py` |
| (not listed) | `db/migrate.py` exists and is substantial — the migration runner and checksum verifier |
| `ledger/transfers.py` | Not written, and not planned. Transfer *pairing* — matching both sides of a move between two accounts you own — needs a second own account, which this ledger does not have, so it is unreachable rather than pending. One-sided detection is nine patterns in the shared rules file (`kind: "transfer"`); a person's correction is a row in `category_override`; both are folded into one answer by `v_txn_transfer` |
| `ledger/beancount_export.py` | Written, tested and wired: `render_beancount(conn)`, `export_beancount(conn, target)`, and `ledgerbox export beancount` |
| `ingest/parsers/generic_csv.py` | Not written. P3 |
| `analytics/categorize.py` + `rules/categories.json` | Written and wired into the ingest transaction. `aggregate.py` and `subscriptions.py` are not written |
| `api/`, `web/` | Written in P1; the trees above are what exists |
| `tools/gen_synthetic.py`, `tools/sanitize.py` | Do not exist. P5. `tools/` holds `dump_schema.py` and `check_repo_data.py` |
| `tests/fixtures/` | Does not exist. Parser tests build `Document`s from coordinates via `tests/synth.py` |

Dependency direction is one-way and enforced by what the packages re-export.
`ingest/__init__.py` deliberately does **not** re-export `pipeline`, because
`pipeline` imports the `db` and `ledger` layers and re-exporting it would turn a
one-way dependency into a cycle. Likewise `db/__init__.py` does not re-export
`migrate`, which would shadow the module of the same name.

Base runtime dependencies: **five.** `fastapi`, `pdfplumber`, `platformdirs`,
`python-multipart`, `uvicorn`. The web trio was planned as an optional `web`
extra and became required in P1 instead, because `uvx ledgerbox` with no
arguments starts the server: the documented first experience cannot depend on
an extra a newcomer has not heard of. There is no `web` extra in
`pyproject.toml`. `cli.cmd_serve` is still the only place the three are
imported and it imports them inside the function, so a headless install can
strip them and everything except `serve` keeps working.

The Agent bridge is different: `mcp>=1.27,<2` is an explicit `[mcp]` optional extra and
`agent_mcp.py` imports it lazily. Importing or running the ordinary `ledgerbox` CLI does not load
the SDK. `ledgerbox-mcp --data-dir <explicit path>` starts only a STDIO child process and exposes
seven capabilities shared by two five-tool workflows; no tool accepts SQL, a filesystem path, or an
approval action. All wire serialization, reads, validation, and pending submission delegate to
`agent.py`, `proposals.py`, and `triage.py`.

A6.5 C2 implements the separate remaining-coverage contract. It is not an extension field on category
proposals: `possible_transfer`, `taxonomy_gap`, and `uncertain` are review routes rather than category
ids. Validation requires an exhaustive candidate set, a scope revision that changes when effective
uncategorized membership changes, fixed reason codes, and separate audit tables/tools/Skill. It permits
no confidence, free-text reason, invented category, or effective write at submit time. Only the web
human-review API can apply an existing category; gap/uncertain decisions remain unclassified. See
[`COVERAGE_TRIAGE_CONTRACT.md`](COVERAGE_TRIAGE_CONTRACT.md).

---

## The data model

Full DDL: `src/ledgerbox/db/migrations/0001_init.sql`, with a generated snapshot
in `src/ledgerbox/db/schema.sql`. Every one of the 19 tables in a migrated
database is `STRICT` — the original 14, `schema_migration`, two proposal audit
tables, and two triage audit tables. Connections run with `foreign_keys = ON`,
`journal_mode = WAL` and `synchronous = FULL`.

```
BRONZE (append-only, never updated)
  source_file      content-addressed by SHA-256; re-upload is a no-op by construction
  raw_record       verbatim JSON payload + page/bbox provenance, per row

SILVER
  commodity        USD, VTSAX, … with scale, CUSIP/ISIN (tickers get reused)
  account          hierarchy + kind + booking_method + is_own_account
  txn              date, payee, narration, is_transfer, superseded_by
  posting          amount_minor AND quantity_scaled — separate columns
  lot              tax lots as first-class rows (present, unused in P0)

IDENTITY
  txn_identity     natural_key and source_id side by side, never merged

CONTROL
  balance_assertion   the statement's own printed balances
  review_item         what failed, why, and where on the page
  category, category_override, price, corporate_action
```

### Double entry, because it makes a class of bug impossible

This is not accounting purism. In the predecessor, **82.6% of "income" and 77.5%
of "spending" were internal transfers**; the largest single "spending category"
in the pie chart was *Transfers, $31,493*. That is what produced a 78% savings
rate out of a real rate of zero.

With every statement row leaving `ledger/posting.py` as **two postings that sum
to zero**, a transfer between two accounts you own is *one* transaction with two
legs. There is no representation in which the same money appears twice. The bug
cannot be reintroduced by a careless aggregation, because the structure does not
admit it.

Postings are stored; single-entry is a rendering concern. `v_transaction` joins
through `txn_identity` rather than `posting` alone, which is what keeps the
counter-leg out of the row count.

The counter leg is always one of two seeded accounts, `income:uncategorized` or
`expenses:uncategorized`, chosen by **sign alone**. Sign is a property of the
data; anything finer needs a classifier, and a classifier that is merely
plausible has no business deciding the *structure* of a transaction.

P2's categorization engine (`analytics/categorize.py`) holds to that: it writes
`posting.category_id` and changes no account, no amount and no leg. It runs
inside the ingest transaction rather than as a later pass, because it is a pure
function of the descriptor and the shipped rules file — so re-ingesting the
archive into an empty database reproduces the same category on the same
posting, and the rebuild invariant stays an equality with no exception for one
column.

The category is written on the **bank leg**. `v_transaction` is the
single-entry rendering and it joins that leg, so a category recorded anywhere
else would be invisible to every reader; and `category_override` is keyed by
`txn_id`, which says a category belongs to a transaction rather than to one of
its legs. An unmatched descriptor is stored as SQL NULL — there is no
"uncategorized" category to fall into, because a bucket that collects
everything left over is indistinguishable in a chart from one that was matched
on purpose, and that indistinguishability is what made the predecessor's
breakdown look complete.

What a person or Agent decides instead is a row in `category_override`, and P2 M4's
migration 0006 folds the two into one answer — `v_txn_category` — exactly as
0005 did for the transfer flag, and for the same reason: without it, the write
endpoint M4 adds would leave somebody's correction stored and the table still
showing the rule's old answer. Two things there are deliberately unlike 0005,
because `posting.category_id` is not shaped like `txn.is_transfer`. It is
reached by a **scalar subquery**, since the column lives on a posting and
reaching it through `txn_identity` would emit a row per identity row and
multiply `v_transaction` out. And `decided_by` has a third value, `none`: the
transfer flag is NOT NULL so the rules always have an answer, while a category
is absent on 275 of the author's 415 real lines, and reporting "nothing claimed
this" as `rule` would be a field claiming a decision nobody made. Migration 0011 adds an explicit
`human | agent` source and requires every Agent row to name its originating proposal run. Existing
rows migrate to `human`; both category and transfer views expose Agent answers as `decided_by='agent'`
rather than claiming the user set them.

The two are still two questions. `classify()` never returns a transfer category
however well its patterns fit, so a line the *rules* flagged carries
`is_transfer = 1` and a NULL category, while a line a *person* moved carries
both. Deriving the flag from the category's kind would miss the only kind the
rules can produce, which is why `v_txn_category` deliberately does not expose
`kind` at all.

There are now two transfer-kind labels. `transfer` names generic self-account
movement and carries the small conservative pattern set. `investment` names
principal moving into or back from the person's investment/digital-asset
account and deliberately carries no pattern: the same platform descriptor can
mean principal, proceeds, fees, rewards or a purchase. A person may choose it,
or a local Agent may place it in the approval queue, and `v_txn_transfer`
excludes it by `category.kind = 'transfer'`. This is cash-flow presentation,
not P4 asset accounting: it creates no position, lot, price, gain or cost basis.

For the same reason `txn.payee` is `NULL` in P0 and the bank's line goes
verbatim into `narration`. A payee cut from "the first few words of the
descriptor" would read as a fact in every export and every chart while being a
heuristic — which is exactly what the predecessor's category column was.

### Money is an integer count of minor units

Everywhere, without exception. SQLite's own documentation says it plainly: of
all the cent values, only `.00`, `.25`, `.50` and `.75` are exactly
representable in binary floating point.

The discipline is enforced at three depths: `money.py` parses to `int` and
refuses a bare integer as an amount; `repo.py` rejects a `float` before it can
reach the driver; and `test_db.py` asserts that **no floating-point column
exists anywhere in the schema**.

### `quantity_scaled` is separate from `amount_minor`

`posting` carries both:

```sql
amount_minor    INTEGER NOT NULL,     -- signed, minor units of `currency`
currency        TEXT NOT NULL REFERENCES commodity(id),
quantity_scaled INTEGER,              -- separate on purpose
commodity_id    TEXT REFERENCES commodity(id),
```

One row means "150.00 USD" and "10 IBM" at the same time. Collapsing them into a
single `amount` column is the classic beginner mistake in investment modelling —
GnuCash keeps `value` and `quantity` apart, and Beancount does the same. P0
never populates `quantity_scaled`. It is there because **adding these columns
later is extremely painful and adding them now costs a few empty tables.**

The same reasoning keeps `lot`, `posting.cost_*`, `price` and `corporate_action`
in the schema while P4 is skipped. `posting.date` also exists in addition to
`txn.date`, because the two legs of a checking→brokerage transfer settle one to
three days apart.

### Idempotency

```python
NATURAL_KEY_VERSION = 1
SEP = "\x1f"   # ASCII unit separator — mandatory

natural_key = sha256(SEP.join([
    account_id,                     # ours, not the bank's
    posted_date_iso,
    str(amount_minor),
    normalize_descriptor(description),
    str(occurrence_index),
]))
```

Four properties, each fixing a specific known failure:

1. **There is a separator.** `sha1(date + memo + amount)` — what `ofxstatement`
   does — collides: `("ABC", "12")` and `("ABC1", "2")` hash identically.
2. **`occurrence_index` is in the key.** Two $4.75 coffees on the same day are
   two transactions, not a duplicate. Numbering is by statement row order, which
   is stable for as long as the PDF is — which is the entire premise of
   re-reading it.
3. **It hashes content, never a row number.** Line order changes between
   downloads; identity must not.
4. **`natural_key` and `source_id` coexist and are never merged.**

On that last point: **bank-supplied transaction ids are not trusted as
identity**, for structural reasons rather than anecdotal ones. The OFX spec
guarantees uniqueness only within one institution and account; it ships
`CORRECTFITID` / `CORRECTACTION` to supersede ids, which is an admission that
ids get replaced; and a pending transaction changes its id when it posts (Plaid
has `pending_transaction_id` for exactly this reason). So `source_id` is stored
alongside `natural_key` and the two are never collapsed. For PDF rows
`source_id` is `NULL` — Chase's PDF carries no FITID — and the column exists
anyway.

`normalize_descriptor()` is deliberately shy: it folds Unicode form, case and
whitespace, and nothing else. Stripping card fragments or trailing store numbers
would merge genuinely distinct rows. The verbatim text survives in
`txn_identity.raw_descriptor` regardless — **normalisation never happens in
place**. Changing the normalisation changes every key ever produced, which is
what `NATURAL_KEY_VERSION` is for: bump it, keep the old rows, re-key forward.

`repo.py` uses two different idempotency mechanisms, chosen per table because
they fail differently. `ON CONFLICT DO NOTHING` where a conflict can only mean
"identical content re-inserted" (`account`, `raw_record`, an already-triaged
`review_item`) — note **not** `INSERT OR IGNORE`, which would also swallow CHECK
and NOT NULL violations. And check-then-insert for transactions, where
`OR IGNORE` would give correct row counts and a wrong database: if only the
identity row collided, the transaction and postings would still be written,
leaving **money in the ledger with no provenance**.

### Balance assertions collide on purpose

Each statement emits two `balance_assertion` rows: the closing balance dated
`period_end`, and the opening balance dated `period_start − 1 day`. That
one-day shift makes one statement's closing assertion land on exactly the same
`(account_id, as_of, commodity_id)` as the next statement's opening assertion —
the same id, because the id is a digest of those three fields, and
`balance_assertion` is UNIQUE on the triple.

That is the point, not a problem to route around. The two statements were parsed
independently, so if they disagree about the balance on the day they share, one
of them was read wrong. `repo.upsert_balance_assertions()` raises
`BalanceAssertionConflict` rather than overwriting: it is an evidence problem,
not an idempotency problem, and overwriting would destroy the only trace that
the disagreement existed. A free cross-statement check that needed no extra
data.

---

## The rebuild invariant

> **Every statement-derived ledger row must be fully reconstructible from
> `archive/` plus the migrations. Human decisions and Agent proposal audit are
> local user data and must be backed up with `ledger.db`.**

`tests/test_rebuild.py` deletes the database, re-ingests every archived PDF, and
asserts the result is row-for-row and id-for-id identical to what was there
before.

This is why **every id in the system is a pure function of content**.
`ledger/identity.py` derives `txn`, `posting`, `raw_record`,
`balance_assertion`, `review_item` and `account` ids by hashing or formatting
content — not one `uuid4()`, not one autoincrement, not one row number. The only
non-deterministic values written anywhere are `created_at` and `ingested_at`,
which are provenance rather than identity.

A single random id would not make the rebuild *wrong*. It would make it
**untestable**: the rebuilt ledger would be correct and no assertion could
recognise it as correct, and the strongest structural guarantee in the project
would quietly degrade into a slogan. This is the reason behind the constraint in
`CONTRIBUTING.md`, and it is worth understanding rather than merely obeying.

The same requirement runs upward through the stack: `parser.parse()` and
`build_entries()` are pure functions of their inputs — no clock, no randomness,
no filesystem — and `test_real_corpus_is_deterministic` asserts that building
twice produces equal objects.

Note that the archive is what is authoritative, not `extracted/`. The extraction
cache is an NDJSON mirror written *after* a successful ingest and is regenerated
on rebuild. Losing it costs nothing. Losing `archive/` costs everything, which
is why the README tells you to back that up first.

Migrations 0009 and 0011 make the non-rebuildable boundary explicit rather than weakening it silently:
`agent_proposal_run` and `agent_category_proposal` record what an external Agent suggested and what
the person or Core did with that suggestion. `category_override.agent_run_id` keeps an Agent-applied
answer attached to that audit. Like a human override and a resolved/dismissed review item,
those facts are not in statement bytes. Content-derived run/group ids make the audit deterministic;
they do not make it reproducible after the audit rows are lost. `export beancount` exports the
financial ledger, not this workflow history, so a database backup is required to preserve it.

### Deleting a statement is held to the same invariant

`ingest/forget.py` is the inverse of the pipeline, and it answers to one
standard: **what is left must equal what re-ingesting the remaining archive into
an empty database would produce.** That is the invariant above applied to a
smaller archive, and it settles the questions deletion raises in a double-entry
ledger rather than leaving them to judgement:

* removing a month in the middle leaves the balances printed after it with
  nothing to replay from. That is **correct** — a rebuild from the remaining
  archive has the same hole in the same places, because the ledger really does.
  `plan_forget` performs the deletion inside a transaction, runs the six
  ledger-level checks against the result and rolls back, so the operator is told
  before rather than after. The three archive checks are excluded and said to be
  excluded: the file is still on disk while the measurement is taken, which is a
  state that never exists once the deletion completes;
* an assertion on a day two statements share survives with its provenance moved
  to the survivor, because ingesting the survivor alone still produces that row;
* the opening entry is re-derived from the earliest surviving assertion.

Two things are refused rather than done badly. A statement whose period overlaps
a surviving one: `insert_entries` is check-then-insert, so a transaction printed
on both is booked once under whichever arrived first, and deleting that one takes
a row the survivor also evidences while `unbooked_statements` still calls the
survivor booked. And a statement holding a transaction that supersedes one
elsewhere, which nothing writes today.

The scope of the invariant is stated rather than implied, and it is stated in
the test: `account`, `category` and `commodity` are reference rows created at
ingest and are idempotent, so they survive a deletion that empties the ledger
while a rebuild from an emptied archive would not create them. The equality
holds over the eight statement-derived tables.

Three kinds of local history a deletion destroys rather than derives are named to the operator before
they confirm: a category somebody set by hand, an Agent proposal/outcome attached to the deleted
transactions, and a review item somebody resolved or dismissed. `archive/` holds documents, not what
a person or Agent decided about them — re-ingesting the identical bytes brings the transactions back
and returns the queue item as `open`, never as the answer it was given.

The database goes first and the filesystem second. A crash between them leaves
bytes with no row — `archived_not_recorded`, repaired by re-ingesting that very
file. The other order leaves a row whose bytes are gone, whose documented repair
is to re-ingest the file that was just deleted.

---

## Where the data lives, and the runtime guard

**Outside the repository, by design.**

| OS | Path |
|---|---|
| Windows | `%LOCALAPPDATA%\ledgerbox\` |
| macOS | `~/Library/Application Support/ledgerbox/` |
| Linux | `~/.local/share/ledgerbox/` |

```
archive/2026/03/<sha256>.pdf   immutable originals — back this up first
extracted/<sha256>.ndjson      rebuildable from archive/
ledger.db                      SQLite, system of record
export/ledger.beancount        plain-text escape hatch (`export beancount`)
config.toml
```

Resolution order is `--data-dir` → `$LEDGERBOX_DATA_DIR` → the OS data
directory. The archive is sharded by **ingest** date, not statement period,
because the period is not known yet — parsing is two steps away, and the whole
point of archiving first is that a file which cannot be parsed is still
preserved. Lookup is by content hash across *all* shards, so the same statement
ingested in August and again in January resolves to the one copy already on
disk.

### The guard

**ledgerbox refuses to write user data into any directory that has a `.git`
ancestor.**

`.gitignore` is a mitigation. Refusing to write is a control.

```python
def guard_data_dir(directory: Path) -> None:
    marker = find_git_marker(directory)     # checks the dir and every parent
    if marker is None:
        return
    raise DataDirRefused(...)               # subclasses SystemExit
```

Details that make it a control rather than a gesture:

- It walks **every** ancestor, and checks `.git` with `exists()` rather than
  `is_dir()` — in a worktree or submodule, `.git` is a regular *file* holding a
  gitdir pointer.
- Paths are `resolve()`d first, so a symlink pointing into a repository cannot
  slip past.
- It runs in `DataPaths.__post_init__`, not only in `DataPaths.resolve()`, so
  there is no way to construct a `DataPaths` pointing inside a repository. A
  control with a bypass is not a control.
- It also runs in `db.connection.connect()` for writable handles. `ledger.db` is
  the most sensitive artefact this project produces and it does not get a second
  unguarded door. Read-only opens are *not* guarded — reading inside a
  repository is harmless; only writing puts data there.
- `DataDirRefused` subclasses `SystemExit` deliberately: the pipeline's per-file
  `except Exception` must never swallow it, and at the CLI boundary it exits
  with a message rather than a traceback.

It is possible for the guard to reject the *default* directory — most often
because the user's home directory is itself a repository, from `git init ~` for
dotfiles or from a stray empty repo. That is not a bug; it means one
`git add -A` away from committing financial data. The error message says how to
move the data directory.

This same reality is why tests use the `git_free_tmp` fixture rather than
`tmp_path` — see `CONTRIBUTING.md`.

Full picture of what is stored and what is explicitly not protected against:
[`THREAT_MODEL.md`](THREAT_MODEL.md). **It is currently written in Chinese**; a
translation would be a welcome contribution.

---

## Deliberately rejected

Recorded so they do not get "helpfully" reintroduced. The README has a shorter
version of this table; this one is more technical.

| Rejected | Reason |
|---|---|
| **PyMuPDF** | **AGPL-3.0 — it would infect the whole project** and foreclose any future move to a permissive license. pdfplumber is MIT and exposes better data for this problem: per-word `(x0, top, x1, bottom)`, which is what makes correct column binding possible at all. It also gives per-character colour, which is how the white-on-white layout markers are dropped. Only `ingest/extract.py` imports it |
| `import beancount` | GPL-2.0-**only**, incompatible with linking into AGPL-3.0-or-later. The data model and file format are borrowed; `bean-check` is invoked as a **subprocess** — arm's length, no derivative work — and [Fava](https://github.com/beancount/fava) (MIT) comes free as a second UI |
| camelot / tabula table extraction | A bank statement "table" is **positioned text with no ruling lines**. Lattice mode finds no lines; stream mode breaks on wrapped descriptions. Word coordinates plus column x-ranges is the correct technique, and it is what the one well-maintained Chase parser in the wild (`monopoly`) also does |
| DuckDB as system of record | Forward compatibility is "best effort" and the storage version moves on most minor releases. Excellent analytics engine, **disqualifying as a twenty-year archive**. The data is tens of megabytes, so the speed difference is irrelevant; `ATTACH` the SQLite file when you want window functions |
| Floating-point money | Only `.00 .25 .50 .75` are exactly representable. Ghostfolio storing money as `Float` is a real, shipped defect in a real project. Integer minor units everywhere |
| An ORM | **The schema is the product.** An ORM hides the integer-minor-units discipline, makes migrations implicit, and obscures the exact shape of an idempotent insert — and those two things are most of `repo.py`. Explicit SQL, stdlib `sqlite3` |
| CRDT sync (Actual Budget's model) | Buys conflict-free multi-device merge at the cost of — per its own author's retrospective — schema migrations becoming "incredibly difficult" and bulk operations becoming impossible. One user does not need to pay that bill |
| Full bitemporal modelling | `posted_date` + `ingested_at` + `superseded_by` gets 95% of the benefit for 5% of the complexity |
| React / Vue + a bundler | A 2026 Vite config will not install in 2036, and "still runs in 2036" is the same requirement as "long-lived". The cost — vanilla JS growing into a 5,000-line monolith, which is exactly what the predecessor did — is paid down with enforced modularity and a 400-line-per-file split signal (P1/P2) |
| An LLM anywhere on the critical path | A regex that breaks is **loud**. A model that transposes a digit is **silent and plausible**, and will pass an eyeball check every time. Deterministic first, LLM as a fallback if ever, never the reverse — and never gated on self-reported confidence. The gate is a deterministic reconciliation failure or it is nothing |

---

## Current status and roadmap

| Phase | Scope | Status |
|---|---|---|
| **P0** | Repo skeleton, config + runtime guard, schema + migrations, Chase checking parser, reconciliation engine, CLI ingest. No web | **Done** |
| **P1** | FastAPI on `127.0.0.1`, upload endpoint, review-queue API and minimal UI, `ledgerbox serve` launcher | **Done** |
| **P2** | Categorization engine, transfer handling, dashboard and transaction table | **Done.** M1–M6 and the later connection/bulk features completed three acceptance rounds; the final two defects were repaired and independently re-tested. Subscription detection and i18n remain deferred indefinitely |
| **BYOA A7** | Versioned local classification by the user's Codex or Claude Code | **A7.0-A7.4 complete; A7.5 underway.** Proposal and exhaustive remaining-coverage triage retain separate state machines, contracts and Skills. Proposal v1 and v2 `review_first` remain pending; explicit v2 `automatic` atomically writes audit, Agent-sourced ordinary/transfer overrides, outcomes and completion. Schema 15 adds strict local policy/session evidence, persistent import jobs and exact job-to-run attribution. A successful enabled import queues one bounded job; the runner starts only the selected user-owned client in a read-only official Agent workspace. Checkout Skills are canonical and are mapped into wheel/sdist for installed-runner use. An explicit personal classification-Skill installer targets each client's documented user directory, diagnoses missing/current/known-outdated/custom by package-recognised file fingerprints, never replaces custom content by default, and restores the old directory if promotion fails. The compact sidebar separates backend readiness, client installation, Skill/MCP compatibility, live session, latest job and omissions. Triage remains audit-only; missing or mismatched client, workspace, policy, session or version fails closed. |
| **P3** | Generic CSV importer with a column-mapping wizard; real parser plugin story | Planned |
| **P4** | Investments: lots, cost basis, holdings reconciliation, corporate actions | **Deliberately skipped** |
| **P5** | Synthetic data generator, `tools/sanitize.py`, span fixtures, `SECURITY.md`, CI, `uvx ledgerbox` | Part done: CI and the tracked-data gate exist; the rest is open |

P0's acceptance criteria were hard numbers, and they are asserted in the test
suite rather than claimed here: 13 real statements passing every block-level
check; **415** transactions; deposits **$58,725.12**; withdrawals
**−$58,937.52**; net **−$212.40**; the replayed chain landing on the printed
closing balance of **$288.71**; **13 distinct** statement months including
2025-06, 2025-09 and 2025-12; triple ingestion changing no rows; a tampered
amount blocking the ingest; CJK paths not crashing; and the rebuild invariant.
Those tests skip without `LEDGERBOX_REAL_FIXTURES`. On a machine with no real
data — which is every CI runner, deliberately — the current suite is
`869 passed, 100 skipped`. Those skips are a real coverage gap and the correct trade:
CI must never need somebody's bank statements. It does mean **a green CI does
not exercise the parser against a single real document**, which is why the local
run with the fixture variable set is the one that counts.

P1's acceptance was checked the same way, against a real socket rather than a
test client: loopback-only binding with the LAN address refusing the connection;
a real statement importing with `verdict=ok`; the same bytes a second time
reporting `duplicate` and changing no rows; an unreadable PDF archived, queued
and booking nothing; 415/413/400 leaving `incoming/` empty; and dismissing a
blocking item leaving `verify` red on `unbooked_statements`.

**P4 was skipped on purpose**, and the reasoning is the same one that limits the
project to Chase checking: there are no real brokerage statements to validate
against, and a parser written without real samples produces confident,
plausible, wrong output. It resumes if and when real samples exist. The schema
keeps every investment-modelling column in the meantime, because adding them
afterwards is the painful direction.

---

## Related reading

- [`../README.md`](../README.md) — what this is and what it refuses to do
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — rule zero, DCO, the non-negotiable
  code constraints
- [`ADDING_A_BANK.md`](ADDING_A_BANK.md) — the parser interfaces, and the column
  binding lesson in full
- [`AUTOMATION.md`](AUTOMATION.md) — why there is no fetcher, and how to write
  one safely
- [`THREAT_MODEL.md`](THREAT_MODEL.md) — the security boundary (in Chinese)
- [`EXECUTION_PLAN.md`](EXECUTION_PLAN.md) — the full phased plan with DDL and
  acceptance criteria (in Chinese)
- [`PROJECT_SUMMARY.md`](PROJECT_SUMMARY.md) — the audit that produced all of
  this (in Chinese)
