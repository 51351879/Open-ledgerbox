# SPDX-License-Identifier: AGPL-3.0-or-later
"""Locate the read-only project workspace used by local classification clients."""

from __future__ import annotations

from pathlib import Path

CHECKOUT_ROOT = Path(__file__).resolve().parents[2]
PACKAGED_ROOT = Path(__file__).resolve().parent / "_agent_workspace"

_REQUIRED_FILES = (
    Path(".agents/skills/ledgerbox/SKILL.md"),
    Path(".agents/skills/ledgerbox/references/workflow.md"),
    Path(".claude/skills/ledgerbox/SKILL.md"),
    Path("docs/AGENT_CONTRACT.md"),
)


class AgentWorkspaceMissing(RuntimeError):
    """Neither a source checkout nor an installed package contains the official Skill."""


def agent_workspace_root() -> Path:
    """Return a complete checkout workspace, falling back to packaged resources."""
    for candidate in (CHECKOUT_ROOT, PACKAGED_ROOT):
        if all((candidate / relative).is_file() for relative in _REQUIRED_FILES):
            return candidate
    raise AgentWorkspaceMissing(
        "the official Ledgerbox Agent workspace is missing from this installation"
    )
