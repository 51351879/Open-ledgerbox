# SPDX-License-Identifier: AGPL-3.0-or-later
"""Installed-package Agent workspace selection; no model or client is started."""

from __future__ import annotations

from pathlib import Path

import pytest

from ledgerbox import agent_workspace


def _write_minimum_workspace(root: Path) -> None:
    for relative in agent_workspace._REQUIRED_FILES:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("synthetic packaged workspace\n", encoding="utf-8")


def test_installed_package_workspace_is_used_without_a_source_checkout(
    git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing_checkout = git_free_tmp / "no-checkout"
    packaged = git_free_tmp / "installed-package" / "_agent_workspace"
    _write_minimum_workspace(packaged)
    monkeypatch.setattr(agent_workspace, "CHECKOUT_ROOT", missing_checkout)
    monkeypatch.setattr(agent_workspace, "PACKAGED_ROOT", packaged)

    assert agent_workspace.agent_workspace_root() == packaged


def test_missing_official_workspace_fails_closed(
    git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_workspace, "CHECKOUT_ROOT", git_free_tmp / "no-checkout")
    monkeypatch.setattr(agent_workspace, "PACKAGED_ROOT", git_free_tmp / "no-package")

    with pytest.raises(agent_workspace.AgentWorkspaceMissing):
        agent_workspace.agent_workspace_root()
