# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared fixtures.

Two host realities this file works around:

1. ``tempfile.gettempdir()`` may itself live inside a git repository (an
   accidental ``git init`` in the home directory is common). The data-dir guard
   would then refuse every temp path, so tests that need a *writable, non-repo*
   directory use :func:`git_free_tmp` instead of pytest's ``tmp_path``.
2. Real statements live outside the repo and may be absent. Tests that need
   them **skip**, never fail — CI must not require real financial data.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from ledgerbox.config import find_git_marker, real_fixtures_dir

ENV_TEST_TMPDIR = "LEDGERBOX_TEST_TMPDIR"


def force_rmtree(path: Path) -> None:
    """Remove *path*, including the read-only files ledgerbox deliberately makes.

    ``shutil.rmtree(..., ignore_errors=True)`` cannot delete a read-only file on
    Windows and, being told to ignore errors, says nothing about it. Every
    archived statement is chmod'd read-only on purpose, so every run left its
    temp root behind -- 72 of them accumulated over one session here. That is
    only litter until a pid is recycled, at which point ``git_free_tmp`` finds
    its directory already present and the suite dies with ``FileExistsError``
    in 45 places at once, for a reason nowhere near the code being tested.

    Clearing the bit first is version-independent; ``onexc`` is 3.12+ and this
    project supports 3.11.
    """
    if not path.exists():
        return
    for child in path.rglob("*"):
        try:
            if child.is_file():
                child.chmod(0o600)
        except OSError:
            continue
    shutil.rmtree(path, ignore_errors=True)


def _candidate_roots() -> Iterator[Path]:
    override = os.environ.get(ENV_TEST_TMPDIR, "").strip()
    if override:
        yield Path(override)
    yield Path(tempfile.gettempdir())
    # Last resort: a named directory at the root of the drive holding the repo.
    yield Path(Path(__file__).resolve().anchor) / "ledgerbox-test-tmp"


@pytest.fixture(scope="session")
def git_free_tmp_root() -> Iterator[Path]:
    """A session-scoped temp root guaranteed to have no ``.git`` ancestor."""
    for candidate in _candidate_roots():
        if find_git_marker(candidate) is not None:
            continue
        base = candidate / f"lbx-{os.getpid()}"
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".writable"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            continue
        try:
            yield base
        finally:
            force_rmtree(base)
            # Leave no empty shell behind if we invented this root ourselves.
            with contextlib.suppress(OSError):
                candidate.rmdir()
        return
    pytest.skip("no writable temp directory outside a git repository on this host")


@pytest.fixture
def git_free_tmp(git_free_tmp_root: Path, request: pytest.FixtureRequest) -> Iterator[Path]:
    """Per-test directory under :func:`git_free_tmp_root`."""
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in request.node.name)
    path = git_free_tmp_root / safe_name[:80]
    force_rmtree(path)
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        force_rmtree(path)


@pytest.fixture(scope="session")
def real_parsed(real_statements: list[Path]) -> list:
    """The 13 real statements, parsed once and shared across modules."""
    from ledgerbox.ingest.extract import extract_spans
    from ledgerbox.ingest.registry import identify_or_raise

    parsed = []
    for path in real_statements:
        doc = extract_spans(path)
        parsed.append(identify_or_raise(doc).parse(doc))
    return sorted(parsed, key=lambda statement: statement.period_end)


@pytest.fixture(scope="session")
def real_statements() -> list[Path]:
    """The 13 real Chase statements, or skip.

    Never fails on their absence: ``LEDGERBOX_REAL_FIXTURES`` points outside the
    repository and is expected to be unset on CI.
    """
    directory = real_fixtures_dir()
    if directory is None:
        pytest.skip("LEDGERBOX_REAL_FIXTURES not set — skipping real-statement regression")
    pdfs = sorted(directory.glob("*.pdf"))
    if not pdfs:
        pytest.skip(f"no PDFs under {directory}")
    return pdfs


@pytest.fixture(scope="session")
def real_expected(real_statements: list[Path]) -> dict[str, int]:
    """The real corpus's expected totals, read from beside the statements.

    These numbers used to be module constants, which put the owner's real
    aggregate figures in the repository. They are facts about the private
    corpus, so they live with the private corpus: an untracked
    ``expected-totals.json`` in the fixtures directory. The repository keeps
    only the synthetic story figures.
    """
    path = real_statements[0].parent / "expected-totals.json"
    if not path.is_file():
        pytest.skip(
            "expected-totals.json is missing beside the real statements; create it "
            "with rows, months, claimed, deposits_minor, withdrawals_minor, "
            "net_minor, opening_minor and closing_minor measured from the corpus"
        )
    values = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "rows",
        "months",
        "claimed",
        "deposits_minor",
        "withdrawals_minor",
        "net_minor",
        "opening_minor",
        "closing_minor",
    }
    missing = required - values.keys()
    if missing:
        pytest.skip(f"expected-totals.json lacks {sorted(missing)}")
    return {name: int(values[name]) for name in required}
