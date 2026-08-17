# SPDX-License-Identifier: AGPL-3.0-or-later
"""HTTP adapter for local proposal audit and explicit human review.

This route does not invoke an Agent and does not accept a filter-shaped write.
Every transaction id came from the request, and every mutation delegates to
``ledgerbox.proposals`` so the future CLI and MCP adapter cannot acquire a
second state machine.
"""

from __future__ import annotations

from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from ...db import repo
from ...db.connection import read_transaction
from ...proposals import (
    PROPOSAL_SCHEMA_VERSION,
    Producer,
    ProposalConflict,
    ProposalGroup,
    ProposalNotFound,
    ProposalRun,
    ProposalSubmission,
    dismiss_run,
    get_run,
    ledger_revision,
    list_runs,
    review_proposals,
    submit_proposal,
    withdraw_run,
)
from ..dependencies import AppState, get_state, ledger_ro, ledger_rw
from ..schemas import (
    HASH_ID_PATTERN,
    AgentProducerIn,
    AgentProposalOut,
    AgentProposalReviewIn,
    AgentProposalReviewOut,
    AgentProposalRunOut,
    AgentProposalRunSummaryOut,
    AgentProposalStatusOut,
    AgentProposalSubmitIn,
    AgentProposalSubmitOut,
    AgentProposalWithdrawOut,
    TransactionOut,
)

router = APIRouter(prefix="/api/agent-proposals", tags=["agent proposals"])
StateDep = Annotated[AppState, Depends(get_state)]
RunId = Annotated[str, Path(pattern=HASH_ID_PATTERN)]


def _conflict(error: ProposalConflict) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


def _not_found(error: ProposalNotFound) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(error))


def _run_out(
    run: ProposalRun, current: dict[str, TransactionOut | None]
) -> AgentProposalRunOut:
    return AgentProposalRunOut(
        run_id=run.run_id,
        ledger_revision=run.ledger_revision,
        schema_version=cast(Literal[1, 2], run.schema_version),
        application_mode=run.application_mode,
        producer=_producer_out(run.producer),
        created_at=run.created_at,
        state=cast(Literal["open", "completed", "dismissed"], run.state),
        proposals=[
            AgentProposalOut(
                txn_id=row.txn_id,
                group_id=row.group_id,
                suggested_category_id=row.suggested_category_id,
                outcome=cast(
                    Literal["pending", "accepted", "edited", "rejected", "withdrawn"],
                    row.outcome,
                ),
                applied_category_id=row.applied_category_id,
                reviewed_at=row.reviewed_at,
                current_transaction=current.get(row.txn_id),
            )
            for row in run.proposals
        ],
    )


def _producer_out(producer: Producer) -> AgentProducerIn:
    return AgentProducerIn(
        client=cast(Literal["codex", "claude-code", "other"], producer.client),
        client_version=producer.client_version,
        model_reported=producer.model_reported,
    )


@router.get("/status")
def proposal_status(state: StateDep) -> AgentProposalStatusOut:
    """The version and structural ledger revision a proposal must echo."""
    with ledger_ro(state) as conn, read_transaction(conn):
        revision = ledger_revision(conn)
    return AgentProposalStatusOut(
        schema_version=cast(Literal[2], PROPOSAL_SCHEMA_VERSION),
        ledger_revision=revision,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_proposal_run(
    body: AgentProposalSubmitIn, state: StateDep
) -> AgentProposalSubmitOut:
    submission = ProposalSubmission(
        schema_version=body.schema_version,
        ledger_revision=body.ledger_revision,
        producer=Producer(
            client=body.producer.client,
            client_version=body.producer.client_version,
            model_reported=body.producer.model_reported,
        ),
        groups=tuple(
            ProposalGroup(
                group_id=group.group_id,
                category_id=group.category_id,
                txn_ids=tuple(group.txn_ids),
            )
            for group in body.groups
        ),
        application_mode=body.application_mode,
    )
    try:
        with ledger_rw(state) as conn:
            result = submit_proposal(conn, submission)
    except ProposalConflict as error:
        raise _conflict(error) from error
    return AgentProposalSubmitOut(
        run_id=result.run_id,
        created=result.created,
        proposal_count=result.proposal_count,
    )


@router.get("")
def read_proposal_runs(
    state: StateDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AgentProposalRunSummaryOut]:
    """A bounded audit index; current transaction facts stay on the run read."""
    with ledger_ro(state) as conn, read_transaction(conn):
        runs = list_runs(conn, limit=limit)
    return [
        AgentProposalRunSummaryOut(
            run_id=run.run_id,
            created_at=run.created_at,
            state=cast(Literal["open", "completed", "dismissed"], run.state),
            producer=_producer_out(run.producer),
            proposal_count=run.proposal_count,
            pending=run.pending,
            accepted=run.accepted,
            edited=run.edited,
            rejected=run.rejected,
            withdrawn=run.withdrawn,
        )
        for run in runs
    ]


@router.get("/{run_id}")
def read_proposal_run(run_id: RunId, state: StateDep) -> AgentProposalRunOut:
    with ledger_ro(state) as conn, read_transaction(conn):
        run = get_run(conn, run_id)
        current = (
            {}
            if run is None
            else {
                proposal.txn_id: (
                    None
                    if (row := repo.get_transaction(conn, proposal.txn_id)) is None
                    else TransactionOut.model_validate(dict(row))
                )
                for proposal in run.proposals
            }
        )
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no proposal run {run_id!r}")
    return _run_out(run, current)


@router.post("/{run_id}/review")
def review_proposal_run(
    run_id: RunId, body: AgentProposalReviewIn, state: StateDep
) -> AgentProposalReviewOut:
    try:
        with ledger_rw(state) as conn:
            result = review_proposals(
                conn,
                run_id,
                tuple(body.txn_ids),
                action=body.action,
                category_id=body.category_id,
            )
    except ProposalNotFound as error:
        raise _not_found(error) from error
    except ProposalConflict as error:
        raise _conflict(error) from error
    return AgentProposalReviewOut(
        run_id=result.run_id,
        accepted=result.accepted,
        edited=result.edited,
        rejected=result.rejected,
        state=cast(Literal["open", "completed", "dismissed"], result.state),
    )


@router.post("/{run_id}/dismiss")
def dismiss_proposal_run(run_id: RunId, state: StateDep) -> AgentProposalReviewOut:
    try:
        with ledger_rw(state) as conn:
            result = dismiss_run(conn, run_id)
    except ProposalNotFound as error:
        raise _not_found(error) from error
    except ProposalConflict as error:
        raise _conflict(error) from error
    return AgentProposalReviewOut(
        run_id=result.run_id,
        accepted=0,
        edited=0,
        rejected=result.rejected,
        state="dismissed",
    )


@router.post("/{run_id}/withdraw")
def withdraw_proposal_run(run_id: RunId, state: StateDep) -> AgentProposalWithdrawOut:
    try:
        with ledger_rw(state) as conn:
            result = withdraw_run(conn, run_id)
    except ProposalNotFound as error:
        raise _not_found(error) from error
    return AgentProposalWithdrawOut(
        run_id=result.run_id,
        withdrawn=result.withdrawn,
        already_absent=result.already_absent,
        changed_later=result.changed_later,
        rules_unlearned=result.rules_unlearned,
        learned_cleared=result.learned_cleared,
    )
