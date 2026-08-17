# SPDX-License-Identifier: AGPL-3.0-or-later
"""M4: the Chase checking parser.

Synthetic statements cover the logic; the real 13 are a regression gate that
skips when ``LEDGERBOX_REAL_FIXTURES`` is unset.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from synth import Row, StatementBuilder, simple_statement

from ledgerbox.ingest.extract import Document, Page, Span, extract_spans
from ledgerbox.ingest.parsers.base import ParseError
from ledgerbox.ingest.parsers.chase_checking import PARSER
from ledgerbox.ingest.registry import UnknownLayout, identify, identify_or_raise

# The real corpus's expected totals live beside the corpus itself, in an
# untracked expected-totals.json read by the `real_expected` fixture. They are
# the owner's real aggregate figures, and the repository publishes only the
# synthetic story set (README, docs/PROJECT_SUMMARY.md §5).


# --------------------------------------------------------------------------
# identification
# --------------------------------------------------------------------------


def test_recognises_a_chase_statement() -> None:
    assert identify(simple_statement()) is PARSER


def test_recognises_a_statement_whose_summary_starts_on_page_two() -> None:
    """Four of the thirteen real statements look like this."""
    doc = StatementBuilder(
        summary_on_page_two=True,
        rows=[Row("01/02", "Something", "37.11", "857.26")],
        ending="$857.26",
    ).build()
    assert identify(doc) is PARSER


def test_refuses_an_unknown_producer() -> None:
    doc = StatementBuilder(producer="Some Other Bank Renderer 1.0").build()
    assert identify(doc) is None
    with pytest.raises(UnknownLayout, match="no parser recognises"):
        identify_or_raise(doc)


def test_refuses_a_document_with_no_checking_summary() -> None:
    doc = Document(
        producer="OpenText Output Transformation Engine - 23.4.25",
        page_count=1,
        pages=(Page(1, 612.0, 792.0, (Span("JPMorgan Chase Bank", 40.0, 120.0, 50.0, 58.0),)),),
    )
    assert identify(doc) is None


# --------------------------------------------------------------------------
# the bug this parser exists to prevent
# --------------------------------------------------------------------------


def test_amount_and_balance_are_bound_by_column_not_by_order() -> None:
    """A deposit row: amount 37.11, balance 857.26. Order must not decide."""
    parsed = PARSER.parse(simple_statement())
    first = parsed.transactions[0]
    assert first.amount_minor == 3711
    assert first.balance_minor == 85726


def test_a_row_whose_amount_is_missing_is_recovered_from_the_balance_chain() -> None:
    doc = StatementBuilder(
        rows=[
            Row("01/02", "Deposit with no amount column", None, "857.26"),
            Row("01/03", "Normal row", "-12.44", "844.82"),
        ],
        beginning="$820.15",
        ending="$844.82",
        components=(("Deposits and Additions", "37.11"), ("Fees", "-12.44")),
    ).build()
    parsed = PARSER.parse(doc)

    recovered = parsed.transactions[0]
    assert recovered.amount_minor == 85726 - 82015 == 3711
    assert recovered.amount_source == "derived"
    assert any("derived" in warning for warning in parsed.warnings)
    assert parsed.transactions[1].amount_source == "column"


def test_a_number_in_the_description_is_not_mistaken_for_an_amount() -> None:
    doc = StatementBuilder(
        rows=[Row("01/02", "Payment ref 1234 and 99.99 inside text", "37.11", "857.26")],
        ending="$857.26",
    ).build()
    txn = PARSER.parse(doc).transactions[0]
    assert txn.amount_minor == 3711
    assert "99.99" in txn.description and "1234" in txn.description


def test_the_balance_column_is_never_read_as_the_amount() -> None:
    """The predecessor's exact failure: 72 deposits booked at their balance."""
    doc = StatementBuilder(
        rows=[
            Row("01/02", "Zelle Payment From Someone", "37.11", "857.26"),
            Row("01/05", "Zelle Payment From Someone Else", "271.45", "1,128.71"),
        ],
        beginning="$820.15",
        ending="$1,128.71",
        components=(("Deposits and Additions", "308.56"),),
    ).build()
    parsed = PARSER.parse(doc)
    deposits = sum(t.amount_minor for t in parsed.transactions if t.amount_minor > 0)
    assert deposits == 30856
    assert deposits == parsed.summary.component("Deposits and Additions")


# --------------------------------------------------------------------------
# rows, descriptions, skip rules
# --------------------------------------------------------------------------


def test_wrapped_descriptions_are_joined() -> None:
    doc = StatementBuilder(
        rows=[
            Row(
                "01/03",
                "Recurring Card Purchase 01/02 Some Vendor",
                "-16.35",
                "853.26",
                continuations=("Card", "4321"),
            )
        ],
        beginning="$869.61",
        ending="$853.26",
        components=(("Fees", "-16.35"),),
    ).build()
    txn = PARSER.parse(doc).transactions[0]
    assert txn.description.endswith("Card 4321")
    assert txn.amount_minor == -1635


@pytest.mark.parametrize(
    "merchant",
    [
        "House of Sushi",  # the predecessor's "of" rule ate this
        "Coffee Shop",  # "Coffee" contains "of"
        "SMOG CHECK 4 LESS",  # "SM" substring
        "Page One Books",  # "Page" substring
        "Fees And More Cafe",  # "Fees" substring
    ],
)
def test_substring_skip_rules_do_not_eat_real_merchants(merchant: str) -> None:
    """Skip rules match whole lines only. Substrings destroyed descriptions."""
    doc = StatementBuilder(
        rows=[Row("01/02", f"Card Purchase 01/01 {merchant} CA", "-12.44", "488.88")],
        beginning="$820.15",
        ending="$488.88",
        components=(("Fees", "-12.44"),),
    ).build()
    txn = PARSER.parse(doc).transactions[0]
    assert merchant in txn.description
    assert txn.amount_minor == -1244


def test_right_margin_furniture_is_not_appended_to_a_description() -> None:
    """A tall barcode word at x0≈607 is not a wrapped description line.

    It happened: two real rows carried a 20-digit margin barcode inside their
    description. Amounts were unaffected, so only reading the text showed it.
    """
    doc = StatementBuilder(
        rows=[Row("01/02", "Payment To Chase Card Ending IN 8765", "-500.00", "-4.14")],
        beginning="$820.15",
        ending="-$4.14",
        components=(("Electronic Withdrawals", "-500.00"),),
    ).build()
    doc = _inject_barcode(doc, top=_top_of(doc, "-500.00") + 7.64)

    parsed = PARSER.parse(doc)
    txn = parsed.transactions[0]
    assert BARCODE not in txn.description
    assert txn.description.endswith("8765")
    assert txn.amount_minor == -50000
    assert any(BARCODE in w for w in parsed.warnings)


BARCODE = "12345678901234567890"


def _top_of(doc: Document, text: str) -> float:
    """Top of *text* on the **detail** page.

    Scoped to the last page on purpose: an amount appears twice in a statement,
    once in the summary block and once in the transaction row, and picking the
    first match placed the barcode above the table header where nothing would
    ever look at it — a test that passes by testing nothing.
    """
    page = doc.pages[-1]
    tops = [s.top for s in page.spans if s.text == text]
    if not tops:
        raise AssertionError(f"{text!r} not found on the detail page")
    return max(tops)


def _inject_barcode(doc: Document, *, top: float) -> Document:
    """Add the right-margin barcode at *top* — a tall, narrow, 20-digit word.

    Placed by absolute position rather than "below the last row": the ending
    balance anchor stops parsing, so anything dropped past it is never seen and
    a test aimed at the barcode would quietly test nothing.
    """
    pages = list(doc.pages)
    last = pages[-1]
    placed = Span(BARCODE, 606.66, 606.90, top, top + 67.2)
    pages[-1] = Page(last.number, last.width, last.height, (*last.spans, placed))
    return Document(doc.producer, doc.page_count, tuple(pages))


def test_a_wrapped_line_starting_with_ending_balance_does_not_end_the_table() -> None:
    """`startswith` here dropped every remaining row — and every later page."""
    doc = StatementBuilder(
        rows=[
            Row(
                "01/02",
                "Card Purchase 01/01 Vendor",
                "-12.44",
                "488.88",
                continuations=("Ending Balance Yoga Studio",),
            ),
            Row("01/03", "Second transaction", "37.11", "844.82"),
        ],
        beginning="$820.15",
        ending="$844.82",
        components=(("Deposits and Additions", "37.11"), ("Fees", "-12.44")),
    ).build()
    parsed = PARSER.parse(doc)
    assert len(parsed.transactions) == 2, "the second row must survive"
    assert "Yoga Studio" in parsed.transactions[0].description


def test_a_detail_table_without_a_header_row_is_refused() -> None:
    """No header means no column positions; guessing them is not an option."""
    doc = simple_statement()
    stripped = Document(
        producer=doc.producer,
        page_count=doc.page_count,
        pages=tuple(
            Page(
                p.number,
                p.width,
                p.height,
                tuple(
                    s
                    for s in p.spans
                    if not (s.text in {"DATE", "DESCRIPTION", "AMOUNT", "BALANCE"} and s.top > 600)
                ),
            )
            for p in doc.pages
        ),
    )
    with pytest.raises(ParseError, match="header"):
        PARSER.parse(stripped)


def test_page_furniture_is_not_appended_to_a_description() -> None:
    doc = simple_statement()
    parsed = PARSER.parse(doc)
    for txn in parsed.transactions:
        assert "Page" not in txn.description
        assert "TRANSACTION DETAIL" not in txn.description


def test_rows_outside_the_period_are_reported_not_guessed() -> None:
    doc = StatementBuilder(
        rows=[
            Row("01/02", "Inside the period", "37.11", "857.26"),
            Row("07/15", "Outside the period", "-10.00", "533.86"),
        ],
        ending="$857.26",
    ).build()
    parsed = PARSER.parse(doc)
    assert len(parsed.transactions) == 1
    assert any("outside the statement period" in w for w in parsed.warnings)


def test_a_december_row_in_a_year_crossing_period_keeps_its_year() -> None:
    doc = StatementBuilder(
        period="December 07, 2024 through January 07, 2025",
        rows=[
            Row("12/28", "Late December", "-10.00", "485.86"),
            Row("01/02", "Early January", "37.11", "533.86"),
        ],
        beginning="$820.15",
        ending="$533.86",
        components=(("Deposits and Additions", "37.11"), ("Fees", "-10.00")),
    ).build()
    parsed = PARSER.parse(doc)
    assert parsed.transactions[0].posted_date == date(2024, 12, 28)
    assert parsed.transactions[1].posted_date == date(2025, 1, 2)
    assert parsed.statement_month == "2025-01"


# --------------------------------------------------------------------------
# summary block
# --------------------------------------------------------------------------


def test_summary_components_are_kept_verbatim() -> None:
    doc = StatementBuilder(
        rows=[Row("01/02", "Something", "37.11", "857.26")],
        ending="$857.26",
        components=(
            ("Deposits and Additions", "2,345.67"),
            ("ATM & Debit Card Withdrawals", "-317.45"),
            ("Electronic Withdrawals", "-906.12"),
            ("Fees", "-12.00"),
        ),
    ).build()
    summary = PARSER.parse(doc).summary
    assert summary.component("Deposits and Additions") == 234567
    assert summary.component("ATM & Debit Card Withdrawals") == -31745
    assert summary.component("Fees") == -1200
    assert summary.declared_net_minor == 234567 - 31745 - 90612 - 1200


def test_month_comes_from_the_period_end() -> None:
    doc = StatementBuilder(
        period="May 31, 2025 through June 30, 2025",
        rows=[Row("06/02", "Something", "37.11", "857.26")],
        ending="$857.26",
    ).build()
    parsed = PARSER.parse(doc)
    assert parsed.statement_month == "2025-06"
    assert parsed.period_start == date(2025, 5, 31)


def test_a_statement_without_a_summary_is_refused() -> None:
    doc = StatementBuilder(rows=[Row("01/02", "x", "37.11", "857.26")]).build()
    stripped = Document(
        producer=doc.producer,
        page_count=doc.page_count,
        pages=tuple(
            Page(
                p.number,
                p.width,
                p.height,
                tuple(s for s in p.spans if s.text not in {"CHECKING", "SUMMARY"}),
            )
            for p in doc.pages
        ),
    )
    with pytest.raises(ParseError, match="CHECKING SUMMARY"):
        PARSER.parse(stripped)


def test_a_statement_without_a_period_is_refused() -> None:
    doc = StatementBuilder(period="Statement of account").build()
    with pytest.raises(ParseError, match="statement period"):
        PARSER.parse(doc)


def test_account_number_is_reduced_to_four_digits() -> None:
    doc = StatementBuilder(
        account_number="000000000001234",
        rows=[Row("01/02", "x", "37.11", "857.26")],
        ending="$857.26",
    ).build()
    parsed = PARSER.parse(doc)
    assert parsed.account_mask == "1234"
    assert "000000000001234" not in str(parsed.account_mask)


# --------------------------------------------------------------------------
# the real corpus
# --------------------------------------------------------------------------


def test_real_statements_all_parse(real_parsed: list, real_expected: dict[str, int]) -> None:
    assert len(real_parsed) == real_expected["months"]
    assert sum(len(s.transactions) for s in real_parsed) == real_expected["rows"]


def test_real_totals_match_the_statements_own_figures(
    real_parsed: list, real_expected: dict[str, int]
) -> None:
    deposits = sum(
        t.amount_minor for s in real_parsed for t in s.transactions if t.amount_minor > 0
    )
    withdrawals = sum(
        t.amount_minor for s in real_parsed for t in s.transactions if t.amount_minor < 0
    )
    assert deposits == real_expected["deposits_minor"]
    assert withdrawals == real_expected["withdrawals_minor"]
    assert deposits + withdrawals == real_expected["net_minor"]


def test_real_balance_chain_replays_to_the_final_printed_balance(
    real_parsed: list, real_expected: dict[str, int]
) -> None:
    running = real_parsed[0].summary.beginning_balance_minor
    assert running == real_expected["opening_minor"]
    for statement in real_parsed:
        assert statement.summary.beginning_balance_minor == running
        for txn in statement.transactions:
            running += txn.amount_minor
            if txn.balance_minor is not None:
                assert txn.balance_minor == running, (
                    f"{statement.statement_month} row {txn.row_index}: chain broke"
                )
        assert running == statement.summary.ending_balance_minor
    assert running == real_expected["closing_minor"]


def test_real_statement_months_are_all_distinct(real_parsed: list) -> None:
    months = [s.statement_month for s in real_parsed]
    assert len(set(months)) == 13
    assert {"2025-06", "2025-09", "2025-12"} <= set(months)


def test_real_statements_need_no_fallbacks(real_parsed: list) -> None:
    """Column binding gets every amount; the balance-chain recovery never runs."""
    derived = [t for s in real_parsed for t in s.transactions if t.amount_source == "derived"]
    assert derived == []


def test_real_statements_warn_only_about_margin_furniture(real_parsed: list) -> None:
    """Three statements carry a right-margin barcode among the detail rows.

    It reaches the table three different ways — inside a transaction row, on a
    line of its own, and next to a wrapped card fragment — and each was found
    only after the previous one was fixed. Dropping it is right; dropping it
    *silently* is not, so every instance is reported.
    """
    warnings = [(s.statement_month, w) for s in real_parsed for w in s.warnings]
    unexpected = [(m, w) for m, w in warnings if "outside the description column" not in w]
    assert unexpected == []
    assert {m for m, _ in warnings} == {"2025-02", "2025-03", "2026-01"}
    assert all("2600" in w for _, w in warnings), "the only furniture here is the barcode"


def test_a_wrapped_card_fragment_beside_the_barcode_keeps_the_fragment() -> None:
    """`4321 <20-digit barcode>` used to match a `\\d+ \\d+` page-number rule.

    The whole row was dropped, so one real 2025-03 description lost its card
    fragment while its neighbours kept theirs — amounts intact, no warning.
    """
    doc = StatementBuilder(
        rows=[
            Row(
                "03/28",
                "Card Purchase 03/28 Some Merchant CA Card",
                "-13.16",
                "488.88",
                continuations=("4321",),
            )
        ],
        beginning="$502.04",
        ending="$488.88",
        components=(("ATM & Debit Card Withdrawals", "-13.16"),),
        period="March 01, 2025 through March 31, 2025",
    ).build()
    doc = _inject_barcode(doc, top=_top_of(doc, "4321"))

    parsed = PARSER.parse(doc)
    assert parsed.transactions[0].description.endswith("Card 4321")
    assert BARCODE not in parsed.transactions[0].description
    assert any("outside the description column" in w for w in parsed.warnings)


def test_a_lone_barcode_row_is_reported_not_mistaken_for_a_page_number() -> None:
    doc = StatementBuilder(
        rows=[Row("03/28", "Card Purchase 03/28 Vendor CA Card 4321", "-13.16", "488.88")],
        beginning="$502.04",
        ending="$488.88",
        components=(("ATM & Debit Card Withdrawals", "-13.16"),),
        period="March 01, 2025 through March 31, 2025",
    ).build()
    doc = _inject_barcode(doc, top=_top_of(doc, "-13.16") + 9.0)

    parsed = PARSER.parse(doc)
    assert parsed.transactions[0].description.endswith("Card 4321")
    assert any(BARCODE in w for w in parsed.warnings), "a 20-digit barcode is not a page number"


def test_the_page_number_footer_is_still_skipped_quietly() -> None:
    """`1 4` really is furniture; warning about it on every statement is noise."""
    parsed = PARSER.parse(simple_statement())
    assert parsed.warnings == ()


def test_real_summary_subtotals_agree_with_the_rows(real_parsed: list) -> None:
    for statement in real_parsed:
        rows_net = sum(t.amount_minor for t in statement.transactions)
        assert statement.summary.declared_net_minor == rows_net, statement.statement_month
        assert (
            statement.summary.beginning_balance_minor + rows_net
            == statement.summary.ending_balance_minor
        )


def test_real_descriptions_survive(real_parsed: list) -> None:
    """A row that kept its amount but lost its description looks fine."""
    empty = [
        (s.statement_month, t.row_index)
        for s in real_parsed
        for t in s.transactions
        if len(t.description.strip()) < 4
    ]
    assert empty == []


def test_real_descriptions_are_not_polluted_by_page_furniture(real_parsed: list) -> None:
    """Length alone does not prove a description is clean.

    Two rows once carried a 20-digit right-margin barcode. Both were long,
    both had correct amounts, and a `len(desc) >= 4` check saw nothing.
    """
    import re

    suspicious = [
        (s.statement_month, t.row_index, run)
        for s in real_parsed
        for t in s.transactions
        for run in re.findall(r"\d{12,}", t.description)
        # genuine long digit runs exist: Venmo/Zelle reference ids and wire
        # originator numbers. The barcode is distinguishable: it is the whole
        # tail of the description and 20 digits long.
        if len(run) >= 20 and t.description.rstrip().endswith(run)
    ]
    assert suspicious == []


def test_real_rows_carry_provenance(real_parsed: list) -> None:
    for statement in real_parsed:
        for txn in statement.transactions:
            assert txn.provenance.page >= 1
            assert txn.provenance.x1 > 0
            # amounts sit in the right-hand columns, never in the description
            assert txn.provenance.x1 > 400


def test_real_statements_use_two_header_geometries(real_statements: list[Path]) -> None:
    """Column positions differ between pages; hard-coding them would break."""
    seen = set()
    for path in real_statements:
        doc = extract_spans(path)
        for page in doc.pages:
            for span in page.spans:
                if span.text == "BALANCE":
                    seen.add(round(span.x1, 1))
    assert len(seen) > 1, f"expected per-page variation, saw {seen}"
    assert seen == {532.5, 534.7}
