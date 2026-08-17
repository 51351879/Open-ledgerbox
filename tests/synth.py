# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic Chase-shaped documents, built from coordinates in code.

Parser tests need statements, and real statements can never enter this
repository. Building :class:`Document` objects directly gives the bank logic
full coverage on CI with no PDF and no real data — the same split the plan
calls for (``extract_spans`` tested against PDFs, ``spans_to_transactions``
tested against spans), just with the spans authored rather than captured.

Geometry matches the real corpus: the header positions and the right-edge
alignment of the AMOUNT and BALANCE columns are the measured values.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ledgerbox.ingest.extract import Document, Page, Span

PRODUCER = "OpenText Output Transformation Engine - 23.4.25"

DATE_X0 = 36.2
DESC_X0 = 79.9
AMOUNT_X0, AMOUNT_X1 = 432.2, 462.9
BALANCE_X0, BALANCE_X1 = 500.6, 534.7
SUMMARY_AMOUNT_X0, SUMMARY_AMOUNT_X1 = 346.0, 376.7

CHAR_WIDTH = 5.0
ROW_HEIGHT = 12.2


def _right_aligned(text: str, right: float, top: float) -> Span:
    """Numbers are right-aligned in the real statements; so are these."""
    width = len(text) * CHAR_WIDTH
    return Span(text=text, x0=round(right - width, 2), x1=right, top=top, bottom=top + 7.8)


def _at(text: str, x0: float, top: float) -> Span:
    return Span(
        text=text, x0=x0, x1=round(x0 + len(text) * CHAR_WIDTH, 2), top=top, bottom=top + 7.8
    )


def _words(text: str, x0: float, top: float) -> list[Span]:
    spans = []
    cursor = x0
    for word in text.split():
        span = _at(word, cursor, top)
        spans.append(span)
        cursor = span.x1 + CHAR_WIDTH
    return spans


@dataclass
class Row:
    """One transaction line, optionally with wrapped continuation lines."""

    date: str
    description: str
    amount: str | None
    balance: str | None
    continuations: tuple[str, ...] = ()


@dataclass
class StatementBuilder:
    period: str = "January 01, 2025 through January 31, 2025"
    account_number: str = "000000000001234"
    beginning: str = "$820.15"
    ending: str = "$857.26"
    components: tuple[tuple[str, str], ...] = (("Deposits and Additions", "37.11"),)
    rows: list[Row] = field(default_factory=list)
    #: Extra lines placed inside the detail table, to test skip rules.
    detail_noise: tuple[str, ...] = ()
    #: Push the summary onto page 2, as four real statements do.
    summary_on_page_two: bool = False
    include_beginning_anchor: bool = True
    include_ending_anchor: bool = True
    producer: str = PRODUCER

    def build(self) -> Document:
        header_spans: list[Span] = []
        top = 47.6
        header_spans += _words(self.period, 200.0, top)
        header_spans += _words("JPMorgan Chase Bank, N.A.", 40.0, top + 8)
        header_spans += _words("Account Number:", 40.0, top + 13)
        header_spans += [_at(self.account_number, 436.0, top + 21)]

        summary_spans = self._summary_spans(start_top=520.0)
        detail_top = 200.0 if self.summary_on_page_two else 650.0
        detail_spans = self._detail_spans(start_top=detail_top)

        if self.summary_on_page_two:
            pages = [
                Page(1, 612.0, 792.0, tuple(header_spans + _words("Message area", 40.0, 300.0))),
                Page(2, 612.0, 792.0, tuple(header_spans + summary_spans + detail_spans)),
            ]
        else:
            pages = [
                Page(1, 612.0, 792.0, tuple(header_spans + summary_spans)),
                Page(2, 612.0, 792.0, tuple(header_spans + detail_spans)),
            ]
        return Document(producer=self.producer, page_count=len(pages), pages=tuple(pages))

    def _summary_spans(self, start_top: float) -> list[Span]:
        spans: list[Span] = []
        top = start_top
        spans += _words("CHECKING SUMMARY", 40.5, top)
        top += ROW_HEIGHT
        spans += [_right_aligned("AMOUNT", SUMMARY_AMOUNT_X1, top)]
        top += ROW_HEIGHT
        spans += _words("Beginning Balance", 39.6, top)
        spans += [_right_aligned(self.beginning, SUMMARY_AMOUNT_X1, top)]
        for label, value in self.components:
            top += ROW_HEIGHT
            spans += _words(label, 39.6, top)
            spans += [_right_aligned(value, SUMMARY_AMOUNT_X1, top)]
        top += ROW_HEIGHT
        spans += _words("Ending Balance", 39.6, top)
        spans += [_right_aligned(self.ending, SUMMARY_AMOUNT_X1, top)]
        return spans

    def _detail_spans(self, start_top: float) -> list[Span]:
        spans: list[Span] = []
        top = start_top
        spans += _words("TRANSACTION DETAIL", 35.7, top)
        top += ROW_HEIGHT
        spans += [
            _at("DATE", DATE_X0, top),
            _at("DESCRIPTION", DESC_X0, top),
            Span("AMOUNT", AMOUNT_X0, AMOUNT_X1, top, top + 7.8),
            Span("BALANCE", BALANCE_X0, BALANCE_X1, top, top + 7.8),
        ]
        top += ROW_HEIGHT

        if self.include_beginning_anchor:
            spans += _words("Beginning Balance", 87.1, top)
            spans += [_right_aligned(self.beginning, BALANCE_X1, top)]
            top += ROW_HEIGHT

        for row in self.rows:
            spans += [_at(row.date, DATE_X0, top)]
            spans += _words(row.description, DESC_X0, top)
            if row.amount is not None:
                spans += [_right_aligned(row.amount, AMOUNT_X1, top)]
            if row.balance is not None:
                spans += [_right_aligned(row.balance, BALANCE_X1, top)]
            top += ROW_HEIGHT
            for continuation in row.continuations:
                spans += _words(continuation, DESC_X0, top)
                top += ROW_HEIGHT

        for noise in self.detail_noise:
            spans += _words(noise, DESC_X0, top)
            top += ROW_HEIGHT

        if self.include_ending_anchor:
            spans += _words("Ending Balance", 84.9, top)
            spans += [_right_aligned(self.ending, BALANCE_X1, top)]
            top += ROW_HEIGHT

        # Page furniture, far enough below to be no one's continuation line.
        top += 35
        spans += [_at("Page", 466.0, top), _at("of", 487.0, top)]
        spans += [_at("1", 482.0, top + 6), _at("4", 494.0, top + 6)]
        return spans


def simple_statement(**kwargs: object) -> Document:
    """A two-transaction statement that reconciles."""
    defaults: dict[str, object] = {
        "rows": [
            Row("01/02", "Zelle Payment From A Name 10000000001", "37.11", "857.26"),
            Row("01/03", "Card Purchase 01/02 Some Merchant CA", "-12.44", "844.82"),
        ],
        "beginning": "$820.15",
        "ending": "$844.82",
        "components": (("Deposits and Additions", "37.11"), ("Fees", "-12.44")),
    }
    defaults.update(kwargs)
    return StatementBuilder(**defaults).build()  # type: ignore[arg-type]
