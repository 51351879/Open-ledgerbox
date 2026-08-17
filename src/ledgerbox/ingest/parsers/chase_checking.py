# SPDX-License-Identifier: AGPL-3.0-or-later
"""Chase (US) personal checking, PDF.

The one thing this file exists to get right:

    **Amounts and balances are bound by x coordinate, never by text order.**

The predecessor used "the first number in the block is the amount, the second
is the balance". On deposit rows Chase's text extraction pushed the amount into
a different block, so the block held only the balance — and the balance was
booked as the amount. Every one of 72 deposits was wrong and income came out
4.57× too high.

Measured on the 13-statement corpus: an amount's right edge sits within
**0.08 pt** of the AMOUNT header's right edge, and a balance's within 0.08 pt
of BALANCE's, while the two columns are ~72 pt apart. Left edges wander by
±13 pt because the numbers are right-aligned. So the anchor is the right edge,
and the header positions are *learned per page* — they differ between page 1
(432.2 / 500.6) and later pages (430.0 / 498.4) of the same statement.
"""

from __future__ import annotations

import re
from datetime import date

from ...dates import DateParseError, parse_mmdd, parse_statement_period
from ...money import parse_amount_minor, try_parse_amount_minor
from ..extract import Document, Page, Span, group_rows, row_text
from .base import ParsedStatement, ParseError, Provenance, StatementSummary, StatementTxn

PARSER_ID = "chase_checking"
PARSER_VERSION = "1"

INSTITUTION = "Chase"
PRODUCER_MARKER = "OpenText Output Transformation Engine"

#: Distance within which a number's right edge counts as "in this column".
#: Measured spread is 0.08 pt; 2.0 is a wide margin that still cannot reach
#: the neighbouring column 72 pt away.
COLUMN_TOLERANCE = 2.0

#: A continuation line must start inside the description column…
DESCRIPTION_SLACK = 6.0
#: …and follow its transaction closely. Page furniture sits much further down.
MAX_CONTINUATION_GAP = 18.0

#: "Page 1 of 4" renders as a bare ``1 4``. Anything longer than this in the
#: margin is not a page number.
_MAX_PAGE_NUMBER_DIGITS = 3

_DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}$")
_ACCOUNT_DIGITS_RE = re.compile(r"^\d{8,20}$")

DETAIL_HEADER = ("DATE", "DESCRIPTION", "AMOUNT", "BALANCE")

#: Whole-line matches only, compared case-insensitively after whitespace
#: collapsing. **Never substrings**: the predecessor's substring rule contained
#: "of", which eats "House of Sushi" — and, because "Coffee" contains "of",
#: "Coffee Shop" as well. It kept the amount and dropped the description, so
#: the damage was invisible.
SKIP_LINES = frozenset(
    {
        "transaction detail",
        "transaction detail (continued)",
        "(continued)",
        "date description amount balance",
        "page of",
        "checking summary",
        "amount",
    }
)

BEGINNING_LABEL = "beginning balance"
ENDING_LABEL = "ending balance"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def _is_skipped(text: str) -> bool:
    """Whole-line exact match. Nothing else — no substrings, no wildcards."""
    return _norm(text) in SKIP_LINES


class Columns:
    """Column geometry learned from one page's header row."""

    __slots__ = ("date_x0", "desc_x0", "amount_x1", "balance_x1", "top")

    def __init__(self, header: dict[str, Span]) -> None:
        self.date_x0 = header["DATE"].x0
        self.desc_x0 = header["DESCRIPTION"].x0
        self.amount_x1 = header["AMOUNT"].x1
        self.balance_x1 = header["BALANCE"].x1
        self.top = header["DATE"].top

    def classify(self, span: Span) -> str | None:
        """Which column a span's right edge belongs to, if any."""
        if abs(span.x1 - self.amount_x1) <= COLUMN_TOLERANCE:
            return "amount"
        if abs(span.x1 - self.balance_x1) <= COLUMN_TOLERANCE:
            return "balance"
        return None

    def in_description(self, span: Span) -> bool:
        """Bounded on **both** sides.

        A left bound alone lets the right page margin in: Chase prints a
        vertical barcode column at x0≈607, which pdfplumber emits as a single
        tall 20-digit "word". It belongs to no column and sits ~8 pt below the
        preceding row, so a left-only test accepted it as a wrapped description
        and glued a barcode onto two real transactions in the corpus. The
        amounts stayed right while the descriptions went wrong — the exact
        shape of failure this project exists to catch.
        """
        return self.desc_x0 - DESCRIPTION_SLACK <= span.x0 and span.x1 <= self.amount_x1


def _numeric(span: Span) -> int | None:
    """Parse a span as money. ``$495.86`` appears on the anchor rows."""
    return try_parse_amount_minor(span.text.lstrip("$"))


def _is_page_number_row(row: list[Span], columns: Columns) -> bool:
    """The bare ``1 4`` page footer — recognised by *position*, not by shape.

    A textual rule (``\\d+ \\d+``) is what a page-number row looks like, and it
    is also what a wrapped description looks like when a card-number fragment
    lands next to the right-margin barcode. That rule silently ate the ``4321``
    continuation of a real 2025-03 row: the description came out short, no
    warning was raised, and the amounts were untouched — invisible.

    Page numbers live outside the description column. Card fragments do not.

    The length bound matters just as much: a lone right-margin barcode is also
    all-digits and also outside the description column, so without it the
    barcode row would be dropped as page furniture — silently, which is the
    thing being fixed. At 20 digits it fails this test, falls through to the
    catch-all, and gets reported.
    """
    return all(
        not columns.in_description(span)
        and span.text.isdigit()
        and len(span.text) <= _MAX_PAGE_NUMBER_DIGITS
        for span in row
    )


def _anchor_label(row: list[Span], columns: Columns) -> str | None:
    """``'beginning'``, ``'ending'`` or None — an **exact** label match.

    ``startswith`` was wrong here for the same reason substring skip rules are
    wrong everywhere else: a wrapped description line beginning "Ending Balance
    Yoga Studio" ended the table, and with it every remaining page, silently
    and without a warning. The label is matched against the row with its
    balance-column number removed, so it must be the whole line.
    """
    numbers = [
        span for span in row if columns.classify(span) is not None and _numeric(span) is not None
    ]
    label = _norm(" ".join(span.text for span in row if span not in numbers))
    if label == BEGINNING_LABEL:
        return "beginning"
    if label == ENDING_LABEL:
        return "ending"
    return None


def _find_detail_header(rows: list[list[Span]]) -> tuple[int, Columns] | None:
    for index, row in enumerate(rows):
        texts = tuple(span.text for span in row)
        if texts[: len(DETAIL_HEADER)] == DETAIL_HEADER:
            return index, Columns({span.text: span for span in row[: len(DETAIL_HEADER)]})
    return None


class ChaseCheckingParser:
    parser_id = PARSER_ID
    parser_version = PARSER_VERSION

    # -- identification --------------------------------------------------

    def matches(self, doc: Document) -> bool:
        """Markers are looked for document-wide, not on page 1.

        Four of the thirteen real statements carry a longer message block up
        front, which pushes CHECKING SUMMARY onto page 2. Requiring page 1
        rejected 2025-02, -03, -05 and -06 outright — a third of the corpus,
        including two of the three months the predecessor also lost.
        """
        if not doc.pages:
            return False
        if PRODUCER_MARKER not in (doc.producer or ""):
            return False
        text = " ".join(page.text() for page in doc.pages)
        return (
            "JPMorgan Chase Bank" in text
            and "CHECKING SUMMARY" in text
            and "TRANSACTION DETAIL" in text
        )

    # -- parsing ---------------------------------------------------------

    def parse(self, doc: Document) -> ParsedStatement:
        warnings: list[str] = []
        page_rows = [group_rows(page.spans) for page in doc.pages]

        period_start, period_end = self._parse_period(page_rows)
        mask = self._parse_account_mask(page_rows, warnings)
        summary = self._parse_summary(page_rows)
        transactions = self._parse_transactions(
            doc.pages, page_rows, period_start, period_end, summary, warnings
        )

        return ParsedStatement(
            institution=INSTITUTION,
            account_mask=mask,
            account_subtype="checking",
            currency="USD",
            period_start=period_start,
            period_end=period_end,
            summary=summary,
            transactions=tuple(transactions),
            parser_id=self.parser_id,
            parser_version=self.parser_version,
            warnings=tuple(warnings),
        )

    # -- header ----------------------------------------------------------

    def _parse_period(self, page_rows: list[list[list[Span]]]) -> tuple[date, date]:
        for rows in page_rows:
            for row in rows[:12]:
                text = row_text(row)
                if "through" not in text.casefold():
                    continue
                try:
                    return parse_statement_period(text)
                except DateParseError:
                    continue
        raise ParseError("no statement period in the page headers — layout not recognised")

    def _parse_account_mask(
        self, page_rows: list[list[list[Span]]], warnings: list[str]
    ) -> str | None:
        """Last four digits only.

        The full account number is on the archived PDF; there is no reason for
        it to also be in the database, where it would spread into every export.
        """
        for rows in page_rows:
            for index, row in enumerate(rows):
                text = row_text(row)
                if "Account" not in text or "Number" not in text:
                    continue
                for candidate in rows[index : index + 3]:
                    for span in candidate:
                        if _ACCOUNT_DIGITS_RE.match(span.text):
                            return span.text[-4:]
        warnings.append("account number not found; account identified without a mask")
        return None

    # -- CHECKING SUMMARY ------------------------------------------------

    def _parse_summary(self, page_rows: list[list[list[Span]]]) -> StatementSummary:
        located = next(
            (
                (rows, index)
                for rows in page_rows
                for index, row in enumerate(rows)
                if _norm(row_text(row)) == "checking summary"
            ),
            None,
        )
        if located is None:
            raise ParseError("no CHECKING SUMMARY block — layout not recognised")
        rows, start = located

        amount_x1: float | None = None
        beginning: int | None = None
        ending: int | None = None
        components: dict[str, int] = {}

        for row in rows[start + 1 :]:
            texts = [span.text for span in row]
            if texts == ["AMOUNT"]:
                amount_x1 = row[0].x1
                continue
            if _norm(row_text(row)) == "transaction detail":
                break
            if amount_x1 is None:
                continue

            values: list[tuple[Span, int]] = []
            for span in row:
                if abs(span.x1 - amount_x1) > COLUMN_TOLERANCE:
                    continue
                parsed = _numeric(span)
                if parsed is not None:
                    values.append((span, parsed))
            if not values:
                continue
            if len(values) > 1:
                raise ParseError(f"two amounts in one summary row: {row_text(row)!r}")

            value_span, value = values[0]
            label = " ".join(span.text for span in row if span is not value_span).strip()
            key = _norm(label)
            if key == BEGINNING_LABEL:
                beginning = value
            elif key == ENDING_LABEL:
                ending = value
                # "Ending Balance" always closes the summary block. Stopping
                # here rather than at the next section keeps stray numbers that
                # happen to align with this column out of the totals — on the
                # statements whose summary sits on page 1 while the detail
                # starts on page 2, there is no next section to stop at.
                break
            else:
                components[label] = value

        if beginning is None or ending is None:
            raise ParseError("CHECKING SUMMARY lacks a beginning or ending balance")
        return StatementSummary(
            beginning_balance_minor=beginning,
            ending_balance_minor=ending,
            components=components,
        )

    # -- TRANSACTION DETAIL ----------------------------------------------

    def _parse_transactions(
        self,
        pages: tuple[Page, ...],
        page_rows: list[list[list[Span]]],
        period_start: date,
        period_end: date,
        summary: StatementSummary,
        warnings: list[str],
    ) -> list[StatementTxn]:
        transactions: list[StatementTxn] = []
        ignored: list[str] = []
        previous_balance = summary.beginning_balance_minor
        saw_beginning_anchor = False
        saw_ending_anchor = False
        saw_header = False
        finished = False

        for page, rows in zip(pages, page_rows, strict=True):
            if finished:
                break
            found = _find_detail_header(rows)
            if found is None:
                continue
            saw_header = True
            header_index, columns = found

            last_row_top: float | None = None
            for row in rows[header_index + 1 :]:
                text = row_text(row)

                anchor_kind = _anchor_label(row, columns)
                if anchor_kind == "beginning":
                    anchor = self._anchor_balance(row, columns)
                    if anchor is not None:
                        saw_beginning_anchor = True
                        if anchor != summary.beginning_balance_minor:
                            warnings.append(
                                "beginning balance in the table "
                                f"({anchor}) differs from the summary "
                                f"({summary.beginning_balance_minor})"
                            )
                        previous_balance = anchor
                    last_row_top = row[0].top
                    continue

                if anchor_kind == "ending":
                    saw_ending_anchor = True
                    finished = True
                    break

                if _is_skipped(text) or _is_page_number_row(row, columns):
                    continue

                if _DATE_RE.match(row[0].text):
                    txn = self._parse_row(
                        row, columns, page, period_start, period_end, previous_balance,
                        len(transactions), warnings, ignored,
                    )
                    if txn is None:
                        continue
                    transactions.append(txn)
                    if txn.balance_minor is not None:
                        previous_balance = txn.balance_minor
                    else:
                        previous_balance += txn.amount_minor
                    last_row_top = row[0].top
                    continue

                # Not a new transaction: a wrapped description line?
                #
                # Test *and* merge the same spans. Testing row[0] while merging
                # row_text(row) made the right-hand bound decorative: spans are
                # sorted by x0, so the margin barcode is always last and row[0]
                # is always innocent.
                wrapped = [span for span in row if columns.in_description(span)]
                outside = [span for span in row if not columns.in_description(span)]
                if (
                    transactions
                    and last_row_top is not None
                    and wrapped
                    and row[0].top - last_row_top <= MAX_CONTINUATION_GAP
                    and all(columns.classify(span) is None for span in row)
                ):
                    previous = transactions[-1]
                    addition = " ".join(span.text for span in wrapped)
                    transactions[-1] = StatementTxn(
                        posted_date=previous.posted_date,
                        description=f"{previous.description} {addition}".strip(),
                        amount_minor=previous.amount_minor,
                        balance_minor=previous.balance_minor,
                        row_index=previous.row_index,
                        provenance=previous.provenance,
                        amount_source=previous.amount_source,
                    )
                    ignored.extend(span.text for span in outside)
                    last_row_top = row[0].top
                else:
                    # Inside the table, not a transaction, not an anchor, not a
                    # known skip line, not a plausible continuation. Dropping it
                    # is almost certainly right — and saying so is the price of
                    # being allowed to drop anything at all.
                    ignored.append(text)

        if not saw_header:
            # Unknown means refuse. Returning zero transactions from a
            # statement that plainly has some would let a layout change look
            # like a quiet month.
            raise ParseError(
                "TRANSACTION DETAIL has no 'DATE DESCRIPTION AMOUNT BALANCE' header — "
                "the column positions are learned from that row, so without it "
                "nothing can be bound to a column"
            )
        if not saw_beginning_anchor:
            warnings.append("transaction table had no 'Beginning Balance' anchor row")
        if not saw_ending_anchor:
            warnings.append("transaction table had no 'Ending Balance' anchor row")
        if ignored:
            # Aggregated, not one per row: dropping text silently is how a
            # description gets hollowed out without anyone noticing, and one
            # warning per statement stays readable.
            warnings.append(
                f"{len(ignored)} span(s) outside the description column were not "
                f"treated as description text (e.g. {ignored[0][:24]!r})"
            )
        return transactions

    def _anchor_balance(self, row: list[Span], columns: Columns) -> int | None:
        for span in row:
            if columns.classify(span) == "balance":
                value = _numeric(span)
                if value is not None:
                    return value
        return None

    def _parse_row(
        self,
        row: list[Span],
        columns: Columns,
        page: Page,
        period_start: date,
        period_end: date,
        previous_balance: int,
        row_index: int,
        warnings: list[str],
        ignored: list[str],
    ) -> StatementTxn | None:
        posted = parse_mmdd(row[0].text, period_start, period_end)
        if posted is None:
            warnings.append(
                f"page {page.number}: date {row[0].text!r} is outside the statement period"
            )
            return None

        amount_span: Span | None = None
        balance_span: Span | None = None
        for span in row[1:]:
            column = columns.classify(span)
            if column is None or _numeric(span) is None:
                continue
            if column == "amount" and amount_span is None:
                amount_span = span
            elif column == "balance" and balance_span is None:
                balance_span = span

        description_spans = []
        for span in row[1:]:
            if span in (amount_span, balance_span):
                continue
            if not columns.in_description(span):
                # The right-margin barcode shares a baseline with a real row,
                # so row grouping puts it *inside* the transaction, not only
                # after it. Filtering only continuation lines left two real
                # descriptions with 20 digits of page furniture glued on.
                ignored.append(span.text)
                continue
            description_spans.append(span)
        description = " ".join(span.text for span in description_spans).strip()

        balance = _numeric(balance_span) if balance_span is not None else None
        amount_source = "column"

        if amount_span is not None:
            amount = parse_amount_minor(amount_span.text.lstrip("$"))
            anchor = amount_span
        elif balance is not None:
            # Recovery path: the amount column was empty, so take the step in
            # the balance chain. Verified exact on all 13 real statements when
            # forced; in practice the column binding above means it never runs.
            amount = balance - previous_balance
            amount_source = "derived"
            anchor = balance_span  # type: ignore[assignment]
            warnings.append(
                f"page {page.number}: row {row_index} had no amount column; "
                f"derived {amount} from the balance chain"
            )
        else:
            warnings.append(
                f"page {page.number}: row with a date but no numbers: {row_text(row)!r}"
            )
            return None

        return StatementTxn(
            posted_date=posted,
            description=description,
            amount_minor=amount,
            balance_minor=balance,
            row_index=row_index,
            provenance=Provenance(
                page=page.number,
                top=anchor.top,
                x0=anchor.x0,
                x1=anchor.x1,
                bottom=anchor.bottom,
            ),
            amount_source=amount_source,
        )


PARSER = ChaseCheckingParser()
