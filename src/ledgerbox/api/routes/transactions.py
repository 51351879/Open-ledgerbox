# SPDX-License-Identifier: AGPL-3.0-or-later
"""Transactions over HTTP: read them, and record what a person says one is.

Until this module existed the operator could see four figures and a list of
files, and **not one transaction**. That was the gap the roadmap was reordered
around (``docs/STATUS.md`` §2.5): a tool that can prove its numbers and cannot
show them has finished only half of a sentence.

**Filtering, sorting and paging are the database's job**, per
``docs/EXECUTION_PLAN.md`` §6. Not for speed on 415 rows — because a browser
that holds the whole ledger in order to slice it grows a second definition of
every question it slices by, which is how the predecessor came to have two
month definitions and a table that disagreed with its own chart.

**The write is one field wide and that is the design.** ``PATCH`` sets
``category_override`` and nothing else. Naming the category whose kind is
``transfer`` says "this is a transfer"; naming any income or expense category
says "it is not, it is X". One table, one sentence, both directions reachable,
no sentinel (§5.49) — and no second definition of what counts as a transfer,
which §5.29 is the standing record of the cost of. This route decides nothing
of its own: the **two** functions it writes through —
:func:`~ledgerbox.db.repo.set_category_override` and
:func:`~ledgerbox.db.repo.clear_category_override` — have had tests and no
caller since M2, which is why §7 listed "the ability exists, the feature does
not" as a product gap rather than an implementation detail.

This paragraph said *four*, counting §5.49's whole family. An acceptance run
checked the M3 tree and found that only those two of the seven repository
functions this module calls were already there; the five on the reading side
were written for it. A sentence arguing that a milestone added no new logic is
a poor place to be approximately right.

Why that matters more than it sounds: the transfer rules claim **none** of the
13 real statements (§5.52). Marking by hand is not a convenience here, it is
the only path that does anything at all on this ledger today.

**Three status codes, and they mean different things to the browser.**

``404``
    no such transaction. Also the answer for a ``txn`` that is not a statement
    line — the opening entry has no identity row, so it is not something a
    person can look at or recategorise.
``422``
    no such category. Retrying with a different body works, which is what
    separates it from the 422 in :mod:`ledgerbox.api.routes.statements`, where
    the refusal is permanent.
``409``
    is deliberately not used. Nothing here is irreversible: an override can be
    withdrawn and the rules answer again, so asking a person to confirm would
    be ceremony with nothing behind it. Deletion earns its 409 by destroying
    something ``archive/`` cannot rebuild; this does not.

The read holds one snapshot across both of its queries — see
:func:`~ledgerbox.db.connection.read_transaction`. The write holds one
``ledger_rw`` handle across the lookup, the write and the read-back, so the row
returned is the row that exists rather than an echo of the request.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ...db import repo
from ...db.connection import read_transaction, transaction
from ...learning import apply_learned_rules
from ...money import format_minor
from ..dependencies import AppState, get_state, ledger_ro, ledger_rw
from ..schemas import (
    BulkCategoryOut,
    BulkCategoryPatch,
    CategoryOut,
    CategoryPatch,
    LargeFlowsOut,
    TransactionDirection,
    TransactionListOut,
    TransactionOut,
    TransactionSort,
    TransactionTotalsOut,
    TransactionUpdateOut,
)

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["transactions"])

StateDep = Annotated[AppState, Depends(get_state)]

#: An unknown category id. Written as a number for the reason
#: :mod:`ledgerbox.api.routes.statements` gives: Starlette's *name* for 422
#: changed between RFC 4918 and RFC 9110, the number did not.
UNKNOWN_CATEGORY = 422

#: A date range that cannot select anything. Same number, different sentence:
#: :data:`UNKNOWN_CATEGORY` is about the body of a write, this is about the
#: query string of a read.
UNUSABLE_RANGE = 422


def _transaction_out(row: sqlite3.Row) -> TransactionOut:
    """One row of ``v_transaction`` as the wire model.

    ``category_id`` may be ``None`` and is passed through as ``None``. There is
    no substitution here and there must not be one: a placeholder invented at
    this layer would make "no rule claimed this line" and "somebody chose this"
    arrive looking identical, which is the predecessor's best-hidden defect
    (``docs/STATUS.md`` §5.38) rebuilt one layer higher.
    """
    return TransactionOut(
        txn_id=row["txn_id"],
        posting_id=row["posting_id"],
        date=row["date"],
        statement_month=row["statement_month"],
        amount_minor=row["amount_minor"],
        currency=row["currency"],
        raw_descriptor=row["raw_descriptor"],
        occurrence_index=row["occurrence_index"],
        category_id=row["category_id"],
        category_decided_by=row["category_decided_by"],
        is_transfer=bool(row["is_transfer"]),
        transfer_decided_by=row["transfer_decided_by"],
        source_file_id=row["source_file_id"],
    )


def _list_summary(totals: dict[str, int], shown: int) -> str:
    """One sentence over the table. Display only, and it names its own leg.

    The clause about the figures at the top of the page is not padding. Those
    are measured on the income and expense legs with transfers and the opening
    entry excluded; these are measured on this account's own leg with
    everything in. Two cashflow figures that looked alike are what
    ``docs/STATUS.md`` §5.45 records costing this project a block-level check to
    settle, and a third one arrives here labelled rather than explained later.

    **This sentence has now been refuted twice, and the third version stopped
    describing filters.**

    It said "will not agree". Acceptance put the two responses side by side and
    found them identical to the cent — which they are out of the box, because
    every statement line contributes a bank leg to one figure and its income or
    expense counter-leg to the other, the opening entry is absent from both, and
    the rules flag no transfer on the author's corpus (§5.52).

    The replacement said the two "come out the same with no filter applied, and
    either condition separates them". Also false, in both directions: with
    nothing flagged, ``transfer=false`` selects every row and they stay equal,
    and so does a search for a single space; with one line flagged, that same
    ``transfer=false`` makes them equal again. **Whether a filter was typed was
    never the question — which rows it selects is.**

    So the wording states the row-set condition and nothing else, and
    ``tests/test_api.py`` pins the six cases. ``docs/STATUS.md`` §5.43 is the
    precedent: a sentence that keeps being refuted has stopped being a wording
    problem, and the answer there was the same one — state the guarantee, and
    let an assertion carry it.
    """
    matched = totals["matched"]
    if matched == 0:
        return "No transaction matches this filter."

    sentence = (
        f"{matched} transaction(s) match: {format_minor(totals['bank_in_minor'])} in and "
        f"{format_minor(totals['bank_out_minor'])} out, measured on this account's own leg — "
        f"what these lines did to the balance, transfers included. The In and Out at the top "
        f"of the page are a different measurement: the income and expense legs, with "
        f"transfers and the opening entry left out. The two line up only while this list "
        f"holds the lines those figures count."
    )
    if shown == 0:
        # Said rather than shown as an empty table under a count of 415.
        sentence += " This page is past the end of the result."
    return sentence


def _empty_page(query: repo.TransactionQuery) -> TransactionListOut:
    """The answer before anything has been ingested.

    Zeroes are the truth here, unlike ``/api/health``'s totals: "no rows matched
    this filter" is a measurement, while a balance of $0.00 on a ledger that has
    never been written to is a claim.
    """
    totals = {"matched": 0, "bank_in_minor": 0, "bank_out_minor": 0, "bank_net_minor": 0}
    return TransactionListOut(
        items=[],
        totals=TransactionTotalsOut(**totals),
        limit=query.limit,
        offset=query.offset,
        # Cast for the type checker only; pydantic still validates it against
        # the Literal, so a sort key that drifts from repo.SORT_KEYS fails here
        # rather than reaching the browser. Same device as statements.py.
        sort=cast(TransactionSort, query.sort),
        descending=query.descending,
        summary=_list_summary(totals, 0),
    )


#: $1,000 in minor units. Large money answered by anything other than a direct
#: human decision waits on the board until a person has looked at it once.
DEFAULT_LARGE_FLOW_THRESHOLD_MINOR = 100_000

#: The board is a confirmation queue, not a browser; a queue longer than this
#: means the threshold is set below what this ledger considers large.
MAX_LARGE_FLOW_ROWS = 200


@router.get("/large-flows")
def read_large_flows(
    state: StateDep,
    threshold_minor: Annotated[
        int,
        Query(
            ge=1,
            description="Minimum absolute amount, in minor units, to count as large.",
        ),
    ] = DEFAULT_LARGE_FLOW_THRESHOLD_MINOR,
) -> LargeFlowsOut:
    """Every large line whose current answer no person has directly confirmed.

    ``category_decided_by = 'override'`` is a person's own decision and is
    confirmed by definition. Everything else large -- shipped-rule answers,
    learned-rule answers, agent answers, and unclassified lines -- earns one
    human look, because at this size a wrong bucket moves every chart it
    touches. Confirming re-decides the same category through the normal
    override path, so it also teaches.
    """
    with ledger_ro(state) as conn:
        rows = conn.execute(
            "SELECT * FROM v_transaction "
            "WHERE ABS(amount_minor) >= ? AND category_decided_by != 'override' "
            "ORDER BY ABS(amount_minor) DESC, txn_id "
            "LIMIT ?",
            (threshold_minor, MAX_LARGE_FLOW_ROWS + 1),
        ).fetchall()
    truncated = len(rows) > MAX_LARGE_FLOW_ROWS
    shown = rows[:MAX_LARGE_FLOW_ROWS]
    return LargeFlowsOut(
        threshold_minor=threshold_minor,
        items=[_transaction_out(row) for row in shown],
        total_count=len(shown),
        truncated=truncated,
    )


@router.get("/transactions")
def read_transactions(
    state: StateDep,
    q: Annotated[
        str | None,
        Query(max_length=200, description="Substring of the bank's verbatim line."),
    ] = None,
    month: Annotated[
        str | None,
        Query(pattern=r"^\d{4}-\d{2}$", description="statement_month, from the period's end."),
    ] = None,
    category: Annotated[
        str | None,
        Query(description=f"A category id, or {repo.NO_CATEGORY!r} for lines nothing claimed."),
    ] = None,
    transfer: Annotated[bool | None, Query(description="The effective flag.")] = None,
    direction: Annotated[TransactionDirection | None, Query()] = None,
    since: Annotated[
        str | None,
        Query(pattern=r"^\d{4}-\d{2}-\d{2}$", description="Earliest transaction date, inclusive."),
    ] = None,
    until: Annotated[
        str | None,
        Query(pattern=r"^\d{4}-\d{2}-\d{2}$", description="Latest transaction date, inclusive."),
    ] = None,
    sort: Annotated[TransactionSort, Query()] = "date",
    descending: Annotated[bool, Query()] = True,
    limit: Annotated[int, Query(ge=1, le=repo.MAX_PAGE_SIZE)] = repo.DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TransactionListOut:
    """A page of statement lines, with what the whole filter matched.

    ``totals`` describes every matching row, not the page, so it does not change
    when somebody turns the page. Both come from one WHERE clause read inside
    one transaction, which is what stops a table and its own summary describing
    two different sets of rows.

    ``category_id`` and ``is_transfer`` are effective values — a person's
    decision folded over the rules' — and the two ``*_decided_by`` fields say
    which spoke. ``category_id: null`` means nothing claimed the line; on the
    author's own 13 statements that is currently 275 of 415, and it is stored and reported
    as null rather than swept into an "other" bucket.

    An absent database answers with an empty page rather than a 500, as
    ``/api/statements`` does: before the first ingest, "no database" and "no
    transactions" are the same fact from here.
    """
    # Every argument above is already constrained by FastAPI — the two that
    # reach SQL as text, `sort` and `direction`, are Literals — so the
    # ValueError TransactionQuery raises is unreachable from HTTP. It is the
    # guard for every other caller, which is why it lives there and not here.
    try:
        span = repo.DateSpan(since=since, until=until)
    except ValueError as bad:
        # The patterns above already refused a malformed date and a shape this
        # column never holds, so what is left is a real date that is not a real
        # day (`2025-13-01`) or an ordering somebody actually typed.
        raise HTTPException(UNUSABLE_RANGE, str(bad)) from bad

    query = repo.TransactionQuery(
        text=q,
        month=month,
        category=category,
        transfer=transfer,
        direction=direction,
        span=span,
        sort=sort,
        descending=descending,
        limit=limit,
        offset=offset,
    )

    if not state.paths.db.exists():
        return _empty_page(query)

    with ledger_ro(state) as conn, read_transaction(conn):
        rows = repo.list_transactions(conn, query)
        totals = repo.summarize_transactions(conn, query)

    return TransactionListOut(
        items=[_transaction_out(row) for row in rows],
        totals=TransactionTotalsOut(**totals),
        limit=query.limit,
        offset=query.offset,
        sort=sort,
        descending=query.descending,
        summary=_list_summary(totals, len(rows)),
    )


@router.get("/categories")
def read_categories(state: StateDep) -> list[CategoryOut]:
    """Every category a person can choose from, grouped by ``kind`` at the client.

    The ``category`` table mirrors the shipped rules file (``docs/STATUS.md``
    §5.37), so this is the taxonomy the ledger actually uses. The alternative —
    the page carrying its own copy of eighteen ids — is the two-definitions
    shape §5.29 exists to name, and the copy is always the one that goes stale.

    Nothing is filtered by sign. ``classify()`` will not let an expense rule
    claim a deposit because a derivation has no business guessing, but an
    override is a person overruling the derivation, and a refunded restaurant
    charge arrives as a deposit and really is dining.

    Empty before the first ingest: the rows are created when a statement is
    booked, not seeded by a migration.
    """
    if not state.paths.db.exists():
        return []

    with ledger_ro(state) as conn:
        rows = repo.list_categories(conn)

    return [
        CategoryOut(id=row["id"], kind=row["kind"], parent_id=row["parent_id"]) for row in rows
    ]


def _update_summary(
    before: sqlite3.Row, after: sqlite3.Row, *, category_id: str | None, changed: bool
) -> str:
    """What just happened to this line, including what it did to the totals.

    The transfer transition is stated because it is the only consequence that
    reaches beyond this row: a line becoming a transfer leaves the In and Out
    figures, and a line ceasing to be one rejoins them. It is stated as a
    direction and never as an amount — the amount belongs to the query that
    owns those figures (``repo.ledger_totals``), and prose arithmetic beside a
    real total is exactly how two numbers for one thing get into a page.
    """
    if category_id is None:
        sentence = (
            "Your decision was withdrawn; the rules answer for this line again."
            if changed
            else "There was no decision to withdraw — the rules already answered for this line."
        )
    else:
        sentence = (
            f"Recorded as {category_id}. Your decision stands in place of the rules'."
            if changed
            else f"Already recorded as {category_id}; nothing was changed."
        )

    was, now = bool(before["is_transfer"]), bool(after["is_transfer"])
    if now and not was:
        sentence += (
            " It counts as a transfer now, so it has left the In and Out figures at the top"
            " of the page."
        )
    elif was and not now:
        sentence += (
            " It no longer counts as a transfer, so it has returned to the In and Out figures"
            " at the top of the page."
        )
    return sentence


@router.patch(
    "/transactions/{txn_id}",
    summary="Record what a person says one transaction's category is",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No such transaction in this ledger"},
        UNKNOWN_CATEGORY: {"description": "No such category"},
    },
)
def update_transaction_category(
    txn_id: str, patch: CategoryPatch, state: StateDep
) -> TransactionUpdateOut:
    """Set or withdraw the category a person chose for one transaction.

    ``category_id: null`` withdraws the decision so the rules answer again. The
    field is required even for that, so an empty body cannot silently discard
    somebody's correction.

    Writes ``category_override`` and only that. It never touches
    ``posting.category_id`` or ``txn.is_transfer`` — those hold the *rules'*
    answer, and ``reapply-rules`` must be able to re-derive them without being
    able to lose a person's. The effective answer is composed by
    ``v_txn_category`` and ``v_txn_transfer`` and has exactly one definition
    each.

    An unknown ``txn_id`` is a 404 even though
    :func:`~ledgerbox.db.repo.clear_category_override` tolerates one. That
    tolerance is right where it is — "this transaction has no override" holds
    either way — but a URL naming a resource that does not exist is a 404 at
    this layer, and answering 200 to a mistyped id would report success for
    having done nothing to nothing.

    One handle spans the lookup, the write and the read-back, so the row
    returned cannot have moved in between.
    """
    with ledger_rw(state) as conn:
        before = repo.get_transaction(conn, txn_id)
        if before is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"no transaction {txn_id} in this ledger.",
            )

        if patch.category_id is not None and not repo.category_exists(conn, patch.category_id):
            # Asked here rather than left to the foreign key: an IntegrityError
            # does not say which of its two references failed, and a category
            # the rules file never mirrored is a different problem from a stale
            # transaction id.
            raise HTTPException(
                UNKNOWN_CATEGORY,
                f"no category {patch.category_id!r}. GET /api/categories lists the ones this "
                f"ledger knows; they come from the shipped rules file.",
            )

        with transaction(conn):
            if patch.category_id is None:
                changed = repo.clear_category_override(conn, txn_id=txn_id)
            else:
                changed = repo.set_category_override(
                    conn, txn_id=txn_id, category_id=patch.category_id
                )
                # The answer just taught claims its remaining twins now, so the
                # person is never asked the same merchant twice in one sitting.
                apply_learned_rules(conn)

        after = repo.get_transaction(conn, txn_id)
        if after is None:  # pragma: no cover - read back under the same write lock
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"transaction {txn_id} disappeared while it was being updated.",
            )

    return TransactionUpdateOut(
        changed=changed,
        transaction=_transaction_out(after),
        summary=_update_summary(before, after, category_id=patch.category_id, changed=changed),
    )


def _bulk_summary(
    result: repo.BulkOverrideResult, *, requested: int, category_id: str | None
) -> str:
    """One sentence for the toolbar, and it leads with what was lost.

    The order is deliberate. ``changed`` is the good news and ``replaced`` is
    the part that cannot be undone by doing this again with a different answer,
    so the sentence does not bury it behind a total — the same shape the
    deletion prompt uses for the two things it destroys rather than removes.
    """
    what = "the rules' answer" if category_id is None else category_id
    parts = [
        f"{result.changed} of {requested} line(s) now read {what}."
        if category_id is not None
        else f"{result.changed} of {requested} decision(s) withdrawn; the rules answer again."
    ]
    if result.unchanged:
        parts.append(f"{result.unchanged} already did.")
    if result.replaced:
        parts.append(
            f"{result.replaced} of them carried a different category you had set by hand, and "
            f"that decision is gone — archive/ holds documents, not what you decided about them."
        )
    if result.transfer_added:
        parts.append(
            f"{result.transfer_added} line(s) count as transfers now, so they have left the In "
            f"and Out figures at the top of the page."
        )
    if result.transfer_removed:
        parts.append(
            f"{result.transfer_removed} line(s) no longer count as transfers and have returned "
            f"to those figures."
        )
    return " ".join(parts)


@router.post(
    "/transactions/category",
    summary="Record one category decision about many transactions",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "One or more ids are not in this ledger"},
        UNKNOWN_CATEGORY: {"description": "No such category"},
    },
)
def update_many_categories(patch: BulkCategoryPatch, state: StateDep) -> BulkCategoryOut:
    """Say one thing about a list of transactions, in a single transaction.

    The rules claim none of the author's 415 real lines and 86.9% of the
    unclaimed spending is money moving between his own accounts, so the only
    thing that makes the breakdown mean anything is marking those by hand — 79
    rows, and until now one click each.

    **Nothing new decides anything here.** Every write goes through the same
    :func:`~ledgerbox.db.repo.set_category_override` and
    :func:`~ledgerbox.db.repo.clear_category_override` the single-row ``PATCH``
    calls, so marking eighty lines cannot come to mean something subtly
    different from marking one — which matters most for the word this exists
    for, since naming the ``transfer`` category is how a person says "transfer"
    and a second definition of that is what §5.29 records the cost of.

    **An unknown id refuses the whole request.** Not because a partial write is
    hard, but because a caller holding a stale id is holding a stale *list*: it
    read those ids out of a query, and if some have since gone the answer is to
    read again, not to write the part that still resolves and report the rest as
    a footnote. All of it lands or none of it does.

    ``422`` for an unknown category, exactly as the single-row write does, and
    for the same reason it is asked here rather than left to the foreign key.

    **No 409.** Deletion earns one by destroying what ``archive/`` cannot
    rebuild; this is reversible by sending a different answer. The one part that
    is *not* — replacing a category somebody set by hand — is reported in
    ``replaced`` and is on screen before the click, because every row already
    carries ``category_decided_by``.
    """
    with ledger_rw(state) as conn:
        if patch.category_id is not None and not repo.category_exists(conn, patch.category_id):
            raise HTTPException(
                UNKNOWN_CATEGORY,
                f"no category {patch.category_id!r}. GET /api/categories lists the ones this "
                f"ledger knows; they come from the shipped rules file.",
            )

        # Checked before anything is written, so the refusal below cannot leave
        # a half-applied decision behind. One connection spans the check and the
        # write, so nothing can vanish in between.
        missing = [txn_id for txn_id in patch.txn_ids if repo.get_transaction(conn, txn_id) is None]
        if missing:
            shown = ", ".join(missing[:5])
            more = f" and {len(missing) - 5} more" if len(missing) > 5 else ""
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"{len(missing)} of {len(patch.txn_ids)} transaction(s) are not in this ledger "
                f"({shown}{more}). Nothing was written — re-read the table and try again.",
            )

        with transaction(conn):
            result = repo.set_category_overrides(
                conn, txn_ids=patch.txn_ids, category_id=patch.category_id
            )
            if patch.category_id is not None:
                apply_learned_rules(conn)

    return BulkCategoryOut(
        requested=len(patch.txn_ids),
        changed=result.changed,
        unchanged=result.unchanged,
        replaced=result.replaced,
        transfer_added=result.transfer_added,
        transfer_removed=result.transfer_removed,
        summary=_bulk_summary(result, requested=len(patch.txn_ids), category_id=patch.category_id),
    )
