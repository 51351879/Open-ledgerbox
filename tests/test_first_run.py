# SPDX-License-Identifier: AGPL-3.0-or-later
"""One command sets a fresh machine up, and it lies about nothing.

The counterexamples pin the same honesty rules the copied setup steps obey: a
failed personal-Skill install stops everything before MCP registration, a
custom Skill stops with a doctor pointer instead of force, registration is
skipped when the client already knows this ledger, and a refused data
directory surfaces the guard's own sentence.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ledgerbox.first_run import FirstRunError, first_run


class FakeRuns:
    """Record every spawned command; answer from a script of results."""

    def __init__(self, results: dict[str, object] | None = None) -> None:
        self.seen: list[list[str]] = []
        self.results = results or {}

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.seen.append(list(command))
        key = " ".join(command[1:3])
        result = self.results.get(key)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, subprocess.CompletedProcess):
            return result
        return subprocess.CompletedProcess(command, 0, stdout=str(result or ""), stderr="")


def _workspace(tmp: Path) -> Path:
    """A minimal canonical workspace the Skill installer can bundle from."""
    root = tmp / "workspace"
    skill = root / ".agents" / "skills" / "ledgerbox"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "read docs/AGENT_CONTRACT.md before proposing\n", encoding="utf-8"
    )
    (skill / "references" / "workflow.md").write_text("synthetic\n", encoding="utf-8")
    claude = root / ".claude" / "skills" / "ledgerbox"
    claude.mkdir(parents=True)
    (claude / "SKILL.md").write_text(
        "read docs/AGENT_CONTRACT.md before proposing\n", encoding="utf-8"
    )
    docs = root / "docs"
    docs.mkdir()
    (docs / "AGENT_CONTRACT.md").write_text("contract\n", encoding="utf-8")
    (docs / "AGENT_SETUP.md").write_text("setup\n", encoding="utf-8")
    return root


@pytest.fixture
def home(git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(
        "ledgerbox.agent_skill_install.agent_workspace_root",
        lambda: _workspace(git_free_tmp),
    )
    return git_free_tmp / "home"


def test_first_run_installs_skill_then_registers_then_verifies(
    git_free_tmp: Path, home: Path
) -> None:
    runs = FakeRuns({"mcp list": "nothing here yet"})

    events = first_run(
        data_dir=git_free_tmp / "ledger-data",
        client="codex",
        home=home,
        run=runs,
    )

    assert (home / ".agents" / "skills" / "ledgerbox" / "SKILL.md").is_file()
    assert [command[1:3] for command in runs.seen] == [["mcp", "list"], ["mcp", "add"]]
    add = runs.seen[1]
    assert add[3] == "ledgerbox" and "--data-dir" in add
    assert not any("--force" in part or "--yes" in part for part in add)
    assert any("personal Skill installed" in event for event in events)
    assert any("registered" in event for event in events)


def test_an_already_registered_client_is_not_registered_twice(
    git_free_tmp: Path, home: Path
) -> None:
    runs = FakeRuns({"mcp list": "ledgerbox  something"})

    events = first_run(
        data_dir=git_free_tmp / "ledger-data",
        client="codex",
        home=home,
        run=runs,
    )

    assert [command[1:3] for command in runs.seen] == [["mcp", "list"]]
    assert any("already registered" in event for event in events)


def test_a_custom_personal_skill_stops_everything_before_registration(
    git_free_tmp: Path, home: Path
) -> None:
    target = home / ".agents" / "skills" / "ledgerbox"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("my own edits\n", encoding="utf-8")
    runs = FakeRuns()

    with pytest.raises(FirstRunError, match="doctor"):
        first_run(
            data_dir=git_free_tmp / "ledger-data",
            client="codex",
            home=home,
            run=runs,
        )

    assert runs.seen == [], "a failed install must leave MCP registration untouched"


def test_a_failed_registration_is_a_failure_not_a_shrug(
    git_free_tmp: Path, home: Path
) -> None:
    runs = FakeRuns(
        {
            "mcp list": "nothing",
            "mcp add": subprocess.CompletedProcess(["x"], 2, stdout="", stderr="boom"),
        }
    )

    with pytest.raises(FirstRunError, match="registration failed"):
        first_run(
            data_dir=git_free_tmp / "ledger-data",
            client="codex",
            home=home,
            run=runs,
        )


def test_a_data_dir_inside_a_git_repository_is_refused_with_the_guards_sentence(
    git_free_tmp: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = git_free_tmp / "repo"
    (repo_dir / ".git").mkdir(parents=True)
    runs = FakeRuns()

    with pytest.raises(FirstRunError, match="git"):
        first_run(data_dir=repo_dir / "data", client="codex", home=home, run=runs)

    assert runs.seen == []


def test_the_claude_registration_uses_the_probed_working_shape(
    git_free_tmp: Path, home: Path
) -> None:
    runs = FakeRuns({"mcp list": ""})

    first_run(
        data_dir=git_free_tmp / "ledger-data",
        client="claude-code",
        home=home,
        run=runs,
    )

    add = runs.seen[1]
    assert "claude" in add[0].lower()
    # The one argument order that survives the PowerShell/npm-shim boundary:
    # command first, then env pairs; never add-json, never inline flags after --.
    assert "add-json" not in add
    assert add.index("ledgerbox") < add.index("-e")
    assert any(part.startswith("LEDGERBOX_MCP_CLIENT=") for part in add)
    assert any(part.startswith("LEDGERBOX_DATA_DIR=") for part in add)


def test_the_cli_refuses_to_choose_where_financial_records_live(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ledgerbox.cli import main

    monkeypatch.delenv("LEDGERBOX_DATA_DIR", raising=False)

    code = main(["setup", "--client", "codex"])

    captured = capsys.readouterr()
    assert code == 2
    assert captured.out == ""
    assert "--data-dir" in captured.err and "git" in captured.err


def test_the_cli_prints_each_setup_event_in_order(
    git_free_tmp: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ledgerbox.cli import main

    monkeypatch.setattr(
        "ledgerbox.first_run.first_run",
        lambda **kwargs: [f"synthetic event for {kwargs['client']}"],
    )

    code = main(
        ["--data-dir", str(git_free_tmp / "fresh"), "setup", "--client", "claude"]
    )

    captured = capsys.readouterr()
    assert code == 0
    assert "synthetic event for claude" in captured.out


def test_a_missing_client_executable_names_what_to_install(
    git_free_tmp: Path, home: Path
) -> None:
    runs = FakeRuns({"mcp list": FileNotFoundError("no codex")})

    with pytest.raises(FirstRunError, match="not installed"):
        first_run(
            data_dir=git_free_tmp / "ledger-data",
            client="codex",
            home=home,
            run=runs,
        )
