# SPDX-License-Identifier: AGPL-3.0-or-later
"""A7.4 persistent import-trigger job counterexamples."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest
from synth import Row, StatementBuilder

from ledgerbox.agent_center import update_policy
from ledgerbox.agent_jobs import (
    AgentJobConflict,
    claim_next_job,
    enqueue_import_job,
    fail_job,
    finish_job,
    get_job,
)
from ledgerbox.config import DataPaths
from ledgerbox.db.migrate import open_ledger
from ledgerbox.ingest import pipeline


@pytest.fixture
def db(git_free_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = open_ledger(git_free_tmp / "ledger.db")
    try:
        yield conn
    finally:
        conn.close()


def _source(conn: sqlite3.Connection, source_id: str) -> None:
    conn.execute(
        "INSERT INTO source_file "
        "(id, sha256, rel_path, media_type, byte_len, ingested_at) "
        "VALUES (?, ?, ?, 'application/pdf', 1, '2026-08-10T12:00:00+00:00')",
        (source_id, source_id, f"2026/08/{source_id}.pdf"),
    )


def _enable(
    conn: sqlite3.Connection,
    *,
    client: str = "codex",
    mode: str = "automatic",
    auto_imports: bool = True,
) -> None:
    update_policy(
        conn,
        selected_client=client,  # type: ignore[arg-type]
        application_mode=mode,  # type: ignore[arg-type]
        enabled=True,
        auto_classify_new_imports=auto_imports,
        acknowledge_provider_data_policy=True,
        now="2026-08-10T12:00:00+00:00",
    )


def _synthetic_document(*, ending: str = "$90.00") -> object:
    return StatementBuilder(
        period="January 01, 2025 through January 31, 2025",
        beginning="$100.00",
        ending=ending,
        components=(("Deposits and Additions", "0.00"), ("Fees", "-10.00")),
        rows=[Row("01/15", "Synthetic Monthly Service Fee", "-10.00", "$90.00")],
    ).build()


def _synthetic_pdf(root: Path, name: str) -> Path:
    source = root / name
    source.write_bytes(b"%PDF-1.7\n% synthetic classification trigger fixture\n")
    return source


def _raise_job_error(*args: object, **kwargs: object) -> NoReturn:
    raise AgentJobConflict("injected queue failure")


def test_disabled_or_opted_out_policy_queues_nothing(db: sqlite3.Connection) -> None:
    _source(db, "disabled")

    assert enqueue_import_job(db, source_file_id="disabled") is None
    _enable(db, auto_imports=False)
    assert enqueue_import_job(db, source_file_id="disabled") is None
    assert db.execute("SELECT COUNT(*) FROM agent_classification_job").fetchone()[0] == 0


def test_one_import_queues_once_with_a_policy_snapshot(db: sqlite3.Connection) -> None:
    _source(db, "one")
    _enable(db, client="claude-code", mode="review_first")

    first = enqueue_import_job(
        db,
        source_file_id="one",
        now="2026-08-10T12:01:00+00:00",
    )
    repeated = enqueue_import_job(
        db,
        source_file_id="one",
        now="2026-08-10T12:02:00+00:00",
    )

    assert first is not None and first.created is True
    assert repeated is not None and repeated.created is False
    assert repeated.job.id == first.job.id
    assert (first.job.client, first.job.application_mode, first.job.state) == (
        "claude-code",
        "review_first",
        "queued",
    )
    assert db.execute("SELECT COUNT(*) FROM agent_classification_job").fetchone()[0] == 1


def test_unknown_source_refuses_without_a_job(db: sqlite3.Connection) -> None:
    _enable(db)

    with pytest.raises(AgentJobConflict, match="source"):
        enqueue_import_job(db, source_file_id="missing")

    assert db.execute("SELECT COUNT(*) FROM agent_classification_job").fetchone()[0] == 0


def test_claim_serializes_jobs_and_completion_exposes_all_four_counts(
    db: sqlite3.Connection,
) -> None:
    _enable(db)
    _source(db, "first")
    _source(db, "second")
    first = enqueue_import_job(db, source_file_id="first")
    second = enqueue_import_job(db, source_file_id="second")
    assert first is not None and second is not None

    claimed = claim_next_job(db, now="2026-08-10T12:03:00+00:00")
    assert claimed is not None and claimed.id == first.job.id
    assert claim_next_job(db, now="2026-08-10T12:03:01+00:00") is None

    finished = finish_job(
        db,
        job_id=claimed.id,
        candidate_count=5,
        submitted_count=3,
        applied_count=3,
        omitted_count=2,
        now="2026-08-10T12:04:00+00:00",
    )
    assert finished.state == "partial"
    assert (
        finished.candidate_count,
        finished.submitted_count,
        finished.applied_count,
        finished.omitted_count,
    ) == (5, 3, 3, 2)
    assert claim_next_job(db, now="2026-08-10T12:04:01+00:00").id == second.job.id  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "candidate,submitted,applied,omitted",
    [
        (4, 3, 3, 0),
        (4, 3, 4, 1),
        (4, 3, 3, 2),
        (-1, 0, 0, -1),
    ],
)
def test_invalid_success_counts_leave_running_job_unchanged(
    db: sqlite3.Connection,
    candidate: int,
    submitted: int,
    applied: int,
    omitted: int,
) -> None:
    _enable(db)
    _source(db, "invalid")
    queued = enqueue_import_job(db, source_file_id="invalid")
    assert queued is not None
    claim_next_job(db)

    with pytest.raises(AgentJobConflict):
        finish_job(
            db,
            job_id=queued.job.id,
            candidate_count=candidate,
            submitted_count=submitted,
            applied_count=applied,
            omitted_count=omitted,
        )

    unchanged = get_job(db, queued.job.id)
    assert unchanged is not None
    assert unchanged.state == "running"
    assert unchanged.candidate_count is None


def test_failure_routes_every_known_candidate_to_omitted(db: sqlite3.Connection) -> None:
    _enable(db)
    _source(db, "failed")
    queued = enqueue_import_job(db, source_file_id="failed")
    assert queued is not None
    claim_next_job(db)

    failed = fail_job(
        db,
        job_id=queued.job.id,
        candidate_count=7,
        error_code="client_exit",
        now="2026-08-10T12:05:00+00:00",
    )

    assert failed.state == "failed"
    assert (failed.submitted_count, failed.applied_count, failed.omitted_count) == (0, 0, 7)
    assert failed.error_code == "client_exit"


def test_terminal_or_unknown_job_cannot_be_finished_twice(db: sqlite3.Connection) -> None:
    _enable(db)
    _source(db, "terminal")
    queued = enqueue_import_job(db, source_file_id="terminal")
    assert queued is not None
    claim_next_job(db)
    finish_job(
        db,
        job_id=queued.job.id,
        candidate_count=0,
        submitted_count=0,
        applied_count=0,
        omitted_count=0,
    )

    with pytest.raises(AgentJobConflict, match="running"):
        fail_job(db, job_id=queued.job.id, candidate_count=0, error_code="late_failure")
    with pytest.raises(AgentJobConflict, match="running"):
        finish_job(
            db,
            job_id="missing",
            candidate_count=0,
            submitted_count=0,
            applied_count=0,
            omitted_count=0,
        )


def test_successful_ingest_atomically_queues_one_job_and_duplicate_queues_none(
    db: sqlite3.Connection,
    git_free_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = DataPaths.resolve(git_free_tmp / "data")
    source = _synthetic_pdf(git_free_tmp, "successful.pdf")
    monkeypatch.setattr(pipeline, "extract_spans", lambda _path: _synthetic_document())
    _enable(db, mode="automatic")

    first = pipeline.ingest_file(db, paths, source)
    repeated = pipeline.ingest_file(db, paths, source)

    assert first.status == pipeline.IMPORTED
    assert first.agent_job_queued is True
    assert repeated.status == pipeline.DUPLICATE
    assert repeated.agent_job_queued is False
    rows = db.execute(
        "SELECT trigger_source_file_id, client, application_mode, state "
        "FROM agent_classification_job"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (first.sha256, "codex", "automatic", "queued")
    ]


def test_refused_ingest_never_queues_a_job(
    db: sqlite3.Connection,
    git_free_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = DataPaths.resolve(git_free_tmp / "data")
    source = _synthetic_pdf(git_free_tmp, "refused.pdf")
    monkeypatch.setattr(
        pipeline,
        "extract_spans",
        lambda _path: _synthetic_document(ending="$91.00"),
    )
    _enable(db)

    outcome = pipeline.ingest_file(db, paths, source)

    assert outcome.status == pipeline.NEEDS_REVIEW
    assert outcome.agent_job_queued is False
    assert db.execute("SELECT COUNT(*) FROM agent_classification_job").fetchone()[0] == 0


def test_job_queue_failure_rolls_back_all_import_database_writes(
    db: sqlite3.Connection,
    git_free_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = DataPaths.resolve(git_free_tmp / "data")
    source = _synthetic_pdf(git_free_tmp, "atomic.pdf")
    monkeypatch.setattr(pipeline, "extract_spans", lambda _path: _synthetic_document())
    monkeypatch.setattr(
        pipeline,
        "enqueue_import_job_in_transaction",
        _raise_job_error,
        raising=False,
    )
    _enable(db)

    with pytest.raises(AgentJobConflict, match="injected"):
        pipeline.ingest_file(db, paths, source)

    assert db.execute("SELECT COUNT(*) FROM source_file").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM txn").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM agent_classification_job").fetchone()[0] == 0
