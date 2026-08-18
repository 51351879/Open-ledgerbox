# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refuse to publish a tag that does not name the version being built.

``docs/RELEASE_PLAN.md`` §2 asked for this in one line: the release job runs
only when the tag and ``pyproject.toml`` agree. It is worth its own file
because the failure it prevents is unfixable. A wheel is uploaded to PyPI once
under one filename; the filename comes from ``pyproject.toml`` and the release
notes, the announcement and every ``git checkout`` come from the tag. If they
disagree, the index has an artifact nobody can reproduce from a named commit,
and PyPI does not allow re-uploading the name to correct it.

    python tools/check_release_tag.py v0.1.0a1

The tag is read from the argument, or from ``GITHUB_REF_NAME`` when there is
none -- the workflow already has it in the environment, and a value passed
twice is a value that can be passed inconsistently.

Note what this does *not* check: that the version is new, that the changelog
mentions it, or that anybody meant to release today. PyPI answers the first,
``tests/test_repo_hygiene.py`` answers the second, and the third is what
pushing a tag *is*.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def declared_version() -> str:
    """The one version this repository packages."""
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    version = tomllib.loads(text)["project"]["version"]
    assert isinstance(version, str)
    return version


def tag_failures(tag: str, declared: str) -> list[str]:
    """Why ``tag`` may not publish ``declared``, or nothing at all.

    The comparison is equality after one leading ``v``, not a prefix or a
    normalisation. ``v0.1.0`` and ``v0.1.0a1`` differ by a suffix that changes
    what pip installs by default, and a check generous enough to call them the
    same is generous in exactly the direction that hurts.
    """
    if not tag:
        return ["no tag to check: nothing said which version this is"]
    if not tag.startswith("v"):
        return [f"the tag {tag!r} does not start with 'v'; release tags are v<version>"]
    named = tag[1:]
    if named != declared:
        return [
            f"the tag names version {named!r} and pyproject.toml packages "
            f"{declared!r}; one of them is wrong and PyPI will not let the "
            f"filename be corrected afterwards"
        ]
    return []


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) > 1:
        print(f"expected at most one tag, got {argv}", file=sys.stderr)
        return 2
    tag = argv[0] if argv else os.environ.get("GITHUB_REF_NAME", "")

    declared = declared_version()
    failures = tag_failures(tag, declared)
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1

    print(f"ok: tag {tag} publishes version {declared}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
