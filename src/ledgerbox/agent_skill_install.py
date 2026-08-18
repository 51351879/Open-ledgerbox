# SPDX-License-Identifier: AGPL-3.0-or-later
"""Install and inspect the official classification Skill at user scope.

The checkout and packaged Agent workspace remain the canonical source.  This
module materialises a small self-contained client bundle from that source; it
never teaches a second copy of the classification rules.  A private manifest
records exactly what Ledgerbox installed so a later release can distinguish an
untouched official bundle from a user's custom Skill before replacing anything.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .agent_workspace import agent_workspace_root

AgentClient = Literal["codex", "claude-code"]
SkillState = Literal["missing", "current", "outdated", "custom"]
InstallAction = Literal["installed", "already_current", "upgraded", "replaced_custom"]

OFFICIAL_SKILL_VERSION = "official-classification-v1"
MANIFEST_NAME = ".ledgerbox-skill.json"
MANIFEST_FORMAT_VERSION = 1

# Future releases append the exact file map shipped by older releases before
# changing the current bundle.  A manifest is not authority merely because it
# calls itself official: only a fingerprint in this package's catalogue may be
# upgraded without explicit custom-Skill confirmation.
# The bundle shipped before the empty-proposal (all-abstention) reporting
# protocol was added to the contract and references on 2026-08-12. The
# knowledge version string is unchanged -- classification semantics did not
# move -- so releases are told apart here by fingerprint, as designed.
_BEFORE_ABSTENTION_PROTOCOL: dict[AgentClient, dict[str, str]] = {
    "codex": {
            "SKILL.md": "e07e069879343f672d7b1ffeca140f14264f3e1cfb987ae7e3ec080b5cc07b4f",
            # Hashed from the checkout's on-disk bytes (CRLF), not `git show`
            # (LF): installs read the working tree, so the catalogue must too.
            # The first recording used git output and misread a real untouched
            # install as custom.
            "agents/openai.yaml": (
                "1cb29aeb8b34557a694b1854621b314a82bd8f939b46ee3f8c86686ff79b5f2b"
            ),
            "references/agent-contract.md": (
                "643283721cd9f8b258b3cdb83c632b9f4008bb91af44730cb8b406a0bc0ac73b"
            ),
            "references/agent-setup.md": (
                "1eb1ddcd0414966fbe739d0447d6b3c4c022c611d6259d995d0d04033a2b4c4a"
            ),
            "references/ambiguous-cases.md": (
                "f92ea8a992923af9ac9d27824db290cac40d35c1fb4f66c64ae27c59881c44fa"
            ),
            "references/category-semantics.md": (
                "4a2bc9b9605939ba7ba7a84d7cd9b32cb4359eeeaf08fc85cb7fcdc1869a2704"
            ),
            "references/grouping-and-abstention.md": (
                "75acb0009e7b852f24ec5eed468863da2be8d805a9048846edb7d9ed93b7c80f"
            ),
            "references/privacy-and-output.md": (
                "c671130e7bf76ab83cee43731ed1ecf943ef48404d1ff52b1ae546dba0ebea49"
            ),
            "references/transfer-boundaries.md": (
                "592b09b613ee4647fff195fb4680d6ea58df51e36134546e1847d356c2b75f4c"
            ),
            "references/workflow.md": (
                "28355904fc0e9c5883ec142e4780ddb5ce5ded46503e6c11308269dae9404675"
            ),
    },
    "claude-code": {
        "SKILL.md": "7ffd854a8066a619ad938f3dfd5a50fd38f4651a662c21dfcb305e7c7c576f27",
            "references/agent-contract.md": (
                "643283721cd9f8b258b3cdb83c632b9f4008bb91af44730cb8b406a0bc0ac73b"
            ),
            "references/agent-setup.md": (
                "1eb1ddcd0414966fbe739d0447d6b3c4c022c611d6259d995d0d04033a2b4c4a"
            ),
            "references/ambiguous-cases.md": (
                "f92ea8a992923af9ac9d27824db290cac40d35c1fb4f66c64ae27c59881c44fa"
            ),
            "references/category-semantics.md": (
                "4a2bc9b9605939ba7ba7a84d7cd9b32cb4359eeeaf08fc85cb7fcdc1869a2704"
            ),
            "references/grouping-and-abstention.md": (
                "75acb0009e7b852f24ec5eed468863da2be8d805a9048846edb7d9ed93b7c80f"
            ),
            "references/privacy-and-output.md": (
                "c671130e7bf76ab83cee43731ed1ecf943ef48404d1ff52b1ae546dba0ebea49"
            ),
            "references/transfer-boundaries.md": (
                "592b09b613ee4647fff195fb4680d6ea58df51e36134546e1847d356c2b75f4c"
            ),
            "references/workflow.md": (
                "28355904fc0e9c5883ec142e4780ddb5ce5ded46503e6c11308269dae9404675"
            ),
    },
}

# The same bundle one setup-guide revision earlier (git b2d0445): only
# references/agent-setup.md differs. A real personal install from that day was
# read as custom because this fingerprint was missing, which blocked the very
# upgrade path built for it.
_BEFORE_PASTE_SAFE_SETUP: dict[AgentClient, dict[str, str]] = {
    client: {
        **_BEFORE_ABSTENTION_PROTOCOL[client],
        "references/agent-setup.md": (
            "55a304e0ca670620fbfa1797df5e135c8bb3ab64b376aa2fa9527f7071aad4ca"
        ),
    }
    for client in ("codex", "claude-code")
}

# Every prior official release a personal install may legitimately hold, newest
# first. One knowledge version can name several shipped bundles, so each version
# maps to every fingerprint that ever shipped under it.
# The bundle shipped before docs/AGENT_SETUP.md gained the one-command
# `ledgerbox setup` path on 2026-08-12. Hashed from the working tree, per the
# CRLF lesson above.
_BEFORE_ONE_COMMAND_SETUP: dict[AgentClient, dict[str, str]] = {
    "codex": {
        "SKILL.md": "e07e069879343f672d7b1ffeca140f14264f3e1cfb987ae7e3ec080b5cc07b4f",
        "agents/openai.yaml": (
            "1cb29aeb8b34557a694b1854621b314a82bd8f939b46ee3f8c86686ff79b5f2b"
        ),
        "references/agent-contract.md": (
            "d3b79ad98fdf093cb87b0202250beec1686031266a3ad3a1d04bcba642fd8dfa"
        ),
        "references/agent-setup.md": (
            "5fbd3d1f3fd4ca10f6b88e57b24fedf8f039b1c0810adbf81d03ceea63b3f437"
        ),
        "references/ambiguous-cases.md": (
            "f92ea8a992923af9ac9d27824db290cac40d35c1fb4f66c64ae27c59881c44fa"
        ),
        "references/category-semantics.md": (
            "4a2bc9b9605939ba7ba7a84d7cd9b32cb4359eeeaf08fc85cb7fcdc1869a2704"
        ),
        "references/grouping-and-abstention.md": (
            "0fe6e01f8828b9eeccd78ab72af6f0722dc8bfba1302b1aba5a7c359eb775b96"
        ),
        "references/privacy-and-output.md": (
            "c671130e7bf76ab83cee43731ed1ecf943ef48404d1ff52b1ae546dba0ebea49"
        ),
        "references/transfer-boundaries.md": (
            "592b09b613ee4647fff195fb4680d6ea58df51e36134546e1847d356c2b75f4c"
        ),
        "references/workflow.md": (
            "fb3d6ef7761edd2ab504cf9547b5c85c262b18443c7dfb9dd241725c14b0c36a"
        ),
    },
    "claude-code": {
        "SKILL.md": "7ffd854a8066a619ad938f3dfd5a50fd38f4651a662c21dfcb305e7c7c576f27",
        "references/agent-contract.md": (
            "d3b79ad98fdf093cb87b0202250beec1686031266a3ad3a1d04bcba642fd8dfa"
        ),
        "references/agent-setup.md": (
            "5fbd3d1f3fd4ca10f6b88e57b24fedf8f039b1c0810adbf81d03ceea63b3f437"
        ),
        "references/ambiguous-cases.md": (
            "f92ea8a992923af9ac9d27824db290cac40d35c1fb4f66c64ae27c59881c44fa"
        ),
        "references/category-semantics.md": (
            "4a2bc9b9605939ba7ba7a84d7cd9b32cb4359eeeaf08fc85cb7fcdc1869a2704"
        ),
        "references/grouping-and-abstention.md": (
            "0fe6e01f8828b9eeccd78ab72af6f0722dc8bfba1302b1aba5a7c359eb775b96"
        ),
        "references/privacy-and-output.md": (
            "c671130e7bf76ab83cee43731ed1ecf943ef48404d1ff52b1ae546dba0ebea49"
        ),
        "references/transfer-boundaries.md": (
            "592b09b613ee4647fff195fb4680d6ea58df51e36134546e1847d356c2b75f4c"
        ),
        "references/workflow.md": (
            "fb3d6ef7761edd2ab504cf9547b5c85c262b18443c7dfb9dd241725c14b0c36a"
        ),
    },
}

# The bundle shipped before `descriptor_template` and `occurrences` were added
# to the candidate wire on 2026-08-18. The contract and the grouping reference
# both had to say what the two fields are and, more importantly, what they are
# not; classification semantics did not move, so the knowledge version is
# unchanged and releases are told apart here by fingerprint, as designed.
_BEFORE_CANDIDATE_TEMPLATE_FIELDS: dict[AgentClient, dict[str, str]] = {
    "codex": {
        "SKILL.md": "e07e069879343f672d7b1ffeca140f14264f3e1cfb987ae7e3ec080b5cc07b4f",
        "agents/openai.yaml": (
            "1cb29aeb8b34557a694b1854621b314a82bd8f939b46ee3f8c86686ff79b5f2b"
        ),
        "references/agent-contract.md": (
            "d3b79ad98fdf093cb87b0202250beec1686031266a3ad3a1d04bcba642fd8dfa"
        ),
        "references/agent-setup.md": (
            "a9e6afcb4915efc8c0cf721912ecd53464bea207b7274f10ebf533d0163c9480"
        ),
        "references/ambiguous-cases.md": (
            "f92ea8a992923af9ac9d27824db290cac40d35c1fb4f66c64ae27c59881c44fa"
        ),
        "references/category-semantics.md": (
            "4a2bc9b9605939ba7ba7a84d7cd9b32cb4359eeeaf08fc85cb7fcdc1869a2704"
        ),
        "references/grouping-and-abstention.md": (
            "0fe6e01f8828b9eeccd78ab72af6f0722dc8bfba1302b1aba5a7c359eb775b96"
        ),
        "references/privacy-and-output.md": (
            "c671130e7bf76ab83cee43731ed1ecf943ef48404d1ff52b1ae546dba0ebea49"
        ),
        "references/transfer-boundaries.md": (
            "592b09b613ee4647fff195fb4680d6ea58df51e36134546e1847d356c2b75f4c"
        ),
        "references/workflow.md": (
            "fb3d6ef7761edd2ab504cf9547b5c85c262b18443c7dfb9dd241725c14b0c36a"
        ),
    },
    "claude-code": {
        "SKILL.md": "7ffd854a8066a619ad938f3dfd5a50fd38f4651a662c21dfcb305e7c7c576f27",
        "references/agent-contract.md": (
            "d3b79ad98fdf093cb87b0202250beec1686031266a3ad3a1d04bcba642fd8dfa"
        ),
        "references/agent-setup.md": (
            "a9e6afcb4915efc8c0cf721912ecd53464bea207b7274f10ebf533d0163c9480"
        ),
        "references/ambiguous-cases.md": (
            "f92ea8a992923af9ac9d27824db290cac40d35c1fb4f66c64ae27c59881c44fa"
        ),
        "references/category-semantics.md": (
            "4a2bc9b9605939ba7ba7a84d7cd9b32cb4359eeeaf08fc85cb7fcdc1869a2704"
        ),
        "references/grouping-and-abstention.md": (
            "0fe6e01f8828b9eeccd78ab72af6f0722dc8bfba1302b1aba5a7c359eb775b96"
        ),
        "references/privacy-and-output.md": (
            "c671130e7bf76ab83cee43731ed1ecf943ef48404d1ff52b1ae546dba0ebea49"
        ),
        "references/transfer-boundaries.md": (
            "592b09b613ee4647fff195fb4680d6ea58df51e36134546e1847d356c2b75f4c"
        ),
        "references/workflow.md": (
            "fb3d6ef7761edd2ab504cf9547b5c85c262b18443c7dfb9dd241725c14b0c36a"
        ),
    },
}

# The bundle in force through 2026-08-17, before `docs/AGENT_SETUP.md` gained a
# paragraph about the checked-in translation Skill.
#
# **That paragraph shipped without this entry**, which is the failure this
# catalogue exists to prevent and which the comment above `_BEFORE_PASTE_SAFE_SETUP`
# already records happening once. The setup guide is packaged into the Skill as
# `references/agent-setup.md`, so editing a documentation file two directories
# away silently changed every user's official fingerprint and would have read a
# real untouched install as custom -- blocking the very upgrade path built for
# it. The commit after it is the one that noticed. Editing any packaged file is
# a bundle change, whatever the file looks like.
_BEFORE_TRANSLATE_SKILL_NOTE: dict[AgentClient, dict[str, str]] = {
    client: {
        **_BEFORE_CANDIDATE_TEMPLATE_FIELDS[client],
        "references/agent-setup.md": (
            "08d5b6b74925f91d52ef8305ff02c7b68565bcc5d60e4b44cf4faba3b29fa8f1"
        ),
    }
    for client in ("codex", "claude-code")
}

PREVIOUS_OFFICIAL_BUNDLES: dict[AgentClient, dict[str, tuple[dict[str, str], ...]]] = {
    "codex": {
        OFFICIAL_SKILL_VERSION: (
            _BEFORE_ONE_COMMAND_SETUP["codex"],
            _BEFORE_ABSTENTION_PROTOCOL["codex"],
            _BEFORE_PASTE_SAFE_SETUP["codex"],
            _BEFORE_TRANSLATE_SKILL_NOTE["codex"],
            _BEFORE_CANDIDATE_TEMPLATE_FIELDS["codex"],
        ),
    },
    "claude-code": {
        OFFICIAL_SKILL_VERSION: (
            _BEFORE_ONE_COMMAND_SETUP["claude-code"],
            _BEFORE_ABSTENTION_PROTOCOL["claude-code"],
            _BEFORE_PASTE_SAFE_SETUP["claude-code"],
            _BEFORE_TRANSLATE_SKILL_NOTE["claude-code"],
            _BEFORE_CANDIDATE_TEMPLATE_FIELDS["claude-code"],
        ),
    },
}


class SkillInstallConflict(RuntimeError):
    """A custom or concurrently changed Skill cannot be replaced implicitly."""


class SkillBundleInvalid(RuntimeError):
    """The packaged canonical workspace cannot produce a self-contained Skill."""


@dataclass(frozen=True)
class SkillInspection:
    client: AgentClient
    target: Path
    state: SkillState
    installed_version: str | None
    current_version: str
    changed_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillInstallResult:
    client: AgentClient
    target: Path
    action: InstallAction
    skill_version: str


def _user_home() -> Path:
    return Path.home()


def _client(value: str) -> AgentClient:
    if value == "claude":
        return "claude-code"
    if value not in {"codex", "claude-code"}:
        raise ValueError("client must be 'codex', 'claude', or 'claude-code'")
    return cast(AgentClient, value)


def user_skill_target(client: str, *, home: Path | None = None) -> Path:
    """Return the current personal discovery path for one supported client."""
    canonical = _client(client)
    root = _user_home() if home is None else home
    if canonical == "codex":
        return root / ".agents" / "skills" / "ledgerbox"
    return root / ".claude" / "skills" / "ledgerbox"


def _standalone_skill(client: AgentClient, source: str) -> str:
    replacements = (
        ("${CLAUDE_PROJECT_DIR}/docs/AGENT_CONTRACT.md", "references/agent-contract.md"),
        ("${CLAUDE_PROJECT_DIR}/docs/AGENT_SETUP.md", "references/agent-setup.md"),
        ("${CLAUDE_PROJECT_DIR}/.agents/skills/ledgerbox/references/", "references/"),
        ("docs/AGENT_CONTRACT.md", "references/agent-contract.md"),
        ("docs/AGENT_SETUP.md", "references/agent-setup.md"),
    )
    rendered = source
    for old, new in replacements:
        rendered = rendered.replace(old, new)
    if "${CLAUDE_PROJECT_DIR}" in rendered:
        raise SkillBundleInvalid(f"the {client} Skill still depends on a project directory")
    if "references/agent-contract.md" not in rendered:
        raise SkillBundleInvalid(f"the {client} Skill does not name its installed contract")
    return rendered


def _official_files(client: AgentClient) -> dict[str, bytes]:
    workspace = agent_workspace_root()
    if client == "codex":
        adapter = workspace / ".agents" / "skills" / "ledgerbox"
    else:
        adapter = workspace / ".claude" / "skills" / "ledgerbox"
    canonical = workspace / ".agents" / "skills" / "ledgerbox"

    skill_source = (adapter / "SKILL.md").read_text(encoding="utf-8")
    files: dict[str, bytes] = {
        "SKILL.md": _standalone_skill(client, skill_source).encode("utf-8"),
        "references/agent-contract.md": (workspace / "docs" / "AGENT_CONTRACT.md").read_bytes(),
        "references/agent-setup.md": (workspace / "docs" / "AGENT_SETUP.md").read_bytes(),
    }
    for reference in sorted((canonical / "references").glob("*.md")):
        files[f"references/{reference.name}"] = reference.read_bytes()
    if client == "codex":
        metadata = adapter / "agents" / "openai.yaml"
        if metadata.is_file():
            files["agents/openai.yaml"] = metadata.read_bytes()
    return files


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _manifest(client: AgentClient, files: Mapping[str, bytes]) -> bytes:
    document = {
        "format_version": MANIFEST_FORMAT_VERSION,
        "client": client,
        "skill_version": OFFICIAL_SKILL_VERSION,
        "files": {name: _digest(content) for name, content in sorted(files.items())},
    }
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _actual_files(target: Path) -> dict[str, bytes]:
    if not target.is_dir():
        return {}
    return {
        path.relative_to(target).as_posix(): path.read_bytes()
        for path in sorted(target.rglob("*"))
        if path.is_file() and path.name != MANIFEST_NAME
    }


def _read_manifest(target: Path, client: AgentClient) -> tuple[str, dict[str, str]] | None:
    path = target / MANIFEST_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("format_version") != MANIFEST_FORMAT_VERSION:
        return None
    if raw.get("client") != client or not isinstance(raw.get("skill_version"), str):
        return None
    hashes = raw.get("files")
    if not isinstance(hashes, dict) or not all(
        isinstance(name, str) and isinstance(value, str) for name, value in hashes.items()
    ):
        return None
    return str(raw["skill_version"]), cast(dict[str, str], hashes)


def inspect_user_skill(client: str, *, home: Path | None = None) -> SkillInspection:
    """Classify a personal Skill without modifying it or trusting its manifest blindly."""
    canonical = _client(client)
    target = user_skill_target(canonical, home=home)
    if not target.exists():
        return SkillInspection(canonical, target, "missing", None, OFFICIAL_SKILL_VERSION)

    actual = _actual_files(target)
    recorded = _read_manifest(target, canonical)
    if recorded is None:
        return SkillInspection(
            canonical,
            target,
            "custom",
            None,
            OFFICIAL_SKILL_VERSION,
            tuple(sorted(actual)) or (target.name,),
        )

    installed_version, recorded_hashes = recorded
    actual_hashes = {name: _digest(content) for name, content in actual.items()}
    changed = tuple(
        sorted(
            name
            for name in set(recorded_hashes) | set(actual_hashes)
            if recorded_hashes.get(name) != actual_hashes.get(name)
        )
    )
    if changed:
        return SkillInspection(
            canonical,
            target,
            "custom",
            installed_version,
            OFFICIAL_SKILL_VERSION,
            changed,
        )

    expected_hashes = {
        name: _digest(content) for name, content in _official_files(canonical).items()
    }
    if installed_version == OFFICIAL_SKILL_VERSION and actual_hashes == expected_hashes:
        state: SkillState = "current"
        changed_files: tuple[str, ...] = ()
    elif actual_hashes in PREVIOUS_OFFICIAL_BUNDLES[canonical].get(installed_version, ()):
        state = "outdated"
        changed_files = ()
    else:
        state = "custom"
        changed_files = (MANIFEST_NAME,)
    return SkillInspection(
        canonical,
        target,
        state,
        installed_version,
        OFFICIAL_SKILL_VERSION,
        changed_files,
    )


def _write_stage(parent: Path, client: AgentClient, files: Mapping[str, bytes]) -> Path:
    stage = Path(tempfile.mkdtemp(prefix=".ledgerbox-skill-stage-", dir=parent))
    for relative, content in files.items():
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    (stage / MANIFEST_NAME).write_bytes(_manifest(client, files))
    return stage


def _replace_directory(target: Path, stage: Path) -> None:
    if not target.exists():
        stage.replace(target)
        return
    backup = target.parent / f".ledgerbox-skill-backup-{uuid.uuid4().hex}"
    target.replace(backup)
    try:
        stage.replace(target)
    except BaseException:
        backup.replace(target)
        raise
    else:
        shutil.rmtree(backup)


def install_user_skill(
    client: str,
    *,
    home: Path | None = None,
    force: bool = False,
    preview: Callable[[tuple[str, ...]], None] | None = None,
    confirm: Callable[[], bool] | None = None,
) -> SkillInstallResult:
    """Install or safely upgrade one personal Skill.

    Missing Skills install directly. Untouched older official bundles upgrade
    directly. A custom bundle is immutable unless ``force`` is explicit, its
    replacement file list has been shown, and ``confirm`` returns true.
    """
    canonical = _client(client)
    before = inspect_user_skill(canonical, home=home)
    if before.state == "current":
        return SkillInstallResult(
            canonical, before.target, "already_current", OFFICIAL_SKILL_VERSION
        )

    action: InstallAction
    if before.state == "custom":
        if not force:
            raise SkillInstallConflict("custom Skill found; refusing to overwrite it")
        replacements = tuple(sorted(set(before.changed_files) | set(_official_files(canonical))))
        if preview is not None:
            preview(replacements)
        if confirm is None or not confirm():
            raise SkillInstallConflict("custom Skill replacement was not confirmed")
        action = "replaced_custom"
    elif before.state == "outdated":
        action = "upgraded"
    else:
        action = "installed"

    files = _official_files(canonical)
    target = before.target
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = _write_stage(target.parent, canonical, files)
    try:
        # Fail closed if the directory changed after the decision or confirmation.
        if inspect_user_skill(canonical, home=home) != before:
            raise SkillInstallConflict("Skill changed during installation; nothing was replaced")
        _replace_directory(target, stage)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return SkillInstallResult(canonical, target, action, OFFICIAL_SKILL_VERSION)
