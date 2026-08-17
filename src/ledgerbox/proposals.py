# SPDX-License-Identifier: AGPL-3.0-or-later
"""Versioned Agent classification state machine shared by Web, CLI and MCP.

There is no model client here.  A caller supplies a versioned, explicit-id
proposal that was produced elsewhere.  Schema v1 and v2 ``review_first`` store
pending audit rows only.  Schema v2 ``automatic`` stores that audit, applies
Agent-sourced overrides, records outcomes, and completes the run in one
``BEGIN IMMEDIATE`` transaction.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from .agent_jobs import AgentJobConflict, link_job_proposal_run_in_transaction
from .content_ids import content_hash
from .db import repo
from .db.connection import transaction
from .learning import apply_learned_rules, unlearn_agent_run

PROPOSAL_SCHEMA_V1 = 1
PROPOSAL_SCHEMA_VERSION = 2
PROPOSAL_SCHEMA_V2 = PROPOSAL_SCHEMA_VERSION
APPLICATION_MODES = frozenset({"review_first", "automatic"})
CLIENTS = frozenset({"codex", "claude-code", "other"})


class ProposalError(RuntimeError):
    """Base class for proposal contract failures safe to show to a caller."""


class ProposalConflict(ProposalError):
    """The payload or ledger state cannot be accepted as the requested whole."""


class ProposalNotFound(ProposalError):
    """No proposal run with the explicit content id exists."""


@dataclass(frozen=True, slots=True)
class Producer:
    client: str
    client_version: str | None = None
    model_reported: str | None = None


@dataclass(frozen=True, slots=True)
class ProposalGroup:
    group_id: str
    category_id: str
    txn_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProposalSubmission:
    schema_version: int
    ledger_revision: str
    producer: Producer
    groups: tuple[ProposalGroup, ...]
    application_mode: Literal["review_first", "automatic"] | None = None


@dataclass(frozen=True, slots=True)
class Proposal:
    txn_id: str
    group_id: str
    suggested_category_id: str
    outcome: str
    applied_category_id: str | None
    reviewed_at: str | None


@dataclass(frozen=True, slots=True)
class ProposalRun:
    run_id: str
    ledger_revision: str
    schema_version: int
    producer: Producer
    created_at: str
    state: str
    proposals: tuple[Proposal, ...]
    application_mode: Literal["review_first", "automatic"] | None = None


@dataclass(frozen=True, slots=True)
class ProposalRunSummary:
    run_id: str
    created_at: str
    state: str
    producer: Producer
    proposal_count: int
    pending: int
    accepted: int
    edited: int
    rejected: int
    withdrawn: int


@dataclass(frozen=True, slots=True)
class SubmitResult:
    run_id: str
    created: bool
    proposal_count: int


@dataclass(frozen=True, slots=True)
class ValidationResult:
    run_id: str
    proposal_count: int


@dataclass(frozen=True, slots=True)
class ReviewResult:
    run_id: str
    accepted: int = 0
    edited: int = 0
    rejected: int = 0
    state: str = "open"


@dataclass(frozen=True, slots=True)
class WithdrawResult:
    run_id: str
    withdrawn: int
    already_absent: int
    changed_later: int
    # Withdrawal also takes what the run taught. These are reported rather
    # than silent because they change lines beyond the run's own proposals.
    rules_unlearned: int = 0
    learned_cleared: int = 0


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ledger_revision(conn: sqlite3.Connection) -> str:
    """Hash immutable statement facts and the currently available taxonomy.

    Effective category answers are checked per proposal instead of entering
    this hash.  Otherwise accepting the first group would make every remaining
    group in the same run stale.  Ingest, forget, changed evidence or taxonomy
    still changes the revision; a later human/rule answer on a pending row is
    caught by the explicit current-state check.
    """
    transactions = [
        {
            "txn_id": str(row["txn_id"]),
            "date": str(row["date"]),
            "amount_minor": int(row["amount_minor"]),
            "currency": str(row["currency"]),
            "raw_descriptor": str(row["raw_descriptor"]),
        }
        for row in repo.proposal_revision_transactions(conn)
    ]
    categories = [
        {
            "id": str(row["id"]),
            "kind": str(row["kind"]),
            "parent_id": None if row["parent_id"] is None else str(row["parent_id"]),
        }
        for row in repo.list_categories(conn)
    ]
    return content_hash(
        {
            "revision_schema": 1,
            "transactions": transactions,
            "categories": categories,
        }
    )


def group_id_for(category_id: str, txn_ids: tuple[str, ...]) -> str:
    """The content id for one category plus an explicit transaction set."""
    return content_hash(
        {"category_id": category_id, "txn_ids": sorted(txn_ids)}
    )


def _normalised_groups(groups: tuple[ProposalGroup, ...]) -> list[dict[str, object]]:
    return sorted(
        [
            {
                "group_id": group.group_id,
                "category_id": group.category_id,
                "txn_ids": sorted(group.txn_ids),
            }
            for group in groups
        ],
        key=lambda group: str(group["group_id"]),
    )


def _run_id(submission: ProposalSubmission) -> str:
    identity: dict[str, object] = {
        "schema_version": submission.schema_version,
        "ledger_revision": submission.ledger_revision,
        "producer": {
            "client": submission.producer.client,
            "client_version": submission.producer.client_version,
            "model_reported": submission.producer.model_reported,
        },
        "groups": _normalised_groups(submission.groups),
    }
    if submission.schema_version == PROPOSAL_SCHEMA_V2:
        identity["application_mode"] = submission.application_mode
    return content_hash(identity)


def _validate_submission_shape(submission: ProposalSubmission) -> list[tuple[str, str, str]]:
    if type(submission.schema_version) is not int or submission.schema_version not in {
        PROPOSAL_SCHEMA_V1,
        PROPOSAL_SCHEMA_VERSION,
    }:
        raise ProposalConflict(
            f"proposal schema_version must be {PROPOSAL_SCHEMA_V1} or {PROPOSAL_SCHEMA_VERSION}"
        )
    if submission.schema_version == PROPOSAL_SCHEMA_V1:
        if submission.application_mode is not None:
            raise ProposalConflict("proposal schema version 1 is permanently review-only")
    elif (
        type(submission.application_mode) is not str
        or submission.application_mode not in APPLICATION_MODES
    ):
        raise ProposalConflict(
            "proposal schema version 2 application_mode must be review_first or automatic"
        )
    if submission.producer.client not in CLIENTS:
        raise ProposalConflict("producer client must be codex, claude-code, or other")
    for field_name, value in (
        ("client_version", submission.producer.client_version),
        ("model_reported", submission.producer.model_reported),
    ):
        if value is not None and len(value) > 200:
            raise ProposalConflict(f"producer {field_name} is longer than 200 characters")
    if not submission.groups and submission.schema_version == PROPOSAL_SCHEMA_V1:
        # V1 semantics are frozen. V2 accepts the empty proposal because "I
        # examined every candidate and abstain on all of them" is an outcome the
        # Skill has always been allowed to reach; a real run reached it and the
        # only exit the wire offered was being recorded as a client failure.
        raise ProposalConflict("a schema v1 proposal run must contain at least one group")

    seen: set[str] = set()
    rows: list[tuple[str, str, str]] = []
    for group in submission.groups:
        if not group.txn_ids:
            raise ProposalConflict(f"group {group.group_id!r} has no txn_ids")
        if len(group.txn_ids) != len(set(group.txn_ids)):
            raise ProposalConflict(f"group {group.group_id!r} repeats a txn_id")
        expected = group_id_for(group.category_id, group.txn_ids)
        if group.group_id != expected:
            raise ProposalConflict(
                f"group_id does not match the category and explicit txn_ids: want {expected}"
            )
        for txn_id in group.txn_ids:
            if txn_id in seen:
                raise ProposalConflict(f"txn_id {txn_id!r} appears in more than one group")
            seen.add(txn_id)
            rows.append((txn_id, group.group_id, group.category_id))

    if len(rows) > repo.MAX_PAGE_SIZE:
        raise ProposalConflict(
            f"a proposal run may name at most {repo.MAX_PAGE_SIZE} transactions"
        )
    return sorted(rows)


def _validate_current_rows(
    conn: sqlite3.Connection, rows: list[tuple[str, str, str]]
) -> None:
    for category_id in sorted({category_id for _, _, category_id in rows}):
        if not repo.category_exists(conn, category_id):
            raise ProposalConflict(f"no category {category_id!r}")
    for txn_id, _, _ in rows:
        current = repo.get_transaction(conn, txn_id)
        if current is None or current["category_decided_by"] != "none":
            raise ProposalConflict(
                f"transaction {txn_id!r} is not eligible: it is missing or already answered"
            )


def submit_proposal(
    conn: sqlite3.Connection,
    submission: ProposalSubmission,
    *,
    job_id: str | None = None,
    session_id: str | None = None,
) -> SubmitResult:
    """Validate and execute one whole versioned proposal run atomically."""
    if (job_id is None) != (session_id is None):
        raise ProposalConflict("job_id and session_id must be supplied together")
    if job_id is not None and (
        submission.schema_version != 2
        or submission.application_mode not in APPLICATION_MODES
    ):
        raise ProposalConflict("job-linked proposal requires schema v2 and an application mode")
    rows = _validate_submission_shape(submission)
    run_id = _run_id(submission)
    with transaction(conn):
        existing = repo.get_agent_proposal_run(conn, run_id)
        if existing is not None:
            # Content identity is the idempotency key.  A later review may have
            # made these rows ineligible; repeating the original submission is
            # still a no-op returning the already-existing audit run, not a new
            # attempt to write proposals over their current state.
            if job_id is not None and session_id is not None and rows:
                # Applied work keeps strict single-job attribution. An empty
                # declaration is exempt: it leaves the revision unchanged, so a
                # later round declining the same pool deduplicates onto the same
                # run -- and turning that repeat into a conflict made honesty
                # fail forever at one revision. Nothing was applied on the
                # repeating job's watch, so it has nothing to claim.
                try:
                    link_job_proposal_run_in_transaction(
                        conn,
                        job_id=job_id,
                        session_id=session_id,
                        proposal_run_id=run_id,
                        client=cast(Literal["codex", "claude-code"], submission.producer.client),
                        application_mode=cast(
                            Literal["review_first", "automatic"],
                            submission.application_mode,
                        ),
                        allow_new_link=False,
                    )
                except AgentJobConflict as error:
                    raise ProposalConflict(str(error)) from error
            return SubmitResult(run_id=run_id, created=False, proposal_count=len(rows))

        current_revision = ledger_revision(conn)
        if submission.ledger_revision != current_revision:
            raise ProposalConflict(
                f"ledger revision changed: read {current_revision} and propose again"
            )
        _validate_current_rows(conn, rows)

        repo.insert_agent_proposal_run(
            conn,
            run_id=run_id,
            ledger_revision=submission.ledger_revision,
            schema_version=submission.schema_version,
            client=submission.producer.client,
            client_version=submission.producer.client_version,
            model_reported=submission.producer.model_reported,
            application_mode=submission.application_mode,
        )
        repo.insert_agent_category_proposals(conn, run_id=run_id, rows=rows)
        if not rows:
            # Nothing to apply and nothing to review: an open empty run would
            # sit in the review area forever, so it completes on arrival.
            repo.set_agent_proposal_run_state(conn, run_id=run_id, state="completed")
        elif submission.application_mode == "automatic":
            now = _now()
            writes: dict[str, list[str]] = defaultdict(list)
            for txn_id, _, category_id in rows:
                writes[category_id].append(txn_id)
            for category_id, txn_ids in sorted(writes.items()):
                repo.set_category_overrides(
                    conn,
                    txn_ids=txn_ids,
                    category_id=category_id,
                    source="agent",
                    agent_run_id=run_id,
                )
            for txn_id, _, category_id in rows:
                repo.review_agent_category_proposal(
                    conn,
                    run_id=run_id,
                    txn_id=txn_id,
                    outcome="accepted",
                    applied_category_id=category_id,
                    reviewed_at=now,
                )
            repo.set_agent_proposal_run_state(conn, run_id=run_id, state="completed")
            # What this run just taught claims every remaining identical
            # template in the same transaction, so one answered merchant does
            # not leave its unanswered twins for the next round to re-litigate.
            apply_learned_rules(conn, now=now)
        if job_id is not None and session_id is not None:
            try:
                link_job_proposal_run_in_transaction(
                    conn,
                    job_id=job_id,
                    session_id=session_id,
                    proposal_run_id=run_id,
                    client=cast(Literal["codex", "claude-code"], submission.producer.client),
                    application_mode=cast(
                        Literal["review_first", "automatic"],
                        submission.application_mode,
                    ),
                    allow_new_link=True,
                )
            except AgentJobConflict as error:
                raise ProposalConflict(str(error)) from error
    return SubmitResult(run_id=run_id, created=True, proposal_count=len(rows))


def validate_proposal(
    conn: sqlite3.Connection, submission: ProposalSubmission
) -> ValidationResult:
    """Run the full submit contract without persisting a run or category."""
    rows = _validate_submission_shape(submission)
    current_revision = ledger_revision(conn)
    if submission.ledger_revision != current_revision:
        raise ProposalConflict(
            f"ledger revision changed: read {current_revision} and propose again"
        )
    _validate_current_rows(conn, rows)
    return ValidationResult(run_id=_run_id(submission), proposal_count=len(rows))


def get_run(conn: sqlite3.Connection, run_id: str) -> ProposalRun | None:
    row = repo.get_agent_proposal_run(conn, run_id)
    if row is None:
        return None
    proposals = tuple(
        Proposal(
            txn_id=str(item["txn_id"]),
            group_id=str(item["group_id"]),
            suggested_category_id=str(item["suggested_category_id"]),
            outcome=str(item["outcome"]),
            applied_category_id=(
                None
                if item["applied_category_id"] is None
                else str(item["applied_category_id"])
            ),
            reviewed_at=None if item["reviewed_at"] is None else str(item["reviewed_at"]),
        )
        for item in repo.list_agent_category_proposals(conn, run_id)
    )
    return ProposalRun(
        run_id=str(row["id"]),
        ledger_revision=str(row["ledger_revision"]),
        schema_version=int(row["schema_version"]),
        producer=Producer(
            client=str(row["client"]),
            client_version=(
                None if row["client_version"] is None else str(row["client_version"])
            ),
            model_reported=(
                None if row["model_reported"] is None else str(row["model_reported"])
            ),
        ),
        created_at=str(row["created_at"]),
        state=str(row["state"]),
        proposals=proposals,
        application_mode=cast(
            Literal["review_first", "automatic"] | None,
            row["application_mode"],
        ),
    )


def list_runs(conn: sqlite3.Connection, *, limit: int = 50) -> tuple[ProposalRunSummary, ...]:
    """Return a bounded, newest-first proposal audit index for review clients."""
    if not 1 <= limit <= 100:
        raise ValueError("proposal run limit must be from 1 to 100")
    return tuple(
        ProposalRunSummary(
            run_id=str(row["id"]),
            created_at=str(row["created_at"]),
            state=str(row["state"]),
            producer=Producer(
                client=str(row["client"]),
                client_version=(
                    None if row["client_version"] is None else str(row["client_version"])
                ),
                model_reported=(
                    None if row["model_reported"] is None else str(row["model_reported"])
                ),
            ),
            proposal_count=int(row["proposal_count"]),
            pending=int(row["pending"]),
            accepted=int(row["accepted"]),
            edited=int(row["edited"]),
            rejected=int(row["rejected"]),
            withdrawn=int(row["withdrawn"]),
        )
        for row in repo.list_agent_proposal_runs(conn, limit=limit)
    )


def _named_pending(
    conn: sqlite3.Connection, run_id: str, txn_ids: tuple[str, ...]
) -> list[sqlite3.Row]:
    if not txn_ids or len(txn_ids) > repo.MAX_PAGE_SIZE:
        raise ProposalConflict(
            f"review must name 1..{repo.MAX_PAGE_SIZE} explicit txn_ids"
        )
    if len(txn_ids) != len(set(txn_ids)):
        raise ProposalConflict("review txn_ids must be unique")
    rows = repo.get_agent_category_proposals(conn, run_id, txn_ids)
    if len(rows) != len(txn_ids):
        raise ProposalConflict("one or more proposal txn_ids are not in this run")
    if any(row["outcome"] != "pending" for row in rows):
        raise ProposalConflict("one or more proposals are no longer pending")
    return rows


def review_proposals(
    conn: sqlite3.Connection,
    run_id: str,
    txn_ids: tuple[str, ...],
    *,
    action: Literal["accept", "reject"],
    category_id: str | None = None,
) -> ReviewResult:
    """Accept/edit or reject explicit pending rows as one atomic review."""
    if action not in {"accept", "reject"}:
        raise ProposalConflict("review action must be accept or reject")
    if action == "reject" and category_id is not None:
        raise ProposalConflict("a rejected proposal cannot carry an applied category")

    accepted = edited = rejected = 0
    with transaction(conn):
        run = repo.get_agent_proposal_run(conn, run_id)
        if run is None:
            raise ProposalNotFound(f"no proposal run {run_id!r}")
        if run["state"] != "open":
            raise ProposalConflict(f"proposal run is {run['state']}, not open")
        rows = _named_pending(conn, run_id, txn_ids)
        now = _now()

        if action == "accept":
            if str(run["ledger_revision"]) != ledger_revision(conn):
                raise ProposalConflict("ledger revision changed; this run must be reviewed again")
            if category_id is not None and not repo.category_exists(conn, category_id):
                raise ProposalConflict(f"no category {category_id!r}")

            writes: dict[str, list[str]] = defaultdict(list)
            outcomes: list[tuple[sqlite3.Row, str, str]] = []
            for row in rows:
                txn_id = str(row["txn_id"])
                current = repo.get_transaction(conn, txn_id)
                if current is None or current["category_decided_by"] != "none":
                    raise ProposalConflict(
                        f"transaction {txn_id!r} is no longer eligible for this proposal"
                    )
                applied = category_id or str(row["suggested_category_id"])
                outcome = (
                    "accepted"
                    if applied == str(row["suggested_category_id"])
                    else "edited"
                )
                writes[applied].append(txn_id)
                outcomes.append((row, outcome, applied))

            for applied, ids in sorted(writes.items()):
                repo.set_category_overrides(conn, txn_ids=ids, category_id=applied)
            for row, outcome, applied in outcomes:
                repo.review_agent_category_proposal(
                    conn,
                    run_id=run_id,
                    txn_id=str(row["txn_id"]),
                    outcome=outcome,
                    applied_category_id=applied,
                    reviewed_at=now,
                )
                if outcome == "accepted":
                    accepted += 1
                else:
                    edited += 1
        else:
            for row in rows:
                repo.review_agent_category_proposal(
                    conn,
                    run_id=run_id,
                    txn_id=str(row["txn_id"]),
                    outcome="rejected",
                    applied_category_id=None,
                    reviewed_at=now,
                )
                rejected += 1

        state = "open" if repo.count_pending_agent_proposals(conn, run_id) else "completed"
        repo.set_agent_proposal_run_state(conn, run_id=run_id, state=state)

    return ReviewResult(
        run_id=run_id,
        accepted=accepted,
        edited=edited,
        rejected=rejected,
        state=state,
    )


def dismiss_run(conn: sqlite3.Connection, run_id: str) -> ReviewResult:
    """Reject every still-pending row and dismiss the run; never write a category."""
    rejected = 0
    with transaction(conn):
        run = repo.get_agent_proposal_run(conn, run_id)
        if run is None:
            raise ProposalNotFound(f"no proposal run {run_id!r}")
        if run["state"] != "open":
            raise ProposalConflict(f"proposal run is {run['state']}, not open")
        now = _now()
        for row in repo.list_agent_category_proposals(conn, run_id):
            if row["outcome"] == "pending":
                repo.review_agent_category_proposal(
                    conn,
                    run_id=run_id,
                    txn_id=str(row["txn_id"]),
                    outcome="rejected",
                    applied_category_id=None,
                    reviewed_at=now,
                )
                rejected += 1
        repo.set_agent_proposal_run_state(conn, run_id=run_id, state="dismissed")
    return ReviewResult(run_id=run_id, rejected=rejected, state="dismissed")


def withdraw_run(conn: sqlite3.Connection, run_id: str) -> WithdrawResult:
    """Withdraw still-matching answers without erasing a later decision."""
    withdrawn = absent = changed_later = 0
    with transaction(conn):
        run = repo.get_agent_proposal_run(conn, run_id)
        if run is None:
            raise ProposalNotFound(f"no proposal run {run_id!r}")
        now = _now()
        for row in repo.list_agent_category_proposals(conn, run_id):
            if row["outcome"] not in {"accepted", "edited"}:
                continue
            txn_id = str(row["txn_id"])
            applied = str(row["applied_category_id"])
            current = repo.get_category_override(conn, txn_id)
            if current is None:
                absent += 1
            elif (
                str(current["category_id"]) == applied
                and (
                    run["application_mode"] != "automatic"
                    or (
                        current["source"] == "agent"
                        and current["agent_run_id"] == run_id
                    )
                )
            ):
                repo.clear_category_override(conn, txn_id=txn_id)
                withdrawn += 1
            else:
                changed_later += 1
            repo.withdraw_agent_category_proposal(
                conn, run_id=run_id, txn_id=txn_id, reviewed_at=now
            )
        # The run's rules and the answers those rules derived go with it; a
        # template a person has since re-taught no longer names this run and
        # is left exactly as the person left it.
        rules_unlearned, learned_cleared = unlearn_agent_run(conn, run_id=run_id)
        if run["state"] != "dismissed":
            state = "open" if repo.count_pending_agent_proposals(conn, run_id) else "completed"
            repo.set_agent_proposal_run_state(conn, run_id=run_id, state=state)
    return WithdrawResult(
        run_id=run_id,
        withdrawn=withdrawn,
        already_absent=absent,
        changed_later=changed_later,
        rules_unlearned=rules_unlearned,
        learned_cleared=learned_cleared,
    )
