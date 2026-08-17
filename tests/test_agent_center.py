# SPDX-License-Identifier: AGPL-3.0-or-later
"""A7.3 Core counterexamples for local policy and honest MCP activity."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from ledgerbox.agent_center import (
    AgentCenterConflict,
    end_session,
    read_client_activity,
    read_policy,
    record_session_result,
    start_session,
    update_policy,
)
from ledgerbox.agent_jobs import claim_next_job, enqueue_import_job, get_job
from ledgerbox.db.migrate import open_ledger


@pytest.fixture
def db(git_free_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = open_ledger(git_free_tmp / "ledger.db")
    try:
        yield conn
    finally:
        conn.close()


def _policy_tuple(conn: sqlite3.Connection) -> tuple[object, ...]:
    policy = read_policy(conn)
    return (
        policy.selected_client,
        policy.application_mode,
        policy.enabled,
        policy.auto_classify_new_imports,
    )


def _running_job(conn: sqlite3.Connection, *, client: str = "codex") -> str:
    conn.execute(
        "INSERT INTO source_file "
        "(id, sha256, rel_path, media_type, byte_len, ingested_at) "
        "VALUES ('job-source', 'job-source', '2026/08/job-source.pdf', "
        "'application/pdf', 1, '2026-08-10T12:00:00+00:00')"
    )
    update_policy(
        conn,
        selected_client=client,  # type: ignore[arg-type]
        application_mode="automatic",
        enabled=True,
        auto_classify_new_imports=True,
        acknowledge_provider_data_policy=True,
        now="2026-08-10T12:00:00+00:00",
    )
    queued = enqueue_import_job(conn, source_file_id="job-source")
    assert queued is not None
    claimed = claim_next_job(conn)
    assert claimed is not None
    return claimed.id


def test_policy_defaults_disconnected_but_keeps_approved_post_connection_defaults(
    db: sqlite3.Connection,
) -> None:
    assert _policy_tuple(db) == (None, "automatic", False, True)


@pytest.mark.parametrize(
    "changes,match",
    [
        ({"selected_client": None, "enabled": True}, "selected_client"),
        ({"selected_client": "other"}, "selected_client"),
        ({"application_mode": "automatic-ish"}, "application_mode"),
        ({"enabled": 1}, "enabled"),
        ({"auto_classify_new_imports": 1}, "auto_classify_new_imports"),
    ],
)
def test_invalid_policy_is_rejected_as_one_unchanged_write(
    db: sqlite3.Connection,
    changes: dict[str, object],
    match: str,
) -> None:
    before = _policy_tuple(db)
    values: dict[str, object] = {
        "selected_client": "codex",
        "application_mode": "review_first",
        "enabled": False,
        "auto_classify_new_imports": False,
        "acknowledge_provider_data_policy": False,
    }
    values.update(changes)

    with pytest.raises(AgentCenterConflict, match=match):
        update_policy(db, **values)  # type: ignore[arg-type]

    assert _policy_tuple(db) == before


def test_enabling_requires_explicit_provider_data_acknowledgement(
    db: sqlite3.Connection,
) -> None:
    before = _policy_tuple(db)

    with pytest.raises(AgentCenterConflict, match="acknowledge"):
        update_policy(
            db,
            selected_client="claude-code",
            application_mode="automatic",
            enabled=True,
            auto_classify_new_imports=True,
            acknowledge_provider_data_policy=False,
        )

    assert _policy_tuple(db) == before


def test_valid_policy_persists_and_disconnect_preserves_the_user_choices(
    db: sqlite3.Connection,
) -> None:
    update_policy(
        db,
        selected_client="claude-code",
        application_mode="review_first",
        enabled=True,
        auto_classify_new_imports=False,
        acknowledge_provider_data_policy=True,
        now="2026-08-10T12:00:00+00:00",
    )
    assert _policy_tuple(db) == ("claude-code", "review_first", True, False)

    update_policy(
        db,
        selected_client="claude-code",
        application_mode="review_first",
        enabled=False,
        auto_classify_new_imports=False,
        acknowledge_provider_data_policy=False,
        now="2026-08-10T12:01:00+00:00",
    )
    assert _policy_tuple(db) == ("claude-code", "review_first", False, False)


def test_session_activity_distinguishes_active_stale_and_cleanly_ended(
    db: sqlite3.Connection,
) -> None:
    session_id = start_session(
        db,
        client="codex",
        session_id="synthetic-session",
        now="2026-08-10T12:00:00+00:00",
    )

    active = read_client_activity(
        db,
        client="codex",
        now="2026-08-10T12:00:20+00:00",
        stale_after_seconds=30,
    )
    stale = read_client_activity(
        db,
        client="codex",
        now="2026-08-10T12:00:31+00:00",
        stale_after_seconds=30,
    )
    assert active.session_active is True
    assert stale.session_active is False
    assert active.last_seen_at == "2026-08-10T12:00:00+00:00"

    end_session(db, session_id=session_id, now="2026-08-10T12:00:25+00:00")
    ended = read_client_activity(
        db,
        client="codex",
        now="2026-08-10T12:00:26+00:00",
        stale_after_seconds=30,
    )
    assert ended.session_active is False


def test_newer_ended_session_does_not_hide_an_older_still_active_session(
    db: sqlite3.Connection,
) -> None:
    start_session(
        db,
        client="codex",
        session_id="still-active",
        now="2026-08-10T12:00:10+00:00",
    )
    ended_id = start_session(
        db,
        client="codex",
        session_id="newer-ended",
        now="2026-08-10T12:00:20+00:00",
    )
    end_session(db, session_id=ended_id, now="2026-08-10T12:00:25+00:00")

    activity = read_client_activity(
        db,
        client="codex",
        now="2026-08-10T12:00:26+00:00",
        stale_after_seconds=30,
    )

    assert activity.session_active is True
    assert activity.last_seen_at == "2026-08-10T12:00:25+00:00"


def test_job_session_starts_and_binds_as_one_write(db: sqlite3.Connection) -> None:
    job_id = _running_job(db)

    session_id = start_session(
        db,
        client="codex",
        session_id="job-session",
        job_id=job_id,
        now="2026-08-10T12:00:10+00:00",
    )

    job = get_job(db, job_id)
    assert job is not None and job.session_id == session_id


def test_wrong_client_job_session_is_zero_write(db: sqlite3.Connection) -> None:
    job_id = _running_job(db, client="codex")

    with pytest.raises(AgentCenterConflict, match="client"):
        start_session(
            db,
            client="claude-code",
            session_id="wrong-client",
            job_id=job_id,
            now="2026-08-10T12:00:10+00:00",
        )

    assert db.execute("SELECT COUNT(*) FROM agent_local_session").fetchone()[0] == 0
    job = get_job(db, job_id)
    assert job is not None and job.session_id is None


@pytest.mark.parametrize(
    "candidate_count,submitted_count,result_state,error_code",
    [
        (2, 2, "completed", None),
        (3, 2, "partial", None),
        (None, None, "failed", "proposal_conflict"),
    ],
)
def test_session_result_states_are_persisted_without_private_details(
    db: sqlite3.Connection,
    candidate_count: int | None,
    submitted_count: int | None,
    result_state: str,
    error_code: str | None,
) -> None:
    session_id = start_session(
        db,
        client="claude-code",
        session_id=f"synthetic-{result_state}",
        now="2026-08-10T12:00:00+00:00",
    )
    record_session_result(
        db,
        session_id=session_id,
        result_state=result_state,  # type: ignore[arg-type]
        candidate_count=candidate_count,
        submitted_count=submitted_count,
        error_code=error_code,
        now="2026-08-10T12:00:10+00:00",
    )

    activity = read_client_activity(
        db,
        client="claude-code",
        now="2026-08-10T12:00:11+00:00",
    )
    assert activity.last_result == result_state
    assert activity.candidate_count == candidate_count
    assert activity.submitted_count == submitted_count
    assert activity.error_code == error_code


@pytest.mark.parametrize(
    "result_state,candidate_count,submitted_count,error_code",
    [
        ("partial", 2, 2, None),
        ("completed", 3, 2, None),
        ("failed", None, None, None),
        ("running", None, None, None),
    ],
)
def test_invalid_session_result_is_zero_write(
    db: sqlite3.Connection,
    result_state: str,
    candidate_count: int | None,
    submitted_count: int | None,
    error_code: str | None,
) -> None:
    session_id = start_session(
        db,
        client="codex",
        session_id="synthetic-invalid-result",
        now="2026-08-10T12:00:00+00:00",
    )

    with pytest.raises(AgentCenterConflict):
        record_session_result(
            db,
            session_id=session_id,
            result_state=result_state,  # type: ignore[arg-type]
            candidate_count=candidate_count,
            submitted_count=submitted_count,
            error_code=error_code,
            now="2026-08-10T12:00:10+00:00",
        )

    activity = read_client_activity(db, client="codex", now="2026-08-10T12:00:11+00:00")
    assert activity.last_result is None
