# SPDX-License-Identifier: AGPL-3.0-or-later
"""Both of M5's charts, over HTTP: ``GET /api/analytics``.

**One endpoint, not two, and one snapshot behind it.** The two queries run
inside a single deferred read transaction — the device
:func:`~ledgerbox.db.connection.read_transaction` exists for and the same one
:func:`ledgerbox.api.routes.transactions.read_transactions` uses to keep a page
of rows and its own totals from describing different states of the ledger. Two
requests would be two snapshots, and a writer is free to commit between them:
bars and wedges drawn side by side, each captioned *this ledger*, must not be
able to describe two different ones. ``docs/EXECUTION_PLAN.md`` §6 sketched two
endpoints; this is the one place that plan is deliberately not followed.

**No database file is answered, not raised.** Empty lists and zero sums, as
:func:`ledgerbox.api.routes.transactions._empty_page` and
:func:`ledgerbox.api.routes.statements.read_statements` answer: before the first
ingest, "no database" and "nothing booked" are the same fact from here, and
``/api/health`` is the endpoint whose job it is to tell them apart.

The zeroes are truthful for the same reason ``/api/health``'s ``totals`` is
``null`` rather than zeroed. A sum over a list this response also carries is a
**measurement** of that list: no slices, therefore nothing spent, and the reader
can see the empty list the figure was taken over. :attr:`HealthOut.totals`
carries a ``balance_minor``, which is a **claim about money** that exists
whether or not anything has been booked — printing $0.00 for it would be
answering a question nobody has the data for. Same shape as
:func:`ledgerbox.api.routes.statements._totals`.

**Nothing here is prose, and that is the deliberate part.** Every other body in
this API carries a ``summary`` sentence for the top of its panel; this one
carries numbers and category ids and stops. ``docs/STATUS.md`` §5.69 is why: the
sentence over the transaction table asserted a relationship between two figures,
was published in five places at once — including, through
:mod:`ledgerbox.api.schemas`, the OpenAPI *description* served to every client —
and was **refuted by two consecutive acceptance rounds**. The conclusion drawn
there, following §5.43, was to state the guarantee and let an assertion carry
it. A caption under a chart is the same trap with a better view: the one
relationship worth stating here — that ``categories.total_minor`` *is* the
``totals.outflow_minor`` **of this same response** — is asserted by ``verify``'s
``cashflow_agreement`` check on the operator's own ledger and by
``tests/test_api.py`` over HTTP, which is worth more than a sentence saying so.

That used to say ``/api/health``'s ``outflow_minor``, and it was true until M6
gave this endpoint a date range: the breakdown narrows with the window and
``/api/health`` deliberately does not, so the two are equal for the unscoped
window and for no other. The equality that survives every window is the one
inside a single response, which is also the only one a client can check without
issuing a second request.

Read-only handle, and no migration: the schema is brought up to date once in
:func:`ledgerbox.api.app.create_app`, and a ``mode=ro`` handle with ``PRAGMA
query_only`` could not apply one anyway.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ...db import repo
from ...db.connection import read_transaction
from ..dependencies import AppState, get_state, ledger_ro
from ..schemas import (
    AnalyticsOut,
    CashflowMonthOut,
    CategoryBreakdownOut,
    CategorySliceOut,
    DateSpanOut,
    MonthlyCashflowOut,
    TotalsOut,
)

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["analytics"])

StateDep = Annotated[AppState, Depends(get_state)]

#: A range that cannot select anything. Written as a number for the reason the
#: neighbouring routes give: Starlette's *name* for 422 changed between RFC 4918
#: and RFC 9110, the number did not.
UNUSABLE_RANGE = 422


def _monthly_out(monthly: repo.MonthlyCashflow) -> MonthlyCashflowOut:
    """``v_cashflow_monthly`` as the wire model, oldest first.

    The four sums are the repository's, summed from the rows beside them rather
    than recomputed here. Re-summing at this layer would be a second answer to a
    question that already has one, which is the shape ``docs/STATUS.md`` §5.29
    is the standing record of the cost of — and the bars and the figure under
    them would then be two chances to be right.
    """
    return MonthlyCashflowOut(
        months=[
            CashflowMonthOut(
                month=month.month,
                inflow_minor=month.inflow_minor,
                outflow_minor=month.outflow_minor,
                net_minor=month.net_minor,
                txn_count=month.txn_count,
            )
            for month in monthly.months
        ],
        inflow_minor=monthly.inflow_minor,
        outflow_minor=monthly.outflow_minor,
        net_minor=monthly.net_minor,
        txn_count=monthly.txn_count,
    )


def _categories_out(breakdown: repo.CategoryBreakdown) -> CategoryBreakdownOut:
    """``v_category_spend`` as the wire model, largest spend first.

    ``category_id`` is passed through as ``None`` when nothing claimed those
    lines. No substitution, no rename, no drop — the same refusal
    :func:`ledgerbox.api.routes.transactions._transaction_out` makes for a
    single row, and for a sharper reason here: an unclaimed slice folded into a
    bucket called "other" is indistinguishable *in a chart* from one that was
    matched on purpose, which is the predecessor's best-hidden defect
    (``docs/STATUS.md`` §5.38) and the one that made a wrong breakdown render
    perfectly.
    """
    return CategoryBreakdownOut(
        slices=[
            CategorySliceOut(
                category_id=part.category_id,
                spend_minor=part.spend_minor,
                txn_count=part.txn_count,
            )
            for part in breakdown.slices
        ],
        total_minor=breakdown.total_minor,
        txn_count=breakdown.txn_count,
    )


def _empty(span: repo.DateSpan) -> AnalyticsOut:
    """The answer before anything has been ingested.

    Zeroes over empty lists, for the reason this module's docstring gives: each
    figure here is a sum of a list the same response carries, so a reader can
    see what was measured. ``totals`` is ``None`` rather than zeroed, because it
    carries a balance, and $0.00 there would be a claim about money instead of
    the absence of any.
    """
    return AnalyticsOut(
        span=DateSpanOut(since=span.since, until=span.until),
        totals=None,
        monthly=MonthlyCashflowOut(
            months=[], inflow_minor=0, outflow_minor=0, net_minor=0, txn_count=0
        ),
        categories=CategoryBreakdownOut(slices=[], total_minor=0, txn_count=0),
    )


@router.get(
    "/analytics",
    summary="The figures and both charts, from one read of the ledger",
    responses={
        UNUSABLE_RANGE: {
            "description": (
                "The range cannot select anything: since is after until, or one of them "
                "matches the date pattern without being a real day (2025-13-01)."
            )
        }
    },
)
def read_analytics(
    state: StateDep,
    since: Annotated[
        str | None,
        Query(pattern=r"^\d{4}-\d{2}-\d{2}$", description="Earliest transaction date, inclusive."),
    ] = None,
    until: Annotated[
        str | None,
        Query(pattern=r"^\d{4}-\d{2}-\d{2}$", description="Latest transaction date, inclusive."),
    ] = None,
) -> AnalyticsOut:
    """The four figures, monthly cashflow and the category breakdown, together.

    ``since`` and ``until`` bound **transaction dates**, inclusive at both ends.
    They narrow all three, which is the point: the headline and its two
    decompositions must describe one window or none of them can be checked
    against the others. Omitting both reads the whole ledger.

    A reversed range is a ``422`` rather than an empty result. "No rows matched"
    would be true of it and useless: nothing a person can type into a date
    control should silently mean "nothing".

    ``monthly.months`` is oldest first, which is the direction a time axis
    reads, and its ``month`` is the month of the **transaction date** — the same
    column ``since`` and ``until`` bound, so the bars and the window they are
    drawn for cannot mean two different things. It is **not**
    ``statement_month``, which asks which statement a line is printed on and is
    still what ``/api/statements`` and the transaction table's month control
    mean. Both questions exist here and both are labelled; the predecessor had
    both, labelled neither, and 83 of its 415 rows fell in different months
    depending on which chart was asking.

    ``categories.slices`` is largest spend first, ties broken by category id so
    that a legend does not reshuffle between two loads of the same data.
    ``spend_minor`` is negative, in the same sign convention as
    ``totals.outflow_minor`` — and ``categories.total_minor`` equals that figure
    exactly, **for whatever window was asked for**. Likewise the months sum to
    ``totals``. Both hold because all three are sums of the same rows under the
    same bound (``v_cashflow_line``, migration 0008) rather than because
    separately written queries were observed to agree; ``verify``'s
    ``cashflow_agreement`` check asserts the unscoped case against an
    independently built view on the operator's own ledger.

    A slice whose ``category_id`` is ``null`` is a slice like any other: no rule
    claimed those lines and nobody has overruled that. There is no
    ``uncategorized`` category in this ledger to fall into, and a client that
    draws it as "other" has rebuilt the defect described in
    :class:`~ledgerbox.api.schemas.CategorySliceOut`.

    Both figures come from one deferred read transaction on one connection. An
    absent database answers with empty lists and zero sums rather than a 500,
    as ``/api/transactions`` and ``/api/statements`` do.
    """
    try:
        span = repo.DateSpan(since=since, until=until)
    except ValueError as bad:
        # The patterns above already refused a malformed date and a shape this
        # column never holds, so what is left is a real date that is not a real
        # day (`2025-13-01` matches `\d{4}-\d{2}-\d{2}` and is not a day) or an
        # ordering somebody actually typed. The earlier version of this comment
        # named only the second and was wrong about the first, which is also why
        # the `responses` entry above described only half of what it answers.
        raise HTTPException(UNUSABLE_RANGE, str(bad)) from bad

    if not state.paths.db.exists():
        return _empty(span)

    with ledger_ro(state) as conn, read_transaction(conn):
        booked = repo.row_counts(conn).get("txn", 0) > 0
        totals = repo.ledger_totals(conn, span) if booked else None
        monthly = repo.monthly_cashflow(conn, span)
        breakdown = repo.category_spend(conn, span)

    return AnalyticsOut(
        span=DateSpanOut(since=span.since, until=span.until),
        totals=TotalsOut(**totals) if totals is not None else None,
        monthly=_monthly_out(monthly),
        categories=_categories_out(breakdown),
    )
