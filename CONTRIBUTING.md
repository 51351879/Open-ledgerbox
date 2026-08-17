# Contributing to ledgerbox

Thank you for considering it. This document is short on ceremony and long on the
few rules that actually matter, because most of them were learned the expensive
way.

---

## Rule zero: never attach real financial data

> **Never attach a real bank statement — or any file derived from one — to an
> issue, a pull request, a discussion, or a commit.**

This outranks everything else in this document, including "the bug is hard to
reproduce without it."

That covers, without exception:

- statement PDFs (`archive/`)
- extracted text layers (`extracted/*.ndjson`)
- `ledger.db`, or any `VACUUM INTO` copy of it
- `export/ledger.beancount`
- CSV or JSON exports of your transactions
- **screenshots** of the dashboard, the CLI, or your file manager
- **pasted tracebacks and log output**, which quote transaction descriptions
  verbatim
- **file names**, which in the predecessor project all carried the last four
  digits of an account number

### Why this rule is absolute

A Chase statement PDF contains your **full account number**, your **legal
name**, and your **street address**. Those are not rotatable. A leaked API key
is a bad afternoon; a leaked account number and home address is permanent.

More importantly, it is not only your data. Zelle rows carry **the real names of
third parties** — the person you split rent with, the person who paid you back
for dinner — each one bound to a date and an amount. They never agreed to appear
in a public issue tracker, they will never know they did, and you cannot consent
on their behalf. This is a sufficient reason on its own, even if you personally
do not care about your own privacy.

### What to send instead

- **A failure, described.** The reconciliation report is designed for exactly
  this: it names a `check_id`, a severity, a page, and a bounding box, and every
  money value in it is an integer count of minor units. Paste the `check_id`,
  the shape of the failure, and the page geometry — not the row.
- **A synthetic reproduction.** `tests/synth.py` builds `Document` objects from
  coordinates in code. It has never held real data because it never had any.
  A ten-line `StatementBuilder` case that reproduces the bug is a better bug
  report than a real statement would be, and it can be merged as a test.
- **Nothing, if you cannot do either.** An unreproducible bug report is a normal
  cost of doing business. A leaked account number is not.

There is no sanitizer to fall back on yet — see
[Adding a bank](docs/ADDING_A_BANK.md#the-sanitizer-does-not-exist-yet) for what
that means in practice and what to do about it.

---

## What state the project is actually in

Read this before proposing work, so you do not build against something that
isn't there.

| Thing | Status |
|---|---|
| Chase (US) personal **checking** PDF parser | Works. Validated against 13 real statements, 415 transactions |
| `ledgerbox ingest` / `verify` / `doctor` / `export beancount` | Work. See `src/ledgerbox/cli.py` |
| Reconciliation gate (`src/ledgerbox/reconcile/checks.py`) | Works |
| SQLite ledger + forward-only migrations | Works |
| Beancount export | Works. `ledgerbox export beancount` writes `<data-dir>/export/ledger.beancount`, validated against real `bean-check`. That oracle is optional, so its tests skip unless `bean-check` is on `PATH` or `$LEDGERBOX_BEAN_CHECK` points at one |
| Web UI / upload page / review queue UI | **Planned (P1).** No `api/` or `web/` package exists |
| Categorization, transfer pairing, subscriptions | **Planned (P2).** No `analytics/` package exists |
| Generic CSV importer | **Planned (P3)** |
| Investment / brokerage parsing | **Deliberately skipped (P4).** The schema models lots and cost basis; nothing populates them |
| `tools/sanitize.py`, `tools/gen_synthetic.py` | **Planned (P5).** Do not exist |
| `SECURITY.md` | **Planned (P5).** Does not exist |
| Continuous integration | **Planned (P5).** Does not exist — the commands below are what you run, locally, yourself |

The full phased plan is [`docs/EXECUTION_PLAN.md`](docs/EXECUTION_PLAN.md) §7;
the architecture is [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

---

## Developer Certificate of Origin

Contributions are accepted under the
[Developer Certificate of Origin](https://developercertificate.org/) (DCO).
Sign off every commit:

```bash
git commit -s
```

That appends one line:

```
Signed-off-by: Your Name <your.email@example.com>
```

It is an assertion that you wrote the patch, or otherwise have the right to
submit it under the project's license. Nothing more.

**Why DCO and not a CLA.** A CLA is a legal agreement a contributor signs before
their first patch can be merged. For a single-maintainer project, that friction
costs more than it buys — it turns a two-line fix into a paperwork transaction.
The DCO is a line in a commit message.

The trade-off is stated plainly rather than hidden: the project is
[AGPL-3.0-or-later](LICENSE), and accepting outside contributions under AGPL
with no CLA means it can **never** be relicensed without every contributor's
individual agreement. That is a deliberate, one-way decision, taken before the
first external pull request (see `docs/EXECUTION_PLAN.md` §9.4). It is the
reason a proprietary or dual-licensed fork of this project cannot appear later,
which for a tool whose selling point is "this is your money, you can audit it"
is a feature.

---

## Setting up

Requires Python 3.11 or newer.

```bash
uv venv --system-site-packages
uv pip install -e ".[dev]"
python -m pytest -q
```

Everything should pass, with a batch of skips. Two things skip by design and
neither is a problem:

- the **real-statement regression tests**, unless `LEDGERBOX_REAL_FIXTURES` is
  set (see below) — expected to skip on any machine that is not the
  maintainer's;
- the **`bean-check` tests**, unless the beancount validator is on `PATH` or
  `LEDGERBOX_BEAN_CHECK` points at one. beancount is deliberately not a
  dependency of this project.

Runtime dependencies are deliberately two: `pdfplumber` and `platformdirs`.
Adding a third is a design decision, not an implementation detail — open an
issue first. This has to still install years from now.

---

## Test conventions

Two of these are non-obvious and both are load-bearing.

### Use `git_free_tmp`, not `tmp_path`

Any test that needs a **writable directory outside a git repository** must use
the `git_free_tmp` fixture from `tests/conftest.py`:

```python
def test_something(git_free_tmp: Path) -> None:
    paths = DataPaths.resolve(git_free_tmp / "data")
```

The reason: ledgerbox has a runtime guard that refuses to write user data into
any directory with a `.git` ancestor (`src/ledgerbox/config.py`,
`guard_data_dir`). On many machines the **system temp directory is itself inside
a repository** — an accidental `git init` in the home directory is common, and
on the author's machine the home directory is exactly that: an empty repo
created by mistake.

So on such a host, a guard test written with `tmp_path` **passes for the wrong
reason**: the guard fires because of the accidental ancestor repo, not because
of the `.git` the test created. It would keep passing if the guard logic were
deleted and replaced with `raise`. `git_free_tmp` walks a list of candidate
roots, skips any with a `.git` ancestor, and skips the test outright if no such
root exists on the host — so a passing test means the thing under test actually
worked.

Override the search root with `LEDGERBOX_TEST_TMPDIR` if your machine needs it.

Plain `tmp_path` is fine for tests that never touch the guard — `test_config.py`
uses it for the atomic-write helpers, for instance.

### Real statements are referenced, never committed

The 13 real Chase statements live **outside the repository** and are found
through an environment variable:

```bash
export LEDGERBOX_REAL_FIXTURES="/path/to/your/statements"   # Windows: setx
```

Tests that need them take the `real_statements` or `real_parsed` fixture. When
the variable is unset — or points somewhere with no PDFs — those tests **skip**.
They must never fail for absence. Continuous integration will never have real
financial data, and a suite that goes red without it is a suite people learn to
ignore.

If you add a test that reads real fixtures, take the fixture; do not read the
environment variable yourself.

---

## Non-negotiable code constraints

Each of these exists because violating it produced a specific, confirmed defect.
If you have a reason to break one, open an issue before writing the patch.

### Money is always an integer count of minor units

No `float`. No `Decimal`. `int` cents, everywhere, from
`src/ledgerbox/money.py` through the `INTEGER` columns of a `STRICT` table.

*Why:* of all the cent values, only `.00`, `.25`, `.50` and `.75` are exactly
representable in binary floating point — SQLite says so in its own
documentation. `Decimal` avoids that but introduces a second numeric type that
has to be converted at every database boundary, and a conversion that can be
forgotten will be. `repo.py` rejects a float before it can reach SQLite, and
there is a test asserting no floating-point column exists anywhere in the
schema.

### Do not add PyMuPDF

`pdfplumber` (MIT) is the only PDF library, and `src/ledgerbox/ingest/extract.py`
is the only module that imports it.

*Why:* PyMuPDF is **AGPL-3.0 and would infect the whole project**, foreclosing
any future move to a permissive license. pdfplumber also exposes better data for
this problem — per-word `(x0, top, x1, bottom)` — which is precisely what makes
correct column binding possible.

### Do not `import beancount`

Beancount is used as a *file format* and as an external *oracle*. If you want to
validate an export, shell out to `bean-check` as a **subprocess**.

*Why:* beancount is **GPL-2.0-only**, which is incompatible with linking into an
AGPL-3.0-or-later codebase. Arm's-length subprocess invocation is not a
derivative work — and it comes with a bonus: [Fava](https://github.com/beancount/fava)
(MIT) becomes a free second UI for anyone who wants one.

`src/ledgerbox/ledger/beancount_export.py` borrows the *file format* only, and
`tests/test_beancount_export.py::test_the_exporter_never_imports_beancount`
greps the module to enforce it — because a rule nobody checks is a comment. The
`bean-check` tests skip when the executable is absent (set
`LEDGERBOX_BEAN_CHECK` to point at one), so beancount is never a build
dependency.

### Every id must be a pure function of content

`src/ledgerbox/ledger/identity.py` derives every id — `txn`, `posting`,
`raw_record`, `balance_assertion`, `review_item`, `account` — by hashing or
formatting content. Not one `uuid4()`, not one autoincrement, not one row
number.

*Why:* the project's central invariant is *"delete `ledger.db`, rebuild it from
`archive/`, and get the same rows back."* `tests/test_rebuild.py` asserts it
byte for byte. A single random id would make that invariant **untestable** — the
rebuild would produce a correct ledger that no assertion could recognise as
correct, and the strongest structural guarantee in the project would quietly
become a slogan. The only non-deterministic values written anywhere are
`created_at` / `ingested_at`, which are provenance, not identity.

### Migrations only go forward — never edit one that has been applied

Add `src/ledgerbox/db/migrations/NNNN_name.sql`. Never touch an existing file.

*Why:* `src/ledgerbox/db/migrate.py` records the SHA-256 of every applied
migration and re-verifies all of them on every startup. Editing an applied file
raises `MigrationError` with a message telling you to add a new one instead —
which is correct, because the alternative is a database whose schema silently
does not match the code that claims to have produced it. Versions must also be
contiguous from `0001`.

If you change the schema, regenerate the snapshot: `python tools/dump_schema.py`.
`src/ledgerbox/db/schema.sql` is generated, never hand-edited, and a test
compares it byte-for-byte against a live database.

### Skip rules match whole lines exactly — never substrings

See `SKIP_LINES` and `_is_skipped` in
`src/ledgerbox/ingest/parsers/chase_checking.py`: the line is whitespace-collapsed,
case-folded, and compared for **equality** against a frozen set.

*Why:* the predecessor used substring matching, and its skip list contained
`"of"`. That eats `House of Sushi`. It also eats `Coffee Shop`, because
"Coffee" contains "of". The damage was invisible in exactly the way this project
exists to prevent: the **amount survived and the description was destroyed**, so
the totals still looked right. The same reasoning killed `startswith` for the
"Ending Balance" anchor — a wrapped description beginning "Ending Balance Yoga
Studio" ended the table, and every remaining page with it, silently.

---

## Before you open a pull request

Run all three. There is no CI to catch what you skip.

```bash
python -m pytest -q
ruff check src tests tools
mypy
```

- `ruff` is configured in `pyproject.toml`: line length 100, rule sets
  `E, F, I, UP, B, SIM`. `ruff format` is not part of the gate.
- `mypy` runs in **strict** mode over `src/ledgerbox` (the `files` setting is in
  `pyproject.toml`, so bare `mypy` is the whole command).
- Both must be clean. They are clean today; "it was already broken" is not
  available.

If your change touches parsing or reconciliation, also run the suite with
`LEDGERBOX_REAL_FIXTURES` set, if you have statements of your own. The 51 skips
becoming 51 passes is the real regression gate.

---

## Every new check needs a positive **and** a negative test

If you add a reconciliation assertion, a guard, or an invariant, it needs two
tests: one where it passes, and one where it **fails on data that is wrong in
exactly the way the check exists to catch**.

*Why:* a check nobody has ever seen fail has not been tested. It has been
observed not to crash. The predecessor's dashboard was full of code that ran
without error on every input, and it was wrong by a factor of 4.57. The single
most valuable test in this repository is
`test_a_tampered_real_statement_is_blocked` — it takes a statement that
reconciles, changes one amount, and asserts the gate closes.

`tests/test_reconcile.py` is written in pass/fail pairs throughout; follow that
shape. If your check has a "skip" outcome, test that too — in this codebase a
skipped block-level check is *blocking*, not passing, and that distinction is
worth an assertion of its own.

---

## Adding support for another bank

Read [`docs/ADDING_A_BANK.md`](docs/ADDING_A_BANK.md) first. In summary: commit
the text layer, never the PDF; bind columns by x coordinate, never by text
order; refuse loudly on an unrecognised layout; and pass every block-level check
in `src/ledgerbox/reconcile/checks.py` before the parser is considered done.

Be aware that the plugin story is a P3 deliverable. Today the registry is a
tuple in `src/ledgerbox/ingest/registry.py`, and the fixture tooling that would
make contributing a parser comfortable does not exist yet.

---

## What is in scope

**Welcome:**

- bug fixes with a failing test attached
- new reconciliation checks (with a positive and a negative test)
- parsers for layouts you have real statements for — see above
- documentation corrections, especially anywhere these docs describe something
  that does not exist. That is the worst possible defect in this project and
  reporting one is a real contribution.

**Out of scope** — stated up front so the project stays finishable (see
[README](README.md#non-goals)):

multi-user support · cloud sync or a hosted service · automatic bank data
fetching (see [`docs/AUTOMATION.md`](docs/AUTOMATION.md)) · envelope budgeting ·
tax preparation or wash-sale tracking · real-time market data · a mobile app ·
a frontend build step.

**Also out of scope: an LLM anywhere on the critical path.** Deterministic
parsing first, always. A regex that breaks is loud; a model that transposes a
digit is silent, plausible, and passes an eyeball check every time. And nothing
is ever gated on self-reported confidence — the gate is a deterministic
reconciliation failure or nothing.

---

## Reporting a security issue

`SECURITY.md` and private vulnerability reporting are P5 deliverables and do not
exist yet. Until they do: **do not open a public issue for a vulnerability**,
and above all do not attach anything that demonstrates it using real data.
Contact the maintainer privately.

The current threat model — what is protected, and the considerable list of what
deliberately is not — is [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).
(It is currently written in Chinese; a translation is welcome.)

---

## License

By contributing, you agree that your contributions are licensed under
[AGPL-3.0-or-later](LICENSE), and you certify the
[DCO](https://developercertificate.org/) by signing off your commits.
