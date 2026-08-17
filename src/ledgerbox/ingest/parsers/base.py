# SPDX-License-Identifier: AGPL-3.0-or-later
"""What a parser must produce.

A parser's job ends at "here is what the page says". It does not decide
whether the statement is trustworthy — that is the reconciler's job, and
keeping the two apart is what stops a parser from quietly papering over its
own mistakes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from ..extract import Document


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where on the page a value came from. Kept for every parsed field."""

    page: int
    top: float
    x0: float
    x1: float
    bottom: float

    def as_bbox(self) -> tuple[float, float, float, float]:
        return (self.x0, self.top, self.x1, self.bottom)


@dataclass(frozen=True, slots=True)
class StatementTxn:
    """One row of the transaction table, exactly as printed."""

    posted_date: date
    description: str
    amount_minor: int
    balance_minor: int | None
    row_index: int
    provenance: Provenance
    #: ``column`` — read from the AMOUNT column.
    #: ``derived`` — the row had no amount, so it was recovered from the
    #: balance chain (``bal[n] - bal[n-1]``). Recorded because a statement
    #: where this happens often is a statement whose layout has drifted.
    amount_source: str = "column"


@dataclass(frozen=True, slots=True)
class StatementSummary:
    """The statement's own printed totals — the reconciler's evidence."""

    beginning_balance_minor: int
    ending_balance_minor: int
    #: label → signed minor units, verbatim from the summary block, e.g.
    #: ``{"Deposits and Additions": 234567, "Fees": -1200}``.
    components: dict[str, int] = field(default_factory=dict)
    #: How many transactions the statement says it contains, when it says so.
    #: Chase does not print one. Kept as its own field rather than fished out
    #: of ``components``: a count is not money, and ``components`` only ever
    #: holds values that passed a money parser requiring two decimal places —
    #: so a count could never have arrived there, and the check that looked for
    #: one was unreachable for every bank, not just this one.
    declared_transaction_count: int | None = None

    @property
    def declared_net_minor(self) -> int:
        return sum(self.components.values())

    def component(self, label: str) -> int | None:
        for key, value in self.components.items():
            if key.casefold() == label.casefold():
                return value
        return None


@dataclass(frozen=True, slots=True)
class ParsedStatement:
    institution: str
    account_mask: str | None
    account_subtype: str
    currency: str
    period_start: date
    period_end: date
    summary: StatementSummary
    transactions: tuple[StatementTxn, ...]
    parser_id: str
    parser_version: str
    #: Non-fatal oddities. Anything fatal raises instead.
    warnings: tuple[str, ...] = ()

    @property
    def statement_month(self) -> str:
        """Keyed on the period's END day. See ledgerbox.dates for why."""
        return f"{self.period_end.year:04d}-{self.period_end.month:02d}"


class ParseError(RuntimeError):
    """The document is not parseable as this layout. Never guess instead."""


@runtime_checkable
class Parser(Protocol):
    parser_id: str
    parser_version: str

    def matches(self, doc: Document) -> bool:
        """True only when this parser recognises the layout with certainty."""

    def parse(self, doc: Document) -> ParsedStatement: ...
