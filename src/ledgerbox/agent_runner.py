# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded A7.4 queue consumer for user-owned Codex or Claude Code clients."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .agent import read_agent_candidates
from .agent_jobs import (
    AgentJob,
    ClientOutcome,
    claim_next_job,
    enqueue_followup_job,
    fail_job,
    finish_job,
    get_job,
)
from .agent_workspace import AgentWorkspaceMissing, agent_workspace_root
from .config import DataPaths
from .db import repo
from .db.connection import read_transaction
from .db.migrate import open_ledger

CLIENT_TIMEOUT_SECONDS = 600
MAX_DRAIN_JOBS = 100

# One submission may carry hundreds of proposals, and observed rounds were
# submitting one to thirteen of a hundred candidates before stopping. That is
# not the Skill's abstention rule doing its job -- that rule is about candidates
# whose evidence is genuinely unclear, and it stays exactly as strict. It is a
# client finishing early on work it could have done, so the ask is made explicit.
_COVERAGE = (
    "In that one submission include every candidate whose category the returned evidence "
    "clearly supports, not just the first few you are confident about. Keep omitting any "
    "candidate the Skill's abstention rules cover; never guess to raise coverage. "
    "Report only the Skill's fixed aggregate result, and then stop."
)
_CODEX_PROMPT = (
    "Use $ledgerbox to classify current eligible transactions in my local Ledgerbox. "
    "Complete exactly one bounded classification submission. " + _COVERAGE
)
_CLAUDE_PROMPT = (
    "/ledgerbox classify current eligible transactions in my local Ledgerbox. "
    "Complete exactly one bounded classification submission. " + _COVERAGE
)
_CLAUDE_TOOLS = (
    "Read",
    "Skill",
    "mcp__ledgerbox__ledgerbox_status",
    "mcp__ledgerbox__ledgerbox_categories",
    "mcp__ledgerbox__ledgerbox_candidates",
    "mcp__ledgerbox__ledgerbox_validate_proposal",
    "mcp__ledgerbox__ledgerbox_submit_proposal",
)


def _mcp_executable() -> str:
    suffix = "ledgerbox-mcp.exe" if sys.platform == "win32" else "ledgerbox-mcp"
    beside_python = Path(sys.executable).with_name(suffix)
    return str(beside_python) if beside_python.is_file() else "ledgerbox-mcp"


def _mcp_args(paths: DataPaths, job: AgentJob) -> list[str]:
    return [
        "--client",
        job.client,
        "--data-dir",
        str(paths.root),
        "--job-id",
        job.id,
    ]


def _client_command(paths: DataPaths, job: AgentJob) -> list[str]:
    bridge = _mcp_executable()
    mcp_args = _mcp_args(paths, job)
    workspace = agent_workspace_root()
    if job.client == "codex":
        return [
            shutil.which("codex") or "codex",
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            "read-only",
            "--cd",
            str(workspace),
            "-c",
            f"mcp_servers.ledgerbox.command={json.dumps(bridge)}",
            "-c",
            f"mcp_servers.ledgerbox.args={json.dumps(mcp_args)}",
            _CODEX_PROMPT,
        ]
    config = json.dumps(
        {
            "mcpServers": {
                "ledgerbox": {
                    "type": "stdio",
                    "command": bridge,
                    "args": mcp_args,
                }
            }
        },
        separators=(",", ":"),
    )
    return [
        shutil.which("claude") or "claude",
        "--print",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--mcp-config",
        config,
        "--setting-sources",
        "project",
        "--permission-mode",
        "dontAsk",
        "--allowedTools",
        ",".join(_CLAUDE_TOOLS),
        "--",
        _CLAUDE_PROMPT,
    ]


def _captured(output: str | bytes | None) -> str | None:
    """Normalise whatever the client wrote; bounding happens in the job store."""
    if output is None:
        return None
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace") or None
    return output or None


def _current_candidate_count(conn: sqlite3.Connection, paths: DataPaths) -> int:
    with read_transaction(conn):
        return read_agent_candidates(conn, paths, limit=1).matched


def _session_result(conn: sqlite3.Connection, job: AgentJob) -> sqlite3.Row | None:
    if job.session_id is None:
        return None
    return cast(
        sqlite3.Row | None,
        conn.execute(
            "SELECT result_state, candidate_count, submitted_count, error_code "
            "FROM agent_local_session WHERE id = ?",
            (job.session_id,),
        ).fetchone(),
    )


def _valid_submission_result(
    conn: sqlite3.Connection,
    job: AgentJob,
    session: sqlite3.Row | None,
    fallback_candidate_count: int,
) -> tuple[int, int, int, int] | None:
    if session is not None and session["result_state"] in {"completed", "partial"}:
        candidate_count = session["candidate_count"]
        submitted_count = session["submitted_count"]
        if (
            type(candidate_count) is not int
            or type(submitted_count) is not int
            or not 0 <= submitted_count <= candidate_count
        ):
            return None
    elif job.proposal_run_id is not None:
        candidate_count = fallback_candidate_count
        submitted_count = len(
            repo.list_agent_category_proposals(conn, job.proposal_run_id)
        )
        if submitted_count > candidate_count:
            return None
    else:
        return None
    if job.proposal_run_id is not None:
        # Whatever the session aggregate says, the linked run is the durable
        # copy and the two must agree -- including on zero: an all-abstention
        # round links an empty run, and that agreement is a finished round, not
        # a client failure.
        run = conn.execute(
            "SELECT client, application_mode FROM agent_proposal_run WHERE id = ?",
            (job.proposal_run_id,),
        ).fetchone()
        if (
            run is None
            or run["client"] != job.client
            or run["application_mode"] != job.application_mode
            or len(repo.list_agent_category_proposals(conn, job.proposal_run_id))
            != submitted_count
        ):
            return None
    elif submitted_count:
        return None
    applied_count = submitted_count if job.application_mode == "automatic" else 0
    omitted_count = candidate_count - submitted_count
    return candidate_count, submitted_count, applied_count, omitted_count


def _failed_result_code(session: sqlite3.Row | None, fallback: str) -> str:
    if session is not None and session["result_state"] == "failed":
        error_code = session["error_code"]
        if isinstance(error_code, str) and error_code:
            return error_code
    return fallback


@dataclass(frozen=True, slots=True)
class _ClientEvidence:
    """What the runner itself saw of the client process, whatever the ledger says."""

    outcome: ClientOutcome
    exit_code: int | None = None
    log: str | None = None


def _finish_from_evidence(
    paths: DataPaths,
    *,
    job_id: str,
    fallback_candidate_count: int,
    fallback_error_code: str,
    client: _ClientEvidence,
) -> AgentJob:
    conn = open_ledger(paths.db)
    try:
        job = get_job(conn, job_id)
        if job is None:
            raise RuntimeError("claimed classification job disappeared")
        session = _session_result(conn, job)
        success = _valid_submission_result(
            conn,
            job,
            session,
            fallback_candidate_count,
        )
        if success is not None:
            candidate, submitted, applied, omitted = success
            return finish_job(
                conn,
                job_id=job.id,
                candidate_count=candidate,
                submitted_count=submitted,
                applied_count=applied,
                omitted_count=omitted,
                client_outcome=client.outcome,
                client_exit_code=client.exit_code,
                client_log_excerpt=client.log,
            )
        return fail_job(
            conn,
            job_id=job.id,
            candidate_count=fallback_candidate_count,
            error_code=_failed_result_code(session, fallback_error_code),
            client_outcome=client.outcome,
            client_exit_code=client.exit_code,
            client_log_excerpt=client.log,
        )
    finally:
        conn.close()


def run_next_job(paths: DataPaths) -> AgentJob | None:
    """Run at most one queued job and return its durable terminal state."""
    conn = open_ledger(paths.db)
    try:
        job = claim_next_job(conn)
        if job is None:
            return None
        try:
            candidate_count = _current_candidate_count(conn, paths)
        except Exception:  # noqa: BLE001 - the terminal code is deliberately aggregate-only.
            return fail_job(
                conn,
                job_id=job.id,
                candidate_count=0,
                error_code="ledger_not_ready",
            )
    finally:
        conn.close()

    try:
        command = _client_command(paths, job)
        workspace = agent_workspace_root()
    except AgentWorkspaceMissing:
        return _finish_from_evidence(
            paths,
            job_id=job.id,
            fallback_candidate_count=candidate_count,
            fallback_error_code="agent_workspace_missing",
            client=_ClientEvidence("workspace_missing"),
        )

    try:
        # The client's own account of the run is captured rather than discarded:
        # it is the only thing that can answer "why did it leave these alone".
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            command,
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=CLIENT_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return _finish_from_evidence(
            paths,
            job_id=job.id,
            fallback_candidate_count=candidate_count,
            fallback_error_code="client_not_found",
            client=_ClientEvidence("not_found"),
        )
    except subprocess.TimeoutExpired as expired:
        return _finish_from_evidence(
            paths,
            job_id=job.id,
            fallback_candidate_count=candidate_count,
            fallback_error_code="client_timeout",
            client=_ClientEvidence("timeout", log=_captured(expired.output)),
        )
    except OSError:
        return _finish_from_evidence(
            paths,
            job_id=job.id,
            fallback_candidate_count=candidate_count,
            fallback_error_code="client_spawn_failed",
            client=_ClientEvidence("spawn_failed"),
        )

    return _finish_from_evidence(
        paths,
        job_id=job.id,
        fallback_candidate_count=candidate_count,
        fallback_error_code=("client_no_result" if completed.returncode == 0 else "client_exit"),
        client=_ClientEvidence(
            "exited",
            exit_code=completed.returncode,
            log=_captured(completed.stdout),
        ),
    )


def drain_jobs(
    paths: DataPaths,
    *,
    max_jobs: int = MAX_DRAIN_JOBS,
) -> tuple[AgentJob, ...]:
    """Consume queued jobs serially, stopping on an empty/busy queue or a hard cap."""
    if type(max_jobs) is not int or max_jobs <= 0:
        raise ValueError("max_jobs must be a positive integer")
    completed: list[AgentJob] = []
    for _ in range(max_jobs):
        result = run_next_job(paths)
        if result is None:
            break
        completed.append(result)
        _continue_chain(paths, result)
    return tuple(completed)


def _continue_chain(paths: DataPaths, finished: AgentJob) -> None:
    """Queue the next round when the finished one was still finding work."""
    conn = open_ledger(paths.db)
    try:
        enqueue_followup_job(conn, finished=finished)
    finally:
        conn.close()
