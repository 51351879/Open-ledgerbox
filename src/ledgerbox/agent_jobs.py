# SPDX-License-Identifier: AGPL-3.0-or-later
"""Persistent A7.4 import-trigger classification job state machine."""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from .agent_center import AgentClient, ApplicationMode, read_policy
from .db.connection import transaction

JobState = Literal["queued", "running", "completed", "partial", "failed"]
ClientOutcome = Literal["exited", "timeout", "not_found", "spawn_failed", "workspace_missing"]
CLIENT_OUTCOMES: frozenset[str] = frozenset(
    ("exited", "timeout", "not_found", "spawn_failed", "workspace_missing")
)
# Enough to hold the end of a client's own account of a run without turning the
# ledger into a log file. The tail is kept because that is where a run explains
# itself; a truncated excerpt says so rather than passing for the whole thing.
MAX_CLIENT_LOG_CHARS = 16_384
TriggerKind = Literal["import", "manual", "followup"]
# A chain of rounds stops when a round finds nothing new. This cap is what stops
# it when every round keeps finding a little: the observed real run produced
# 96, 18, 5, 6, 2, 9, 1, 2, 1, 4, 5, 1, 2 and never once returned zero, so
# "no progress" alone would have run until the operator's patience did.
#
# At roughly a minute a round this is a walk-away length of time, which is the
# point: the operator presses once. A lower cap simply guaranteed the chain
# stopped with work still obviously left to do.
MAX_CLASSIFICATION_ROUNDS = 25
# A client that exits without submitting anything ends up here. It is not
# obviously a broken client: pressing the button again right after one of these
# immediately produced more work, so a chain tolerates a couple before it
# accepts that the remaining candidates are not going to be answered.
MAX_CONSECUTIVE_FAILED_ROUNDS = 3
_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


class AgentJobConflict(RuntimeError):
    """A classification job transition is invalid or no longer current."""


@dataclass(frozen=True, slots=True)
class AgentJob:
    id: str
    trigger_source_file_id: str | None
    trigger_kind: TriggerKind
    round_index: int
    client: AgentClient
    application_mode: ApplicationMode
    state: JobState
    candidate_count: int | None
    submitted_count: int | None
    applied_count: int | None
    omitted_count: int | None
    error_code: str | None
    client_outcome: ClientOutcome | None
    client_exit_code: int | None
    queued_at: str
    started_at: str | None
    finished_at: str | None
    session_id: str | None
    proposal_run_id: str | None
    # client_log_excerpt is deliberately absent: it holds the client's words
    # about real descriptors, so it is read through read_job_log() alone and can
    # never ride along into a response that serialises this object.


@dataclass(frozen=True, slots=True)
class AgentJobBatch:
    """Everything one stretch of classification work added up to.

    Reporting only the newest job is how a real fifteen-minute effort that
    classified 152 of 270 candidates came to be displayed as "2 submitted": a
    multi-file import queues one job per file, and the last one is always the
    one with the least left to find.
    """

    job_count: int
    state: JobState
    candidate_count: int | None
    submitted_count: int
    applied_count: int
    omitted_count: int | None
    error_code: str | None
    client_outcome: ClientOutcome | None
    rounds_capped: bool
    failed_rounds: int
    queued_at: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    job: AgentJob
    created: bool


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _timestamp(value: str | None) -> str:
    result = value or _utc_now()
    try:
        parsed = datetime.fromisoformat(result)
    except ValueError as error:
        raise AgentJobConflict("now must be an ISO timestamp with a timezone") from error
    if parsed.tzinfo is None:
        raise AgentJobConflict("now must be an ISO timestamp with a timezone")
    return result


def _job(row: sqlite3.Row) -> AgentJob:
    return AgentJob(
        id=str(row["id"]),
        trigger_source_file_id=cast(str | None, row["trigger_source_file_id"]),
        trigger_kind=cast(TriggerKind, row["trigger_kind"]),
        round_index=int(row["round_index"]),
        client=cast(AgentClient, row["client"]),
        application_mode=cast(ApplicationMode, row["application_mode"]),
        state=cast(JobState, row["state"]),
        candidate_count=cast(int | None, row["candidate_count"]),
        submitted_count=cast(int | None, row["submitted_count"]),
        applied_count=cast(int | None, row["applied_count"]),
        omitted_count=cast(int | None, row["omitted_count"]),
        error_code=cast(str | None, row["error_code"]),
        client_outcome=cast(ClientOutcome | None, row["client_outcome"]),
        client_exit_code=cast(int | None, row["client_exit_code"]),
        queued_at=str(row["queued_at"]),
        started_at=cast(str | None, row["started_at"]),
        finished_at=cast(str | None, row["finished_at"]),
        session_id=cast(str | None, row["session_id"]),
        proposal_run_id=cast(str | None, row["proposal_run_id"]),
    )


def get_job(conn: sqlite3.Connection, job_id: str) -> AgentJob | None:
    row = conn.execute(
        "SELECT * FROM agent_classification_job WHERE id = ?", (job_id,)
    ).fetchone()
    return None if row is None else _job(row)


def read_job_log(conn: sqlite3.Connection, job_id: str) -> str | None:
    """Read one job's bounded client log.

    This is the operator's own copy of what their client said while reading
    their own transactions. It is served to their terminal by the CLI and to
    nothing else -- never call this from an HTTP route.
    """
    row = conn.execute(
        "SELECT client_log_excerpt FROM agent_classification_job WHERE id = ?",
        (job_id,),
    ).fetchone()
    return None if row is None else cast(str | None, row["client_log_excerpt"])


def read_latest_batch(conn: sqlite3.Connection) -> AgentJobBatch | None:
    """Summarise the most recent unbroken stretch of classification work.

    Two jobs belong to the same stretch when the older one had not finished yet
    at the moment the newer one was queued -- which is exactly what a multi-file
    import produces, and exactly what a chain of rounds produces. A job queued
    long after everything went quiet starts a new stretch.
    """
    rows = conn.execute(
        "SELECT state, candidate_count, submitted_count, applied_count, omitted_count, "
        "error_code, client_outcome, round_index, queued_at, started_at, finished_at "
        "FROM agent_classification_job ORDER BY queued_at DESC, rowid DESC LIMIT 200"
    ).fetchall()
    if not rows:
        return None
    batch = [rows[0]]
    for older in rows[1:]:
        oldest = batch[-1]
        if older["finished_at"] is None or oldest["queued_at"] <= older["finished_at"]:
            batch.append(older)
            continue
        break
    batch.reverse()

    unfinished = [row for row in batch if row["state"] in {"queued", "running"}]
    finished = [row for row in batch if row["state"] not in {"queued", "running"}]
    candidate_count = next(
        (row["candidate_count"] for row in batch if row["candidate_count"] is not None),
        None,
    )
    submitted = sum(int(row["submitted_count"] or 0) for row in finished)
    applied = sum(int(row["applied_count"] or 0) for row in finished)
    last = finished[-1] if finished else None
    if unfinished:
        moving = any(row["state"] == "running" for row in unfinished)
        state: JobState = "running" if moving else "queued"
        omitted: int | None = None
    elif last is None:  # pragma: no cover - a batch always holds at least one row.
        return None
    elif submitted and last["state"] == "failed":
        # Four rounds that classified and one that returned nothing is not a
        # failed stretch of work, and calling it one is how a working run gets
        # thrown away. The leftover count is still the last real accounting.
        state = "partial" if last["omitted_count"] else "completed"
        omitted = cast(int | None, last["omitted_count"])
    else:
        state = cast(JobState, last["state"])
        omitted = cast(int | None, last["omitted_count"])
    return AgentJobBatch(
        job_count=len(batch),
        state=state,
        candidate_count=cast(int | None, candidate_count),
        submitted_count=submitted,
        applied_count=applied,
        omitted_count=omitted,
        error_code=None if last is None else cast(str | None, last["error_code"]),
        client_outcome=None if last is None else cast(ClientOutcome | None, last["client_outcome"]),
        rounds_capped=(
            last is not None
            and not unfinished
            and int(last["round_index"]) >= MAX_CLASSIFICATION_ROUNDS
        ),
        failed_rounds=sum(1 for row in finished if row["state"] == "failed"),
        queued_at=str(batch[0]["queued_at"]),
        started_at=cast(str | None, batch[0]["started_at"]),
        finished_at=None if unfinished or last is None else cast(str | None, last["finished_at"]),
    )


def _client_evidence(
    outcome: ClientOutcome | None,
    exit_code: int | None,
    log_excerpt: str | None,
) -> tuple[str | None, int | None, str | None]:
    """Validate and bound what the runner observed about the client process."""
    if outcome is not None and outcome not in CLIENT_OUTCOMES:
        raise AgentJobConflict("client_outcome is not a known runner outcome")
    if exit_code is not None:
        if type(exit_code) is not int:
            raise AgentJobConflict("client_exit_code must be an integer or absent")
        if outcome != "exited":
            raise AgentJobConflict("only a client that exited reports an exit code")
    if log_excerpt is None or not log_excerpt:
        return outcome, exit_code, None
    if type(log_excerpt) is not str:
        raise AgentJobConflict("client log excerpt must be text")
    if len(log_excerpt) <= MAX_CLIENT_LOG_CHARS:
        return outcome, exit_code, log_excerpt
    keep = MAX_CLIENT_LOG_CHARS - 80
    notice = f"[truncated: {len(log_excerpt) - keep} earlier characters dropped]\n"
    return outcome, exit_code, notice + log_excerpt[-keep:]


def enqueue_import_job(
    conn: sqlite3.Connection,
    *,
    source_file_id: str,
    now: str | None = None,
) -> EnqueueResult | None:
    """Queue one policy-snapshot job for an import, or leave everything unchanged."""
    with transaction(conn):
        return enqueue_import_job_in_transaction(
            conn,
            source_file_id=source_file_id,
            now=now,
        )


def enqueue_import_job_in_transaction(
    conn: sqlite3.Connection,
    *,
    source_file_id: str,
    now: str | None = None,
) -> EnqueueResult | None:
    """Queue an import job inside the caller's existing all-or-nothing write."""
    if not conn.in_transaction:
        raise AgentJobConflict("import job enqueue requires an active transaction")
    timestamp = _timestamp(now)
    policy = read_policy(conn)
    if not (
        policy.enabled
        and policy.auto_classify_new_imports
        and policy.selected_client is not None
    ):
        return None
    source = conn.execute(
        "SELECT 1 FROM source_file WHERE id = ?", (source_file_id,)
    ).fetchone()
    if source is None:
        raise AgentJobConflict("trigger source file is missing")
    existing = conn.execute(
        "SELECT * FROM agent_classification_job WHERE trigger_source_file_id = ?",
        (source_file_id,),
    ).fetchone()
    if existing is not None:
        return EnqueueResult(job=_job(existing), created=False)
    return EnqueueResult(
        job=_insert_job(
            conn,
            trigger_source_file_id=source_file_id,
            trigger_kind="import",
            round_index=1,
            client=policy.selected_client,
            application_mode=policy.application_mode,
            timestamp=timestamp,
        ),
        created=True,
    )


def _insert_job(
    conn: sqlite3.Connection,
    *,
    trigger_source_file_id: str | None,
    trigger_kind: TriggerKind,
    round_index: int,
    client: AgentClient,
    application_mode: ApplicationMode,
    timestamp: str,
) -> AgentJob:
    job_id = f"job-{uuid.uuid4().hex}"
    conn.execute(
        "INSERT INTO agent_classification_job "
        "(id, trigger_source_file_id, trigger_kind, round_index, client, "
        "application_mode, queued_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            job_id,
            trigger_source_file_id,
            trigger_kind,
            round_index,
            client,
            application_mode,
            timestamp,
        ),
    )
    created = get_job(conn, job_id)
    if created is None:  # pragma: no cover - the insert above created it.
        raise AgentJobConflict("queued job could not be read back")
    return created


def _unfinished_job(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM agent_classification_job WHERE state IN ('queued','running')"
        ).fetchone()
        is not None
    )


def enqueue_manual_job(
    conn: sqlite3.Connection,
    *,
    now: str | None = None,
) -> EnqueueResult | None:
    """Queue one round because a person asked for it, not because a file arrived.

    Unlike the import trigger this ignores ``auto_classify_new_imports``: that
    setting decides whether imports classify themselves, and an explicit request
    is not an import. It still requires an enabled policy with a chosen client,
    and it refuses to stack a second round on a queue that is already busy.
    """
    timestamp = _timestamp(now)
    with transaction(conn):
        policy = read_policy(conn)
        if not (policy.enabled and policy.selected_client is not None):
            return None
        if _unfinished_job(conn):
            return None
        return EnqueueResult(
            job=_insert_job(
                conn,
                trigger_source_file_id=None,
                trigger_kind="manual",
                round_index=1,
                client=policy.selected_client,
                application_mode=policy.application_mode,
                timestamp=timestamp,
            ),
            created=True,
        )


def _trailing_failed_rounds(conn: sqlite3.Connection) -> int:
    """Count the failed rounds at the tail of the CURRENT chain only.

    Counting the whole table's tail let yesterday's dead chain spend today's
    tolerance: after one old failure, every fresh press got exactly one round
    before the cross-chain count reached the limit. A chain head (any
    non-followup trigger) is where this chain began; nothing older is ours.
    """
    rows = conn.execute(
        "SELECT state, trigger_kind FROM agent_classification_job "
        "WHERE state NOT IN ('queued','running') "
        "ORDER BY queued_at DESC, rowid DESC LIMIT ?",
        (MAX_CONSECUTIVE_FAILED_ROUNDS + 1,),
    ).fetchall()
    trailing = 0
    for row in rows:
        if row["state"] != "failed":
            break
        trailing += 1
        if row["trigger_kind"] != "followup":
            break
    return trailing


def enqueue_followup_job(
    conn: sqlite3.Connection,
    *,
    finished: AgentJob,
    now: str | None = None,
) -> EnqueueResult | None:
    """Continue a chain of rounds only while the last one still found work.

    A round that deliberately submitted nothing has told us the remaining
    candidates are beyond what it will answer, and repeating it only spends the
    operator's time and their provider's tokens. A round that left nothing
    behind is done. A round that ended without reporting at all is neither of
    those: it is tolerated a few times, because doing so demonstrably finds work
    that the first attempt did not.
    """
    if finished.round_index >= MAX_CLASSIFICATION_ROUNDS:
        return None
    if finished.state == "partial":
        if not finished.submitted_count:
            return None
    elif finished.state == "failed":
        if _trailing_failed_rounds(conn) >= MAX_CONSECUTIVE_FAILED_ROUNDS:
            return None
    else:
        return None
    timestamp = _timestamp(now)
    with transaction(conn):
        policy = read_policy(conn)
        if not (policy.enabled and policy.selected_client == finished.client):
            return None
        if _unfinished_job(conn):
            return None
        return EnqueueResult(
            job=_insert_job(
                conn,
                trigger_source_file_id=None,
                trigger_kind="followup",
                round_index=finished.round_index + 1,
                client=finished.client,
                application_mode=finished.application_mode,
                timestamp=timestamp,
            ),
            created=True,
        )


def link_job_proposal_run_in_transaction(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    session_id: str,
    proposal_run_id: str,
    client: AgentClient,
    application_mode: ApplicationMode,
    allow_new_link: bool,
) -> None:
    """Bind a proposal run to its exact running job inside proposal submit."""
    if not conn.in_transaction:
        raise AgentJobConflict("proposal run link requires an active transaction")
    row = conn.execute(
        "SELECT client, application_mode, state, session_id, proposal_run_id "
        "FROM agent_classification_job WHERE id = ?",
        (job_id,),
    ).fetchone()
    if row is None or row["state"] != "running":
        raise AgentJobConflict("classification job is missing or is not running")
    if row["client"] != client:
        raise AgentJobConflict("classification job client does not match proposal producer")
    if row["application_mode"] != application_mode:
        raise AgentJobConflict("classification job application mode does not match proposal")
    if row["session_id"] != session_id:
        raise AgentJobConflict("classification job does not belong to this MCP session")
    linked = cast(str | None, row["proposal_run_id"])
    if linked is not None:
        if linked != proposal_run_id:
            raise AgentJobConflict("classification job already names another proposal run")
        return
    if not allow_new_link:
        raise AgentJobConflict("existing proposal run was not created by this job")
    try:
        changed = conn.execute(
            "UPDATE agent_classification_job SET proposal_run_id = ? "
            "WHERE id = ? AND state = 'running' AND proposal_run_id IS NULL",
            (proposal_run_id, job_id),
        )
    except sqlite3.IntegrityError as error:
        raise AgentJobConflict("proposal run already belongs to another job") from error
    if changed.rowcount != 1:
        raise AgentJobConflict("classification job changed before proposal attribution")


def claim_next_job(
    conn: sqlite3.Connection,
    *,
    now: str | None = None,
) -> AgentJob | None:
    """Claim the oldest queued job only while no other job is running."""
    timestamp = _timestamp(now)
    with transaction(conn):
        running = conn.execute(
            "SELECT 1 FROM agent_classification_job WHERE state = 'running'"
        ).fetchone()
        if running is not None:
            return None
        row = conn.execute(
            "SELECT id FROM agent_classification_job WHERE state = 'queued' "
            "ORDER BY queued_at, rowid LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        job_id = str(row["id"])
        changed = conn.execute(
            "UPDATE agent_classification_job SET state = 'running', started_at = ? "
            "WHERE id = ? AND state = 'queued'",
            (timestamp, job_id),
        )
        if changed.rowcount != 1:
            raise AgentJobConflict("queued job changed before it could be claimed")
        claimed = get_job(conn, job_id)
        if claimed is None:  # pragma: no cover - the update cannot delete it.
            raise AgentJobConflict("claimed job could not be read back")
        return claimed


def _success_counts(
    *,
    candidate_count: int,
    submitted_count: int,
    applied_count: int,
    omitted_count: int,
) -> None:
    values = (candidate_count, submitted_count, applied_count, omitted_count)
    if any(type(value) is not int or value < 0 for value in values):
        raise AgentJobConflict("job counts must be non-negative integers")
    if submitted_count + omitted_count != candidate_count:
        raise AgentJobConflict("submitted plus omitted must equal candidate count")
    if applied_count > submitted_count:
        raise AgentJobConflict("applied count cannot exceed submitted count")


def finish_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    candidate_count: int,
    submitted_count: int,
    applied_count: int,
    omitted_count: int,
    client_outcome: ClientOutcome | None = None,
    client_exit_code: int | None = None,
    client_log_excerpt: str | None = None,
    now: str | None = None,
) -> AgentJob:
    """Finish one running job with exhaustive aggregate accounting.

    Work that was committed before the client stopped is real and is kept, even
    when the client was killed at the timeout. The recorded client outcome is
    what keeps that case distinguishable from a considered abstention.
    """
    _success_counts(
        candidate_count=candidate_count,
        submitted_count=submitted_count,
        applied_count=applied_count,
        omitted_count=omitted_count,
    )
    outcome, exit_code, excerpt = _client_evidence(
        client_outcome, client_exit_code, client_log_excerpt
    )
    timestamp = _timestamp(now)
    state = "completed" if omitted_count == 0 else "partial"
    with transaction(conn):
        changed = conn.execute(
            "UPDATE agent_classification_job SET state = ?, candidate_count = ?, "
            "submitted_count = ?, applied_count = ?, omitted_count = ?, "
            "client_outcome = ?, client_exit_code = ?, client_log_excerpt = ?, "
            "finished_at = ? WHERE id = ? AND state = 'running'",
            (
                state,
                candidate_count,
                submitted_count,
                applied_count,
                omitted_count,
                outcome,
                exit_code,
                excerpt,
                timestamp,
                job_id,
            ),
        )
        if changed.rowcount != 1:
            raise AgentJobConflict("job is missing or is not running")
        finished = get_job(conn, job_id)
        if finished is None:  # pragma: no cover - the update cannot delete it.
            raise AgentJobConflict("finished job could not be read back")
        return finished


def fail_job(
    conn: sqlite3.Connection,
    *,
    job_id: str,
    candidate_count: int,
    error_code: str,
    client_outcome: ClientOutcome | None = None,
    client_exit_code: int | None = None,
    client_log_excerpt: str | None = None,
    now: str | None = None,
) -> AgentJob:
    """Fail one running job and route every known candidate to omission."""
    if type(candidate_count) is not int or candidate_count < 0:
        raise AgentJobConflict("candidate_count must be a non-negative integer")
    if type(error_code) is not str or _ERROR_CODE.fullmatch(error_code) is None:
        raise AgentJobConflict("error_code must be lowercase letters, numbers, or underscore")
    outcome, exit_code, excerpt = _client_evidence(
        client_outcome, client_exit_code, client_log_excerpt
    )
    timestamp = _timestamp(now)
    with transaction(conn):
        changed = conn.execute(
            "UPDATE agent_classification_job SET state = 'failed', candidate_count = ?, "
            "submitted_count = 0, applied_count = 0, omitted_count = ?, "
            "error_code = ?, client_outcome = ?, client_exit_code = ?, "
            "client_log_excerpt = ?, finished_at = ? WHERE id = ? AND state = 'running'",
            (
                candidate_count,
                candidate_count,
                error_code,
                outcome,
                exit_code,
                excerpt,
                timestamp,
                job_id,
            ),
        )
        if changed.rowcount != 1:
            raise AgentJobConflict("job is missing or is not running")
        failed = get_job(conn, job_id)
        if failed is None:  # pragma: no cover - the update cannot delete it.
            raise AgentJobConflict("failed job could not be read back")
        return failed
