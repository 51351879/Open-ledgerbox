# SPDX-License-Identifier: AGPL-3.0-or-later
"""A7.3 local Agent policy and evidence-backed readiness status."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from ...agent import read_agent_status
from ...agent_center import (
    AgentCenterConflict,
    AgentClient,
    AgentClientActivity,
    AgentPolicy,
    read_client_activity,
    update_policy,
)
from ...agent_jobs import MAX_CLASSIFICATION_ROUNDS, enqueue_manual_job, read_latest_batch
from ...agent_runner import drain_jobs
from ...agent_skill_install import (
    SkillBundleInvalid,
    SkillState,
    inspect_user_skill,
)
from ...agent_workspace import AgentWorkspaceMissing, agent_workspace_root
from ...proposals import PROPOSAL_SCHEMA_VERSION
from ..dependencies import AppState, get_state, ledger_ro, ledger_rw
from ..schemas import (
    AgentCenterClientOut,
    AgentCenterLedgerOut,
    AgentCenterOut,
    AgentCenterPolicyIn,
    AgentCenterPolicyOut,
    AgentClassificationBatchOut,
    AgentClassificationJobOut,
    ClientOutcomeOut,
)

router = APIRouter(prefix="/api/agent-center", tags=["agent center"])
StateDep = Annotated[AppState, Depends(get_state)]

CLIENT_COMMANDS: dict[AgentClient, str] = {
    "codex": "codex",
    "claude-code": "claude",
}
SUPPORTED_CLIENTS: tuple[AgentClient, ...] = ("codex", "claude-code")
PROVIDER_DISCLOSURE = (
    "Ledgerbox itself sends nothing to a model. When you ask the selected local Agent to classify, "
    "that client may send returned transaction facts to the provider configured in your own "
    "account."
)
RUN_PROMPTS: dict[Literal["codex", "claude-code"], str] = {
    "codex": "Use $ledgerbox to classify current eligible transactions in my local Ledgerbox.",
    "claude-code": "/ledgerbox classify current eligible transactions in my local Ledgerbox",
}
SETUP_GUIDE = "docs/AGENT_SETUP.md"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _powershell_arg(value: str) -> str:
    """Quote one literal PowerShell argument without evaluating its contents."""
    return "'" + value.replace("'", "''") + "'"


def _mcp_executable() -> str:
    suffix = "ledgerbox-mcp.exe" if os.name == "nt" else "ledgerbox-mcp"
    beside_python = Path(sys.executable).with_name(suffix)
    if beside_python.is_file():
        return str(beside_python)
    return shutil.which("ledgerbox-mcp") or "ledgerbox-mcp"


def _ledgerbox_executable() -> str:
    suffix = "ledgerbox.exe" if os.name == "nt" else "ledgerbox"
    beside_python = Path(sys.executable).with_name(suffix)
    if beside_python.is_file():
        return str(beside_python)
    return shutil.which("ledgerbox") or "ledgerbox"


def _guarded_setup(client: AgentClient, registration: str) -> str:
    """Install the personal Skill first and register MCP only when that succeeded.

    A console consumes pasted text one line at a time, so the guard and the
    registration must stay in the same statement: a newline between them would let
    registration run after a failed installation.
    """
    installer = (
        f"& {_powershell_arg(_ledgerbox_executable())} agent install-skill "
        f"--client {client}"
    )
    refusal = _powershell_arg(
        "Personal Skill installation failed; MCP registration was not changed."
    )
    return f"{installer}; if ($?) {{ {registration} }} else {{ Write-Error {refusal} }}"


def _setup_commands(data_dir: Path) -> dict[Literal["codex", "claude-code"], str]:
    bridge = _mcp_executable()
    root = str(data_dir)
    codex = (
        "codex mcp add ledgerbox -- "
        f"{_powershell_arg(bridge)} --client codex --data-dir {_powershell_arg(root)}"
    )
    # Environment form, command before the -e options, no `--`. Every other
    # shape fails on the tested client: add-json's JSON argument loses its
    # inner quotes at the PowerShell/npm-shim boundary ("Invalid configuration:
    # Invalid input"), plain add parses child --flags as its own even after
    # `--` ("unknown option '--client'"), and the variadic -e placed before a
    # positional swallows it ("missing required argument"). This exact order
    # registered and connected on the real machine.
    claude = (
        "claude mcp add --scope local ledgerbox "
        f"{_powershell_arg(bridge)} "
        f"-e LEDGERBOX_MCP_CLIENT=claude-code -e {_powershell_arg('LEDGERBOX_DATA_DIR=' + root)}"
    )
    return {
        "codex": _guarded_setup("codex", codex),
        "claude-code": _guarded_setup("claude-code", claude),
    }


def _runner_skill_compatible(client: AgentClient) -> bool:
    try:
        workspace = agent_workspace_root()
        skill = {
            "codex": workspace / ".agents" / "skills" / "ledgerbox" / "SKILL.md",
            "claude-code": workspace / ".claude" / "skills" / "ledgerbox" / "SKILL.md",
        }[client]
        contract = workspace / "docs" / "AGENT_CONTRACT.md"
        skill_text = skill.read_text(encoding="utf-8")
        contract_text = contract.read_text(encoding="utf-8")
    except (AgentWorkspaceMissing, OSError):
        return False
    return all(
        marker in skill_text + contract_text
        for marker in ("proposal_schema_version", "review_first", "automatic")
    )


def _personal_skill_state(client: AgentClient) -> SkillState:
    """Return one aggregate state; private inspection details never reach the API."""
    try:
        return inspect_user_skill(client).state
    except (AgentWorkspaceMissing, SkillBundleInvalid, OSError):
        # An unreadable or uncheckable personal tree must never be called current.
        return "custom"


def _mcp_bridge_available() -> bool:
    return importlib.util.find_spec("mcp") is not None


def _client_out(
    *,
    client: AgentClient,
    activity: AgentClientActivity,
) -> AgentCenterClientOut:
    if activity.session_active:
        mcp_session: Literal["active", "seen_before", "not_seen"] = "active"
    elif activity.last_seen_at is not None:
        mcp_session = "seen_before"
    else:
        mcp_session = "not_seen"
    return AgentCenterClientOut(
        client=client,
        installed=shutil.which(CLIENT_COMMANDS[client]) is not None,
        runner_skill_compatible=_runner_skill_compatible(client),
        personal_skill_state=_personal_skill_state(client),
        mcp_bridge_available=_mcp_bridge_available(),
        mcp_session=mcp_session,
        session_active=activity.session_active,
        last_seen_at=activity.last_seen_at,
        last_result=activity.last_result,
        result_at=activity.result_at,
        candidate_count=activity.candidate_count,
        submitted_count=activity.submitted_count,
        error_code=activity.error_code,
    )


def _policy_out(policy: AgentPolicy) -> AgentCenterPolicyOut:
    return AgentCenterPolicyOut(
        selected_client=policy.selected_client,
        application_mode=policy.application_mode,
        enabled=policy.enabled,
        auto_classify_new_imports=policy.auto_classify_new_imports,
    )


def _job_out(row: object) -> AgentClassificationJobOut:
    item = cast(dict[str, object], row)
    return AgentClassificationJobOut(
        client=cast(AgentClient, item["client"]),
        application_mode=cast(Literal["review_first", "automatic"], item["application_mode"]),
        state=cast(
            Literal["queued", "running", "completed", "partial", "failed"],
            item["state"],
        ),
        candidate_count=cast(int | None, item["candidate_count"]),
        submitted_count=cast(int | None, item["submitted_count"]),
        applied_count=cast(int | None, item["applied_count"]),
        omitted_count=cast(int | None, item["omitted_count"]),
        error_code=cast(str | None, item["error_code"]),
        client_outcome=cast(ClientOutcomeOut | None, item["client_outcome"]),
        client_exit_code=cast(int | None, item["client_exit_code"]),
        queued_at=str(item["queued_at"]),
        started_at=cast(str | None, item["started_at"]),
        finished_at=cast(str | None, item["finished_at"]),
    )


def _center_out(state: AppState) -> AgentCenterOut:
    now = _utc_now()
    with ledger_ro(state) as conn:
        agent_status = read_agent_status(conn, state.paths)
        activities = {
            client: read_client_activity(conn, client=client, now=now)
            for client in SUPPORTED_CLIENTS
        }
        pending = int(
            conn.execute(
                "SELECT COUNT(*) FROM agent_category_proposal WHERE outcome = 'pending'"
            ).fetchone()[0]
        )
        pending_triage = int(
            conn.execute(
                "SELECT COUNT(*) FROM agent_triage_item WHERE outcome = 'pending'"
            ).fetchone()[0]
        )
        open_review = int(
            conn.execute(
                "SELECT COUNT(*) FROM review_item WHERE status = 'open'"
            ).fetchone()[0]
        )
        batch = read_latest_batch(conn)
        latest_job_row = conn.execute(
            "SELECT client, application_mode, state, candidate_count, submitted_count, "
            "applied_count, omitted_count, error_code, client_outcome, client_exit_code, "
            "queued_at, started_at, finished_at "
            "FROM agent_classification_job ORDER BY queued_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
    clients = [
        _client_out(client=client, activity=activities[client])
        for client in SUPPORTED_CLIENTS
    ]
    passed = sum(check.status == "pass" for check in agent_status.checks)
    return AgentCenterOut(
        schema_version=3,
        ledgerbox=AgentCenterLedgerOut(
            ready_for_proposals=agent_status.ready_for_proposals,
            passed_checks=passed,
            total_checks=len(agent_status.checks),
            proposal_schema_version=cast(Literal[2], PROPOSAL_SCHEMA_VERSION),
            uncategorized_count=agent_status.uncategorized_count,
            pending_review_count=pending,
            pending_triage_count=pending_triage,
            open_review_count=open_review,
            ledger_label=state.paths.root.name,
            data_dir=str(state.paths.root),
        ),
        policy=_policy_out(agent_status.local_policy),
        clients=clients,
        latest_batch=(
            None
            if batch is None
            else AgentClassificationBatchOut(
                **asdict(batch),
                max_rounds=MAX_CLASSIFICATION_ROUNDS,
            )
        ),
        latest_job=None if latest_job_row is None else _job_out(dict(latest_job_row)),
        provider_disclosure=PROVIDER_DISCLOSURE,
        run_prompts=RUN_PROMPTS,
        setup_commands=_setup_commands(state.paths.root),
        setup_guide=SETUP_GUIDE,
    )


@router.get("")
def read_agent_center(state: StateDep) -> AgentCenterOut:
    return _center_out(state)


@router.post("/classify", status_code=status.HTTP_202_ACCEPTED)
def start_classification_round(
    background_tasks: BackgroundTasks,
    state: StateDep,
) -> AgentCenterOut:
    """Queue one round because a person asked, then let the chain run behind us.

    Queueing is the whole of the durable effect. The client is started by the
    same bounded runner an import uses, so this route never talks to a model and
    never blocks on one.
    """
    with ledger_rw(state) as conn:
        queued = enqueue_manual_job(conn)
    if queued is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "enable a local Agent in Classification settings, and wait for any "
            "classification already in flight to finish",
        )
    background_tasks.add_task(drain_jobs, state.paths)
    return _center_out(state)


@router.put("/policy")
def replace_agent_policy(body: AgentCenterPolicyIn, state: StateDep) -> AgentCenterPolicyOut:
    if body.enabled:
        if body.selected_client is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "select an installed local Agent before enabling classification",
            )
        with ledger_ro(state) as conn:
            activity = read_client_activity(conn, client=body.selected_client)
        readiness = _client_out(client=body.selected_client, activity=activity)
        if not (
            readiness.installed
            and readiness.runner_skill_compatible
            and readiness.personal_skill_state == "current"
            and readiness.mcp_bridge_available
        ):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "the selected local Agent is not ready; run agent doctor, install the personal "
                "Skill without force, and check the MCP bridge",
            )
    try:
        with ledger_rw(state) as conn:
            policy = update_policy(
                conn,
                selected_client=body.selected_client,
                application_mode=body.application_mode,
                enabled=body.enabled,
                auto_classify_new_imports=body.auto_classify_new_imports,
                acknowledge_provider_data_policy=body.acknowledge_provider_data_policy,
            )
    except AgentCenterConflict as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return _policy_out(policy)
