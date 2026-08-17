# ledgerbox

**A local-first personal ledger that refuses to give you numbers it cannot prove.**

Drop a bank statement PDF into a web page running on your own machine. ledgerbox parses it, **reconciles it against the statement's own printed totals**, and only then commits it to a local SQLite ledger. If the numbers don't add up, it tells you exactly which check failed instead of quietly showing you a wrong chart.

Ledgerbox never sends your data anywhere. There is no Ledgerbox cloud, telemetry, model account,
or API key. If you explicitly hand the optional Agent CLI output to your own Codex or Claude Code,
that tool's handling of the returned data is governed by the Agent and account you chose — the local
interface is not a claim that every third-party Agent runs entirely offline.

---

> ### ⚠️ Status: pre-alpha — core ledger complete; human-reviewed BYOA workflows are in progress
>
> **Phases P0 and P1 are complete, and P2 plus its independent acceptance are complete.** Chase checking
> statements are parsed, reconciled, booked and exportable, validated against 13
> real statements. Drop a PDF on the local page and you get "imported 26
> transactions" or "needs review — nothing was booked", plus a queue of what
> failed and why. Every booked line is listed, searchable and filterable in SQL,
> and each row can be told what it actually is.
>
> There are now two charts — money in and out per month, and what was spent by
> category — and a date range that moves the headline figures, both charts and
> the table together. Both charts are decompositions of the same four figures:
> the wedges sum to the "Out" printed above them and the months sum to all four,
> **for any window**, because all three are sums of the same rows under the same
> predicate. `verify` asserts the unscoped case against an independently built
> view, so a breakdown that has quietly stopped being a breakdown is a red line
> rather than a plausible picture.
>
> The lines no rule claimed keep a labelled, hatched slice of their own and are
> never swept into an "other". That matters more than it sounds: on the author's
> own 13 statements the shipped rules claim 140 of 415 lines. Most of the rest
> still stays unclaimed by design because money moving between the author's
> own accounts is not guessed from ambiguous wording: a Zelle to
> yourself and a Zelle to a friend are the same string. The predecessor guessed,
> and that is how 82.6% of its "income" came to be transfers.
>
> `investment` is a separate transfer-kind label for principal moving between a
> bank account and the same person's investment or digital-asset account. In the
> proposal schema v1 it remains manual/Agent-proposal-only; schema v2 may apply it only when the caller
> explicitly selects automatic mode and the evidence supports the category. A platform name alone does not say
> whether a line is principal, sale proceeds, a fee, a reward or a purchase.
> Selecting it removes that one-sided movement from In/Out/Net; it does not add
> holdings, lots, gains or cost-basis accounting (those remain P4).
>
> **Optional BYOA status:** the proposal audit model, atomic review service/API, current-fact
> review page, Agent-neutral JSON CLI, optional local STDIO MCP adapter, and project Skills
> for Codex and Claude Code are implemented and locally verified. Both clients loaded their Skill and
> called the same adapter on Windows. Ledgerbox still does not invoke a model. Proposal v1 and v2
> `review_first` submissions remain pending; an explicit v2 `automatic` submission atomically writes the
> audit and Agent-sourced ordinary or transfer categories. A6 real quality review and adversarial withdrawal checks
> are complete for both clients. That review also showed why high agreement on proposed rows is not
> enough: proposal coverage, classified line coverage, and classified spending-amount coverage are
> separate measures. A6.5 C0 now reports both coverage views, and C1 has frozen a separate exhaustive
> remaining-coverage triage workflow for possible transfers, taxonomy gaps, and genuinely uncertain
> items. The exhaustive contract, separate audit tables, strict CLI/MCP submission, and local human
> review UI are implemented and synthetically accepted. Real-ledger review and taxonomy convergence
> are complete after adding `pet`, `rewards`, and `cash-deposit`, fixing an unsafe category-picker
> default, and preserving already-confirmed decisions. The current effective ledger has no remaining
> unclassified spending lines. The same-baseline Codex/Claude rerun and C5 product review are complete.
> C5 approved a versioned A7 path that supports both clients and, after explicit local-Agent enablement,
> defaults to automatic application for ordinary and transfer proposals. The Core v2 atomic state machine,
> provenance and rollback gates are implemented. A local Agent Center now stores a strict policy, reports
> client/Skill/MCP/session evidence separately, and lets the official Skill negotiate automatic mode only for
> the enabled matching client. It never launches a model; import-triggered runs remain unimplemented. See
> [`docs/A7_AUTOMATIC_CLASSIFICATION_PLAN.md`](docs/A7_AUTOMATIC_CLASSIFICATION_PLAN.md).
> Not published to PyPI,
> so `uvx ledgerbox` does not work; run it from a checkout, or double-click
> `start-ledgerbox.cmd` on Windows.
>
> Current progress, what is and is not built, and the decisions taken while
> building it: [`docs/STATUS.md`](docs/STATUS.md). The original phased roadmap is in
> [`docs/EXECUTION_PLAN.md`](docs/EXECUTION_PLAN.md). The current Agent-native product,
> official Classification Skill and open-source release order is in
> [`docs/AGENT_NATIVE_OPEN_SOURCE_PLAN.md`](docs/AGENT_NATIVE_OPEN_SOURCE_PLAN.md). The C4 same-baseline
> Codex/Claude evaluation is specified in
> [`docs/C4_FROZEN_BASELINE_PLAN.md`](docs/C4_FROZEN_BASELINE_PLAN.md), and the
> next-session handoff is [`docs/NEXT_SESSION_PROMPT.md`](docs/NEXT_SESSION_PROMPT.md).
> The audit that shaped the whole project is in
> [`docs/PROJECT_SUMMARY.md`](docs/PROJECT_SUMMARY.md).

---

## Why this exists

This project was born from auditing a homegrown statement parser that had been running for a year. It looked fine. Every chart rendered. The dashboard reported a **78% savings rate**.

The real savings rate was approximately **zero** — 13 months of statements netted **−$212.40**. The parser had been reading the *running balance* column as the *transaction amount* on every deposit row, overstating income by **4.57×** ($268,391 reported vs. $58,725 actual).

Nothing caught it. Not the parser, not the CSV, not the dashboard, not a year of looking at it.

Here is the thing: **every one of those statements had the answer printed on it.** Chase prints the beginning balance, the ending balance, the deposit total, and the withdrawal total on page one. A fifteen-line assertion would have failed on all 13 statements and stopped the bad data at the door.

That assertion is the entire thesis of this project.

### The design principle

> **Reconciliation is the product. The parser is an implementation detail.**

Every parser is eventually wrong. The only question that matters is *how long until you find out.* ledgerbox is built to **fail loudly** rather than be quietly wrong, because for money the second failure mode is far more expensive.

Three rules follow from this:

1. **Deterministic first, LLM as fallback — never the reverse.** A regex parser that breaks is *loud*. An LLM that transposes a digit is *silent and plausible* — it will pass your eyeball check every time.
2. **Never gate on self-reported confidence.** The gate is a deterministic reconciliation failure, nothing else.
3. **Unknown means refuse.** Unknown layout, unknown format, books that don't balance → review queue. Never guess.

---

## ⚠️ Scope: what actually works

**ledgerbox ships one statement parser: Chase (US) personal checking, PDF format.**

That is not a soft limitation. A statement parser is only trustworthy if it has been validated against real statements, and the author only has real Chase checking statements. Writing a parser for a bank you have never seen a statement from produces confident, plausible, wrong output — exactly the failure mode this project exists to prevent.

| Input | Status |
|---|---|
| Chase (US) personal **checking** PDF | ✅ Supported and validated against 13 real statements |
| Generic **CSV** (map your own columns) | 🔜 Phase P3 — covers most banks, cards, and brokerages |
| Chase credit card / savings / business | ❌ Not supported (different layouts, untested) |
| Any other bank's PDF | ❌ Not supported — see [Adding a bank](#adding-a-bank) |
| **Investment / brokerage accounts** | ❌ Not implemented. The database schema models lots and cost basis, but no brokerage parser exists |
| Automatic bank syncing | ❌ Deliberately out of scope — see [Automation](#automation) |

If your bank is not Chase checking, there is no supported import path today. The planned routes are
the P3 CSV mapper or a real-sample-backed parser adapter; neither should be described as supported
before it exists and passes reconciliation against actual statements.

**The supported platform is Windows.** Everything above holds on one validated environment:
Windows 11, PowerShell, a current Chromium-based browser, and the Windows Narrator screen
reader for the Agent flows. That is the machine the maintainer runs their own money on, and
"validated" means exactly that machine — the same real-sample rule that limits the parser
list. The code is plain Python and SQLite and will largely run elsewhere, and the setup
command deliberately fails closed rather than guessing at non-Windows client paths — but
macOS, Linux, other browsers, and other screen readers are **untested, unsupported, and
community territory**. Adaptations are welcome as pull requests that come with their own
validation, not as claims added to this table.

| Platform | Status |
|---|---|
| Windows 11 + PowerShell | ✅ Supported; every real-machine gate ran here |
| Windows Narrator (Agent flows) | ✅ Checked by a real acceptance pass |
| macOS / Linux | ❌ Untested — community adaptation welcome |
| NVDA / JAWS / VoiceOver, non-Chromium browsers | ❌ Untested |

---

## Non-goals

Stated up front so the project stays finishable:

- **No multi-user support.** One person, one machine.
- **No cloud sync or hosted service.** Directly contradicts the point.
- **No automatic bank data fetching.** See [Automation](#automation) for research and how to build it yourself.
- **No envelope budgeting.** [Actual Budget](https://actualbudget.org) does this well; use both.
- **No tax preparation or wash-sale tracking.** Nobody in this ecosystem gets it right; not attempting it.
- **No real-time market data.** End-of-day prices are sufficient.
- **No mobile app.**

---

## Quick start

> `uvx ledgerbox` is the target interface and does **not** work yet — nothing is published
> to PyPI until P5. From a checkout, everything below works today.

```bash
ledgerbox
```

Starts a server on `http://127.0.0.1:8787` and opens it in your browser. Drag statement
PDFs onto the page. Each one is archived, reconciled against its own printed totals, and
only then booked; anything that fails lands in the review queue with the failing check
attached, and nothing from it enters the ledger.

The server binds loopback and there is **no flag to change that**. It has no
authentication of any kind, so the bind address is the access control.

Or use the CLI directly — none of these need the web dependencies:

```bash
ledgerbox ingest ~/statements/20250131-statement.pdf
ledgerbox verify              # re-run all reconciliation checks
ledgerbox forget <id>         # measure what removing a statement would cost
ledgerbox forget <id> --yes   # …and do it
ledgerbox export beancount    # plain-text escape hatch
ledgerbox doctor              # data dir, schema version, pending reviews
```

`forget` without `--yes` deletes nothing and exits non-zero: it performs the
deletion inside a transaction, runs every ledger-level check against the result,
rolls back, and prints what it saw. A command that removed nothing has no
business reporting success to a cron job.

`verify` exits non-zero if anything is outstanding — including a statement that was
archived but never booked, *even if you have dismissed its review item*. Clearing the
queue records that you looked; it never books a transaction, and it cannot make `verify`
green over an incomplete ledger.

### Optional Agent JSON and local MCP interfaces (development contract)

These commands are available now for a local tool that can invoke shell commands:

```bash
ledgerbox agent status
ledgerbox agent categories
ledgerbox agent candidates --since 2025-01-01 --until 2025-12-31 --limit 500
ledgerbox agent validate-proposal < proposal.json
ledgerbox agent submit-proposal < proposal.json
ledgerbox agent validate-triage < triage.json
ledgerbox agent submit-triage < triage.json
```

Every successful command writes exactly one versioned JSON document to stdout. Expected failures
write versioned JSON to stderr: exit `2` is invalid input, `3` means ledger verification refused to
produce candidates, and `4` is a stale or otherwise conflicting proposal. Candidate rows contain
only `txn_id`, date, direction, signed integer `amount_minor`, currency, and `raw_descriptor`.
Descriptions are untrusted bank data, never instructions.

`validate-proposal` and `validate-triage` write nothing. A draft may omit only its workflow's
derived content IDs; validation derives them and returns the exact normalized object that must be
submitted. `submit-proposal` remains strict: schema v1 and v2 `review_first` write only pending
proposal audit rows, while explicit v2 `automatic` atomically writes the audit and Agent-sourced
effective category. There is intentionally no filter-shaped apply command or separate approval tool.
Triage submission is a second audit-only workflow: it must account for
every currently eligible row exactly once, and it does not itself apply a category.

The proposal and triage workflows share seven capabilities over an optional local STDIO MCP process:

```bash
pip install -e ".[mcp]"
ledgerbox-mcp --data-dir "/your/explicit/ledgerbox/data"
```

The process is normally started by the user's Codex or Claude Code client, not by hand. The data
directory is fixed when it starts; tools cannot supply a path or SQL. Five tools are read-only;
`ledgerbox_submit_proposal` and `ledgerbox_submit_triage` are the two audit-only write tools. Neither
can approve or directly classify a transaction. Human review remains the only path to an effective
category. Classification and exhaustive triage use separate project Skills and contracts, neither
of which contains credentials or private connection settings. See
[`docs/AGENT_SETUP.md`](docs/AGENT_SETUP.md) for local install/connect/disconnect steps and the exact
privacy boundary, [`docs/AGENT_CONTRACT.md`](docs/AGENT_CONTRACT.md) for the proposal contract,
[`docs/TRIAGE_AGENT_CONTRACT.md`](docs/TRIAGE_AGENT_CONTRACT.md) for the exhaustive triage contract, and
[`docs/AGENT_CLASSIFICATION_PLAN.md`](docs/AGENT_CLASSIFICATION_PLAN.md) for the delivery sequence.

---

## How it works

```
┌── 1. INGEST ─────────────────────────────────────────────────┐
│  SHA-256 the file → already seen? return "duplicate", no-op  │
│  Copy original into archive/ (immutable, content-addressed)  │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌── 2. IDENTIFY ───────────────────────────────────────────────┐
│  PDF /Producer + first-page markers → versioned layout config│
│  Unknown layout → review queue. Never guess.                 │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌── 3. EXTRACT ────────────────────────────────────────────────┐
│  pdfplumber extract_words() + column x-ranges                │
│  Every field keeps (page, x0, top, x1, bottom) provenance    │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌── 4. RECONCILE ──────────── the gate ────────────────────────┐
│  0. double-entry postings sum to zero      (structural)      │
│  1. running balance chain: bal[n-1]+amt[n] == bal[n]  ← best │
│  2. beginning + Σ amounts == ending                          │
│  3. statement's own subtotals (deposits/withdrawals/fees)    │
│  4. transaction count      5. period continuity              │
│                                                              │
│  ALL PASS → commit.   ANY BLOCK FAILS → review queue.        │
└──────────────────────────────────────────────────────────────┘
                              ↓
┌── 5. COMMIT ─────────────────────────────────────────────────┐
│  Idempotency key → dedupe → single-entry to double-entry     │
│  Write txn + postings to SQLite                              │
└──────────────────────────────────────────────────────────────┘
```

**Check #1 is the strongest and it is free** — Chase already prints the running balance next to every transaction. Check #2 alone is *not sufficient*: two equal-and-opposite errors cancel out and it passes.

---

## Architecture & tech stack

Documented in detail so you (or an AI coding assistant) can extend it confidently.

### Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | PDF ecosystem, low contributor barrier |
| Web | FastAPI + uvicorn, bound to `127.0.0.1` | Uploads, validation, OpenAPI out of the box |
| Database | **SQLite** (`STRICT` tables, WAL), stdlib `sqlite3` | File format stable since 2004; a Library of Congress recommended preservation format |
| ORM | **None** | The schema *is* the product. An ORM hides the integer-cents discipline and makes migrations implicit |
| PDF | **pdfplumber** (MIT) | Gives per-word `(x0, top, x1, bottom)` — required to bind the amount and balance **columns** correctly |
| Frontend | **Vanilla ES modules, no build step** | A 2026 build config won't install in 2036. This is the same requirement as "long-lived" |
| Charts | **Hand-written SVG, no library** | A test greps every shipped `.js` for `innerHTML` and friends, with no exclusions. A minified bundle that tripped it could only be admitted by weakening the guard, and being a blunt instrument is that guard's whole point. Thirteen monthly points do not need a charting library. (This row said *Chart.js, vendored locally* until P2 planning; whether Chart.js would actually trip the guard was never measured — the choice is to not have to find out) |
| Tests | pytest + pytest-regressions | `dataframe_regression` gives per-cell diffs on transaction tables |
| Packaging | uv / uvx + `pyproject.toml` | `uvx ledgerbox` bootstraps its own Python |

**Runtime dependencies: five.** `fastapi`, `uvicorn`, `pdfplumber`, `platformdirs`, `python-multipart`. Kept deliberately small — this has to still install years from now.

### Deliberately rejected

Recording these so they don't get "helpfully" reintroduced:

| Rejected | Reason |
|---|---|
| **PyMuPDF** | **AGPL-3.0 — infects the whole project.** pdfplumber is MIT and exposes better coordinate data anyway |
| `import beancount` | GPL-2.0-**only**. We borrow its *data model* and *file format*, and shell out to `bean-check` as a **subprocess** — arms-length, no derivative work, and you get [Fava](https://github.com/beancount/fava) (MIT) as a free second UI |
| camelot / tabula table extraction | Bank statement "tables" are **positioned text with no ruling lines**. Lattice finds no lines; stream breaks on wrapped descriptions. Word coordinates + column x-ranges is the correct technique |
| DuckDB as system of record | Forward compatibility is only "best effort" and the storage version bumps most minor releases. Correct as an analytics engine, disqualifying as a 20-year archive. `ATTACH` the SQLite file when you want window functions |
| Floating-point money | SQLite documents it plainly: the only cents values exactly representable are `.00 .25 .50 .75`. **Integer minor units everywhere** |
| CRDT sync (Actual Budget's model) | Buys conflict-free multi-device merge at the cost of — per its own author's retrospective — schema migrations becoming "incredibly difficult". One user doesn't need to pay that |
| React / Vue + bundler | See "no build step" above |

### Data model

Double-entry. This is not accounting purism — it makes **double-counting a transfer between two accounts you own structurally impossible**, which is the single largest source of garbage numbers in naive personal finance tools. Store postings; render single-entry in the UI.

Being exact about the scope, because the loose version of that sentence is itself the kind of claim this project is against: it holds for a transfer whose *both* ends are accounts in the ledger. A one-sided transfer — a card payment, a Zelle to yourself — has no second account here, so its other leg is an ordinary expense and it is **excluded by a flag rather than by the structure**. Two things set that flag and one view composes them: a conservative rule set, which matches none of the 13 real statements, and a person marking a row in the transaction table, which is the only one of the two that does anything on this ledger today. `verify` asserts that both figures reporting income and spending agree about the result. [`docs/STATUS.md`](docs/STATUS.md) §5.43 has the full account of why that agreement is an assertion rather than a paragraph.

Key tables (full DDL in [`docs/EXECUTION_PLAN.md`](docs/EXECUTION_PLAN.md) §3.2):

```
BRONZE (append-only, never updated)
  source_file      content-addressed by SHA-256; re-upload is a no-op by construction
  raw_record       verbatim JSON payload + page/bbox provenance

SILVER
  account          hierarchy + kind + booking_method + is_own_account
  commodity        USD, VTSAX, … with scale and CUSIP/ISIN (tickers get reused)
  txn              date, payee, is_transfer, superseded_by
  posting          amount_minor (integer) AND quantity_scaled — kept separate,
                   so one row means "150.00 USD" and "10 IBM" at the same time
  lot              tax lots as first-class rows (schema present, unused for now)

IDENTITY
  txn_identity     natural_key + source_id side by side, never merged

CONTROL
  balance_assertion   the statement's own printed balances
  review_item         what failed, why, and where on the page
```

Two details worth knowing before you extend it:

- **`posting.quantity_scaled` is separate from `posting.amount_minor`.** Collapsing them into one `amount` column is the classic beginner mistake in investment modeling. GnuCash and Beancount both keep them separate.
- **`posting.date` exists in addition to `txn.date`**, because the two legs of a checking→brokerage transfer post one to three days apart.

### Idempotency

```python
NATURAL_KEY_VERSION = 1
SEP = "\x1f"   # non-printing separator — mandatory

natural_key = sha256(SEP.join([
    account_id,                       # ours, not the bank's
    posted_date_iso,
    str(amount_minor),
    normalize_descriptor(description),
    str(occurrence_index),            # two $4.75 coffees on one day are two txns
]))
```

Without the separator, `("ABC","12")` and `("ABC1","2")` collide. Without `occurrence_index`, genuine same-day duplicates get silently merged.

**Bank-supplied transaction IDs are not trusted as identity.** The OFX spec only guarantees uniqueness within one institution+account, ships `CORRECTFITID`/`CORRECTACTION` to supersede IDs, and pending→posted transitions change the ID entirely. We store `source_id` alongside `natural_key` and never collapse the two.

---

## Where your data lives

**Outside this repository, by design.**

| OS | Path |
|---|---|
| Windows | `%LOCALAPPDATA%\ledgerbox\` |
| macOS | `~/Library/Application Support/ledgerbox/` |
| Linux | `~/.local/share/ledgerbox/` |

```
archive/2026/03/<sha256>.pdf   immutable originals — back this up first
extracted/<sha256>.ndjson      rebuildable from archive/
ledger.db                      SQLite, system of record
export/ledger.beancount        plain-text escape hatch
```

Override with `--data-dir`. A **runtime guard refuses to write user data into any directory containing `.git`** — `.gitignore` is a mitigation, physical separation is a control.

**Invariant enforced by CI:** every statement-derived row in `ledger.db` is rebuildable from
`archive/` + `migrations/`. Human category overrides, resolved-review decisions, and Agent
proposal/outcome history are deliberately not derivable from a PDF. Back up `ledger.db` to keep them.

### Back it up three ways

Different mechanisms fail differently:

1. `VACUUM INTO` + offsite copy → recovers from a bad write
2. Litestream or `sqlite3_rsync` → recovers from disk death
3. **Git the `archive/` folder and the beancount export** → recovers the financial evidence from
   *your own software being wrong*. The beancount export does not contain Agent proposal history,
   and archive/ contains neither proposal outcomes nor hand-set categories; the database backup in
   step 1 is the copy of those local decisions.

People skip the third. At a twenty-year horizon it's the one that matters.

---

## Adding a bank

Each bank is a **plugin** with its own fixtures. You maintain your statements; the maintainer never sees them.

The most important convention: **commit the text layer, not the PDF.**

Serialize `(text, x0, top, x1, bottom)` spans to JSON and commit *that*. It's reviewable in a diff, tiny, and — critically — **you can redact it with confidence, which you cannot do with a PDF** (content streams, embedded fonts, XMP metadata, and incremental-update history all leak).

The test suite splits accordingly:

- `extract_spans(pdf)` — tested against a few synthetic PDFs
- `spans_to_transactions(spans)` — where all bank logic lives — tested against many JSON fixtures

`tools/sanitize.py` — **not written yet (P5)** — will turn a real statement into a committable fixture with stable pseudonyms, rescaled amounts, and shifted dates. Until it exists, do not attempt this by hand: build a synthetic statement in code instead. See [`docs/ADDING_A_BANK.md`](docs/ADDING_A_BANK.md).

See [`docs/ADDING_A_BANK.md`](docs/ADDING_A_BANK.md).

> **Never attach a real bank statement to an issue or pull request.**

---

## Automation

ledgerbox does **not** fetch data from your bank. Manual upload is the supported workflow.

That said, [`docs/AUTOMATION.md`](docs/AUTOMATION.md) contains detailed research (verified August 2026) on what is actually possible, so you can build it yourself — including with an AI coding assistant. Headlines:

- **Chase has no consumer API.** `developer.chase.com` is entirely behind JPMorgan enterprise SSO.
- **OFX / Direct Connect is dead.** `ofx.chase.com`'s CNAME target returns NXDOMAIN. Chase moved to EWC+, where the OAuth token is held by an aggregator — you never possess a credential you could hand to third-party software. GnuCash and ofxtools have no path.
- **FDX sells the specification, not the data.** It operates no data interface, and its FAPI + mTLS + dynamic-client-registration stack structurally excludes natural persons.
- **CFPB's Section 1033 open-banking rule is enjoined** (Oct 2025, *Forcht Bank v. CFPB*) and under reconsideration.
- **The one realistic path is SimpleFIN Bridge, ~$15/year** — read-only by protocol design, token stored on your machine, revocable unilaterally. Your bank credentials still live at a third-party aggregator; no aggregator option changes that.
- **A serious warning about community finance MCP servers.** The two most-starred ones ask for your brokerage username, password, and **TOTP seed**. Handing over a TOTP seed makes your 2FA decorative. Stars are a popularity signal, not a security signal.

The safe shape — which is what this project already is — separates the process that holds secrets from the process an AI agent talks to:

```
[fetchers: hold ALL secrets, run on a timer]
        ↓
   ledger.db  (your disk)
        ↑  read-only, PRAGMA query_only=ON
[a ~150-line MCP server you write yourself]  ← zero credentials, zero egress
        ↑
     your agent
```

Note there is **no official SQLite MCP server** in 2026 — the reference implementation was archived in 2025. Those ~150 lines are yours to write. Given the alternative is trusting a stranger with your brokerage password, it's a good trade.

---

## Privacy & security

| | |
|---|---|
| **Network** | Binds `127.0.0.1` only. Zero outbound requests, zero telemetry, zero CDN |
| **Encryption at rest** | **None**, unless your disk volume is encrypted (BitLocker / FileVault / LUKS). Stated plainly rather than implied |
| **Authentication** | None. The security boundary is "the local user of this machine" |
| **What's stored** | Transactions, balances, merchant names, counterparty names, and original statement PDFs — which contain your full name, address, and account number |
| **Out of scope** | Malicious local users, a compromised OS, hostile browser extensions |

Do not expose the server beyond loopback. Full threat model: [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). One rule above all others:

> **Never attach a real bank statement — or any real transaction data — to an issue, pull request, or discussion.**

Build fixtures in code instead: `tests/synth.py` assembles statements from coordinates and **can never contain real data because it never had any.**

Two tools are planned for this and **do not exist yet (P5)**: `tools/sanitize.py` (real statement → committable, redacted fixture) and `tools/gen_synthetic.py` (a generated financial life — biweekly payroll with withholding, rent with variance, subscriptions, travel). Do not hand-redact a statement in the meantime; see [`docs/ADDING_A_BANK.md`](docs/ADDING_A_BANK.md) for why that goes wrong.

Contributions are accepted under the [DCO](https://developercertificate.org/) (`git commit -s`).

---

## License

[AGPL-3.0-or-later](LICENSE).

Chosen to match the norm for self-hosted finance tools (Firefly III, Ghostfolio, Maybe) and to keep a hosted fork reciprocal. Note that §13's network clause barely triggers for a genuinely local single-user app — its protection here is largely deterrent, and that's an honest description rather than a claim.

## Prior art

[Actual Budget](https://actualbudget.org) · [Firefly III](https://firefly-iii.org) · [Beancount](https://beancount.github.io) + [Fava](https://github.com/beancount/fava) · [Ghostfolio](https://ghostfol.io) · [monopoly](https://github.com/benjamin-awd/monopoly)

Actual and Firefly are more mature budgeting tools and neither parses PDFs. Beancount has the best data model in the space and this project borrows from it directly. If you want envelope budgeting, use Actual. If you want a reconciled ledger built from the statements your bank actually sends you, that's the gap ledgerbox fills.
