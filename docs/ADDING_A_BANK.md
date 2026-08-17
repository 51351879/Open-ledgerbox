# Adding a bank

How to write a statement parser for ledgerbox, and — more importantly — how to
do it without ever putting a real statement into this repository.

Read [`../CONTRIBUTING.md`](../CONTRIBUTING.md) first. Rule zero applies here
more than anywhere else in the project: **never attach a real bank statement to
an issue, a pull request, or a commit.**

---

## Honest status before you start

ledgerbox ships exactly one parser: **Chase (US) personal checking, PDF**. It is
the only layout the author has real statements for, and writing a parser for a
bank you have never seen a statement from produces confident, plausible, wrong
output — the exact failure mode this project exists to prevent.

What exists today:

- `src/ledgerbox/ingest/parsers/base.py` — the `Parser` protocol and the data
  classes a parser returns
- `src/ledgerbox/ingest/parsers/chase_checking.py` — the reference
  implementation, and the only one
- `src/ledgerbox/ingest/registry.py` — a tuple named `PARSERS`, plus
  `identify()` / `identify_or_raise()`
- `src/ledgerbox/ingest/extract.py` — PDF → positioned words, and the JSON
  fixture format
- `tests/synth.py` — a builder that constructs statement `Document` objects from
  coordinates, in code

What does **not** exist yet, and matters to you:

| Missing | Phase | Consequence for a contributor |
|---|---|---|
| `tools/sanitize.py` | P5 | There is no supported way to turn a real statement into a committable fixture. See [below](#the-sanitizer-does-not-exist-yet) |
| `tools/gen_synthetic.py` | P5 | No synthetic-PDF generator. `tests/synth.py` is **not** this — see [below](#what-testssynthpy-is-and-is-not) |
| `tests/fixtures/spans/` | P5 | The span-JSON fixture directory is planned but not populated. `Document.to_json()` / `Document.from_json()` already define the format |
| A real plugin system (separate packages, entry points) | P3 | Today a parser is a module in `parsers/` and one entry in a tuple |
| The generic CSV importer | P3 | For most banks this will be the realistic path, and it does not exist yet |
| Continuous integration | P5 | Nothing checks your parser except you, locally |

None of that stops you writing a parser. It does change how you test it.

---

## The core convention: commit the text layer, not the PDF

Serialize `(text, x0, x1, top, bottom)` spans to JSON and commit **that**. Never
the PDF.

Three reasons, in increasing order of importance:

1. **It is readable in a diff.** A reviewer can see that a fixture changed from
   `"AMOUNT"` at `x1=462.9` to `x1=460.7` and understand the consequence. A
   changed PDF is an opaque blob.
2. **It is small.** A statement's text layer is tens of kilobytes of JSON.
3. **You can redact it with confidence, and you cannot redact a PDF with
   confidence.** This is the reason that actually decides it.

On that last point, be specific about what leaks out of a PDF you *think* you
have cleaned:

- **content streams** — the drawing operators still hold the original glyphs
  even after a viewer shows something else
- **embedded font subsets** — the subset is built from the characters actually
  used, so the font itself is a partial index of the document's text
- **XMP and document-info metadata** — producer, author, title, and whatever the
  generator put there
- **incremental update history** — PDF appends revisions rather than rewriting;
  "redacted" content routinely survives as an earlier revision inside the same
  file
- **the invisible text layer** — Chase statements carry white-on-white 1pt
  layout markers (`*start*transaction detail`, `*end*summary`). If you did not
  know those were there, consider what else you do not know is there

A span JSON file has exactly one kind of content: strings and four floats each.
You can read all of it. That is the whole argument.

### What `tests/synth.py` is, and is not

`tests/synth.py` is a **test helper that constructs `Document` objects in
Python, from coordinates written in code.** It is not a PDF generator, it does
not read PDFs, and it does not sanitize anything. It builds the object that
`extract_spans()` would have returned, directly:

```python
from synth import Row, StatementBuilder

doc = StatementBuilder(
    period="January 01, 2025 through January 31, 2025",
    beginning="$820.15",
    ending="$844.82",
    components=(("Deposits and Additions", "37.11"), ("Fees", "-12.44")),
    rows=[
        Row("01/02", "Zelle Payment From A Name 10000000001", "37.11", "857.26"),
        Row("01/03", "Card Purchase 01/02 Some Merchant CA", "-12.44", "844.82"),
    ],
).build()
```

Its geometry constants (`AMOUNT_X0, AMOUNT_X1 = 432.2, 462.9`,
`BALANCE_X0, BALANCE_X1 = 500.6, 534.7`) are the measured values from the real
corpus, so the synthetic documents exercise the same column-binding code paths
that real ones do. It can never contain real data, because it never had any.

The planned `tools/gen_synthetic.py` (P5) is a different thing: a generator of a
whole simulated financial life — biweekly payroll with withholding, rent with
variance, subscriptions, travel — rendered as PDFs. Do not conflate the two.

### The sanitizer does not exist yet

The README and the threat model both refer to `tools/sanitize.py`, which would
take a real statement PDF and emit a committable span JSON with stable
pseudonyms, rescaled amounts, and shifted dates. **It has not been written.** It
is a Phase P5 deliverable.

So the contribution path "sanitize a real statement and submit the fixture" is
**not currently available**. Do not attempt to hand-roll it: a partial
sanitizer is worse than none, because it produces a file everyone believes is
clean.

Until it lands, contribute one of these instead:

1. **Synthetic statements authored by coordinate.** Use `tests/synth.py`'s
   `StatementBuilder`, or write a similar builder for your bank's geometry. This
   is how every parser test in this repository works today, and it is the
   recommended path. Author the spans from your understanding of the layout, not
   by copying values out of a real file.
2. **A parser plus a description, no fixture.** Open a pull request with the
   parser and a written account of the layout — column positions, header text,
   summary block shape, the shape of a wrapped description. A maintainer cannot
   merge it without a test, but the analysis is the expensive part and it is
   reviewable on its own.
3. **Keep your fixtures out of tree.** Point `LEDGERBOX_REAL_FIXTURES` at a
   directory outside the repository and run your regression suite locally, the
   same way the Chase corpus is handled. Tests that need it must **skip** when
   it is unset, never fail.

If you write a sanitizer, propose it as its own pull request with its own threat
analysis. It is a security tool; it deserves to be reviewed as one.

---

## The interfaces you implement

### `Parser` (`ingest/parsers/base.py`)

```python
@runtime_checkable
class Parser(Protocol):
    parser_id: str
    parser_version: str

    def matches(self, doc: Document) -> bool:
        """True only when this parser recognises the layout with certainty."""

    def parse(self, doc: Document) -> ParsedStatement: ...
```

- `parser_id` — a stable slug, e.g. `"chase_checking"`. It is written into every
  `raw_record` row, so changing it later orphans provenance.
- `parser_version` — bump it whenever output changes for the same input.
- `matches()` — see [mutual exclusivity](#registration-and-mutual-exclusivity).
- `parse()` — returns a `ParsedStatement`, or raises `ParseError`.

A parser's job **ends at "here is what the page says."** It does not decide
whether the statement is trustworthy; that is the reconciler's job, and keeping
the two apart is what stops a parser from papering over its own mistakes.

### What `parse()` returns

```python
@dataclass(frozen=True, slots=True)
class ParsedStatement:
    institution: str
    account_mask: str | None      # last four digits only, or None
    account_subtype: str          # "checking", "credit_card", …
    currency: str                 # "USD"
    period_start: date
    period_end: date
    summary: StatementSummary
    transactions: tuple[StatementTxn, ...]
    parser_id: str
    parser_version: str
    warnings: tuple[str, ...] = ()
```

`statement_month` is a property, derived from `period_end`. See
[Take the month from the period's end](#take-the-month-from-the-periods-end).

```python
@dataclass(frozen=True, slots=True)
class StatementTxn:
    posted_date: date
    description: str
    amount_minor: int             # signed integer cents. Never a float
    balance_minor: int | None     # the printed running balance, if any
    row_index: int
    provenance: Provenance        # page, x0, top, x1, bottom
    amount_source: str = "column" # or "derived"
```

`Provenance` is not optional decoration. It is the difference between "the
ledger says −12.44" and "page 2, box (438, 266)–(461, 266) of the archived PDF
says −12.44". Every failure report carries it through to the review queue.

`amount_source="derived"` marks a row whose amount was recovered from the
balance chain (`bal[n] − bal[n−1]`) because the amount column was empty. It is a
recovery path, and a statement where it fires often is a statement whose layout
has drifted — which is why it is recorded rather than silently applied.

```python
@dataclass(frozen=True, slots=True)
class StatementSummary:
    beginning_balance_minor: int
    ending_balance_minor: int
    components: dict[str, int] = field(default_factory=dict)
    declared_transaction_count: int | None = None
```

`components` holds the statement's own printed subtotals, **verbatim by label**,
signed: `{"Deposits and Additions": 234567, "Fees": -1200}`. This is the
reconciler's evidence and the single most valuable thing your parser extracts.
Do not normalise the labels, do not merge them, do not drop the ones you do not
recognise.

`declared_transaction_count` is a separate field rather than an entry in
`components` because a count is not money — and everything entering `components`
goes through a money parser that demands two decimal places, so a count could
never have arrived there anyway.

Anything fatal raises `ParseError`. Anything merely odd goes in `warnings`.

### Registration and mutual exclusivity

```python
# src/ledgerbox/ingest/registry.py
PARSERS: tuple[Parser, ...] = (CHASE_CHECKING,)
```

Add your parser's module-level singleton to that tuple.

`identify(doc)` runs `matches()` on every registered parser and collects the
hits. **`matches()` implementations must be mutually exclusive**: if two parsers
claim the same document, `identify()` raises `RuntimeError("ambiguous layout")`
rather than picking one. Order in the tuple affects reporting only — never
resolution. There is no "first match wins" and there will not be one.

If nothing matches, `identify()` returns `None` and `identify_or_raise()` raises
`UnknownLayout`, carrying the producer string and the page count. The pipeline
turns that into a review item. **It never falls back to the closest parser and
it never guesses from the filename.**

Look at how `ChaseCheckingParser.matches()` earns its certainty:

```python
if PRODUCER_MARKER not in (doc.producer or ""):
    return False
text = " ".join(page.text() for page in doc.pages)
return (
    "JPMorgan Chase Bank" in text
    and "CHECKING SUMMARY" in text
    and "TRANSACTION DETAIL" in text
)
```

Note that markers are searched **document-wide, not on page 1**. Four of the
thirteen real statements carry a longer message block up front that pushes
`CHECKING SUMMARY` onto page 2; requiring page 1 rejected a third of the corpus
outright, including two of the three months the predecessor also lost.

---

## The expensive lesson: bind columns by x coordinate

This is the single most important thing in this document. It is the defect the
whole project was built in response to.

### What went wrong

The predecessor parser used a **text-order heuristic**: "the first number in the
block is the amount, the second is the balance."

On Chase deposit rows, PDF text extraction pushes the amount into a *different*
text block. The block therefore held only the balance — and the balance was
booked as the amount. Every one of 72 deposits was wrong. Reported income came
out **4.57× too high** ($268,391 against a true $58,725); the dashboard showed a
78% savings rate against a true rate of approximately zero. Nobody noticed for a
year.

The structural evidence, once someone looked, had zero exceptions: all 72
credits had an empty `balance` field, and all 343 debits had one.

**PDF text order is not layout.** It is the order a generator happened to emit
drawing operations. Treating it as column structure is not a bug that was
introduced; it is an assumption that was never true.

### What to do instead

Bind every number to a column by **x coordinate**, and learn the column
positions **from the header row of each page**.

`src/ledgerbox/ingest/extract.py` gives you the geometry:

```python
@dataclass(frozen=True, slots=True)
class Span:
    text: str
    x0: float
    x1: float
    top: float     # grows downward, pdfplumber's convention
    bottom: float
```

`group_rows(spans)` clusters spans into visual rows by `top` and sorts each row
by `x0` — never by emission order.

Then, per page, find the header and read the geometry off it:

```python
class Columns:
    def __init__(self, header: dict[str, Span]) -> None:
        self.date_x0    = header["DATE"].x0
        self.desc_x0    = header["DESCRIPTION"].x0
        self.amount_x1  = header["AMOUNT"].x1
        self.balance_x1 = header["BALANCE"].x1

    def classify(self, span: Span) -> str | None:
        if abs(span.x1 - self.amount_x1)  <= COLUMN_TOLERANCE: return "amount"
        if abs(span.x1 - self.balance_x1) <= COLUMN_TOLERANCE: return "balance"
        return None
```

Three details that all matter:

**Anchor on the right edge, not the left.** Statement numbers are right-aligned.
Measured across the 13-statement corpus, an amount's `x1` sits within **0.08 pt**
of the AMOUNT header's `x1`, and a balance's within 0.08 pt of BALANCE's — while
the two columns are about **72 pt apart**. Left edges wander by ±13 pt, because a
four-digit number and a six-digit number start in different places. The tolerance
in the Chase parser is `COLUMN_TOLERANCE = 2.0` — twenty-five times the measured
spread, and still nowhere near able to reach the neighbouring column.

**Learn the positions per page. Do not hard-code them.** The header geometry
differs between page 1 and later pages *of the same statement*. Across the real
corpus, `BALANCE.x1` takes exactly two values — **534.7** and **532.5** — and
`tests/test_parse_chase.py::test_real_statements_use_two_header_geometries`
asserts that both appear. The AMOUNT column shifts by the same 2.2 pt (462.9 on
page 1, 460.7 on later pages; the header word left edges move 432.2 → 430.0 and
500.6 → 498.4). Any constant you write down is wrong on some page.

**If the header row is missing, refuse.** The Chase parser raises `ParseError`
rather than returning zero transactions:

> `TRANSACTION DETAIL has no 'DATE DESCRIPTION AMOUNT BALANCE' header — the
> column positions are learned from that row, so without it nothing can be bound
> to a column`

A layout change that produced an empty result would otherwise look exactly like
a quiet month.

### Bound the description column on both sides

`in_description()` checks `desc_x0 - slack <= span.x0` **and**
`span.x1 <= amount_x1`. A left bound alone lets the right page margin in: Chase
prints a vertical barcode at x0≈607 that pdfplumber emits as a single 20-digit
"word" sharing a baseline with a real row. With a left-only test it was accepted
as a wrapped description and glued onto two real transactions — amounts right,
descriptions wrong, no warning. That is the precise shape of failure this
project exists to catch.

Related: when you test a row for continuation, test **and merge the same spans**.
Testing `row[0]` while merging `row_text(row)` makes the right-hand bound
decorative, because spans are sorted by `x0` and the margin barcode is always
last.

---

## Other rules learned the hard way

### The amount regex must require `\.\d{2}`

`src/ledgerbox/money.py`:

```python
_DIGITS_RE = re.compile(r"^(?P<int>\d{1,3}(?:,\d{3})*|\d+)\.(?P<frac>\d{2})$", re.ASCII)
```

A bare integer is **not** an amount. The predecessor made the decimal part
optional, which means a check number in the description column would have been
read as a dollar figure. No such row exists in the current corpus — that is
luck, not safety. Chase statements with a "Checks Paid" section would have
triggered it.

Also rejected on purpose: one or three decimal places, parenthesised negatives
`(5.00)`, trailing minus `5.00-`, and anything with stray characters. If your
bank uses one of those conventions, extend the parser deliberately and add
tests; do not loosen the regex until it stops complaining.

`re.ASCII` is deliberate too: bare `\d` also matches Arabic-Indic and full-width
digits, which no US statement contains and nothing downstream expects.

### Take the month from the period's *end*

Chase statement periods do not start on the 1st. The predecessor keyed on the
start day, and **2025-06, 2025-09 and 2025-12 vanished from its output
entirely** — 13 statements collapsed into 10 months, and their transactions were
folded into the preceding month. Nobody noticed.

`ParsedStatement.statement_month` derives from `period_end`, `v_statement`
derives `statement_month` from `source_file.period_end`, and there are tests on
both.

### Rows carry MM/DD — the year comes from the period

`dates.parse_mmdd(text, period_start, period_end)` tries each candidate year
drawn from the period and returns the one that lands inside it, or `None`. A
December row inside a December→January period belongs to the *earlier* year.
Guessing "the period's year" is wrong once a year, which is exactly often enough
to never be caught.

`None` means the caller reports it, not that the caller picks one.

### Skip rules are whole-line exact matches

Covered in [`../CONTRIBUTING.md`](../CONTRIBUTING.md#skip-rules-match-whole-lines-exactly--never-substrings).
Short version: substring matching with `"of"` in the list eats `House of Sushi`
and `Coffee Shop`, keeps the amount, and destroys the description — invisibly.
The same reasoning rules out `startswith` for section anchors.

### Recognise page furniture by position, not by shape

A textual rule like `\d+ \d+` describes a page-number footer. It also describes
a wrapped card-number fragment sitting next to the right-margin barcode — and
that rule silently ate the card-fragment continuation of a real 2025-03 row: the
description came out short, no warning was raised, and the amounts were
untouched. The Chase parser identifies footers by their being **outside the
description column** and
at most 3 digits long; a 20-digit barcode fails that test, falls through to the
catch-all, and gets reported.

### Invisible text is not statement content

`extract_spans()` drops white-on-white characters before words are assembled.
This is not cosmetic: Chase draws `*start*transaction detail` / `*end*summary`
markers in white at 1pt, positioned on the same baseline as real rows, so
pdfplumber merges them into the same words. On page 1 of a real statement the
date `01/02` comes out as `*end*transac0tion` + `detail1/02` — the transaction
loses its date entirely.

The fix is semantic rather than a blacklist: text that is not visible is not
content. Note the care taken with colour spaces — a tint of 1.0 in Separation or
DeviceN is *full ink*, not white, so the rule only applies inside DeviceGray,
DeviceRGB and DeviceCMYK and otherwise declines to judge. `Page.dropped_chars`
records how many characters were discarded; a sudden change there means the
producer changed.

### Unknown means refuse — loudly

There is no confidence score anywhere in this pipeline, and there will not be
one. Every ambiguity resolves to `ParseError`, `UnknownLayout`, or a warning
attached to output that a human will see. A parser that returns *something*
plausible for an unrecognised layout is worse than one that returns nothing,
because the something goes into a chart.

---

## How the tests are layered

From `docs/EXECUTION_PLAN.md` §8.1:

| Layer | What is tested | Data source |
|---|---|---|
| Unit | amount parsing, date resolution, natural keys | Inline |
| **Extraction** | `extract_spans(pdf)` | A **few** synthetic PDFs |
| **Bank logic** | `parser.parse(doc)` — **all bank logic lives here** | **Many** span fixtures / built `Document`s |
| Reconciliation | every check, positive and negative | Constructed |
| Integration | full pipeline + the rebuild invariant | Synthetic |

The split is the whole point. `extract_spans()` is thin, bank-agnostic, and needs
real PDF bytes to test — so it gets a small number of cases. Everything that
differs between banks operates on `Document` objects, which are plain data — so
it gets as many cases as you can write, on CI, with no PDF and no real data
anywhere.

Today the "many" side is served by `tests/synth.py` builders rather than a
directory of committed span JSON. `Document.to_json()` / `Document.from_json()`
already round-trip, so moving to file-based fixtures is a matter of writing the
files, not of changing the design.

---

## Writing a parser, in order

1. **Get the geometry.** Run `extract_spans()` over one of your own statements,
   locally, and look at the spans. Note the producer string, the header words,
   the column right edges on page 1 and on page 2, where the summary block sits.
   **Do not commit any of this.**
2. **Write `matches()` so it cannot be wrong.** Producer string plus two or three
   distinctive text markers, searched document-wide. It must not match any other
   registered layout.
3. **Parse the period** and derive the month from `period_end`.
4. **Parse the summary block** into `beginning_balance_minor`,
   `ending_balance_minor`, and verbatim-labelled `components`. Without these the
   reconciler has no evidence and block-level checks will *skip* — which, in this
   codebase, blocks the ingest rather than passing it.
5. **Parse the transaction table**, binding amount and balance by column right
   edge, learning the columns from each page's header.
6. **Refuse whatever you do not recognise**, and warn about whatever you drop.
   The Chase parser aggregates dropped spans into a single warning per statement
   — dropping text silently is how a description gets hollowed out.
7. **Register it** in `registry.PARSERS`.
8. **Write the tests**, from synthetic documents.

---

## The acceptance bar

A new parser is finished when a statement it produces passes **every block-level
check** in `src/ledgerbox/reconcile/checks.py`. Nothing is booked otherwise —
the pipeline writes zero rows when the gate closes, by design.

The block-level checks:

| Check | Assertion |
|---|---|
| `double_entry` | Postings sum to zero per (transaction, currency). Structural |
| `balance_chain` | `bal[n-1] + amt[n] == bal[n]` for every row that prints a balance, and the chain ends on the printed ending balance |
| `period_totals` | `beginning + Σ amounts == ending` |
| `declared_subtotals` | Row credits and debits reproduce the statement's own printed subtotals, and the summary block balances itself |

And the warn-level ones, which do not block but which you should not be
casually failing: `declared_buckets`, `transaction_count`, `dates_in_period`,
`period_continuity`, `page_continuity`.

Two things to internalise about this bar:

- **`balance_chain` is the strongest check and it is free**, wherever your bank
  prints a running balance. It localises an error to a single row, which the
  aggregate checks cannot. If your bank prints running balances, extract them —
  the value is not in the number, it is in the assertion.
- **A block-level check that is *skipped* blocks the ingest.** `SKIP` is a
  first-class outcome here, not a quiet pass: a report that cannot say what it
  did not check is not evidence of anything. So a parser that fails to extract
  the summary block does not sail through on the checks that did run.

Beyond the gate, the parser is expected to be a **pure function of the
document**: same `Document` in, byte-identical `ParsedStatement` out, no clock,
no randomness, no filesystem. That is what makes the rebuild invariant testable
— see [`ARCHITECTURE.md`](ARCHITECTURE.md#the-rebuild-invariant).

---

## What to put in the pull request

- the parser module under `src/ledgerbox/ingest/parsers/`
- its entry in `registry.PARSERS`
- tests built from synthetic documents, covering at minimum: identification,
  refusal of a foreign producer, column binding (a case where text order and
  column order disagree), a wrapped description, and a row the parser must
  refuse rather than guess
- a note in the pull request describing the layout, the geometry you measured,
  and anything your bank does that Chase does not

And nothing else. In particular: no PDFs, no span JSON captured from a real
statement, no screenshots, no log excerpts containing descriptions, no file
names containing account digits.

---

## Related reading

- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — rule zero, DCO, test conventions,
  the non-negotiable code constraints
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — where a parser sits in the five-layer
  pipeline, and why reconciliation is a gate rather than a report
- [`EXECUTION_PLAN.md`](EXECUTION_PLAN.md) §4.2 and §8 — the original analysis
  and the testing strategy (in Chinese)
- [`PROJECT_SUMMARY.md`](PROJECT_SUMMARY.md) §2 — the full defect list from the
  predecessor project, which is where most of the rules above come from
  (in Chinese)
- [`THREAT_MODEL.md`](THREAT_MODEL.md) — what is stored and what is not
  protected (in Chinese)
