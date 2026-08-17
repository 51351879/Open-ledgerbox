# SPDX-License-Identifier: AGPL-3.0-or-later
"""Statements over HTTP: list them, measure a deletion, perform one.

``GET /api/statements`` was written in :mod:`ledgerbox.api.routes.health` when it
was the only thing this resource could do. It is here now, unchanged, because a
statement is one resource and a reader looking for what the browser can do to
one should find all of it in one file — the same reasoning ``docs/STATUS.md``
§5.29 records the archive paying for twice.

The two new endpoints are the direction that did not exist: a statement uploaded
by mistake, or archived and refused, could not be removed at all, and ``verify``
stayed red on ``unbooked_statements`` for as long as the file existed.
:mod:`ledgerbox.ingest.forget` is the whole of that work; this module is HTTP and
nothing else. It parses no PDF, counts no row and decides no refusal of its own.

**The plan is a POST and that is not a mistake.** It writes nothing that
survives — but the way it knows what a deletion would do is to *perform* one
inside a transaction, run ``verify`` against the result and roll back, which is
the only way the forecast and the act can be the same code. That needs a
writable handle: :func:`~ledgerbox.api.dependencies.ledger_ro` opens the file
``mode=ro`` with ``PRAGMA query_only``, and a connection like that cannot open a
transaction, let alone the ``BEGIN IMMEDIATE`` this takes. The endpoint is
therefore a POST — safe by contract, unsafe by method — and this paragraph
exists because a reviewer who sees ``POST`` on a read is right to ask.

**Three refusals, three status codes, because the browser branches on them.**

``404``
    no statement with that id. Nothing else has been considered.
``422``
    the deletion is refused — an overlapping period, or a transaction elsewhere
    superseded by one in here. Sending it again will get the same answer, so the
    page must not put a "do it anyway" button under it.
``409``
    the impact has not been acknowledged. This one *is* the server asking the
    question again, and the sentence it comes with names what would be lost.

Both write endpoints hold one :func:`~ledgerbox.api.dependencies.ledger_rw`
handle across the read and the act, exactly as
:mod:`ledgerbox.api.routes.review` does: measuring the deletion on one
connection and performing it on another would put a check-then-act race between
the rules that approved it and the delete they approved.

The id in the path is a **full** ``source_file_id``, never a prefix. The page
already has it from ``GET /api/statements``, and
:func:`ledgerbox.db.repo.find_statement` — which does resolve prefixes and which
the CLI uses — reports an ambiguous one as its own kind of failure. There is no
status code left here to spend on that: 409 already means "confirm this", and
answering an ambiguous prefix with it would put two unrelated meanings behind
one number the browser branches on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, status

from ...db import repo
from ...ingest.forget import ForgetPlan, ForgetRefused, ForgetResult, forget_statement, plan_forget
from ...reconcile.checks import FAIL, SKIP, CheckResult
from ..dependencies import AppState, get_state, ledger_ro, ledger_rw
from ..schemas import (
    CheckOut,
    CheckStatus,
    DeletionImpactOut,
    DeletionPlanOut,
    DeletionResultOut,
    Severity,
    StatementOut,
    TotalsOut,
)

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["statements"])

StateDep = Annotated[AppState, Depends(get_state)]

#: Written out rather than taken from ``fastapi.status``, whose name for it
#: changed: ``HTTP_422_UNPROCESSABLE_ENTITY`` (RFC 4918) became
#: ``HTTP_422_UNPROCESSABLE_CONTENT`` (RFC 9110), so either spelling ties this
#: module to a version range of Starlette. The number is the part the RFCs kept
#: — the same reason ``upload.py`` writes 413 out.
REFUSED = 422


def _label(statement_month: str | None, source_file_id: str) -> str:
    """How to name one statement in a sentence a person reads.

    A refused statement has no month — nothing got far enough to read its
    period — and calling it ``None`` in the confirmation prompt would be worse
    than saying which file it is.
    """
    return statement_month or f"statement {source_file_id[:12]}…"


def _impact(source: repo.DeletionFacts | repo.DeletionCounts) -> DeletionImpactOut:
    """The row counts as the wire model, from either side of the deletion.

    One function, both paths. Six of the eight fields are the same number under
    the same name on a :class:`~ledgerbox.db.repo.DeletionFacts` (counted
    before) and a :class:`~ledgerbox.db.repo.DeletionCounts` (counted after),
    and the two that are not are exactly the interesting ones: the plan knows
    how many assertions this file *owns* and how many of those a surviving
    statement also prints, while the result knows how many were removed and how
    many had their provenance moved. Deriving one from the other in two places
    is how the number a person confirmed and the number they were then shown get
    to disagree.
    """
    if isinstance(source, repo.DeletionFacts):
        reassigned = source.balance_assertions_shared
        removed = source.balance_assertions - source.balance_assertions_shared
    else:
        reassigned = source.balance_assertions_reassigned
        removed = source.balance_assertions_removed
    return DeletionImpactOut(
        txns=source.txns,
        postings=source.postings,
        txn_identities=source.identities,
        raw_records=source.raw_records,
        review_items=source.review_items,
        review_items_decided=source.review_items_decided,
        category_overrides=source.category_overrides,
        agent_proposals=source.agent_proposals,
        agent_proposal_runs=source.agent_proposal_runs,
        agent_triage_items=source.agent_triage_items,
        agent_triage_runs=source.agent_triage_runs,
        balance_assertions_removed=removed,
        balance_assertions_reassigned=reassigned,
    )


def _checks(results: Sequence[CheckResult]) -> list[CheckOut]:
    """Check results as the wire model, passes and skips included.

    ``severity`` and ``status`` are cast for the type checker only; pydantic
    still validates them against the Literals in :mod:`ledgerbox.api.schemas`,
    so a vocabulary that drifts fails loudly here rather than reaching the
    browser as an unstyled badge.
    """
    return [
        CheckOut(
            check_id=result.check_id,
            severity=cast(Severity, result.severity),
            status=cast(CheckStatus, result.status),
            message=result.message,
            detail=result.detail,
        )
        for result in results
    ]


def _totals(measured: repo.LedgerTotals | None) -> TotalsOut | None:
    """Totals, or None when none were measured.

    ``None`` is what :class:`~ledgerbox.ingest.forget.ForgetPlan` carries for a
    refused deletion — nothing was simulated. Zeroes there would render as a
    real balance of $0.00 rather than as the absence of a measurement, the same
    distinction ``/api/health`` keeps.

    Note the two nulls in play and that they are different facts. This one says
    no measurement was taken. ``TotalsOut.balance_minor`` being null inside a
    measurement that *was* taken says the ledger holds no evidence of a balance
    — which is what a deletion of the last statement produces.
    """
    return TotalsOut(**measured) if measured else None


def _check_clause(results: Sequence[CheckResult]) -> str:
    """How a set of checks came out, in words that never round up.

    "All passed" is the sentence ``docs/STATUS.md`` §5.19 is about: a check that
    could not run has established nothing, and a summary that folds it into the
    good news is the one line everybody reads being the one line that is wrong.
    So the only clause that says "all" is the one where every result is a pass,
    and failures and skips are named by id rather than counted.
    """
    total = len(results)
    if total == 0:
        return "none were measured"
    failed = [result.check_id for result in results if result.status == FAIL]
    skipped = [result.check_id for result in results if result.status == SKIP]
    if not failed and not skipped:
        return "all pass"

    clauses = [f"{total - len(failed) - len(skipped)} pass"]
    if failed:
        clauses.append(f"{len(failed)} fail ({', '.join(failed)})")
    if skipped:
        clauses.append(f"{len(skipped)} could not run ({', '.join(skipped)})")
    return ", ".join(clauses)


def _plan_summary(plan: ForgetPlan) -> str:
    """One display-only sentence for the confirmation prompt."""
    label = _label(plan.facts.statement_month, plan.source_file_id)
    if not plan.allowed:
        return (
            f"{label} cannot be deleted; {len(plan.refusals)} reason(s), and asking "
            f"again will not change any of them."
        )
    return (
        f"Deleting {label} would remove {plan.facts.txns} transaction(s); of the "
        f"{len(plan.checks_after)} checks that can be measured before the file is "
        f"removed, {_check_clause(plan.checks_after)}."
    )


def _result_summary(result: ForgetResult) -> str:
    """One display-only sentence for what just happened."""
    label = _label(result.statement_month, result.source_file_id)
    sentence = (
        f"Deleted {label}: {result.counts.txns} transaction(s) removed; of the "
        f"{len(result.checks_after)} checks run afterwards, "
        f"{_check_clause(result.checks_after)}."
    )
    if result.unremoved_files:
        # Not a footnote. The ledger rows are gone and these bytes are not.
        # Deliberately does not name a check: this used to promise
        # `archived_not_recorded`, which walks archive/ only and is therefore
        # silent about a stranded extraction cache — the file that holds the
        # whole text layer. `ledgerbox doctor` reports both.
        sentence += (
            f" {len(result.unremoved_files)} file(s) could not be removed and are "
            f"still on disk; `ledgerbox doctor` reports them until they are gone."
        )
    return sentence


def _refusal_prose(plan: ForgetPlan) -> str:
    """The refusals as one string, for a 422 that is not a confirmation prompt.

    Each reason is already a sentence written to be read —
    :func:`ledgerbox.ingest.forget._refusals` names the other statement, its
    period and what would go wrong — so this joins them rather than rewriting
    them. The closing line is the part the browser needs in words: there is no
    flag that makes this succeed.
    """
    label = _label(plan.facts.statement_month, plan.source_file_id)
    return (
        f"{label} cannot be deleted. "
        + " ".join(plan.refusals)
        + " Nothing has been changed. Sending this again will get the same answer — "
        "this is a refusal, not a confirmation prompt."
    )


def _impact_refusal(plan: ForgetPlan) -> str:
    """The 409: the server asking the question again, with the numbers in it.

    Shaped after :data:`ledgerbox.api.routes.review.BLOCK_DISMISS_REFUSAL`, and
    for the same reason (``docs/STATUS.md`` §5.13): accepting a hole in your own
    ledger should be typed out rather than clicked past, so the sentence has to
    be worth reading rather than a label on a second button.

    Two counts are named separately from everything else, because they are the
    two a rebuild does not return: a category somebody set by hand, and a queue
    item somebody resolved or dismissed. Every other row here comes from bytes a
    re-ingest replays; those two are decisions, and ``archive/`` holds documents
    rather than decisions — a re-ingest brings the queue item back as ``open``,
    never as the answer it was given. Rolling either into one "rows affected"
    figure would hide the irreversible part inside the reversible ones.

    This sentence named only the categories for a milestone (``docs/STATUS.md``
    §5.49), and an acceptance run refuted it by dismissing an item through this
    very API and watching it come back open (§5.65).

    Like the review queue's refusal, this deliberately does not name the field
    that has to come back. It is shown to a person in a browser with a button in
    front of them, and telling them to send ``acknowledge_impact=true`` is asking
    them to read an API they are not using. The parameter is in the OpenAPI
    document, where a client author looks.
    """
    facts = plan.facts
    label = _label(facts.statement_month, plan.source_file_id)
    assertions = facts.balance_assertions - facts.balance_assertions_shared

    # Named from what is actually there. Promising to delete an extraction cache
    # that was never written — a statement refused before it could be read has
    # none — would be this sentence claiming more than the measurement supports,
    # in the one place it is being read for exactly that.
    on_disk = [
        name
        for name, present in (
            ("the archived PDF", plan.archive_path is not None),
            ("its extraction cache", plan.extracted_path is not None),
        )
        if present
    ]
    files = f", and removes {' and '.join(on_disk)} from disk" if on_disk else ""

    parts = [
        f"Deleting {label} takes {facts.txns} transaction(s), {facts.postings} posting(s) "
        f"and {assertions} balance assertion(s) out of the ledger{files}."
    ]
    # The irreversible kinds, named one by one and only when there are any.
    # A sentence listing what is *not* attached on every deletion is a sentence
    # nobody reads on the deletion where something is.
    lost = []
    if facts.category_overrides:
        lost.append(f"{facts.category_overrides} category(ies) you set by hand")
    if facts.review_items_decided:
        lost.append(
            f"{facts.review_items_decided} review item(s) you had already resolved or "
            f"dismissed"
        )
    if facts.agent_proposals:
        run_text = (
            f" across {facts.agent_proposal_runs} proposal run(s) that become empty"
            if facts.agent_proposal_runs
            else ""
        )
        lost.append(f"{facts.agent_proposals} Agent proposal outcome(s){run_text}")
    if facts.agent_triage_items:
        run_text = (
            f" across {facts.agent_triage_runs} triage run(s) that become empty"
            if facts.agent_triage_runs
            else ""
        )
        lost.append(f"{facts.agent_triage_items} Agent triage outcome(s){run_text}")
    if lost:
        parts.append(
            f"It also destroys {' and '.join(lost)}. Those are decisions rather than "
            f"documents: archive/ never held them, so re-ingesting this file would bring "
            f"the transactions back and not them."
        )
    else:
        parts.append(
            "Nothing here is a decision or review history — no hand-set category, Agent "
            "proposal or triage outcome, resolved or dismissed review item — so "
            "re-ingesting the same file "
            "would restore all of it."
        )
    if plan.failing_after:
        parts.append(
            f"Afterwards {len(plan.failing_after)} check(s) would fail: "
            f"{', '.join(result.check_id for result in plan.failing_after)}. That is the "
            f"ledger reporting a real hole, not a glitch to be dismissed."
        )
    parts.append("To accept that, confirm the deletion.")
    return " ".join(parts)


def _plan_out(plan: ForgetPlan) -> DeletionPlanOut:
    return DeletionPlanOut(
        source_file_id=plan.source_file_id,
        statement_month=plan.facts.statement_month,
        period_start=plan.facts.period_start,
        period_end=plan.facts.period_end,
        allowed=plan.allowed,
        refusals=list(plan.refusals),
        impact=_impact(plan.facts),
        checks_after=_checks(plan.checks_after),
        checks_note=plan.checks_note,
        totals_before=_totals(plan.totals_before),
        totals_after=_totals(plan.totals_after),
        archive_file_present=plan.archive_path is not None,
        extracted_file_present=plan.extracted_path is not None,
        summary=_plan_summary(plan),
    )


@router.get("/statements")
def read_statements(state: StateDep) -> list[StatementOut]:
    """Every archived statement, newest period first.

    ``txn_count`` is the column worth reading: zero means the file is in
    ``archive/`` and its transactions are not in the ledger. ``open_block`` and
    ``open_warn`` are that statement's own open queue depth, so the row can say
    why it is at zero without a request per statement.

    An absent database answers with an empty list rather than a 500. Before
    anything has been ingested "no database" and "no statements" are the same
    fact from here, and ``/api/health`` is where the difference is reported.
    """
    if not state.paths.db.exists():
        return []

    with ledger_ro(state) as conn:
        rows = repo.list_statements(conn)

    return [
        StatementOut(
            source_file_id=row["source_file_id"],
            institution=row["institution"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            statement_month=row["statement_month"],
            byte_len=row["byte_len"],
            ingested_at=row["ingested_at"],
            txn_count=row["txn_count"],
            open_block=row["open_block"],
            open_warn=row["open_warn"],
        )
        for row in rows
    ]


@router.post(
    "/statements/{source_file_id}/deletion-plan",
    summary="Measure what deleting one statement would do",
    responses={status.HTTP_404_NOT_FOUND: {"description": "No statement with that id"}},
)
def plan_statement_deletion(source_file_id: str, state: StateDep) -> DeletionPlanOut:
    """What deleting this statement would cost, measured. Changes nothing.

    **A POST that is safe.** The measurement is a real deletion inside a
    transaction that is rolled back — see this module's docstring — and that
    needs the writable handle a GET is not allowed to hold: ``ledger_ro`` opens
    the database ``mode=ro`` with ``PRAGMA query_only`` set, and such a handle
    cannot open a transaction at all. The method is what the implementation
    requires, not what the semantics deserve.

    Every field is an observation. ``checks_after`` are the checks' real answers
    against the real result, six of the nine; ``checks_note`` says which three
    are missing and why, and it is meant to be shown rather than only read here.
    """
    with ledger_rw(state) as conn:
        try:
            plan = plan_forget(conn, state.paths, source_file_id)
        except repo.StatementNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return _plan_out(plan)


@router.delete(
    "/statements/{source_file_id}",
    summary="Delete one statement, its rows and its archived file",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "No statement with that id"},
        status.HTTP_409_CONFLICT: {"description": "The impact has not been acknowledged"},
        REFUSED: {"description": "This deletion is refused and will stay refused"},
    },
)
def delete_statement(
    source_file_id: str,
    state: StateDep,
    acknowledge_impact: bool = False,
) -> DeletionResultOut:
    """Remove one statement from the ledger, the archive and the extraction cache.

    The rules run in this order and the order is what makes the answers useful:
    an unknown id is a 404 before anything is measured, a refused deletion is a
    422 that no flag will turn into a 200, and only then is the acknowledgement
    asked for. A client that offers "do it anyway" after a 422 is offering a
    button that cannot work.

    ``acknowledge_impact`` defaults to false, so the first call from anywhere is
    the 409 that says what would be lost. The flag is deliberately a query
    parameter and not a body: ``DELETE`` with a body is inconsistently handled by
    intermediaries, and this is one boolean.

    The plan and the deletion run on one handle under one write lock. Measuring
    on a second connection would leave a window in which the ledger changes
    between the sentence the operator agreed to and the rows that go.
    """
    with ledger_rw(state) as conn:
        try:
            plan = plan_forget(conn, state.paths, source_file_id)
        except repo.StatementNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

        if not plan.allowed:
            raise HTTPException(REFUSED, _refusal_prose(plan))
        if not acknowledge_impact:
            raise HTTPException(status.HTTP_409_CONFLICT, _impact_refusal(plan))

        try:
            result = forget_statement(conn, state.paths, source_file_id)
        except ForgetRefused as exc:  # pragma: no cover - the lock is held across both
            # Unreachable while one handle covers the plan and the act, which is
            # exactly why it is caught rather than trusted: if that ever stops
            # being true, the answer has to stay the 422 it would have been.
            raise HTTPException(REFUSED, "\n".join(exc.reasons)) from exc

        # Read the totals back through the same connection, after the deletion,
        # rather than deriving them from the plan: the plan's `totals_after` was
        # measured inside a transaction that was rolled back, and reporting a
        # forecast as a result is the small lie this whole module is shaped to
        # avoid. None on an empty ledger, as `/api/health` does — zeroes there
        # would render as a real balance of $0.00.
        booked = repo.row_counts(conn).get("txn", 0)
        totals = TotalsOut(**repo.ledger_totals(conn)) if booked > 0 else None

    return DeletionResultOut(
        source_file_id=result.source_file_id,
        statement_month=result.statement_month,
        removed=_impact(result.counts),
        removed_files=[str(path) for path in result.removed_files],
        unremoved_files=[[str(path), reason] for path, reason in result.unremoved_files],
        checks_after=_checks(result.checks_after),
        totals=totals,
        summary=_result_summary(result),
    )
