# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refuse to let a statement, a ledger or a spreadsheet become a tracked file.

``.gitignore`` is a mitigation and it has a hole this closes: **it does not
apply to files that are already tracked.** Anything added with ``git add -f``,
or added before a rule existed, stays in the index forever and the ignore file
says nothing about it. This asks the index directly.

Runs anywhere ``git`` does, so it is a pre-push habit and not only a CI step —
a gate that only exists on a server is a gate nobody meets until it is too late.

    python tools/check_repo_data.py

Exit status is 0 when the index is clean and 1 when it is not, and the failure
prints every offending path. It never prints file *contents*: this runs when
something has already gone wrong, and the one thing that must not happen next is
a real account number scrolling through a public CI log.

It asks a second question of the same index: **is every tracked name one a
person could have meant?** A shell redirect that lands one character off leaves
a file called something like ``=ro`` or ``2>&1``, and ``git add -A`` picks it up.
Once tracked it is invisible to everything: ``git status`` is clean, the ignore
file has nothing to say, and the data check above passes because it is not a
statement. Three layers of hygiene in this repository let exactly that through
and it took an acceptance run reading ``git ls-files`` by hand to find it.

Note what this does **not** do. It checks names, not contents; a real statement
committed as ``notes.txt`` passes here. ``tests/test_repo_hygiene.py`` is the
content-shaped half, and it is the one with teeth — see ``docs/STATUS.md`` §6.5
for the six times something real got in anyway.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Formats a bank hands you, plus everything that is a ledger by another name.
#: Extensions are matched case-insensitively — ``.PDF`` is a PDF.
DATA_SUFFIXES = frozenset(
    {
        # what a bank sends you
        ".pdf", ".ofx", ".qfx", ".qbo", ".qif", ".mt940", ".camt", ".sta",
        # what you turn it into
        ".csv", ".tsv", ".xls", ".xlsx", ".xlsm",
        # the extraction cache: `extracted/<sha>.ndjson` is the *whole text
        # layer* -- account number, legal name, street address and every
        # counterparty. It is the single most damaging thing in the data
        # directory and it was missing from this list.
        ".ndjson",
        # where it ends up
        ".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".duckdb", ".parquet",
        # the plain-text escape hatch, which holds real balances
        ".beancount", ".ledger", ".journal",
    }
)


def is_data_file(name: str) -> bool:
    """Does this path name a statement, a ledger, a cache or a spreadsheet?

    **Every** suffix is considered, not only the last one. ``ledger.db.bak`` and
    ``statement.pdf.bak`` are precisely what someone produces just before they
    change something, and ``Path.suffix`` sees only ``.bak`` — both walked
    straight through the first version of this gate.

    A dotfile whose entire name is an extension (``.pdf``) has no suffixes at
    all, which is why the name is checked too.

    Erring wide is deliberate: a false positive here costs one argument with a
    contributor, and a false negative costs an account number that cannot be
    rotated.
    """
    path = Path(name)
    if path.name.lower() in DATA_SUFFIXES:
        return True
    return any(suffix.lower() in DATA_SUFFIXES for suffix in path.suffixes)

#: The only places a committed file of those formats can legitimately live.
#:
#: Narrower than the ``^tests/fixtures/`` in ``docs/EXECUTION_PLAN.md`` §7, and
#: deliberately: ``tests/fixtures/spans/`` holds *text layers*, which is the
#: whole reason that directory exists instead of one full of PDFs. These two are
#: exactly the paths ``.gitignore`` re-includes, and both hold data that was
#: generated rather than captured — it cannot contain anything real because it
#: never had anything real.
ALLOWED_PREFIXES = (
    "tests/fixtures/synthetic/",
    "tests/fixtures/malformed/",
)


def tracked_files(repo_root: Path = REPO_ROOT) -> list[str]:
    """Every path in the git index, ``/``-separated on all platforms.

    ``-z`` because a filename may contain anything, newlines included, and git's
    default quoting would otherwise have to be un-quoted correctly to be safe.
    Paths come back ``/``-separated on every platform, Windows included.

    **UTF-8, explicitly.** git emits path bytes; ``text=True`` alone decodes them
    with the locale encoding, which on a Windows console is ``cp1252`` — and
    ``cp1252`` has no mapping for 0x81, 0x8D, 0x8F, 0x90 or 0x9D, all of which
    occur in UTF-8 CJK. A single Chinese filename therefore took this gate down
    with a ``UnicodeDecodeError`` about a byte position, which says nothing about
    the repository. It failed closed, so nothing got through — but a gate that
    reports the wrong problem is a gate somebody switches off. ``surrogateescape``
    keeps a genuinely undecodable byte sequence round-trippable instead of
    raising, and :func:`unmeant_names` will reject the surrogates it produces.
    """
    result = subprocess.run(
        ["git", "ls-files", "-z", "--recurse-submodules"],
        cwd=repo_root,
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
    )
    return [name for name in result.stdout.split("\0") if name]


def offenders(paths: list[str]) -> list[str]:
    """Tracked data files outside the allowlist, sorted."""
    found = []
    for name in paths:
        if any(name.startswith(prefix) for prefix in ALLOWED_PREFIXES):
            continue
        if is_data_file(name):
            found.append(name)
    return sorted(found)


#: What a path component in this repository is allowed to look like: ASCII
#: letters, digits, ``_``, ``.`` and ``-``, starting with any of those but ``-``
#: (a name that is an option) and with at most one leading ``.`` (a dotfile).
#:
#: Every file here is source, prose or configuration and every one of their names
#: is plain ASCII. **That is a property of this repository, not a law**, and it is
#: the only reason the rule can be this narrow; a project with prose filenames in
#: its own language would have to widen it, and the failure message says so.
#:
#: A false positive costs one commit's argument and is noticed immediately, by
#: the person adding the file. A false negative is a file nobody meant to commit,
#: tracked forever, invisible to `git status` and to every other check here.
_SANE_COMPONENT = re.compile(r"\.?[A-Za-z0-9_][A-Za-z0-9._-]*\Z")


def unmeant_names(paths: list[str]) -> list[str]:
    """Tracked paths with a component that is not a plain ASCII filename, sorted.

    Checks each component rather than the whole path, so the separator is not
    part of the vocabulary and a directory with a mangled name is caught by its
    own name rather than by whatever is inside it.

    **What this actually catches**, stated narrowly because the first version of
    this docstring promised more than the pattern delivers: names containing
    characters a filename here has no business containing — ``=``, ``>``, ``&``,
    ``~``, ``$``, quotes, spaces, control characters, anything non-ASCII
    (including the surrogates :func:`tracked_files` produces for undecodable
    bytes) — plus names that lead with ``-`` or with more than one dot.

    **What it does not catch**, and cannot: a mangled redirect whose leftover is
    a *plain* name. ``cmd 2>1`` leaves a file called ``1``, and ``1`` is a
    perfectly ordinary filename. So are ``core``, ``a.out``, ``nohup.out`` and
    ``tmp``. A shape rule cannot separate those from something deliberate, and
    pretending otherwise would be this file making the kind of claim
    ``docs/STATUS.md`` §9 rule 11 is about.
    """
    found = []
    for name in paths:
        for component in name.split("/"):
            if _SANE_COMPONENT.match(component) is None:
                found.append(name)
                break
    return sorted(found)


def main() -> int:
    try:
        paths = tracked_files()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"cannot read the git index: {exc}", file=sys.stderr)
        return 1

    bad = offenders(paths)
    if bad:
        print(
            f"{len(bad)} statement/ledger file(s) are tracked by git. Financial data "
            f"belongs in the OS data directory, outside this repository:",
            file=sys.stderr,
        )
        for name in bad:
            print(f"  {name}", file=sys.stderr)
        print(
            "\nRemoving the file is not enough once it has been pushed — the object "
            "stays in the history. See docs/PROJECT_SUMMARY.md §4 for why this project "
            "started a fresh repository rather than rewriting one.",
            file=sys.stderr,
        )
        return 1

    unmeant = unmeant_names(paths)
    if unmeant:
        print(
            f"{len(unmeant)} tracked path(s) have a component nobody would have typed. "
            f"A mangled shell redirect or an editor's scratch file becomes invisible "
            f"once it is tracked — `git status` is clean and nothing else here looks "
            f"at names:",
            file=sys.stderr,
        )
        for name in unmeant:
            print(f"  {name}", file=sys.stderr)
        print(
            "\nRemove it with `git rm`. If the name is deliberate, widen "
            "_SANE_COMPONENT in tools/check_repo_data.py — it is ASCII-only "
            "because every filename in this repository is, which is a fact about "
            "this repository rather than a rule about filenames.",
            file=sys.stderr,
        )
        return 1

    print(
        f"ok: {len(paths)} tracked file(s), none of them a statement or a ledger, "
        f"and every name is one somebody meant"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
