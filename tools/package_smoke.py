# SPDX-License-Identifier: AGPL-3.0-or-later
"""Prove the built artifact installs and runs, from inside the fresh venv.

``docs/RELEASE_PLAN.md`` §4a: a wheel built from the sdist, installed with the
``[mcp]`` extra into a virtual environment that has nothing else in it, then
asked three questions -- does ``ledgerbox --version`` answer, is
``ledgerbox-mcp`` there, and did the official Skill come along inside the
package.

This ran by hand once, during A7.5. Once is a demonstration; the point of
moving it here is that it runs on every push, and that the assertions live in
one file instead of being retyped into a YAML step where nothing type-checks
them and no counterexample can reach them.

**Run it with the fresh environment's own interpreter**, from the checkout::

    <venv>/Scripts/python tools/package_smoke.py

The script is in the checkout and the package under test is in the venv, which
is the arrangement that makes the provenance checks below possible.

Two of the checks are about this smoke test rather than about the wheel.
``ledgerbox.agent_workspace`` looks for a source checkout *before* it looks
inside the installed package, so a run that reached the checkout -- an editable
install, a stray ``PYTHONPATH``, a working directory on ``sys.path`` -- would
report the Skill as present while the wheel carried nothing at all. So the
script asserts where the import came from and where the workspace resolved to.
A green result then means the wheel, and not the tree it was built from.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: What each client's own Skill has to say: the two application modes it
#: negotiates with Core.
#:
#: Split from the contract's markers below after this script passed a wheel
#: whose Claude Skill had been replaced with one line of nonsense. The first
#: draft mirrored ``_runner_skill_compatible`` in
#: ``ledgerbox/api/routes/agent_center.py``, which searches the Skill and the
#: contract *concatenated* -- and the contract states all three markers on its
#: own, so no Skill file could ever fail. A check that cannot fail is not a
#: weaker check, it is a sentence.
SKILL_MARKERS = ("review_first", "automatic")

#: What the shared contract has to say. The Skills point at it for the wire
#: format instead of restating it, which is why ``proposal_schema_version``
#: is asserted here and not above.
CONTRACT_MARKERS = ("proposal_schema_version", "review_first", "automatic")

#: Where the shared contract sits inside the packaged workspace.
PACKAGED_CONTRACT = Path("docs/AGENT_CONTRACT.md")

#: The two clients the packaged workspace has to serve, and where each one's
#: Skill lives inside it.
PACKAGED_SKILLS = {
    "codex": Path(".agents/skills/ledgerbox/SKILL.md"),
    "claude-code": Path(".claude/skills/ledgerbox/SKILL.md"),
}


def packaged_version() -> str:
    """The version the artifact was built from, read from the checkout."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = tomllib.loads(text)["project"]["version"]
    assert isinstance(version, str)
    return version


def is_within(path: Path, root: Path) -> bool:
    """True when ``path`` is ``root`` or sits underneath it.

    Both sides are resolved. On Windows a temporary directory arrives as a DOS
    8.3 short path from one side and as its long form from the other, and a
    path comparison that resolved only one side is exactly what went red in the
    first hosted CI run.
    """
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def version_failures(*, reported: str, declared: str, imported: str) -> list[str]:
    """Three statements of one version, compared.

    ``pyproject.toml`` and ``ledgerbox/__init__.py`` each hold the number as a
    literal, and the CLI prints the second one while the wheel is named after
    the first. Nothing had ever compared them, so a release could ship a wheel
    called 0.2.0 whose ``--version`` said 0.1.0, with the changelog gate --
    which reads pyproject alone -- still green.
    """
    failures: list[str] = []
    if imported != declared:
        failures.append(
            f"the installed package says {imported!r} and pyproject.toml says {declared!r}"
        )
    if reported.strip() != f"ledgerbox {declared}":
        failures.append(
            f"ledgerbox --version printed {reported.strip()!r}, "
            f"which is not exactly version {declared!r}"
        )
    return failures


def provenance_failures(*, module_file: Path, workspace: Path, venv_root: Path) -> list[str]:
    """Refuse a green result that came from the checkout instead of the wheel."""
    failures: list[str] = []
    if not is_within(module_file, venv_root):
        failures.append(
            f"ledgerbox was imported from {module_file}, which is not inside the "
            f"fresh environment at {venv_root} -- this run tested the checkout"
        )
    if not is_within(workspace, venv_root):
        failures.append(
            f"the Agent workspace resolved to {workspace}, which is not inside the "
            f"fresh environment at {venv_root} -- the packaged Skill was never read"
        )
    return failures


def _marker_failures(path: Path, label: str, markers: tuple[str, ...]) -> list[str]:
    """One packaged document against the words it has to contain."""
    if not path.is_file():
        return [f"the wheel carries no {label}"]
    text = path.read_text(encoding="utf-8")
    missing = [marker for marker in markers if marker not in text]
    if missing:
        return [f"the packaged {label} never mentions {missing}"]
    return []


def skill_failures(workspace: Path) -> list[str]:
    """Both clients' packaged Skills, each judged on its own contents."""
    failures: list[str] = []
    for client, relative in PACKAGED_SKILLS.items():
        failures += _marker_failures(
            workspace / relative,
            f"{client} Skill at {relative.as_posix()}",
            SKILL_MARKERS,
        )
    return failures


def contract_failures(workspace: Path) -> list[str]:
    """The shared contract both Skills send their reader to."""
    return _marker_failures(
        workspace / PACKAGED_CONTRACT,
        f"Agent contract at {PACKAGED_CONTRACT.as_posix()}",
        CONTRACT_MARKERS,
    )


def executable_failures(scripts_dir: Path) -> list[str]:
    """``ledgerbox-mcp`` has to exist as a program, not only as a module.

    The bridge is what a user's Codex or Claude Code launches by name. An extra
    that installs the library but drops the console script is a working import
    and a broken product.
    """
    for suffix in (".exe", ""):
        if (scripts_dir / f"ledgerbox-mcp{suffix}").is_file():
            return []
    return [f"no ledgerbox-mcp program in {scripts_dir}"]


def run_version(scripts_dir: Path) -> tuple[str, list[str]]:
    """What ``ledgerbox --version`` printed, or why nothing was printed.

    Plumbing, kept apart from the judgements above so those stay pure. It
    returns the reason rather than raising: an artifact whose entry point is
    missing has failed this smoke test, and a traceback about ``WinError 2``
    describes the harness instead of the failure.
    """
    try:
        finished = subprocess.run(
            [str(scripts_dir / "ledgerbox"), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError as error:
        return "", [f"ledgerbox --version could not be run: {error}"]
    if finished.returncode != 0:
        return "", [
            f"ledgerbox --version exited {finished.returncode}: {finished.stderr.strip()!r}"
        ]
    return finished.stdout, []


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv:
        print(f"package_smoke takes no arguments, got {argv}", file=sys.stderr)
        return 2

    venv_root = Path(sys.prefix)
    if venv_root == Path(sys.base_prefix):
        print(
            "run this with the fresh environment's own interpreter: it exists to "
            "test an installed artifact, and the interpreter that built it has the "
            "checkout on its path",
            file=sys.stderr,
        )
        return 2

    import ledgerbox
    from ledgerbox.agent_workspace import AgentWorkspaceMissing, agent_workspace_root

    scripts_dir = Path(sys.executable).parent
    reported, failures = run_version(scripts_dir)
    if not failures:
        failures = version_failures(
            reported=reported,
            declared=packaged_version(),
            imported=ledgerbox.__version__,
        )
    failures += executable_failures(scripts_dir)

    workspace: Path | None
    try:
        workspace = agent_workspace_root()
    except AgentWorkspaceMissing as missing:
        workspace = None
        failures.append(f"the installed package resolves no Agent workspace: {missing}")
    if workspace is not None:
        failures += provenance_failures(
            module_file=Path(ledgerbox.__file__),
            workspace=workspace,
            venv_root=venv_root,
        )
        failures += skill_failures(workspace)
        failures += contract_failures(workspace)

    if failures:
        print(f"the installed artifact failed {len(failures)} check(s):", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        return 1

    print(
        f"ok: ledgerbox {ledgerbox.__version__} installed into {venv_root}, "
        f"ledgerbox --version and ledgerbox-mcp both present, and the packaged "
        f"workspace at {workspace} carries both clients' Skills and the contract"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
