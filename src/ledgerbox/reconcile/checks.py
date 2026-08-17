# SPDX-License-Identifier: AGPL-3.0-or-later
"""The gate.

Reconciliation is the product; the parser is an implementation detail. Every
check here is written so that being wrong is *loud*: a statement either
satisfies its own printed arithmetic or it does not get booked.

Ordering follows EXECUTION_PLAN §4.3, strongest first:

===  ==========================================  =========
 #   check                                       severity
===  ==========================================  =========
 0   postings sum to zero per transaction         block
 1   row-by-row balance chain                     block
 2   beginning + Σ amounts == ending              block
 3   the statement's own declared subtotals       block
 3b  each declared bucket reproduced by rules     warn
 4   transaction count vs declared count          warn
 5   dates inside the period; periods contiguous  warn
 6   page continuity                              warn
===  ==========================================  =========

Check 1 is the strongest and it is free - Chase prints a running balance next
to every row. Check 2 alone is *not sufficient*: two equal and opposite errors
cancel and it passes.

**All money in ``detail`` is integer minor units**, with ``_minor`` suffixes.
EXECUTION_PLAN §4.3's example payload shows decimals (``"expected": 857.26``);
using floats in the failure path would put binary floating point back into the
one place that exists to catch arithmetic errors. Human-readable amounts live
in ``message``, which is a string.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..dates import months_between
from ..ingest.parsers.base import ParsedStatement
from ..money import format_minor

BLOCK = "block"
WARN = "warn"

PASS = "pass"
FAIL = "fail"
SKIP = "skip"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One assertion's outcome.

    ``SKIP`` is a first-class outcome, not a silent pass: a report that cannot
    say what it did *not* check is not evidence of anything.
    """

    check_id: str
    severity: str
    status: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == FAIL

    @property
    def blocking(self) -> bool:
        return self.failed and self.severity == BLOCK

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id,
            "severity": self.severity,
            "status": self.status,
            "message": self.message,
            "detail": self.detail,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    statement_month: str
    results: tuple[CheckResult, ...]

    @property
    def blocked(self) -> bool:
        """True ⇒ do not book this statement.

        A block-level check that could not run counts as blocking. "Unknown
        means refuse" applies to our own checks first: a report whose strongest
        assertion was skipped has not established anything, and letting it read
        ``ok`` is precisely the shape of a statement that looks fine.
        """
        return bool(self.blocking_failures) or bool(self.unverified)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.failed)

    @property
    def blocking_failures(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.blocking)

    @property
    def unverified(self) -> tuple[CheckResult, ...]:
        """Block-level checks that were skipped rather than passed."""
        return tuple(r for r in self.results if r.severity == BLOCK and r.status == SKIP)

    @property
    def skipped(self) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status == SKIP)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement_month": self.statement_month,
            "blocked": self.blocked,
            "unverified": [r.check_id for r in self.unverified],
            "results": [r.to_dict() for r in self.results],
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent, sort_keys=True)


def _ok(check_id: str, severity: str, message: str, **detail: Any) -> CheckResult:
    return CheckResult(check_id, severity, PASS, message, detail)


def _bad(check_id: str, severity: str, message: str, **detail: Any) -> CheckResult:
    return CheckResult(check_id, severity, FAIL, message, detail)


def _skip(check_id: str, severity: str, message: str, **detail: Any) -> CheckResult:
    return CheckResult(check_id, severity, SKIP, message, detail)


# ---------------------------------------------------------------------------
# 0 - double entry
# ---------------------------------------------------------------------------


def check_double_entry(postings: Iterable[tuple[str, int, str]]) -> CheckResult:
    """``SUM(amount_minor) GROUP BY (txn, currency) == 0``.

    Structural: it is what makes double-counting an internal transfer
    impossible rather than merely unlikely. Takes ``(txn_id, amount_minor,
    currency)`` triples so it can run before anything is written.
    """
    totals: defaultdict[tuple[str, str], int] = defaultdict(int)
    for txn_id, amount_minor, currency in postings:
        totals[(txn_id, currency)] += amount_minor

    if not totals:
        # Zero postings satisfy "everything balances" vacuously. Saying so out
        # loud is the difference between a verified ledger and an empty one.
        return _skip("double_entry", BLOCK, "no postings to balance", groups=0)

    residuals = {key: value for key, value in totals.items() if value != 0}
    if not residuals:
        return _ok(
            "double_entry",
            BLOCK,
            f"{len(totals)} transaction/currency group(s) balance to zero",
            groups=len(totals),
        )
    worst = max(residuals.items(), key=lambda item: abs(item[1]))
    return _bad(
        "double_entry",
        BLOCK,
        f"{len(residuals)} transaction(s) do not balance; "
        f"worst is {worst[0][0]} off by {format_minor(worst[1])}",
        unbalanced=len(residuals),
        worst_txn_id=worst[0][0],
        worst_currency=worst[0][1],
        worst_residual_minor=worst[1],
    )


# ---------------------------------------------------------------------------
# 1 - the running balance chain
# ---------------------------------------------------------------------------


def check_balance_chain(statement: ParsedStatement) -> CheckResult:
    """``bal[n-1] + amt[n] == bal[n]`` for every row that prints a balance.

    The strongest check available and it costs nothing: the bank already did
    the arithmetic on the page. It localises an error to a single row, which
    checks 2 and 3 cannot.
    """
    running = statement.summary.beginning_balance_minor
    checked = 0
    breaks: list[dict[str, Any]] = []
    for txn in statement.transactions:
        running += txn.amount_minor
        if txn.balance_minor is None:
            continue
        checked += 1
        if txn.balance_minor != running:
            breaks.append(
                {
                    "row": txn.row_index,
                    "page": txn.provenance.page,
                    "bbox": list(txn.provenance.as_bbox()),
                    "expected_minor": running,
                    "actual_minor": txn.balance_minor,
                    "diff_minor": txn.balance_minor - running,
                    "posted_date": txn.posted_date.isoformat(),
                    "amount_minor": txn.amount_minor,
                }
            )
            # Re-anchor on what the bank printed, so one bad row does not
            # report every subsequent row as broken too.
            running = txn.balance_minor

    if breaks:
        first = breaks[0]
        return _bad(
            "balance_chain",
            BLOCK,
            f"{statement.statement_month} page {first['page']}: balance chain broke at "
            f"row {first['row']} - expected {format_minor(first['expected_minor'])}, "
            f"statement prints {format_minor(first['actual_minor'])} "
            f"(off by {format_minor(first['diff_minor'])})"
            + (f"; {len(breaks) - 1} further break(s)" if len(breaks) > 1 else ""),
            break_count=len(breaks),
            breaks=breaks[:20],
            **first,
        )

    if checked == 0:
        return _skip(
            "balance_chain",
            BLOCK,
            "no row printed a running balance; chain not verifiable",
            rows=len(statement.transactions),
        )
    if running != statement.summary.ending_balance_minor:
        return _bad(
            "balance_chain",
            BLOCK,
            f"{statement.statement_month}: chain ends at {format_minor(running)} but the "
            f"statement's ending balance is "
            f"{format_minor(statement.summary.ending_balance_minor)}",
            expected_minor=statement.summary.ending_balance_minor,
            actual_minor=running,
            diff_minor=running - statement.summary.ending_balance_minor,
        )
    return _ok(
        "balance_chain",
        BLOCK,
        f"{checked}/{len(statement.transactions)} rows verified against the printed balance",
        rows_checked=checked,
        rows_total=len(statement.transactions),
    )


# ---------------------------------------------------------------------------
# 2 - period arithmetic
# ---------------------------------------------------------------------------


def check_period_totals(statement: ParsedStatement) -> CheckResult:
    """``beginning + Σ amounts == ending``.

    Necessary but **not sufficient** - two equal and opposite errors cancel.
    It stays because it is the one check that still works when a layout stops
    printing running balances.
    """
    total = sum(txn.amount_minor for txn in statement.transactions)
    expected = statement.summary.beginning_balance_minor + total
    actual = statement.summary.ending_balance_minor
    if expected != actual:
        return _bad(
            "period_totals",
            BLOCK,
            f"{statement.statement_month}: "
            f"{format_minor(statement.summary.beginning_balance_minor)}"
            f" + {format_minor(total)} = {format_minor(expected)}, but the statement ends at "
            f"{format_minor(actual)} (off by {format_minor(expected - actual)})",
            beginning_minor=statement.summary.beginning_balance_minor,
            rows_total_minor=total,
            expected_minor=expected,
            actual_minor=actual,
            diff_minor=expected - actual,
        )
    return _ok(
        "period_totals",
        BLOCK,
        f"{format_minor(statement.summary.beginning_balance_minor)} + {format_minor(total)} = "
        f"{format_minor(actual)}",
        beginning_minor=statement.summary.beginning_balance_minor,
        rows_total_minor=total,
        ending_minor=actual,
    )


# ---------------------------------------------------------------------------
# 3 - the statement's own subtotals
# ---------------------------------------------------------------------------

DEPOSITS_LABEL = "deposits and additions"


def check_declared_subtotals(statement: ParsedStatement) -> CheckResult:
    """Rows must reproduce the summary block Chase prints on page one.

    This is the check that located the predecessor's defect precisely: the
    balance chain says *a* row is wrong, the subtotals say the error is on the
    **income** side.

    Split by sign rather than by the bank's own buckets: sign is a property of
    the data, while bucket membership needs a classifier, and a classifier that
    is merely plausible has no business gating anything. The buckets get their
    own warn-level check below.
    """
    inflow = sum(t.amount_minor for t in statement.transactions if t.amount_minor > 0)
    outflow = sum(t.amount_minor for t in statement.transactions if t.amount_minor < 0)

    # Every positive component, not just "Deposits and Additions". A statement
    # with a second credit line - interest paid, a reversal - would otherwise
    # fail and blame the deposit line for a discrepancy it did not cause.
    # `>= 0`, not `> 0`: a statement that prints "Deposits and Additions $0.00"
    # in a month with no deposits has declared the line, and treating that as
    # "no credit subtotal" would block a perfectly good statement.
    positive_labels = [k for k, v in statement.summary.components.items() if v >= 0]
    declared_in = sum(v for v in statement.summary.components.values() if v >= 0)
    declared_out = sum(v for v in statement.summary.components.values() if v < 0)
    declared_net = statement.summary.declared_net_minor

    problems = []
    if not positive_labels:
        problems.append("statement declares no credit subtotal")
    elif declared_in != inflow:
        problems.append(
            f"credits ({', '.join(sorted(positive_labels))}): rows {format_minor(inflow)} vs "
            f"statement {format_minor(declared_in)} (off by {format_minor(inflow - declared_in)})"
        )
    if declared_out != outflow:
        problems.append(
            f"withdrawals: rows {format_minor(outflow)} vs statement {format_minor(declared_out)} "
            f"(off by {format_minor(outflow - declared_out)})"
        )
    if (
        statement.summary.beginning_balance_minor + declared_net
        != statement.summary.ending_balance_minor
    ):
        problems.append(
            f"the summary block does not balance itself: "
            f"{format_minor(statement.summary.beginning_balance_minor)} + "
            f"{format_minor(declared_net)} != "
            f"{format_minor(statement.summary.ending_balance_minor)}"
        )

    detail = {
        "rows_inflow_minor": inflow,
        "rows_outflow_minor": outflow,
        "declared_inflow_minor": declared_in,
        "declared_outflow_minor": declared_out,
        # `_minor` suffix on purpose: it is money, so the test that walks the
        # payload asserting integers has to reach it.
        "declared_components_minor": dict(statement.summary.components),
    }
    if problems:
        return _bad(
            "declared_subtotals",
            BLOCK,
            f"{statement.statement_month}: " + "; ".join(problems),
            problems=problems,
            **detail,
        )
    return _ok(
        "declared_subtotals",
        BLOCK,
        f"deposits {format_minor(inflow)} and withdrawals {format_minor(outflow)} match the "
        f"statement's own subtotals",
        **detail,
    )


# ---------------------------------------------------------------------------
# 3b - the buckets, reproduced by declared rules (warn)
# ---------------------------------------------------------------------------

#: Priority is an explicit field, not the accident of dictionary order - the
#: predecessor's rules were an object literal whose key order silently decided
#: which category won, so adding one rule re-categorised unrelated rows.
#: Patterns are word-bounded: "chase" as a substring of "Purchase" put 68 rows
#: and $11,726 into "bank fees".
BUCKET_RULES: tuple[tuple[int, str, tuple[str, ...]], ...] = (
    (10, "Fees", (r"\bservice fee\b", r"\bfee\b", r"\boverdraft\b", r"\bcounter check\b")),
    (
        20,
        "ATM & Debit Card Withdrawals",
        (
            r"\bcard purchase\b",
            r"\batm\b",
            r"\bcash withdrawal\b",
            # Chase phrases some card transactions as "Payment Sent …", and the
            # only thing marking them as card rather than electronic is the
            # card-number tag it appends: "… San Francisco CA Card 1234".
            # "Payment To Chase Card Ending IN 1234" is electronic and does not
            # match, because the digits must follow "card" immediately.
            r"\bcard \d{4}\b",
        ),
    ),
    (99, "Electronic Withdrawals", (r".",)),  # default for everything else
)


def classify_bucket(description: str) -> str:
    text = description.casefold()
    for _priority, bucket, patterns in sorted(BUCKET_RULES):
        if any(re.search(pattern, text) for pattern in patterns):
            return bucket
    return "Electronic Withdrawals"  # pragma: no cover - the default rule matches


def check_declared_buckets(statement: ParsedStatement) -> CheckResult:
    """Do our category rules reproduce the bank's own breakdown?

    Warn, never block. A mismatch means our classifier disagrees with Chase,
    which is worth knowing but is not evidence that the *numbers* are wrong —
    checks 1–3 already settled that. Blocking on a heuristic would train the
    operator to click past the gate.
    """
    computed: defaultdict[str, int] = defaultdict(int)
    for txn in statement.transactions:
        if txn.amount_minor < 0:
            computed[classify_bucket(txn.description)] += txn.amount_minor

    declared = {
        label: value for label, value in statement.summary.components.items() if value < 0
    }
    if not declared:
        return _skip("declared_buckets", WARN, "statement declares no withdrawal buckets")

    mismatches = {
        label: {"declared_minor": value, "computed_minor": computed.get(label, 0)}
        for label, value in declared.items()
        if computed.get(label, 0) != value
    }
    if mismatches:
        worst = max(
            mismatches.items(),
            key=lambda item: abs(item[1]["computed_minor"] - item[1]["declared_minor"]),
        )
        return _bad(
            "declared_buckets",
            WARN,
            f"{statement.statement_month}: category rules disagree with the statement in "
            f"{len(mismatches)} bucket(s); worst is {worst[0]} "
            f"(rules {format_minor(worst[1]['computed_minor'])} vs statement "
            f"{format_minor(worst[1]['declared_minor'])})",
            mismatches=mismatches,
        )
    return _ok(
        "declared_buckets",
        WARN,
        f"category rules reproduce all {len(declared)} withdrawal buckets exactly",
        buckets=len(declared),
    )


# ---------------------------------------------------------------------------
# 4 - declared transaction count (warn; not all statements print one)
# ---------------------------------------------------------------------------

def check_transaction_count(statement: ParsedStatement) -> CheckResult:
    """Compare against a count the statement printed, when it printed one.

    Reads ``summary.declared_transaction_count``. It used to search
    ``components`` for a count-shaped label, which could never match: values
    only enter ``components`` through a money parser that demands two decimal
    places, so a plain integer count had no way in. The check was unreachable
    for every bank, and its skip message implied otherwise.
    """
    declared = statement.summary.declared_transaction_count
    if declared is None:
        return _skip(
            "transaction_count",
            WARN,
            "statement declares no transaction count; nothing to compare",
            rows=len(statement.transactions),
        )
    actual = len(statement.transactions)
    if declared != actual:
        return _bad(
            "transaction_count",
            WARN,
            f"{statement.statement_month}: statement declares {declared} transactions, parsed "
            f"{actual}",
            declared=declared,
            actual=actual,
        )
    return _ok("transaction_count", WARN, f"{actual} transactions, as declared", actual=actual)


# ---------------------------------------------------------------------------
# 5 - dates inside the period, and periods without gaps
# ---------------------------------------------------------------------------


def check_dates_within_period(statement: ParsedStatement) -> CheckResult:
    strays = [
        {"row": t.row_index, "date": t.posted_date.isoformat(), "page": t.provenance.page}
        for t in statement.transactions
        if not (statement.period_start <= t.posted_date <= statement.period_end)
    ]
    if strays:
        return _bad(
            "dates_in_period",
            WARN,
            f"{statement.statement_month}: {len(strays)} row(s) fall outside "
            f"{statement.period_start}..{statement.period_end}",
            strays=strays[:20],
            stray_count=len(strays),
        )
    return _ok(
        "dates_in_period",
        WARN,
        f"all {len(statement.transactions)} rows fall inside "
        f"{statement.period_start}..{statement.period_end}",
    )


def check_period_continuity(periods: Sequence[tuple[date, date]]) -> CheckResult:
    """Consecutive statements must abut. A gap means a missing statement.

    Chase periods do not start on the 1st, so "one per month" is not the test;
    the test is that each period starts the day after the previous one ended.
    """
    ordered = sorted(periods)
    if len(ordered) < 2:
        return _skip(
            "period_continuity", WARN, "fewer than two statements; continuity not verifiable"
        )

    gaps = []
    for (_, previous_end), (next_start, _) in zip(ordered, ordered[1:], strict=False):
        delta = (next_start - previous_end).days
        if delta == 1:
            continue
        gaps.append(
            {
                "after": previous_end.isoformat(),
                "before": next_start.isoformat(),
                # Negative days are an overlap, not a gap - two statements
                # covering the same days is a different problem from a missing
                # one, and reporting "-17 days missing" describes neither.
                "kind": "gap" if delta > 1 else "overlap",
                "gap_days": max(delta - 1, 0),
                "overlap_days": max(1 - delta, 0),
            }
        )
    if gaps:
        first = gaps[0]
        detail_text = (
            f"{first['gap_days']} day(s) missing"
            if first["kind"] == "gap"
            else f"{first['overlap_days']} day(s) overlapping"
        )
        kinds = {str(g["kind"]) for g in gaps}
        return _bad(
            "period_continuity",
            WARN,
            f"{len(gaps)} discontinuity/-ies between statement periods "
            f"({', '.join(sorted(kinds))}); the first is after {first['after']} ({detail_text})",
            gaps=gaps,
            months_covered=months_between(ordered[0][0], ordered[-1][1]) + 1,
        )
    return _ok(
        "period_continuity",
        WARN,
        f"{len(ordered)} statements cover {ordered[0][0]}..{ordered[-1][1]} with no gap",
        statements=len(ordered),
    )


# ---------------------------------------------------------------------------
# 6 - page continuity
# ---------------------------------------------------------------------------


def check_page_continuity(statement: ParsedStatement) -> CheckResult:
    """Transaction rows must come from a contiguous run of pages.

    A missing page in the middle of a scan is otherwise indistinguishable from
    a quiet fortnight.
    """
    pages = sorted({t.provenance.page for t in statement.transactions})
    if not pages:
        return _skip("page_continuity", WARN, "no transactions; page continuity not verifiable")
    expected = list(range(pages[0], pages[-1] + 1))
    if pages != expected:
        missing = sorted(set(expected) - set(pages))
        return _bad(
            "page_continuity",
            WARN,
            f"{statement.statement_month}: transactions appear on pages {pages} - "
            f"page(s) {missing} contribute nothing, which usually means a missing page",
            pages=pages,
            missing=missing,
        )
    return _ok(
        "page_continuity",
        WARN,
        f"transactions run continuously across page(s) {pages}",
        pages=pages,
    )


# ---------------------------------------------------------------------------
# the suite
# ---------------------------------------------------------------------------

STATEMENT_CHECKS = (
    check_balance_chain,
    check_period_totals,
    check_declared_subtotals,
    check_declared_buckets,
    check_transaction_count,
    check_dates_within_period,
    check_page_continuity,
)


def run_statement_checks(statement: ParsedStatement) -> ReconciliationReport:
    """Every check, always - never stop at the first failure.

    An operator fixing one problem at a time, one ingest at a time, is how a
    statement gets waved through on the third attempt.
    """
    return ReconciliationReport(
        statement_month=statement.statement_month,
        results=tuple(check(statement) for check in STATEMENT_CHECKS),
    )
