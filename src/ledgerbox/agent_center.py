# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strict local policy and aggregate-only MCP session evidence for A7.3."""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast

from .db.connection import transaction

AgentClient = Literal["codex", "claude-code"]
ApplicationMode = Literal["review_first", "automatic"]
ResultState = Literal["completed", "partial", "failed"]

CLIENTS = frozenset({"codex", "claude-code"})
APPLICATION_MODES = frozenset({"review_first", "automatic"})
RESULT_STATES = frozenset({"completed", "partial", "failed"})
DEFAULT_STALE_AFTER_SECONDS = 30
_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,64}$")


class AgentCenterConflict(RuntimeError):
    """A local policy or session update was malformed or no longer applicable."""


@dataclass(frozen=True, slots=True)
class AgentPolicy:
    selected_client: AgentClient | None
    application_mode: ApplicationMode
    enabled: bool
    auto_classify_new_imports: bool
    updated_at: str


@dataclass(frozen=True, slots=True)
class AgentClientActivity:
    client: AgentClient
    session_active: bool
    last_seen_at: str | None
    last_result: ResultState | None
    result_at: str | None
    candidate_count: int | None
    submitted_count: int | None
    error_code: str | None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _as_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def read_policy(conn: sqlite3.Connection) -> AgentPolicy:
    row = conn.execute(
        "SELECT selected_client, application_mode, enabled, "
        "auto_classify_new_imports, updated_at FROM agent_local_policy WHERE id = 1"
    ).fetchone()
    if row is None:
        raise AgentCenterConflict("agent local policy row is missing")
    return AgentPolicy(
        selected_client=(
            None if row["selected_client"] is None else cast(AgentClient, row["selected_client"])
        ),
        application_mode=cast(ApplicationMode, row["application_mode"]),
        enabled=bool(row["enabled"]),
        auto_classify_new_imports=bool(row["auto_classify_new_imports"]),
        updated_at=str(row["updated_at"]),
    )


def update_policy(
    conn: sqlite3.Connection,
    *,
    selected_client: AgentClient | None,
    application_mode: ApplicationMode,
    enabled: bool,
    auto_classify_new_imports: bool,
    acknowledge_provider_data_policy: bool,
    now: str | None = None,
) -> AgentPolicy:
    """Replace the whole policy or leave the previous row unchanged."""
    if selected_client is not None and (
        type(selected_client) is not str or selected_client not in CLIENTS
    ):
        raise AgentCenterConflict("selected_client must be codex, claude-code, or null")
    if type(application_mode) is not str or application_mode not in APPLICATION_MODES:
        raise AgentCenterConflict("application_mode must be review_first or automatic")
    if type(enabled) is not bool:
        raise AgentCenterConflict("enabled must be a boolean")
    if type(auto_classify_new_imports) is not bool:
        raise AgentCenterConflict("auto_classify_new_imports must be a boolean")
    if type(acknowledge_provider_data_policy) is not bool:
        raise AgentCenterConflict("acknowledge_provider_data_policy must be a boolean")
    if enabled and selected_client is None:
        raise AgentCenterConflict("enabled policy requires selected_client")
    if enabled and not acknowledge_provider_data_policy:
        raise AgentCenterConflict("enabling requires acknowledge_provider_data_policy")

    timestamp = now or _utc_now()
    try:
        _as_datetime(timestamp)
    except ValueError as error:
        raise AgentCenterConflict("now must be an ISO timestamp with a timezone") from error

    with transaction(conn):
        cursor = conn.execute(
            "UPDATE agent_local_policy SET selected_client = ?, application_mode = ?, "
            "enabled = ?, auto_classify_new_imports = ?, updated_at = ? WHERE id = 1",
            (
                selected_client,
                application_mode,
                int(enabled),
                int(auto_classify_new_imports),
                timestamp,
            ),
        )
        if cursor.rowcount != 1:
            raise AgentCenterConflict("agent local policy row is missing")
    return read_policy(conn)


def start_session(
    conn: sqlite3.Connection,
    *,
    client: AgentClient,
    session_id: str | None = None,
    job_id: str | None = None,
    now: str | None = None,
) -> str:
    if type(client) is not str or client not in CLIENTS:
        raise AgentCenterConflict("client must be codex or claude-code")
    identifier = session_id or f"session-{uuid.uuid4().hex}"
    if type(identifier) is not str or not identifier or len(identifier) > 128:
        raise AgentCenterConflict("session_id must be a non-empty string of at most 128 characters")
    if job_id is not None and (type(job_id) is not str or not job_id or len(job_id) > 128):
        raise AgentCenterConflict("job_id must be a non-empty string of at most 128 characters")
    timestamp = now or _utc_now()
    try:
        _as_datetime(timestamp)
    except ValueError as error:
        raise AgentCenterConflict("now must be an ISO timestamp with a timezone") from error
    try:
        with transaction(conn):
            if job_id is not None:
                job = conn.execute(
                    "SELECT client, state, session_id FROM agent_classification_job WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if job is None or job["state"] != "running":
                    raise AgentCenterConflict("job is missing or is not running")
                if job["client"] != client:
                    raise AgentCenterConflict("job client does not match the MCP client")
                if job["session_id"] is not None:
                    raise AgentCenterConflict("job already belongs to an MCP session")
            conn.execute(
                "INSERT INTO agent_local_session "
                "(id, client, started_at, last_seen_at) VALUES (?, ?, ?, ?)",
                (identifier, client, timestamp, timestamp),
            )
            if job_id is not None:
                changed = conn.execute(
                    "UPDATE agent_classification_job SET session_id = ? "
                    "WHERE id = ? AND state = 'running' AND session_id IS NULL",
                    (identifier, job_id),
                )
                if changed.rowcount != 1:
                    raise AgentCenterConflict("job changed before the MCP session could bind")
    except sqlite3.IntegrityError as error:
        raise AgentCenterConflict(
            "session_id already exists or session fields are invalid"
        ) from error
    return identifier


def heartbeat_session(
    conn: sqlite3.Connection, *, session_id: str, now: str | None = None
) -> None:
    timestamp = now or _utc_now()
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE agent_local_session SET last_seen_at = ? "
            "WHERE id = ? AND ended_at IS NULL",
            (timestamp, session_id),
        )
        if cursor.rowcount != 1:
            raise AgentCenterConflict("session is missing or already ended")


def end_session(conn: sqlite3.Connection, *, session_id: str, now: str | None = None) -> None:
    timestamp = now or _utc_now()
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE agent_local_session SET last_seen_at = ?, ended_at = ? "
            "WHERE id = ? AND ended_at IS NULL",
            (timestamp, timestamp, session_id),
        )
        if cursor.rowcount != 1:
            raise AgentCenterConflict("session is missing or already ended")


def record_session_result(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    result_state: ResultState,
    candidate_count: int | None,
    submitted_count: int | None,
    error_code: str | None,
    now: str | None = None,
) -> None:
    if type(result_state) is not str or result_state not in RESULT_STATES:
        raise AgentCenterConflict("result_state must be completed, partial, or failed")
    if result_state == "completed":
        valid = (
            type(candidate_count) is int
            and candidate_count >= 0
            and submitted_count == candidate_count
            and error_code is None
        )
    elif result_state == "partial":
        valid = (
            type(candidate_count) is int
            and type(submitted_count) is int
            and 0 <= submitted_count < candidate_count
            and error_code is None
        )
    else:
        valid = (
            candidate_count is None
            and submitted_count is None
            and type(error_code) is str
            and _ERROR_CODE.fullmatch(error_code) is not None
        )
    if not valid:
        raise AgentCenterConflict("session result fields do not match result_state")

    timestamp = now or _utc_now()
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE agent_local_session SET result_state = ?, result_at = ?, "
            "candidate_count = ?, submitted_count = ?, error_code = ? "
            "WHERE id = ? AND ended_at IS NULL",
            (
                result_state,
                timestamp,
                candidate_count,
                submitted_count,
                error_code,
                session_id,
            ),
        )
        if cursor.rowcount != 1:
            raise AgentCenterConflict("session is missing or already ended")


def read_client_activity(
    conn: sqlite3.Connection,
    *,
    client: AgentClient,
    now: str | None = None,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
) -> AgentClientActivity:
    if type(client) is not str or client not in CLIENTS:
        raise AgentCenterConflict("client must be codex or claude-code")
    if type(stale_after_seconds) is not int or stale_after_seconds <= 0:
        raise AgentCenterConflict("stale_after_seconds must be a positive integer")
    timestamp = now or _utc_now()
    current = _as_datetime(timestamp)
    latest = conn.execute(
        "SELECT last_seen_at, ended_at FROM agent_local_session "
        "WHERE client = ? ORDER BY last_seen_at DESC, id DESC LIMIT 1",
        (client,),
    ).fetchone()
    last_seen_at = None if latest is None else str(latest["last_seen_at"])
    active_rows = conn.execute(
        "SELECT last_seen_at FROM agent_local_session "
        "WHERE client = ? AND ended_at IS NULL",
        (client,),
    ).fetchall()
    session_active = False
    for row in active_rows:
        try:
            age = (current - _as_datetime(str(row["last_seen_at"]))).total_seconds()
            if 0 <= age <= stale_after_seconds:
                session_active = True
                break
        except ValueError:
            continue

    result = conn.execute(
        "SELECT result_state, result_at, candidate_count, submitted_count, error_code "
        "FROM agent_local_session WHERE client = ? AND result_state <> 'none' "
        "ORDER BY result_at DESC, id DESC LIMIT 1",
        (client,),
    ).fetchone()
    return AgentClientActivity(
        client=client,
        session_active=session_active,
        last_seen_at=last_seen_at,
        last_result=(None if result is None else cast(ResultState, result["result_state"])),
        result_at=None if result is None else str(result["result_at"]),
        candidate_count=(None if result is None else cast(int | None, result["candidate_count"])),
        submitted_count=(None if result is None else cast(int | None, result["submitted_count"])),
        error_code=None if result is None else cast(str | None, result["error_code"]),
    )
