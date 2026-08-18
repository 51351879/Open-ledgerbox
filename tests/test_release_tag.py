# SPDX-License-Identifier: AGPL-3.0-or-later
"""Counterexamples for the gate between a pushed tag and PyPI.

The publish step runs once per tag and cannot be taken back: PyPI refuses a
second upload of a filename even after the first is deleted. So the one
judgement standing in front of it is written as a function over two strings and
refuted here, rather than as a shell comparison in a workflow file where the
first time anybody watches it run is the release.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from tools.check_release_tag import declared_version, tag_failures

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_the_declared_version_is_the_packaged_one() -> None:
    packaged = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert declared_version() == packaged["project"]["version"]


def test_a_tag_that_names_the_packaged_version_passes() -> None:
    assert tag_failures("v0.1.0a1", "0.1.0a1") == []


def test_a_tag_naming_another_version_is_refused() -> None:
    failures = tag_failures("v0.2.0", "0.1.0a1")
    assert len(failures) == 1
    assert "0.2.0" in failures[0] and "0.1.0a1" in failures[0]


def test_a_prerelease_suffix_is_part_of_the_version() -> None:
    """``v0.1.0`` against a packaged ``0.1.0a1`` is the mistake most likely to
    happen here, and a prefix comparison would wave it through -- while pip
    treats the two as different releases and installs the final one by default.
    """
    assert tag_failures("v0.1.0", "0.1.0a1")
    assert tag_failures("v0.1.0a1", "0.1.0")


def test_a_tag_without_the_v_is_refused() -> None:
    """Not cosmetic. ``0.1.0a1`` as a tag would be stripped to ``.1.0a1`` by a
    check that removed the first character unconditionally, and the convention
    is stated so the gate can be strict about it.
    """
    failures = tag_failures("0.1.0a1", "0.1.0a1")
    assert len(failures) == 1
    assert "does not start with" in failures[0]


def test_no_tag_at_all_is_refused_rather_than_ignored() -> None:
    """An empty ``GITHUB_REF_NAME`` means the workflow ran somewhere this gate
    cannot reason about. Passing on nothing is how a guard becomes decoration.
    """
    failures = tag_failures("", "0.1.0a1")
    assert len(failures) == 1
    assert "nothing said which version" in failures[0]
