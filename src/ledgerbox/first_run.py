# SPDX-License-Identifier: AGPL-3.0-or-later
"""One command from a fresh checkout to a connected local Agent.

``ledgerbox setup`` exists so "帮我 set up 这个项目" can be one sentence to the
user's own coding agent: the agent runs this command and everything that must
happen in order -- data directory guard, personal Skill install, MCP
registration, verification -- happens in order, with the same honesty rules the
copied setup steps obey. A failed Skill install stops everything before
registration. A custom Skill stops with a doctor pointer, never force. A client
that already knows this ledger is not registered twice. Every argv here is the
shape that was probed working on a real Windows machine; add-json and inline
flags after ``--`` were probed broken and must not come back.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

from .agent_skill_install import (
    AgentClient,
    SkillBundleInvalid,
    SkillInstallConflict,
    install_user_skill,
)
from .config import DataDirRefused, DataPaths

_RunFn = Callable[..., "subprocess.CompletedProcess[str]"]

CLIENT_COMMANDS: dict[AgentClient, str] = {"codex": "codex", "claude-code": "claude"}
REGISTRATION_TIMEOUT_SECONDS = 120


class FirstRunError(RuntimeError):
    """Setup stopped; the message says at which step and why."""


def _mcp_executable() -> str:
    suffix = "ledgerbox-mcp.exe" if sys.platform == "win32" else "ledgerbox-mcp"
    beside_python = Path(sys.executable).with_name(suffix)
    if beside_python.is_file():
        return str(beside_python)
    return shutil.which("ledgerbox-mcp") or "ledgerbox-mcp"


def _client_binary(client: AgentClient) -> str:
    name = CLIENT_COMMANDS[client]
    return shutil.which(name) or name


def _registration(client: AgentClient, binary: str, root: str) -> list[str]:
    bridge = _mcp_executable()
    if client == "codex":
        return [
            binary,
            "mcp",
            "add",
            "ledgerbox",
            "--",
            bridge,
            "--client",
            "codex",
            "--data-dir",
            root,
        ]
    # Probed on the real client: command first, env pairs after. add-json loses
    # its JSON quoting at the PowerShell/npm-shim boundary, and plain add
    # parses child --flags as its own even behind the -- separator.
    return [
        binary,
        "mcp",
        "add",
        "--scope",
        "local",
        "ledgerbox",
        bridge,
        "-e",
        "LEDGERBOX_MCP_CLIENT=claude-code",
        "-e",
        f"LEDGERBOX_DATA_DIR={root}",
    ]


def _spawn(
    run: _RunFn, command: list[str], client: AgentClient
) -> subprocess.CompletedProcess[str]:
    try:
        return run(
            command,
            capture_output=True,
            text=True,
            timeout=REGISTRATION_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise FirstRunError(
            f"the {CLIENT_COMMANDS[client]} command line is not installed or not on PATH; "
            "install the client first, then run setup again"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise FirstRunError(
            f"the {CLIENT_COMMANDS[client]} command did not answer within "
            f"{REGISTRATION_TIMEOUT_SECONDS} seconds"
        ) from error


def first_run(
    *,
    data_dir: Path | None,
    client: str,
    home: Path | None = None,
    run: _RunFn = subprocess.run,
) -> list[str]:
    """Set one machine up for one client; return the sentences worth printing.

    Ordering is the contract: the data-directory guard first because nothing
    may be written anywhere it would refuse, the personal Skill second, MCP
    registration only after the Skill succeeded, verification last.
    """
    canonical = cast(AgentClient, "claude-code" if client == "claude" else client)
    if canonical not in CLIENT_COMMANDS:
        raise FirstRunError("client must be codex, claude, or claude-code")

    try:
        paths = DataPaths.resolve(data_dir)
    except DataDirRefused as error:
        raise FirstRunError(str(error)) from error
    events = [f"data directory accepted: {paths.root}"]

    try:
        installed = install_user_skill(canonical, home=home)
    except SkillInstallConflict as error:
        raise FirstRunError(
            f"{error} -- run `ledgerbox agent doctor --client {canonical}` and decide "
            "manually; setup never overwrites a custom Skill and never registers MCP "
            "after a failed install"
        ) from error
    except (SkillBundleInvalid, OSError) as error:
        raise FirstRunError(f"personal Skill installation failed: {error}") from error
    events.append(
        "personal Skill installed"
        if installed.action in {"installed", "upgraded", "replaced_custom"}
        else "personal Skill already current"
    )

    binary = _client_binary(canonical)
    listing = _spawn(run, [binary, "mcp", "list"], canonical)
    known = "ledgerbox" in f"{listing.stdout}\n{listing.stderr}"
    if known:
        events.append(f"{CLIENT_COMMANDS[canonical]} already registered; left unchanged")
    else:
        added = _spawn(run, _registration(canonical, binary, str(paths.root)), canonical)
        if added.returncode != 0:
            detail = (added.stderr or added.stdout or "").strip()
            raise FirstRunError(
                f"MCP registration failed (exit {added.returncode})"
                + (f": {detail}" if detail else "")
            )
        events.append(f"{CLIENT_COMMANDS[canonical]} MCP registered for this ledger")

    events.append(
        "next: start the server (start-ledgerbox.cmd or `ledgerbox serve`), open the "
        "page, upload statements, and enable classification in the sidebar"
    )
    return events
