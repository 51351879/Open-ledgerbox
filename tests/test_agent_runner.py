# SPDX-License-Identifier: AGPL-3.0-or-later
"""A7.4 bounded local-client runner counterexamples; no real model is started."""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from ledgerbox.agent_center import end_session, record_session_result, start_session, update_policy
from ledgerbox.agent_jobs import (
    MAX_CLASSIFICATION_ROUNDS,
    MAX_CLIENT_LOG_CHARS,
    MAX_CONSECUTIVE_FAILED_ROUNDS,
    AgentJob,
    AgentJobConflict,
    claim_next_job,
    enqueue_followup_job,
    enqueue_manual_job,
    fail_job,
    finish_job,
    get_job,
    link_job_proposal_run_in_transaction,
    read_job_log,
    read_latest_batch,
)
from ledgerbox.agent_runner import drain_jobs, run_next_job
from ledgerbox.agent_workspace import AgentWorkspaceMissing, agent_workspace_root
from ledgerbox.cli import cmd_ingest
from ledgerbox.config import DataPaths
from ledgerbox.db.connection import transaction
from ledgerbox.db.migrate import open_ledger
from ledgerbox.ingest.pipeline import IMPORTED, IngestOutcome


@pytest.fixture
def runner_ledger(git_free_tmp: Path) -> Iterator[tuple[DataPaths, sqlite3.Connection]]:
    paths = DataPaths.resolve(git_free_tmp / "runner-data")
    conn = open_ledger(paths.db)
    try:
        yield paths, conn
    finally:
        conn.close()


def _queue(
    conn: sqlite3.Connection,
    *,
    client: str = "codex",
    mode: str = "automatic",
) -> str:
    conn.execute(
        "INSERT INTO source_file "
        "(id, sha256, rel_path, media_type, byte_len, ingested_at) "
        "VALUES ('runner-source', 'runner-source', '2026/08/runner.pdf', "
        "'application/pdf', 1, '2026-08-10T12:00:00+00:00')"
    )
    update_policy(
        conn,
        selected_client=client,  # type: ignore[arg-type]
        application_mode=mode,  # type: ignore[arg-type]
        enabled=True,
        auto_classify_new_imports=True,
        acknowledge_provider_data_policy=True,
        now="2026-08-10T12:00:00+00:00",
    )
    from ledgerbox.agent_jobs import enqueue_import_job

    queued = enqueue_import_job(conn, source_file_id="runner-source")
    assert queued is not None
    return queued.job.id


def _persist_mcp_success(
    paths: DataPaths,
    command: list[str],
    *,
    job_id: str,
    client: str,
    mode: str,
    exit_code: int = 0,
    record_result: bool = True,
) -> subprocess.CompletedProcess[str]:
    conn = open_ledger(paths.db)
    try:
        session_id = start_session(
            conn,
            client=client,  # type: ignore[arg-type]
            session_id=f"session-{client}",
            job_id=job_id,
            now="2026-08-10T12:00:10+00:00",
        )
        run_id = "sha256:" + "9" * 64
        revision = "sha256:" + "7" * 64
        group_id = "sha256:" + "6" * 64
        with transaction(conn):
            conn.execute(
                "INSERT INTO category (id, parent_id, kind) "
                "VALUES ('synthetic-runner-category', NULL, 'expense')"
            )
            for index in range(2):
                conn.execute(
                    "INSERT INTO txn (id, date, flag, is_transfer, created_at) "
                    "VALUES (?, '2026-08-10', '*', 0, '2026-08-10T12:00:00+00:00')",
                    (f"runner-txn-{index}",),
                )
            conn.execute(
                "INSERT INTO agent_proposal_run "
                "(id, ledger_revision, schema_version, application_mode, client, "
                "created_at, state) "
                "VALUES (?, ?, 2, ?, ?, '2026-08-10T12:00:11+00:00', ?)",
                (run_id, revision, mode, client, "completed" if mode == "automatic" else "open"),
            )
            for index in range(2):
                conn.execute(
                    "INSERT INTO agent_category_proposal "
                    "(run_id, txn_id, group_id, suggested_category_id, outcome, "
                    "applied_category_id, reviewed_at) VALUES (?, ?, ?, "
                    "'synthetic-runner-category', ?, ?, ?)",
                    (
                        run_id,
                        f"runner-txn-{index}",
                        group_id,
                        "accepted" if mode == "automatic" else "pending",
                        "synthetic-runner-category" if mode == "automatic" else None,
                        "2026-08-10T12:00:11+00:00" if mode == "automatic" else None,
                    ),
                )
            link_job_proposal_run_in_transaction(
                conn,
                job_id=job_id,
                session_id=session_id,
                proposal_run_id=run_id,
                client=client,  # type: ignore[arg-type]
                application_mode=mode,  # type: ignore[arg-type]
                allow_new_link=True,
            )
        if record_result:
            record_session_result(
                conn,
                session_id=session_id,
                result_state="partial",
                candidate_count=3,
                submitted_count=2,
                error_code=None,
                now="2026-08-10T12:00:12+00:00",
            )
        end_session(conn, session_id=session_id, now="2026-08-10T12:00:13+00:00")
    finally:
        conn.close()
    return subprocess.CompletedProcess(command, exit_code)


@pytest.mark.parametrize(
    "client,mode,expected_applied",
    [("codex", "automatic", 2), ("claude-code", "review_first", 0)],
)
def test_runner_uses_one_strict_job_scoped_client_and_finishes_from_durable_evidence(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
    client: str,
    mode: str,
    expected_applied: int,
) -> None:
    paths, conn = runner_ledger
    job_id = _queue(conn, client=client, mode=mode)
    seen: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.STDOUT
        assert kwargs["cwd"] == agent_workspace_root()
        return _persist_mcp_success(
            paths,
            command,
            job_id=job_id,
            client=client,
            mode=mode,
        )

    monkeypatch.setattr("ledgerbox.agent_runner._current_candidate_count", lambda *_: 3)
    monkeypatch.setattr("ledgerbox.agent_runner.subprocess.run", fake_run)

    result = run_next_job(paths)

    assert result is not None and result.id == job_id and result.state == "partial"
    assert (result.candidate_count, result.submitted_count) == (3, 2)
    assert (result.applied_count, result.omitted_count) == (expected_applied, 1)
    assert len(seen) == 1
    command = seen[0]
    assert any(job_id in argument for argument in command)
    if client == "codex":
        assert {"--ephemeral", "--ignore-user-config", "read-only"} <= set(command)
        assert command[command.index("--cd") + 1] == str(agent_workspace_root())
    else:
        assert {"--strict-mcp-config", "--no-session-persistence", "dontAsk"} <= set(command)
        assert command[-2] == "--", (
            "Claude's variadic --allowedTools option must not consume the operation prompt"
        )
        assert command[-1].startswith("/ledgerbox classify")


def test_missing_client_fails_closed_and_releases_the_running_slot(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, conn = runner_ledger
    job_id = _queue(conn)

    def missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("synthetic missing client")

    monkeypatch.setattr("ledgerbox.agent_runner._current_candidate_count", lambda *_: 4)
    monkeypatch.setattr("ledgerbox.agent_runner.subprocess.run", missing)

    result = run_next_job(paths)

    assert result is not None and result.id == job_id and result.state == "failed"
    assert (result.candidate_count, result.submitted_count, result.applied_count) == (4, 0, 0)
    assert result.omitted_count == 4
    assert result.error_code == "client_not_found"
    assert get_job(conn, job_id) == result


def test_runner_resolves_the_client_shim_before_spawning_on_windows(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, conn = runner_ledger
    job_id = _queue(conn)
    resolved = r"C:\Users\synthetic\npm\codex.CMD"

    monkeypatch.setattr(
        "ledgerbox.agent_runner.shutil.which",
        lambda name: resolved if name == "codex" else None,
    )
    monkeypatch.setattr("ledgerbox.agent_runner._current_candidate_count", lambda *_: 1)

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[0] == resolved
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr("ledgerbox.agent_runner.subprocess.run", fake_run)

    result = run_next_job(paths)

    assert result is not None and result.id == job_id
    assert result.state == "failed" and result.error_code == "client_exit"


def test_missing_packaged_agent_workspace_fails_without_spawning_a_client(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, conn = runner_ledger
    job_id = _queue(conn)
    spawned: list[bool] = []

    def missing_workspace() -> Path:
        raise AgentWorkspaceMissing("synthetic missing packaged workspace")

    monkeypatch.setattr("ledgerbox.agent_runner.agent_workspace_root", missing_workspace)
    monkeypatch.setattr("ledgerbox.agent_runner._current_candidate_count", lambda *_: 1)
    monkeypatch.setattr(
        "ledgerbox.agent_runner.subprocess.run",
        lambda *_args, **_kwargs: spawned.append(True),
    )

    result = run_next_job(paths)

    assert spawned == []
    assert result is not None and result.id == job_id
    assert result.state == "failed" and result.error_code == "agent_workspace_missing"


def test_committed_proposal_run_wins_over_exit_code_and_missing_session_aggregate(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, conn = runner_ledger
    job_id = _queue(conn)

    def crashed(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return _persist_mcp_success(
            paths,
            command,
            job_id=job_id,
            client="codex",
            mode="automatic",
            exit_code=1,
            record_result=False,
        )

    monkeypatch.setattr("ledgerbox.agent_runner._current_candidate_count", lambda *_: 3)
    monkeypatch.setattr("ledgerbox.agent_runner.subprocess.run", crashed)

    result = run_next_job(paths)

    assert result is not None and result.state == "partial"
    assert (result.candidate_count, result.submitted_count) == (3, 2)
    assert (result.applied_count, result.omitted_count, result.error_code) == (2, 1, None)
    assert (result.client_outcome, result.client_exit_code) == ("exited", 1)


def test_a_timeout_after_a_real_submission_is_never_laundered_into_a_clean_finish(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client killed at the cap and one that deliberately abstained are not the same run.

    Both finish with work already committed, so both keep it. Only the recorded
    client outcome can tell a person which of the two they are looking at, and
    without it a truncated run reads as a considered decision.
    """
    paths, conn = runner_ledger
    job_id = _queue(conn)

    def timed_out(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        _persist_mcp_success(paths, command, job_id=job_id, client="codex", mode="automatic")
        raise subprocess.TimeoutExpired(command, 600, output="reading candidates\n")

    monkeypatch.setattr("ledgerbox.agent_runner._current_candidate_count", lambda *_: 3)
    monkeypatch.setattr("ledgerbox.agent_runner.subprocess.run", timed_out)

    result = run_next_job(paths)

    assert result is not None and result.state == "partial"
    assert (result.submitted_count, result.omitted_count) == (2, 1)
    assert result.error_code is None, "committed work is still real work"
    assert (result.client_outcome, result.client_exit_code) == ("timeout", None)
    assert read_job_log(conn, job_id) == "reading candidates\n"


def test_a_clean_run_records_its_exit_code_and_keeps_a_bounded_log_tail(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, conn = runner_ledger
    job_id = _queue(conn)
    noisy = "".join(f"line {index}\n" for index in range(20_000))

    def loud(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["stdout"] is subprocess.PIPE
        assert kwargs["stderr"] is subprocess.STDOUT
        _persist_mcp_success(paths, command, job_id=job_id, client="codex", mode="automatic")
        return subprocess.CompletedProcess(command, 0, stdout=noisy)

    monkeypatch.setattr("ledgerbox.agent_runner._current_candidate_count", lambda *_: 3)
    monkeypatch.setattr("ledgerbox.agent_runner.subprocess.run", loud)

    result = run_next_job(paths)

    assert result is not None and result.state == "partial"
    assert (result.client_outcome, result.client_exit_code) == ("exited", 0)
    log = read_job_log(conn, job_id)
    assert log is not None and len(log) <= MAX_CLIENT_LOG_CHARS
    assert log.endswith("line 19999\n"), "the tail is where a run says why it stopped"
    assert log.startswith("["), "a truncated log must say so rather than look complete"


def test_a_client_that_never_started_records_that_and_stores_no_log(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, conn = runner_ledger
    job_id = _queue(conn)

    def missing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("synthetic missing client")

    monkeypatch.setattr("ledgerbox.agent_runner._current_candidate_count", lambda *_: 4)
    monkeypatch.setattr("ledgerbox.agent_runner.subprocess.run", missing)

    result = run_next_job(paths)

    assert result is not None and result.state == "failed"
    assert result.error_code == "client_not_found"
    assert (result.client_outcome, result.client_exit_code) == ("not_found", None)
    assert read_job_log(conn, job_id) is None


def test_an_unknown_client_outcome_is_refused_rather_than_stored(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    paths, conn = runner_ledger
    job_id = _queue(conn)
    assert claim_next_job(conn) is not None

    with pytest.raises(AgentJobConflict):
        finish_job(
            conn,
            job_id=job_id,
            candidate_count=1,
            submitted_count=0,
            applied_count=0,
            omitted_count=1,
            client_outcome="exploded",  # type: ignore[arg-type]
        )
    reread = get_job(conn, job_id)
    assert reread is not None and reread.state == "running"
    assert paths.db.exists()


def _finished(
    conn: sqlite3.Connection,
    *,
    submitted: int,
    omitted: int,
    round_index: int = 1,
) -> AgentJob:
    """Drive one queued job to a terminal state with the given accounting."""
    claimed = claim_next_job(conn)
    assert claimed is not None
    conn.execute(
        "UPDATE agent_classification_job SET round_index = ? WHERE id = ?",
        (round_index, claimed.id),
    )
    return finish_job(
        conn,
        job_id=claimed.id,
        candidate_count=submitted + omitted,
        submitted_count=submitted,
        applied_count=submitted,
        omitted_count=omitted,
    )


def test_a_round_that_still_found_work_queues_the_next_one(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    paths, conn = runner_ledger
    _queue(conn)
    finished = _finished(conn, submitted=9, omitted=134)

    queued = enqueue_followup_job(conn, finished=finished)

    assert queued is not None and queued.created
    assert queued.job.trigger_kind == "followup"
    assert queued.job.round_index == 2
    assert queued.job.trigger_source_file_id is None
    assert queued.job.client == finished.client
    assert paths.db.exists()


@pytest.mark.parametrize(
    "submitted,omitted,why",
    [
        (0, 12, "a round that submitted nothing has said what it will not answer"),
        (12, 0, "a round that left nothing behind is finished"),
    ],
)
def test_a_round_that_made_no_progress_ends_the_chain(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    submitted: int,
    omitted: int,
    why: str,
) -> None:
    _, conn = runner_ledger
    _queue(conn)
    finished = _finished(conn, submitted=submitted, omitted=omitted)

    assert enqueue_followup_job(conn, finished=finished) is None, why
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM agent_classification_job WHERE state = 'queued'"
        ).fetchone()[0]
        == 0
    )


def test_the_round_chain_stops_at_its_hard_cap_even_while_it_is_still_finding_work(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    """The observed real run never returned zero, so only the cap can end it."""
    _, conn = runner_ledger
    _queue(conn)
    finished = _finished(
        conn,
        submitted=2,
        omitted=118,
        round_index=MAX_CLASSIFICATION_ROUNDS,
    )

    assert enqueue_followup_job(conn, finished=finished) is None


def test_a_person_can_ask_for_a_round_without_importing_a_statement(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    _, conn = runner_ledger
    update_policy(
        conn,
        selected_client="codex",
        application_mode="automatic",
        enabled=True,
        auto_classify_new_imports=False,
        acknowledge_provider_data_policy=True,
        now="2026-08-10T12:00:00+00:00",
    )

    queued = enqueue_manual_job(conn)

    assert queued is not None and queued.created
    assert queued.job.trigger_kind == "manual"
    assert queued.job.trigger_source_file_id is None, "an explicit ask is not about one file"
    assert queued.job.round_index == 1
    # A second ask must not stack a round onto a queue that is already busy.
    assert enqueue_manual_job(conn) is None


def test_a_disconnected_agent_cannot_be_asked_for_a_round(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    _, conn = runner_ledger

    assert enqueue_manual_job(conn) is None
    assert conn.execute("SELECT COUNT(*) FROM agent_classification_job").fetchone()[0] == 0


def _record_job(
    conn: sqlite3.Connection,
    *,
    index: int,
    candidate: int,
    submitted: int,
    queued_at: str,
    finished_at: str | None,
    state: str = "partial",
) -> None:
    conn.execute(
        "INSERT INTO agent_classification_job "
        "(id, trigger_source_file_id, trigger_kind, round_index, client, application_mode, "
        "state, candidate_count, submitted_count, applied_count, omitted_count, "
        "queued_at, started_at, finished_at) "
        "VALUES (?, NULL, 'manual', 1, 'codex', 'automatic', ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            f"job-{index:032d}",
            state,
            candidate if finished_at else None,
            submitted if finished_at else None,
            submitted if finished_at else None,
            candidate - submitted if finished_at else None,
            queued_at,
            queued_at if finished_at or state == "running" else None,
            finished_at,
        ),
    )


def test_one_import_of_many_files_is_reported_as_the_work_it_actually_was(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    """The shape of a real run: thirteen jobs, 270 candidates, 152 classified.

    Reporting the newest job alone described that as "2 submitted", which read
    as a failed run and was the reason a working import was thrown away.
    """
    _, conn = runner_ledger
    rounds = [(270, 96), (174, 18), (156, 5), (151, 6), (145, 2), (143, 9), (134, 1)]
    for index, (candidate, submitted) in enumerate(rounds):
        _record_job(
            conn,
            index=index,
            candidate=candidate,
            submitted=submitted,
            # Every job was queued while the first one was still running, which
            # is what makes them one stretch of work rather than seven.
            queued_at=f"2026-08-11T03:11:{22 + index:02d}+00:00",
            finished_at=f"2026-08-11T03:{13 + index:02d}:09+00:00",
        )

    batch = read_latest_batch(conn)

    assert batch is not None
    assert batch.job_count == 7
    assert batch.candidate_count == 270, "the pool the work started from"
    assert batch.submitted_count == 137, "every round's work, not the last round's"
    assert batch.omitted_count == 133, "what is actually left now"
    assert batch.state == "partial"
    assert batch.finished_at == "2026-08-11T03:19:09+00:00"
    assert batch.rounds_capped is False


def test_a_run_started_after_the_queue_went_quiet_is_its_own_stretch(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    _, conn = runner_ledger
    _record_job(
        conn,
        index=0,
        candidate=270,
        submitted=96,
        queued_at="2026-08-11T03:11:22+00:00",
        finished_at="2026-08-11T03:13:09+00:00",
    )
    _record_job(
        conn,
        index=1,
        candidate=118,
        submitted=4,
        queued_at="2026-08-11T20:49:00+00:00",
        finished_at="2026-08-11T20:51:00+00:00",
    )

    batch = read_latest_batch(conn)

    assert batch is not None
    assert batch.job_count == 1
    assert (batch.candidate_count, batch.submitted_count) == (118, 4)


def test_a_stretch_still_in_flight_reports_no_leftover_count_yet(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    """A running batch must not publish a leftover number that is about to change."""
    _, conn = runner_ledger
    _record_job(
        conn,
        index=0,
        candidate=270,
        submitted=96,
        queued_at="2026-08-11T03:11:22+00:00",
        finished_at="2026-08-11T03:13:09+00:00",
    )
    _record_job(
        conn,
        index=1,
        candidate=0,
        submitted=0,
        queued_at="2026-08-11T03:13:00+00:00",
        finished_at=None,
        state="running",
    )

    batch = read_latest_batch(conn)

    assert batch is not None
    assert batch.state == "running"
    assert batch.job_count == 2
    assert batch.submitted_count == 96, "finished rounds still count"
    assert batch.omitted_count is None
    assert batch.finished_at is None


def test_one_empty_round_does_not_make_four_classifying_rounds_a_failure(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    """The observed shape of one button press: 2, 2, 13, 1, then nothing."""
    _, conn = runner_ledger
    # A follow-up round is queued the instant the previous one finishes, which
    # is what makes a chain one stretch of work rather than five.
    rounds = [
        (118, 2, "2026-08-11T23:44:01+00:00", "2026-08-11T23:44:59+00:00"),
        (116, 2, "2026-08-11T23:44:59+00:00", "2026-08-11T23:46:05+00:00"),
        (114, 13, "2026-08-11T23:46:05+00:00", "2026-08-11T23:47:15+00:00"),
        (101, 1, "2026-08-11T23:47:15+00:00", "2026-08-11T23:48:01+00:00"),
    ]
    for index, (candidate, submitted, queued_at, finished_at) in enumerate(rounds):
        _record_job(
            conn,
            index=index,
            candidate=candidate,
            submitted=submitted,
            queued_at=queued_at,
            finished_at=finished_at,
        )
    conn.execute(
        "INSERT INTO agent_classification_job "
        "(id, trigger_source_file_id, trigger_kind, round_index, client, application_mode, "
        "state, candidate_count, submitted_count, applied_count, omitted_count, error_code, "
        "queued_at, started_at, finished_at) "
        "VALUES (?, NULL, 'followup', 5, 'codex', 'automatic', 'failed', 100, 0, 0, 100, "
        "'client_no_result', '2026-08-11T23:48:01+00:00', '2026-08-11T23:48:01+00:00', "
        "'2026-08-11T23:48:54+00:00')",
        ("job-" + "9" * 32,),
    )

    batch = read_latest_batch(conn)

    assert batch is not None
    assert batch.state == "partial", "eighteen classified lines are not a failed run"
    assert batch.submitted_count == 18
    assert batch.omitted_count == 100
    assert batch.failed_rounds == 1
    assert batch.job_count == 5


def test_an_all_abstention_round_finishes_as_evidence_not_as_a_client_failure(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client examined every candidate, declined them all, and said so with
    an empty run. That is a finished round with 98 leftovers, not
    client_no_result."""
    paths, conn = runner_ledger
    job_id = _queue(conn)

    def declined_everything(
        command: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        inner = open_ledger(paths.db)
        try:
            session_id = start_session(
                inner,
                client="codex",
                session_id="session-codex",
                job_id=job_id,
                now="2026-08-12T02:34:07+00:00",
            )
            run_id = "sha256:" + "5" * 64
            with transaction(inner):
                inner.execute(
                    "INSERT INTO agent_proposal_run "
                    "(id, ledger_revision, schema_version, application_mode, client, "
                    "created_at, state) "
                    "VALUES (?, ?, 2, 'automatic', 'codex', "
                    "'2026-08-12T02:35:00+00:00', 'completed')",
                    (run_id, "sha256:" + "4" * 64),
                )
                link_job_proposal_run_in_transaction(
                    inner,
                    job_id=job_id,
                    session_id=session_id,
                    proposal_run_id=run_id,
                    client="codex",
                    application_mode="automatic",
                    allow_new_link=True,
                )
            record_session_result(
                inner,
                session_id=session_id,
                result_state="partial",
                candidate_count=98,
                submitted_count=0,
                error_code=None,
                now="2026-08-12T02:35:01+00:00",
            )
            end_session(inner, session_id=session_id, now="2026-08-12T02:35:02+00:00")
        finally:
            inner.close()
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("ledgerbox.agent_runner._current_candidate_count", lambda *_: 98)
    monkeypatch.setattr("ledgerbox.agent_runner.subprocess.run", declined_everything)

    result = run_next_job(paths)

    assert result is not None and result.state == "partial"
    assert (result.candidate_count, result.submitted_count) == (98, 0)
    assert (result.applied_count, result.omitted_count) == (0, 98)
    assert result.error_code is None, "declining everything is an answer, not an error"


def test_failures_in_an_earlier_chain_do_not_consume_this_chains_tolerance(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    """Observed: yesterday's chain ended in a failure, so every press today got
    exactly one round before the cross-chain trailing count hit the limit."""
    _, conn = runner_ledger
    for index in range(MAX_CONSECUTIVE_FAILED_ROUNDS):
        conn.execute(
            "INSERT INTO agent_classification_job "
            "(id, trigger_source_file_id, trigger_kind, round_index, client, "
            "application_mode, state, candidate_count, submitted_count, applied_count, "
            "omitted_count, error_code, queued_at, started_at, finished_at) "
            "VALUES (?, NULL, 'followup', ?, 'codex', 'automatic', 'failed', 100, 0, 0, "
            "100, 'client_no_result', ?, ?, ?)",
            (
                f"job-old-{index:028d}",
                index + 1,
                f"2026-08-11T23:5{index}:01+00:00",
                f"2026-08-11T23:5{index}:01+00:00",
                f"2026-08-11T23:5{index}:59+00:00",
            ),
        )
    job_id = _queue(conn)
    claimed = claim_next_job(conn)
    assert claimed is not None and claimed.id == job_id
    failed = fail_job(
        conn,
        job_id=job_id,
        candidate_count=98,
        error_code="client_no_result",
    )

    queued = enqueue_followup_job(conn, finished=failed)

    assert queued is not None, (
        "a fresh chain's first stumble is its own first stumble, not its fourth"
    )
    assert queued.job.round_index == 2


def test_a_chain_survives_a_round_that_returned_nothing_at_all(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    """Pressing again right after one of these found more, so one is not the end."""
    _, conn = runner_ledger
    _queue(conn)
    claimed = claim_next_job(conn)
    assert claimed is not None
    failed = fail_job(
        conn,
        job_id=claimed.id,
        candidate_count=100,
        error_code="client_no_result",
    )

    queued = enqueue_followup_job(conn, finished=failed)

    assert queued is not None and queued.job.round_index == 2


def test_a_chain_gives_up_after_enough_rounds_return_nothing(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    _, conn = runner_ledger
    failed = None
    for round_index in range(1, MAX_CONSECUTIVE_FAILED_ROUNDS + 1):
        conn.execute(
            "INSERT INTO agent_classification_job "
            "(id, trigger_source_file_id, trigger_kind, round_index, client, application_mode, "
            "state, candidate_count, submitted_count, applied_count, omitted_count, error_code, "
            "queued_at, started_at, finished_at) "
            "VALUES (?, NULL, 'followup', ?, 'codex', 'automatic', 'failed', 100, 0, 0, 100, "
            "'client_no_result', ?, ?, ?)",
            (
                f"job-{round_index:032d}",
                round_index,
                f"2026-08-11T23:5{round_index}:01+00:00",
                f"2026-08-11T23:5{round_index}:01+00:00",
                f"2026-08-11T23:5{round_index}:59+00:00",
            ),
        )
        failed = get_job(conn, f"job-{round_index:032d}")

    assert failed is not None
    assert enqueue_followup_job(conn, finished=failed) is None


def test_a_stretch_that_ran_into_the_round_cap_says_so(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
) -> None:
    _, conn = runner_ledger
    _record_job(
        conn,
        index=0,
        candidate=120,
        submitted=2,
        queued_at="2026-08-11T03:11:22+00:00",
        finished_at="2026-08-11T03:13:09+00:00",
    )
    conn.execute(
        "UPDATE agent_classification_job SET round_index = ?",
        (MAX_CLASSIFICATION_ROUNDS,),
    )

    batch = read_latest_batch(conn)

    assert batch is not None and batch.rounds_capped is True


def test_drain_jobs_is_bounded_and_stops_when_the_queue_is_empty(
    runner_ledger: tuple[DataPaths, sqlite3.Connection],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths, _ = runner_ledger
    calls: list[int] = []
    terminal = object()
    outcomes = iter((terminal, terminal, None))

    def fake_next(_paths: DataPaths) -> object | None:
        calls.append(1)
        return next(outcomes)

    monkeypatch.setattr("ledgerbox.agent_runner.run_next_job", fake_next)
    # Chaining is exercised by its own counterexamples; this one is about the
    # bound alone, and drives run_next_job with a sentinel rather than a job.
    monkeypatch.setattr("ledgerbox.agent_runner._continue_chain", lambda *_: None)

    assert drain_jobs(paths, max_jobs=5) == (terminal, terminal)
    assert len(calls) == 3

    calls.clear()
    def always_one(_paths: DataPaths) -> object:
        calls.append(1)
        return terminal

    monkeypatch.setattr("ledgerbox.agent_runner.run_next_job", always_one)
    assert len(drain_jobs(paths, max_jobs=2)) == 2
    assert len(calls) == 2


def test_cli_drains_only_when_ingest_committed_a_new_job(
    git_free_tmp: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = DataPaths.resolve(git_free_tmp / "cli-runner-data")
    drained: list[DataPaths] = []

    monkeypatch.setattr(
        "ledgerbox.cli._open",
        lambda _args: (paths, sqlite3.connect(":memory:")),
    )
    monkeypatch.setattr(
        "ledgerbox.cli.ingest_paths",
        lambda *_args, **_kwargs: [
            IngestOutcome(
                source=Path("synthetic.pdf"),
                status=IMPORTED,
                agent_job_queued=True,
            )
        ],
    )
    monkeypatch.setattr(
        "ledgerbox.cli.drain_jobs",
        lambda target: drained.append(target) or (),
    )

    assert cmd_ingest(argparse.Namespace(paths=[Path("synthetic.pdf")])) == 0
    assert drained == [paths]
