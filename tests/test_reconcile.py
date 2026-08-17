# SPDX-License-Identifier: AGPL-3.0-or-later
"""M5: the reconciliation gate.

Every check gets a positive case *and* a negative case. A check nobody has
seen fail is a check nobody has tested.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from ledgerbox.ingest.parsers.base import (
    ParsedStatement,
    Provenance,
    StatementSummary,
    StatementTxn,
)
from ledgerbox.reconcile.checks import (
    BLOCK,
    FAIL,
    PASS,
    SKIP,
    WARN,
    check_balance_chain,
    check_dates_within_period,
    check_declared_buckets,
    check_declared_subtotals,
    check_double_entry,
    check_page_continuity,
    check_period_continuity,
    check_period_totals,
    check_transaction_count,
    classify_bucket,
    run_statement_checks,
)
from ledgerbox.reconcile.report import (
    render_report,
    render_summary,
    review_items,
)


def txn(
    *,
    day: int,
    amount: int,
    balance: int | None,
    description: str = "Card Purchase 01/01 Vendor CA Card 4321",
    index: int = 0,
    page: int = 2,
) -> StatementTxn:
    return StatementTxn(
        posted_date=date(2025, 1, day),
        description=description,
        amount_minor=amount,
        balance_minor=balance,
        row_index=index,
        provenance=Provenance(page=page, top=100.0, x0=434.8, x1=462.9, bottom=108.0),
    )


def statement(
    transactions: list[StatementTxn],
    *,
    beginning: int = 82015,
    ending: int | None = None,
    components: dict[str, int] | None = None,
    period: tuple[date, date] = (date(2025, 1, 1), date(2025, 1, 31)),
) -> ParsedStatement:
    total = sum(t.amount_minor for t in transactions)
    if ending is None:
        ending = beginning + total
    if components is None:
        # Default to a summary block that agrees with the rows, buckets and
        # all — so a test that wants a mismatch has to ask for one.
        inflow = sum(t.amount_minor for t in transactions if t.amount_minor > 0)
        components = {"Deposits and Additions": inflow}
        for row in transactions:
            if row.amount_minor < 0:
                bucket = classify_bucket(row.description)
                components[bucket] = components.get(bucket, 0) + row.amount_minor
    return ParsedStatement(
        institution="Chase",
        account_mask="1234",
        account_subtype="checking",
        currency="USD",
        period_start=period[0],
        period_end=period[1],
        summary=StatementSummary(
            beginning_balance_minor=beginning,
            ending_balance_minor=ending,
            components=components,
        ),
        transactions=tuple(transactions),
        parser_id="chase_checking",
        parser_version="1",
    )


CLEAN = [
    txn(day=2, amount=3711, balance=85726, description="Zelle Payment From A 232", index=0),
    txn(day=3, amount=-1244, balance=84482, index=1),
]


# --------------------------------------------------------------------------
# 0 — double entry
# --------------------------------------------------------------------------


def test_balanced_postings_pass() -> None:
    result = check_double_entry([("t1", 3711, "USD"), ("t1", -3711, "USD")])
    assert result.status == PASS
    assert result.severity == BLOCK


def test_no_postings_is_skip_not_pass() -> None:
    """Nothing balances vacuously; an empty ledger is not a verified one."""
    result = check_double_entry([])
    assert result.status == SKIP
    assert not result.blocking


def test_unbalanced_postings_fail_and_name_the_transaction() -> None:
    result = check_double_entry(
        [("t1", 3711, "USD"), ("t1", -3711, "USD"), ("t2", 100, "USD"), ("t2", -99, "USD")]
    )
    assert result.status == FAIL
    assert result.blocking
    assert result.detail["worst_txn_id"] == "t2"
    assert result.detail["worst_residual_minor"] == 1


def test_currencies_are_balanced_separately() -> None:
    """+100 USD and -100 EUR is not a balanced transaction."""
    result = check_double_entry([("t1", 10000, "USD"), ("t1", -10000, "EUR")])
    assert result.status == FAIL
    assert result.detail["unbalanced"] == 2


# --------------------------------------------------------------------------
# 1 — the balance chain
# --------------------------------------------------------------------------


def test_intact_chain_passes() -> None:
    result = check_balance_chain(statement(CLEAN))
    assert result.status == PASS
    assert result.detail["rows_checked"] == 2


def test_broken_chain_localises_the_row_and_the_box() -> None:
    """A wrong amount must be pinned to one row, with page and bbox."""
    broken = [
        CLEAN[0],
        txn(day=3, amount=-1146, balance=84482, index=1),  # amount tampered with
    ]
    result = check_balance_chain(statement(broken, ending=84482))
    assert result.blocking
    assert result.detail["row"] == 1
    assert result.detail["page"] == 2
    assert result.detail["diff_minor"] == -98
    assert len(result.detail["bbox"]) == 4
    assert "row 1" in result.message


def test_chain_catches_what_period_totals_cannot() -> None:
    """Two equal and opposite errors cancel — check 2 passes, check 1 does not.

    This is why check 2 is documented as necessary but not sufficient, and why
    both are kept.
    """
    compensating = [
        txn(day=2, amount=4711, balance=85726, index=0),  # +$10 too much
        txn(day=3, amount=-2244, balance=84482, index=1),  # -$10 too much
    ]
    subject = statement(compensating, ending=84482, components={"Deposits and Additions": 4711,
                                                               "Electronic Withdrawals": -2244})
    assert check_period_totals(subject).status == PASS
    assert check_balance_chain(subject).blocking


def test_chain_without_any_printed_balance_is_skipped_not_passed() -> None:
    no_balances = [txn(day=2, amount=3711, balance=None, index=0)]
    result = check_balance_chain(statement(no_balances))
    assert result.status == SKIP
    assert not result.blocking


def test_an_unverifiable_block_check_still_closes_the_gate() -> None:
    """The strongest check being unrunnable is not the same as it passing."""
    no_balances = [txn(day=2, amount=3711, balance=None, index=0)]
    report = run_statement_checks(statement(no_balances))

    assert [r.check_id for r in report.unverified] == ["balance_chain"]
    assert report.blocking_failures == ()
    assert report.blocked, "a skipped block-level check must not read as ok"
    assert "UNVERIFIED" in render_report(report)
    assert "ok\n" not in render_report(report)

    queued = review_items("file-sha", report)
    assert "balance_chain" in {item.check_id for item in queued}


def test_a_chain_with_several_breaks_reports_them_all() -> None:
    rows = [
        txn(day=2, amount=3711, balance=85726, index=0),
        txn(day=3, amount=-1244, balance=83000, index=1),  # wrong
        txn(day=4, amount=-100, balance=82900, index=2),  # right, relative to above
        txn(day=5, amount=-100, balance=80000, index=3),  # wrong again
    ]
    result = check_balance_chain(statement(rows, ending=80000))
    assert result.detail["break_count"] == 2
    assert [b["row"] for b in result.detail["breaks"]] == [1, 3]
    assert "1 further break" in result.message


def test_chain_that_ends_off_the_printed_ending_balance_fails() -> None:
    subject = statement(CLEAN, ending=84483)
    result = check_balance_chain(subject)
    assert result.blocking
    assert result.detail["diff_minor"] == -1


# --------------------------------------------------------------------------
# 2 — period totals
# --------------------------------------------------------------------------


def test_period_totals_pass_and_fail() -> None:
    assert check_period_totals(statement(CLEAN)).status == PASS
    tampered = statement(CLEAN, ending=84482 + 5000)
    result = check_period_totals(tampered)
    assert result.blocking
    assert result.detail["diff_minor"] == -5000


# --------------------------------------------------------------------------
# 3 — the statement's own subtotals
# --------------------------------------------------------------------------


def test_declared_subtotals_pass() -> None:
    assert check_declared_subtotals(statement(CLEAN)).status == PASS


def test_declared_subtotals_locate_the_error_on_the_income_side() -> None:
    """The predecessor's exact failure: deposits booked at the balance.

    The balance chain says *a* row is wrong. This check says the error is in
    the income column — which is what turned a 4.57× overstatement from a
    mystery into a diagnosis.
    """
    inflated = [
        txn(day=2, amount=85726, balance=85726, description="Zelle Payment From A", index=0),
        txn(day=3, amount=-1244, balance=84482, index=1),
    ]
    subject = statement(
        inflated,
        ending=84482,
        components={"Deposits and Additions": 3711, "Electronic Withdrawals": -1244},
    )
    result = check_declared_subtotals(subject)
    assert result.blocking
    assert result.detail["rows_inflow_minor"] == 85726
    assert result.detail["declared_inflow_minor"] == 3711
    assert any("credits" in problem for problem in result.detail["problems"])
    assert any("Deposits and Additions" in problem for problem in result.detail["problems"])


def test_declared_subtotals_notice_a_summary_that_does_not_balance_itself() -> None:
    subject = statement(
        CLEAN,
        components={"Deposits and Additions": 3711, "Electronic Withdrawals": -1244, "Fees": -100},
    )
    result = check_declared_subtotals(subject)
    assert result.blocking
    assert any("does not balance itself" in p for p in result.detail["problems"])


def test_missing_deposit_subtotal_is_a_failure_not_a_pass() -> None:
    subject = statement(CLEAN, components={"Electronic Withdrawals": -1244})
    assert check_declared_subtotals(subject).blocking


def test_a_second_credit_line_is_counted_not_blamed_on_deposits() -> None:
    """A statement with interest paid as well as deposits still reconciles."""
    rows = [
        txn(day=2, amount=3711, balance=85726, description="Zelle Payment From A", index=0),
        txn(day=3, amount=500, balance=86226, description="Interest Payment", index=1),
        txn(day=4, amount=-1244, balance=84982, index=2),
    ]
    subject = statement(
        rows,
        ending=84982,
        components={
            "Deposits and Additions": 3711,
            "Interest Paid": 500,
            "ATM & Debit Card Withdrawals": -1244,
        },
    )
    result = check_declared_subtotals(subject)
    assert result.status == PASS
    assert result.detail["declared_inflow_minor"] == 3711 + 500


# --------------------------------------------------------------------------
# 3b — bucket rules (warn only)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "bucket"),
    [
        ("Card Purchase 01/01 Vendor CA Card 4321", "ATM & Debit Card Withdrawals"),
        ("Recurring Card Purchase 01/04 Vendor Card 4321", "ATM & Debit Card Withdrawals"),
        ("Payment Sent 01/19 Vendor San Francisco CA Card 4321", "ATM & Debit Card Withdrawals"),
        ("ATM Withdrawal 01/02 Somewhere", "ATM & Debit Card Withdrawals"),
        ("Monthly Service Fee", "Fees"),
        ("Zelle Payment To Someone 233", "Electronic Withdrawals"),
        ("Venmo Payment 103 Web ID: 326", "Electronic Withdrawals"),
        ("01/25 Payment To Chase Card Ending IN 8765", "Electronic Withdrawals"),
        ("Chase Credit Crd Autopay PPD ID: 476", "Electronic Withdrawals"),
    ],
)
def test_bucket_rules(description: str, bucket: str) -> None:
    assert classify_bucket(description) == bucket


def test_bucket_rules_use_word_boundaries() -> None:
    """"chase" inside "Purchase" put 68 rows and $11,726 in the wrong bucket."""
    assert classify_bucket("Card Purchase 01/01 Vendor Card 4321") != "Fees"
    # "Card Ending IN 8765" must not read as the "Card 8765" card marker
    assert classify_bucket("Payment To Chase Card Ending IN 8765") == "Electronic Withdrawals"


def test_bucket_mismatch_warns_but_never_blocks() -> None:
    subject = statement(
        [txn(day=3, amount=-1244, description="Zelle Payment To Someone", index=0, balance=80771)],
        ending=80771,
        components={"Deposits and Additions": 0, "ATM & Debit Card Withdrawals": -1244},
    )
    result = check_declared_buckets(subject)
    assert result.status == FAIL
    assert result.severity == WARN
    assert not result.blocking
    report = run_statement_checks(subject)
    assert not report.blocked, "a heuristic must never gate the ledger"


# --------------------------------------------------------------------------
# 4, 5, 6 — warnings
# --------------------------------------------------------------------------


def test_transaction_count_is_skipped_when_undeclared() -> None:
    result = check_transaction_count(statement(CLEAN))
    assert result.status == SKIP
    assert "no transaction count" in result.message


def test_transaction_count_compares_when_a_statement_declares_one() -> None:
    """The check used to be unreachable for every bank, not only Chase.

    It searched `components` for a count-shaped label, but values only enter
    `components` through a money parser that requires two decimals — an integer
    count could never get in. It now reads a field of its own.
    """
    subject = statement(CLEAN)

    def with_count(count: int) -> ParsedStatement:
        return replace(
            subject,
            summary=replace(subject.summary, declared_transaction_count=count),
        )

    assert check_transaction_count(with_count(2)).status == PASS

    result = check_transaction_count(with_count(7))
    assert result.status == FAIL
    assert result.severity == WARN
    assert result.detail == {"declared": 7, "actual": 2}


def test_dates_outside_the_period_are_reported() -> None:
    stray = [txn(day=2, amount=3711, balance=85726, index=0)]
    subject = statement(stray, period=(date(2025, 2, 1), date(2025, 2, 28)))
    result = check_dates_within_period(subject)
    assert result.status == FAIL
    assert result.severity == WARN
    assert result.detail["stray_count"] == 1


def test_page_gaps_are_reported() -> None:
    rows = [
        txn(day=2, amount=3711, balance=85726, index=0, page=2),
        txn(day=3, amount=-1244, balance=84482, index=1, page=4),
    ]
    result = check_page_continuity(statement(rows, ending=84482))
    assert result.status == FAIL
    assert result.detail["missing"] == [3]


def test_period_continuity_detects_a_missing_statement() -> None:
    periods = [
        (date(2025, 1, 1), date(2025, 1, 31)),
        (date(2025, 2, 1), date(2025, 2, 28)),
        (date(2025, 4, 1), date(2025, 4, 30)),  # March is missing
    ]
    result = check_period_continuity(periods)
    assert result.status == FAIL
    assert result.detail["gaps"][0]["gap_days"] == 31


def test_period_continuity_accepts_periods_that_do_not_start_on_the_first() -> None:
    periods = [
        (date(2025, 8, 1), date(2025, 8, 29)),
        (date(2025, 8, 30), date(2025, 9, 30)),
    ]
    assert check_period_continuity(periods).status == PASS


def test_period_continuity_needs_two_statements() -> None:
    assert check_period_continuity([(date(2025, 1, 1), date(2025, 1, 31))]).status == SKIP


def test_overlapping_periods_are_called_overlaps_not_negative_gaps() -> None:
    periods = [
        (date(2025, 1, 1), date(2025, 1, 31)),
        (date(2025, 1, 15), date(2025, 2, 20)),  # starts before the previous ended
    ]
    result = check_period_continuity(periods)
    assert result.status == FAIL
    gap = result.detail["gaps"][0]
    assert gap["kind"] == "overlap"
    assert gap["overlap_days"] == 17
    assert gap["gap_days"] == 0
    assert "overlap" in result.message
    assert "-17" not in result.message, "an overlap is not a negative gap"


# --------------------------------------------------------------------------
# the report
# --------------------------------------------------------------------------


def test_a_clean_statement_is_not_blocked() -> None:
    report = run_statement_checks(statement(CLEAN))
    assert not report.blocked
    assert report.failures == ()
    assert {r.check_id for r in report.skipped} == {"transaction_count"}


def test_every_check_runs_even_after_one_fails() -> None:
    """Stopping at the first failure trains people to fix one thing per run."""
    broken = [txn(day=2, amount=3711, balance=99999, index=0, page=9)]
    report = run_statement_checks(statement(broken, ending=84482))
    assert report.blocked
    assert len(report.results) == 7
    assert len(report.blocking_failures) >= 2


def test_report_json_keeps_money_as_integer_minor_units() -> None:
    broken = [CLEAN[0], txn(day=3, amount=-600, balance=84482, index=1)]
    report = run_statement_checks(statement(broken, ending=84482))
    payload: dict[str, Any] = json.loads(report.to_json())

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("_minor"):
                    values = list(value.values()) if isinstance(value, dict) else [value]
                    for money in values:
                        assert isinstance(money, int), f"{key} holds {type(money).__name__}"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    assert payload["blocked"] is True


def test_every_money_field_in_the_payload_ends_in_minor() -> None:
    """A money key without the suffix escapes the integer-only assertion."""
    report = run_statement_checks(statement(CLEAN))
    payload = json.loads(report.to_json())
    monetary_words = ("balance", "inflow", "outflow", "amount", "components")

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if any(word in key for word in monetary_words):
                    assert key.endswith("_minor"), f"{key} looks like money but is not _minor"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)


def test_report_text_survives_a_legacy_console_encoding() -> None:
    """✓/✗ raise UnicodeEncodeError on cp1252 and cp936. Reports must not."""
    broken = [CLEAN[0], txn(day=3, amount=-600, balance=84482, index=1)]
    text = render_summary([run_statement_checks(statement(broken, ending=84482))])
    for encoding in ("cp1252", "cp936", "ascii"):
        text.encode(encoding)


def test_review_items_are_deterministic_and_blocking_first() -> None:
    broken = [txn(day=2, amount=3711, balance=99999, index=0)]
    report = run_statement_checks(
        statement(broken, ending=84482, period=(date(2025, 2, 1), date(2025, 2, 28)))
    )
    first = review_items("file-sha", report)
    second = review_items("file-sha", report)

    assert [i.id for i in first] == [i.id for i in second], "re-ingest must not breed duplicates"
    assert len({i.id for i in first}) == len(first)
    assert first[0].severity == BLOCK
    assert all(json.loads(item.detail)["message"] for item in first)


def test_review_items_can_exclude_warnings() -> None:
    broken = [txn(day=2, amount=3711, balance=99999, index=0)]
    report = run_statement_checks(
        statement(broken, ending=84482, period=(date(2025, 2, 1), date(2025, 2, 28)))
    )
    only_blocking = review_items("file-sha", report, include_warnings=False)
    assert all(item.severity == BLOCK for item in only_blocking)
    assert len(only_blocking) < len(review_items("file-sha", report))


def test_review_item_ids_differ_per_file_and_per_check() -> None:
    report = run_statement_checks(statement([txn(day=2, amount=3711, balance=99999, index=0)],
                                            ending=84482))
    a = {i.id for i in review_items("file-a", report)}
    b = {i.id for i in review_items("file-b", report)}
    assert a.isdisjoint(b)


def test_rendered_report_names_the_failure() -> None:
    broken = [CLEAN[0], txn(day=3, amount=-600, balance=84482, index=1)]
    text = render_report(run_statement_checks(statement(broken, ending=84482)))
    assert "BLOCKED" in text
    assert "balance_chain" in text


def test_summary_never_lets_a_skip_look_like_a_pass() -> None:
    text = render_summary([run_statement_checks(statement(CLEAN))])
    assert "skipped: transaction_count" in text
    assert "1 statement(s)" in text


def test_verbose_report_lists_passing_checks_too() -> None:
    text = render_report(run_statement_checks(statement(CLEAN)), verbose=True)
    assert "period_totals" in text
    assert "declared_subtotals" in text


# --------------------------------------------------------------------------
# the real corpus
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def real_reports(real_parsed: list) -> list:
    return [run_statement_checks(s) for s in real_parsed]


def test_every_real_statement_passes_every_blocking_check(real_reports: list) -> None:
    blocked = [r.statement_month for r in real_reports if r.blocked]
    assert blocked == []
    assert len(real_reports) == 13


def test_real_statements_raise_no_warnings_either(real_reports: list) -> None:
    failures = [
        (r.statement_month, f.check_id) for r in real_reports for f in r.failures
    ]
    assert failures == []


def test_bucket_rules_reproduce_chase_own_breakdown_on_every_statement(
    real_reports: list,
) -> None:
    """39 buckets across 13 statements, all reproduced from the rule table."""
    results = [
        r for report in real_reports for r in report.results if r.check_id == "declared_buckets"
    ]
    assert [r.status for r in results] == [PASS] * 13
    assert sum(r.detail["buckets"] for r in results) == 39


def test_the_thirteen_periods_have_no_gaps(real_parsed: list) -> None:
    result = check_period_continuity([(s.period_start, s.period_end) for s in real_parsed])
    assert result.status == PASS
    assert result.detail["statements"] == 13


def test_a_tampered_real_statement_is_blocked(real_parsed: list) -> None:
    """Acceptance item: break one amount on purpose, the gate must close."""
    original = real_parsed[0]
    rows = list(original.transactions)
    victim = rows[1]
    rows[1] = StatementTxn(
        posted_date=victim.posted_date,
        description=victim.description,
        amount_minor=victim.amount_minor + 1000,  # $10 too much
        balance_minor=victim.balance_minor,
        row_index=victim.row_index,
        provenance=victim.provenance,
    )
    tampered = ParsedStatement(
        institution=original.institution,
        account_mask=original.account_mask,
        account_subtype=original.account_subtype,
        currency=original.currency,
        period_start=original.period_start,
        period_end=original.period_end,
        summary=original.summary,
        transactions=tuple(rows),
        parser_id=original.parser_id,
        parser_version=original.parser_version,
    )
    report = run_statement_checks(tampered)
    assert report.blocked
    failed = {r.check_id for r in report.blocking_failures}
    assert "balance_chain" in failed
    assert "period_totals" in failed
    assert "declared_subtotals" in failed

    items = review_items("sha-of-the-real-file", report)
    assert items and items[0].severity == BLOCK
    # diff is (printed − computed): the row was inflated by $10, so the
    # statement's printed balance is $10 *below* where the chain now lands.
    assert json.loads(items[0].detail)["detail"]["diff_minor"] == -1000
