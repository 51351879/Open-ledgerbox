# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP adapter for remaining-coverage triage audit and human review."""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from ...db import repo
from ...db.connection import read_transaction
from ...proposals import Producer
from ...triage import (
    TriageConflict,
    TriageGroup,
    TriageLedgerNotReady,
    TriageNotFound,
    TriageRun,
    TriageScope,
    TriageScopeIncomplete,
    TriageSubmission,
    dismiss_run,
    get_run,
    list_runs,
    review_triage,
    submit_triage,
    withdraw_run,
)
from ..dependencies import AppState, get_state, ledger_ro, ledger_rw
from ..schemas import (
    HASH_ID_PATTERN,
    AgentProducerIn,
    AgentTriageItemOut,
    AgentTriageReviewIn,
    AgentTriageReviewOut,
    AgentTriageRouteSummaryOut,
    AgentTriageRunOut,
    AgentTriageRunSummaryOut,
    AgentTriageScopeIn,
    AgentTriageSubmitIn,
    AgentTriageSubmitOut,
    AgentTriageWithdrawOut,
    AgentTriageWithdrawSelectedIn,
    TransactionOut,
)

router = APIRouter(prefix="/api/agent-triage", tags=["agent triage"])
StateDep = Annotated[AppState, Depends(get_state)]
RunId = Annotated[str, Path(pattern=HASH_ID_PATTERN)]


def _conflict(error: TriageConflict) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


def _not_found(error: TriageNotFound) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(error))


def _producer(producer: Producer) -> AgentProducerIn:
    return AgentProducerIn(
        client=cast(Literal["codex", "claude-code", "other"], producer.client),
        client_version=producer.client_version,
        model_reported=producer.model_reported,
    )


def _scope(scope: TriageScope) -> AgentTriageScopeIn:
    return AgentTriageScopeIn(since=scope.since, until=scope.until)


def _run_out(
    run: TriageRun, current: dict[str, TransactionOut | None]
) -> AgentTriageRunOut:
    summary: dict[str, dict[str, int]] = defaultdict(
        lambda: {"item_count": 0, "pending": 0, "bank_amount_minor": 0}
    )
    items: list[AgentTriageItemOut] = []
    for item in run.items:
        transaction = current.get(item.txn_id)
        route = summary[item.route]
        route["item_count"] += 1
        route["pending"] += 1 if item.outcome == "pending" else 0
        if transaction is not None:
            route["bank_amount_minor"] += transaction.amount_minor
        items.append(
            AgentTriageItemOut(
                txn_id=item.txn_id,
                group_id=item.group_id,
                route=cast(
                    Literal["possible_transfer", "taxonomy_gap", "uncertain"],
                    item.route,
                ),
                reason_code=item.reason_code,
                outcome=cast(
                    Literal[
                        "pending",
                        "confirmed_transfer",
                        "confirmed_taxonomy_gap",
                        "left_uncertain",
                        "classified_existing",
                        "stale",
                        "withdrawn",
                    ],
                    item.outcome,
                ),
                applied_category_id=item.applied_category_id,
                reviewed_at=item.reviewed_at,
                current_transaction=transaction,
            )
        )
    route_summaries = [
        AgentTriageRouteSummaryOut(
            route=cast(
                Literal["possible_transfer", "taxonomy_gap", "uncertain"], route
            ),
            item_count=counts["item_count"],
            pending=counts["pending"],
            bank_amount_minor=counts["bank_amount_minor"],
        )
        for route, counts in sorted(summary.items())
    ]
    return AgentTriageRunOut(
        run_id=run.run_id,
        ledger_revision=run.ledger_revision,
        scope_revision=run.scope_revision,
        schema_version=cast(Literal[1], run.schema_version),
        scope=_scope(run.scope),
        producer=_producer(run.producer),
        created_at=run.created_at,
        state=cast(Literal["open", "completed", "dismissed"], run.state),
        route_summaries=route_summaries,
        items=items,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_triage_run(body: AgentTriageSubmitIn, state: StateDep) -> AgentTriageSubmitOut:
    submission = TriageSubmission(
        schema_version=body.schema_version,
        ledger_revision=body.ledger_revision,
        scope_revision=body.scope_revision,
        scope=TriageScope(since=body.scope.since, until=body.scope.until),
        producer=Producer(
            client=body.producer.client,
            client_version=body.producer.client_version,
            model_reported=body.producer.model_reported,
        ),
        groups=tuple(
            TriageGroup(
                group_id=group.group_id,
                route=group.route,
                reason_code=group.reason_code,
                txn_ids=tuple(group.txn_ids),
            )
            for group in body.groups
        ),
    )
    try:
        with ledger_rw(state) as conn:
            result = submit_triage(conn, state.paths, submission)
    except TriageLedgerNotReady as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except (TriageScopeIncomplete, TriageConflict) as error:
        raise _conflict(error) from error
    return AgentTriageSubmitOut(
        run_id=result.run_id,
        created=result.created,
        item_count=result.item_count,
    )


@router.get("")
def read_triage_runs(
    state: StateDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AgentTriageRunSummaryOut]:
    with ledger_ro(state) as conn, read_transaction(conn):
        runs = list_runs(conn, limit=limit)
    return [
        AgentTriageRunSummaryOut(
            run_id=run.run_id,
            created_at=run.created_at,
            state=cast(Literal["open", "completed", "dismissed"], run.state),
            scope=_scope(run.scope),
            producer=_producer(run.producer),
            item_count=run.item_count,
            pending=run.pending,
            confirmed_transfer=run.confirmed_transfer,
            confirmed_taxonomy_gap=run.confirmed_taxonomy_gap,
            left_uncertain=run.left_uncertain,
            classified_existing=run.classified_existing,
            stale=run.stale,
            withdrawn=run.withdrawn,
        )
        for run in runs
    ]


@router.get("/{run_id}")
def read_triage_run(run_id: RunId, state: StateDep) -> AgentTriageRunOut:
    with ledger_ro(state) as conn, read_transaction(conn):
        run = get_run(conn, run_id)
        current = (
            {}
            if run is None
            else {
                item.txn_id: (
                    None
                    if (row := repo.get_transaction(conn, item.txn_id)) is None
                    else TransactionOut.model_validate(dict(row))
                )
                for item in run.items
            }
        )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no triage run {run_id!r}")
    return _run_out(run, current)


@router.post("/{run_id}/review")
def review_triage_run(
    run_id: RunId, body: AgentTriageReviewIn, state: StateDep
) -> AgentTriageReviewOut:
    try:
        with ledger_rw(state) as conn:
            result = review_triage(
                conn,
                run_id,
                tuple(body.txn_ids),
                action=body.action,
                category_id=body.category_id,
            )
    except TriageNotFound as error:
        raise _not_found(error) from error
    except TriageConflict as error:
        raise _conflict(error) from error
    return AgentTriageReviewOut(
        run_id=result.run_id,
        confirmed_transfer=result.confirmed_transfer,
        confirmed_taxonomy_gap=result.confirmed_taxonomy_gap,
        left_uncertain=result.left_uncertain,
        classified_existing=result.classified_existing,
        state=cast(Literal["open", "completed", "dismissed"], result.state),
    )


@router.post("/{run_id}/dismiss")
def dismiss_triage_run(run_id: RunId, state: StateDep) -> AgentTriageReviewOut:
    try:
        with ledger_rw(state) as conn:
            result = dismiss_run(conn, run_id)
    except TriageNotFound as error:
        raise _not_found(error) from error
    except TriageConflict as error:
        raise _conflict(error) from error
    return AgentTriageReviewOut(
        run_id=result.run_id,
        confirmed_transfer=0,
        confirmed_taxonomy_gap=0,
        left_uncertain=result.left_uncertain,
        classified_existing=0,
        state="dismissed",
    )


@router.post("/{run_id}/withdraw")
def withdraw_triage_run(run_id: RunId, state: StateDep) -> AgentTriageWithdrawOut:
    try:
        with ledger_rw(state) as conn:
            result = withdraw_run(conn, run_id)
    except TriageNotFound as error:
        raise _not_found(error) from error
    return AgentTriageWithdrawOut(
        run_id=result.run_id,
        withdrawn=result.withdrawn,
        already_absent=result.already_absent,
        changed_later=result.changed_later,
    )


@router.post("/{run_id}/withdraw-selected")
def withdraw_selected_triage_rows(
    run_id: RunId,
    body: AgentTriageWithdrawSelectedIn,
    state: StateDep,
) -> AgentTriageWithdrawOut:
    """Undo only explicitly named decisions, preserving every other reviewed row."""
    try:
        with ledger_rw(state) as conn:
            result = withdraw_run(conn, run_id, tuple(body.txn_ids))
    except TriageNotFound as error:
        raise _not_found(error) from error
    except TriageConflict as error:
        raise _conflict(error) from error
    return AgentTriageWithdrawOut(
        run_id=result.run_id,
        withdrawn=result.withdrawn,
        already_absent=result.already_absent,
        changed_later=result.changed_later,
    )
