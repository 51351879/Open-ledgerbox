# SPDX-License-Identifier: AGPL-3.0-or-later
"""A7.5 user-level Skill install, doctor, and safe-upgrade counterexamples."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ledgerbox import agent_skill_install
from ledgerbox.agent_skill_install import (
    OFFICIAL_SKILL_VERSION,
    SkillInstallConflict,
    inspect_user_skill,
    install_user_skill,
    user_skill_target,
)
from ledgerbox.cli import main


@pytest.mark.parametrize(
    "client,relative",
    [
        ("codex", Path(".agents/skills/ledgerbox")),
        ("claude-code", Path(".claude/skills/ledgerbox")),
    ],
)
def test_user_skill_targets_the_clients_current_personal_discovery_directory(
    git_free_tmp: Path, client: str, relative: Path
) -> None:
    assert user_skill_target(client, home=git_free_tmp) == git_free_tmp / relative


@pytest.mark.parametrize("client", ["codex", "claude-code"])
def test_fresh_install_is_self_contained_and_doctor_reports_current(
    git_free_tmp: Path, client: str
) -> None:
    before = inspect_user_skill(client, home=git_free_tmp)
    assert before.state == "missing"

    result = install_user_skill(client, home=git_free_tmp)
    target = user_skill_target(client, home=git_free_tmp)
    skill = (target / "SKILL.md").read_text(encoding="utf-8")

    assert result.action == "installed"
    assert (target / "references/agent-contract.md").is_file()
    assert (target / "references/agent-setup.md").is_file()
    assert (target / "references/workflow.md").is_file()
    assert "official-classification-v1" in "\n".join(
        path.read_text(encoding="utf-8") for path in target.rglob("*.md")
    )
    assert "${CLAUDE_PROJECT_DIR}" not in skill
    assert "docs/AGENT_CONTRACT.md" not in skill
    assert inspect_user_skill(client, home=git_free_tmp).state == "current"


@pytest.mark.parametrize("client", ["codex", "claude-code"])
def test_default_install_never_overwrites_a_custom_skill(
    git_free_tmp: Path, client: str
) -> None:
    target = user_skill_target(client, home=git_free_tmp)
    target.mkdir(parents=True)
    custom = "---\nname: ledgerbox\n---\nmy private instructions\n"
    (target / "SKILL.md").write_text(custom, encoding="utf-8")

    with pytest.raises(SkillInstallConflict, match="custom"):
        install_user_skill(client, home=git_free_tmp)

    assert (target / "SKILL.md").read_text(encoding="utf-8") == custom
    report = inspect_user_skill(client, home=git_free_tmp)
    assert report.state == "custom"
    assert report.installed_version is None


def test_editing_an_official_file_changes_doctor_to_custom_and_blocks_upgrade(
    git_free_tmp: Path,
) -> None:
    install_user_skill("codex", home=git_free_tmp)
    target = user_skill_target("codex", home=git_free_tmp)
    skill = target / "SKILL.md"
    skill.write_text(skill.read_text(encoding="utf-8") + "\nprivate change\n", encoding="utf-8")

    report = inspect_user_skill("codex", home=git_free_tmp)
    assert report.state == "custom"
    assert "SKILL.md" in report.changed_files
    with pytest.raises(SkillInstallConflict, match="custom"):
        install_user_skill("codex", home=git_free_tmp)
    assert skill.read_text(encoding="utf-8").endswith("private change\n")


def test_adding_a_private_file_changes_doctor_to_custom(git_free_tmp: Path) -> None:
    install_user_skill("claude-code", home=git_free_tmp)
    target = user_skill_target("claude-code", home=git_free_tmp)
    private = target / "my-notes.md"
    private.write_text("keep this\n", encoding="utf-8")

    report = inspect_user_skill("claude-code", home=git_free_tmp)

    assert report.state == "custom"
    assert "my-notes.md" in report.changed_files
    with pytest.raises(SkillInstallConflict, match="custom"):
        install_user_skill("claude-code", home=git_free_tmp)
    assert private.read_text(encoding="utf-8") == "keep this\n"


def test_unmodified_older_official_install_upgrades_without_force(
    git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_user_skill("codex", home=git_free_tmp)
    target = user_skill_target("codex", home=git_free_tmp)
    manifest_path = target / agent_skill_install.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["skill_version"] = "official-classification-v0"
    monkeypatch.setitem(
        agent_skill_install.PREVIOUS_OFFICIAL_BUNDLES["codex"],
        "official-classification-v0",
        (manifest["files"],),
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    assert inspect_user_skill("codex", home=git_free_tmp).state == "outdated"
    result = install_user_skill("codex", home=git_free_tmp)

    assert result.action == "upgraded"
    report = inspect_user_skill("codex", home=git_free_tmp)
    assert report.state == "current"
    assert report.installed_version == OFFICIAL_SKILL_VERSION


def test_the_previous_release_fingerprint_is_recorded_before_the_bundle_changed() -> None:
    """The 2026-08-12 protocol change edited the shipped Skill text. Without the
    prior release's fingerprint in the catalogue, every untouched personal
    install would be classified custom and the non-force setup path would brick.
    """
    for client in ("codex", "claude-code"):
        catalogue = agent_skill_install.PREVIOUS_OFFICIAL_BUNDLES[client]
        assert OFFICIAL_SKILL_VERSION in catalogue, (
            "the release being replaced shipped under the same knowledge version"
        )
        releases = catalogue[OFFICIAL_SKILL_VERSION]
        assert len(releases) >= 2, (
            "both shipped prior bundles: pre-abstention-protocol and pre-paste-safe-setup"
        )
        current = {
            name: agent_skill_install._digest(content)
            for name, content in agent_skill_install._official_files(client).items()
        }
        for previous in releases:
            assert "SKILL.md" in previous and "references/workflow.md" in previous
            assert previous != current, "a recorded fingerprint must name a DIFFERENT bundle"
            assert all(
                len(value) == 64 and set(value) <= set("0123456789abcdef")
                for value in previous.values()
            )
        assert len({json.dumps(r, sort_keys=True) for r in releases}) == len(releases), (
            "each recorded release is a distinct bundle"
        )


def test_self_declared_old_manifest_is_custom_and_never_silently_upgraded(
    git_free_tmp: Path,
) -> None:
    install_user_skill("codex", home=git_free_tmp)
    target = user_skill_target("codex", home=git_free_tmp)
    manifest_path = target / agent_skill_install.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["skill_version"] = "made-up-old-official"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    report = inspect_user_skill("codex", home=git_free_tmp)
    assert report.state == "custom"
    assert agent_skill_install.MANIFEST_NAME in report.changed_files
    with pytest.raises(SkillInstallConflict, match="custom"):
        install_user_skill("codex", home=git_free_tmp)


def test_force_lists_replacements_and_requires_confirmation(git_free_tmp: Path) -> None:
    target = user_skill_target("codex", home=git_free_tmp)
    target.mkdir(parents=True)
    custom = target / "SKILL.md"
    custom.write_text("private\n", encoding="utf-8")
    previews: list[tuple[str, ...]] = []

    with pytest.raises(SkillInstallConflict, match="not confirmed"):
        install_user_skill(
            "codex",
            home=git_free_tmp,
            force=True,
            preview=previews.append,
            confirm=lambda: False,
        )

    assert previews and "SKILL.md" in previews[0]
    assert custom.read_text(encoding="utf-8") == "private\n"

    result = install_user_skill(
        "codex",
        home=git_free_tmp,
        force=True,
        preview=previews.append,
        confirm=lambda: True,
    )
    assert result.action == "replaced_custom"
    assert "private" not in custom.read_text(encoding="utf-8")


def test_atomic_replacement_failure_restores_the_original_directory(
    git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = git_free_tmp / "ledgerbox"
    target.mkdir()
    (target / "SKILL.md").write_text("original\n", encoding="utf-8")
    stage = git_free_tmp / "stage"
    stage.mkdir()
    (stage / "SKILL.md").write_text("replacement\n", encoding="utf-8")
    real_replace = Path.replace

    def fail_promoting_stage(self: Path, destination: Path) -> Path:
        if self == stage:
            raise OSError("synthetic promotion failure")
        return real_replace(self, destination)

    monkeypatch.setattr(Path, "replace", fail_promoting_stage)

    with pytest.raises(OSError, match="synthetic promotion failure"):
        agent_skill_install._replace_directory(target, stage)

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "original\n"
    assert not any(
        path.name.startswith(".ledgerbox-skill-backup-") for path in git_free_tmp.iterdir()
    )


def test_agent_skill_cli_doctor_and_install_are_separate_from_ledger_doctor(
    git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(agent_skill_install, "_user_home", lambda: git_free_tmp)

    assert main(["agent", "doctor", "--client", "codex"]) == 1
    missing = capsys.readouterr().out
    assert "missing" in missing
    assert "schema" not in missing.lower()

    assert main(["agent", "install-skill", "--client", "codex"]) == 0
    installed = capsys.readouterr().out
    assert "installed" in installed
    assert OFFICIAL_SKILL_VERSION in installed

    assert main(["agent", "doctor", "--client", "codex"]) == 0
    assert "current" in capsys.readouterr().out

    assert main(["agent", "install-skill", "--client", "codex", "--yes"]) == 2
    assert "--force" in capsys.readouterr().err


def test_unknown_client_fails_before_creating_any_directory(git_free_tmp: Path) -> None:
    with pytest.raises(ValueError, match="client"):
        install_user_skill("other", home=git_free_tmp)
    assert list(git_free_tmp.iterdir()) == []
