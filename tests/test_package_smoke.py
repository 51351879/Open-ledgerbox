# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release smoke test's own counterexamples.

``tools/package_smoke.py`` runs in CI, inside an environment this suite never
sees: a fresh venv holding a wheel and nothing else. So the parts that decide
whether it passes are written as functions over values, and every one of them
is refuted here before it is trusted there.

Discipline rule 7 -- a guard gets its own counterexamples -- has a sharper
point for this one. The whole job is a machine saying "the artifact is fine",
and the only thing standing between that sentence and a wheel with no Skill in
it is whether these functions can still fail.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import ledgerbox
from tools.package_smoke import (
    CONTRACT_MARKERS,
    PACKAGED_CONTRACT,
    PACKAGED_SKILLS,
    SKILL_MARKERS,
    contract_failures,
    executable_failures,
    is_within,
    packaged_version,
    provenance_failures,
    skill_failures,
    version_failures,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, markers: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("official document: " + " ".join(markers) + "\n", encoding="utf-8")


def _workspace(
    root: Path,
    *,
    skill_markers: tuple[str, ...] = SKILL_MARKERS,
    contract_markers: tuple[str, ...] = CONTRACT_MARKERS,
) -> Path:
    """A minimal packaged workspace: both Skills and the shared contract."""
    for relative in PACKAGED_SKILLS.values():
        _write(root / relative, skill_markers)
    _write(root / PACKAGED_CONTRACT, contract_markers)
    return root


def test_the_packaged_version_is_the_one_the_repository_declares() -> None:
    """The literal in ``__init__.py`` against the literal in ``pyproject.toml``.

    Two files, one number, and until now nothing compared them: the changelog
    gate reads pyproject, ``ledgerbox --version`` and ``/api/health`` print
    ``__version__``, and a release could have shipped them disagreeing.
    """
    declared = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert ledgerbox.__version__ == declared["project"]["version"], (
        "the packaged version and the imported version are two literals in two "
        "files and they have to stay one number"
    )
    assert packaged_version() == ledgerbox.__version__


def test_a_version_that_disagrees_with_itself_is_reported() -> None:
    assert version_failures(reported="ledgerbox 1.2.3\n", declared="1.2.3", imported="1.2.3") == []
    mismatch = version_failures(reported="ledgerbox 1.2.3\n", declared="1.2.3", imported="0.9.0")
    assert len(mismatch) == 1
    assert "0.9.0" in mismatch[0]
    silent = version_failures(reported="ledgerbox 0.9.0\n", declared="1.2.3", imported="1.2.3")
    assert len(silent) == 1
    assert "not exactly version" in silent[0]


def test_a_version_string_is_matched_whole_and_not_as_a_prefix() -> None:
    """``0.1.0`` is a prefix of ``0.1.0a1``, in both directions.

    The first draft asked whether the declared version appeared *in* the
    printed line, which passes a 0.1.0a1 build declaring 0.1.0 -- the exact
    pre-release confusion this project is currently shipping through. The
    comparison is equality now, and both directions are asserted so it stays
    equality.
    """
    assert version_failures(
        reported="ledgerbox 0.1.0a1\n", declared="0.1.0a1", imported="0.1.0a1"
    ) == []
    assert version_failures(reported="ledgerbox 0.1.0\n", declared="0.1.0a1", imported="0.1.0a1")
    assert version_failures(reported="ledgerbox 0.1.0a1\n", declared="0.1.0", imported="0.1.0")


def test_an_import_from_the_checkout_is_not_a_pass(tmp_path: Path) -> None:
    """The check that keeps this smoke test honest.

    ``agent_workspace_root`` prefers a source checkout over the installed
    package, so a run that reached the tree would report both Skills present
    while the wheel carried none. The failure text has to name that, because
    "everything is fine" is the wrong answer to give here.
    """
    venv = tmp_path / "fresh"
    checkout = tmp_path / "checkout"
    (venv / "Lib" / "site-packages" / "ledgerbox").mkdir(parents=True)
    checkout.mkdir()
    inside = venv / "Lib" / "site-packages" / "ledgerbox" / "__init__.py"
    inside.write_text("", encoding="utf-8")

    assert provenance_failures(module_file=inside, workspace=venv, venv_root=venv) == []

    from_checkout = provenance_failures(
        module_file=checkout / "src" / "ledgerbox" / "__init__.py",
        workspace=venv,
        venv_root=venv,
    )
    assert len(from_checkout) == 1
    assert "tested the checkout" in from_checkout[0]

    workspace_escaped = provenance_failures(
        module_file=inside, workspace=checkout, venv_root=venv
    )
    assert len(workspace_escaped) == 1
    assert "never read" in workspace_escaped[0]


def test_path_containment_resolves_both_sides(tmp_path: Path) -> None:
    """A relative or unresolved side must not decide the answer -- the first
    hosted CI run went red on a Windows short path compared against its own
    long form.
    """
    root = tmp_path / "root"
    (root / "inner").mkdir(parents=True)
    assert is_within(root / "inner", root)
    assert is_within(root, root)
    assert is_within(root / "inner" / ".." / "inner", root)
    assert not is_within(tmp_path / "elsewhere", root)


def test_a_wheel_without_the_skills_fails(tmp_path: Path) -> None:
    complete = _workspace(tmp_path / "complete")
    assert skill_failures(complete) == []
    assert contract_failures(complete) == []

    empty = tmp_path / "empty"
    empty.mkdir()
    missing = skill_failures(empty)
    assert len(missing) == len(PACKAGED_SKILLS)
    assert all("carries no" in failure for failure in missing)
    assert contract_failures(empty) == [
        f"the wheel carries no Agent contract at {PACKAGED_CONTRACT.as_posix()}"
    ]


def test_a_skill_that_negotiates_nothing_fails(tmp_path: Path) -> None:
    """Present but silent is its own failure: the file shipped, and the runner
    still has no mode to agree on.
    """
    mute = _workspace(tmp_path / "mute", skill_markers=("review_first",))
    failures = skill_failures(mute)
    assert len(failures) == len(PACKAGED_SKILLS)
    assert all("automatic" in failure for failure in failures)


def test_a_full_contract_cannot_launder_a_gutted_skill(tmp_path: Path) -> None:
    """The hole this check shipped with, closed and pinned.

    The first draft searched ``skill_text + contract_text``, mirroring
    ``_runner_skill_compatible``. The contract states every marker by itself,
    so replacing a real 22-line Skill with one line of nonsense in an installed
    wheel still produced ``ok:`` -- observed, not theorised. Each document is
    now read on its own terms.
    """
    gutted = _workspace(tmp_path / "gutted", skill_markers=("nothing to negotiate",))
    assert contract_failures(gutted) == [], "the contract itself is intact here"
    failures = skill_failures(gutted)
    assert len(failures) == len(PACKAGED_SKILLS)
    assert all("review_first" in failure and "automatic" in failure for failure in failures)


def test_a_contract_missing_the_wire_version_fails(tmp_path: Path) -> None:
    """``proposal_schema_version`` lives in the contract and nowhere else, so
    the contract is where its absence has to be caught.
    """
    thin = _workspace(tmp_path / "thin", contract_markers=("review_first", "automatic"))
    assert skill_failures(thin) == []
    failures = contract_failures(thin)
    assert len(failures) == 1
    assert "proposal_schema_version" in failures[0]


def test_a_missing_bridge_program_is_reported(tmp_path: Path) -> None:
    """The library importing is not the bridge existing: a user's Codex starts
    ``ledgerbox-mcp`` by name.
    """
    scripts = tmp_path / "Scripts"
    scripts.mkdir()
    assert executable_failures(scripts) == [f"no ledgerbox-mcp program in {scripts}"]

    (scripts / "ledgerbox-mcp.exe").write_bytes(b"")
    assert executable_failures(scripts) == []

    posix = tmp_path / "bin"
    posix.mkdir()
    (posix / "ledgerbox-mcp").write_text("#!/bin/sh\n", encoding="utf-8")
    assert executable_failures(posix) == []
