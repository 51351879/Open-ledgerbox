# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-sided statement rows become balanced double-entry transactions.

A bank statement is single-entry: one row, one signed amount, one account.
Double entry is not bookkeeping ceremony here — it is the only reason the
predecessor's defect cannot come back. There, 82.6% of "income" and 77.5% of
"spending" were internal transfers, and the largest spending category in the
pie chart was "Transfers $31,493". With every row leaving this module as two
postings that sum to zero, a transfer is *one* transaction with two legs and
there is no representation in which the same money appears twice.

Three things this module refuses to do:

1. **Guess a payee.** P0 has no merchant extraction, so ``payee`` is NULL and
   the bank's line goes verbatim into ``narration``. A payee cut from "the
   first few words of the descriptor" would read as a fact in every export and
   every chart while being a heuristic — which is precisely what the
   predecessor's category column was. The verbatim text is never lost either
   way: ``IdentityRow.raw_descriptor`` holds the bank's bytes, and it is the
   field that identity is computed from.
2. **Normalise anything in place.** Case folding and whitespace collapsing
   happen inside :func:`~ledgerbox.ledger.identity.natural_key` and nowhere
   else. What is stored is what was printed.
3. **Touch anything outside its arguments.** No database, no clock, no
   randomness. :func:`build_entries` is a pure function of the parsed
   statement, which is what makes "delete the database, rebuild it from
   archive/, get row-for-row identical output" a property that can be tested
   rather than hoped for.

This module produces rows; it does not write them. Deciding what to do when a
row already exists — in particular the shared balance assertion described at
:class:`BalanceAssertionRow` — belongs to the ingest layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from ..ingest.parsers.base import ParsedStatement, StatementTxn
from .identity import (
    NATURAL_KEY_VERSION,
    account_id_for,
    assign_occurrence_indexes,
    balance_assertion_id,
    natural_key,
    posting_id,
    txn_id,
)

#: P0 reads PDFs. It is a hardcoded constant rather than a default argument
#: because ``txn_identity`` is unique on ``(account_id, source_system,
#: natural_key, natural_key_version)``: the same statement arriving later as
#: OFX must *not* silently collide with the PDF rows, so this value has to
#: change deliberately when a second source system appears.
SOURCE_SYSTEM = "pdf"

#: The two seeded counter-accounts (``0003_seed.sql``), both
#: ``is_own_account = 0`` so internal-transfer detection can never pair a leg
#: with one of them. P2 re-categorises by rewriting ``posting.category_id``;
#: the structure built here does not change.
INCOME_ACCOUNT_ID = "income:uncategorized"
EXPENSE_ACCOUNT_ID = "expenses:uncategorized"

#: The bank's own leg is always seq 0. Fixed rather than incidental: it is the
#: leg whose sign matches what the statement printed, so anything reading a
#: transaction back as a single-sided row knows which posting to show without
#: having to guess from the account kind.
BANK_LEG_SEQ = 0
COUNTER_LEG_SEQ = 1


class ImbalancedPostingError(RuntimeError):
    """Postings for one transaction do not sum to zero.

    Unreachable by construction — the counter leg is built as the negation of
    the bank leg. That is exactly why the guard is worth keeping: what a future
    edit would change is the construction, and this is the assertion that
    notices.
    """


@dataclass(frozen=True, slots=True)
class PostingRow:
    """One leg. ``amount_minor`` is signed integer minor units, never a float."""

    id: str
    seq: int
    account_id: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class IdentityRow:
    """Everything needed to recognise this row again on the next ingest.

    ``source_id`` is ``None`` for every PDF row: Chase's PDF carries no FITID.
    The column exists anyway and is kept strictly separate from
    ``natural_key`` — a bank's own id is only unique within one institution and
    account, OFX ships ``CORRECTFITID`` to supersede it, and a pending row
    changes its id when it posts. Merging the two notions of identity is how a
    corrected transaction becomes a duplicate.
    """

    account_id: str
    source_system: str
    source_id: str | None
    natural_key: str
    natural_key_version: int
    occurrence_index: int
    #: The bank's bytes, verbatim. Never upper-cased, never trimmed of detail:
    #: normalisation is a property of the *key*, not of the record.
    raw_descriptor: str
    #: Position of this row in the statement's own record stream, matching
    #: ``raw_record.record_index``. Provenance only — never identity. Line
    #: order changes between downloads; ``natural_key`` does not.
    record_index: int


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One statement row as a balanced transaction plus its identity."""

    txn_id: str
    date: str
    payee: str | None
    narration: str | None
    postings: tuple[PostingRow, ...]
    identity: IdentityRow
    record_index: int


@dataclass(frozen=True, slots=True)
class BalanceAssertionRow:
    """On this date, this account held exactly this much.

    Two per statement, and they are deliberately collision-prone: the opening
    assertion is dated ``period_start - 1 day``, so the closing assertion of
    one statement and the opening assertion of the next land on the same
    ``(account_id, as_of, commodity_id)`` — the same id, because the id is a
    digest of exactly those three fields. ``balance_assertion`` has a UNIQUE
    constraint on that triple.

    That is the point, not a problem to route around. The two statements were
    parsed independently, so if they disagree about the balance on the day they
    share, one of them was read wrong. It is a cross-statement check that costs
    nothing and needs no extra data. This module only emits the rows; the
    writer is the one that must assert equality on conflict rather than
    overwrite or ignore.
    """

    id: str
    account_id: str
    as_of: str
    commodity_id: str
    amount_minor: int


@dataclass(frozen=True, slots=True)
class StatementEntries:
    """Everything one statement contributes to the ledger."""

    account_id: str
    account_name: str
    institution: str
    subtype: str
    mask: str | None
    currency: str
    entries: tuple[LedgerEntry, ...]
    balance_assertions: tuple[BalanceAssertionRow, ...]


def counter_account_for(amount_minor: int) -> str:
    """Money in came from somewhere; money out went somewhere.

    Sign is the only classifier used here, because sign is a property of the
    data. Anything finer needs rules, and rules that are merely plausible have
    no business deciding the structure of a transaction — they can be applied
    later to ``posting.category_id`` without rewriting a single posting.

    A zero-amount row (a reversal printed as $0.00, say) balances either way;
    it goes to expenses so that the mapping stays a total function of the sign
    with no third branch to keep in step.
    """
    return INCOME_ACCOUNT_ID if amount_minor > 0 else EXPENSE_ACCOUNT_ID


def account_name_for(institution: str, subtype: str, mask: str | None) -> str:
    """``('Chase', 'checking', '1234')`` → ``'Assets:Chase:Checking:1234'``.

    Derived from :func:`account_id_for` rather than computed independently, so
    the display name and the id can never disagree about how an institution was
    slugged.
    """
    segments = account_id_for(institution, subtype, mask).split(":")
    if mask is None:
        # ``account_id_for`` substitutes a literal "default" segment so two
        # mask-less accounts at one institution cannot collide. That is an
        # identity concern; a name reading "Assets:Chase:Checking:Default"
        # would put an account nobody has in front of the user.
        segments = segments[:-1]
    return ":".join(_display_segment(segment) for segment in segments)


def build_entries(statement: ParsedStatement) -> StatementEntries:
    """Turn a parsed statement into balanced transactions and its two anchors.

    Pure: same statement in, byte-identical ids out, every time.
    """
    account_id = account_id_for(
        statement.institution, statement.account_subtype, statement.account_mask
    )
    currency = statement.currency

    posted_dates = [txn.posted_date.isoformat() for txn in statement.transactions]
    # Occurrence numbering is what keeps two identical $4.75 coffees on one day
    # from collapsing into a single transaction. It is order-dependent by
    # construction, and the order is the statement's own row order — stable for
    # as long as the PDF is, which is the whole premise of re-reading it.
    occurrences = assign_occurrence_indexes(
        [
            (posted_date, txn.amount_minor, txn.description)
            for posted_date, txn in zip(posted_dates, statement.transactions, strict=True)
        ]
    )

    entries = tuple(
        _entry(
            account_id=account_id,
            currency=currency,
            txn=txn,
            posted_date=posted_date,
            occurrence_index=occurrence_index,
            record_index=record_index,
        )
        for record_index, (txn, posted_date, occurrence_index) in enumerate(
            zip(statement.transactions, posted_dates, occurrences, strict=True)
        )
    )

    return StatementEntries(
        account_id=account_id,
        account_name=account_name_for(
            statement.institution, statement.account_subtype, statement.account_mask
        ),
        institution=statement.institution,
        subtype=statement.account_subtype,
        mask=statement.account_mask,
        currency=currency,
        entries=entries,
        balance_assertions=_balance_assertions(statement, account_id, currency),
    )


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _entry(
    *,
    account_id: str,
    currency: str,
    txn: StatementTxn,
    posted_date: str,
    occurrence_index: int,
    record_index: int,
) -> LedgerEntry:
    key = natural_key(account_id, posted_date, txn.amount_minor, txn.description, occurrence_index)
    transaction_id = txn_id(key)

    postings = (
        PostingRow(
            id=posting_id(transaction_id, BANK_LEG_SEQ),
            seq=BANK_LEG_SEQ,
            account_id=account_id,
            # Verbatim, sign included. The statement's sign convention is the
            # ledger's: a withdrawal is negative on the asset account.
            amount_minor=txn.amount_minor,
            currency=currency,
        ),
        PostingRow(
            id=posting_id(transaction_id, COUNTER_LEG_SEQ),
            seq=COUNTER_LEG_SEQ,
            account_id=counter_account_for(txn.amount_minor),
            amount_minor=-txn.amount_minor,
            currency=currency,
        ),
    )
    _require_zero_sum(transaction_id, postings)

    return LedgerEntry(
        txn_id=transaction_id,
        date=posted_date,
        # No merchant extraction in P0 — see the module docstring.
        payee=None,
        # The bank's line, verbatim. ``or None`` only so that a row with no
        # description at all stores SQL NULL instead of an empty string that
        # would later have to be told apart from a real blank.
        narration=txn.description or None,
        postings=postings,
        identity=IdentityRow(
            account_id=account_id,
            source_system=SOURCE_SYSTEM,
            source_id=None,
            natural_key=key,
            natural_key_version=NATURAL_KEY_VERSION,
            occurrence_index=occurrence_index,
            raw_descriptor=txn.description,
            record_index=record_index,
        ),
        record_index=record_index,
    )


def _balance_assertions(
    statement: ParsedStatement, account_id: str, currency: str
) -> tuple[BalanceAssertionRow, ...]:
    # The opening balance is the balance *before* the period's first day, not
    # on it: dating it ``period_start`` would claim the account held that
    # amount at the end of a day on which the statement already books
    # transactions. The one-day shift is also what makes it line up with the
    # previous statement's closing assertion — see BalanceAssertionRow.
    opening_as_of = (statement.period_start - timedelta(days=1)).isoformat()
    closing_as_of = statement.period_end.isoformat()
    return (
        BalanceAssertionRow(
            id=balance_assertion_id(account_id, opening_as_of, currency),
            account_id=account_id,
            as_of=opening_as_of,
            commodity_id=currency,
            amount_minor=statement.summary.beginning_balance_minor,
        ),
        BalanceAssertionRow(
            id=balance_assertion_id(account_id, closing_as_of, currency),
            account_id=account_id,
            as_of=closing_as_of,
            commodity_id=currency,
            amount_minor=statement.summary.ending_balance_minor,
        ),
    )


def _require_zero_sum(transaction_id: str, postings: tuple[PostingRow, ...]) -> None:
    """``SUM(amount_minor) == 0`` per currency, checked before anything is written.

    A plain ``assert`` would be removed by ``python -O``, and this is the one
    invariant the project exists to keep, so it raises instead.
    """
    totals: dict[str, int] = {}
    for posting in postings:
        totals[posting.currency] = totals.get(posting.currency, 0) + posting.amount_minor
    residuals = {currency: total for currency, total in totals.items() if total != 0}
    if residuals:
        detail = ", ".join(f"{code} off by {total}" for code, total in sorted(residuals.items()))
        raise ImbalancedPostingError(f"transaction {transaction_id} does not balance: {detail}")


def _display_segment(segment: str) -> str:
    """``opening-balances`` → ``Opening-Balances``, matching the seeded names."""
    return "-".join(word.capitalize() for word in segment.split("-"))
