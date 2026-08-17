# SPDX-License-Identifier: AGPL-3.0-or-later
"""Filesystem primitives: atomic writes and content hashing.

Every write of user data goes through here. The predecessor truncated its CSV
with ``open(path, "w")`` before knowing whether the new content was any good;
a crash in between left nothing behind.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import stat
import tempfile
from pathlib import Path

_CHUNK = 1024 * 1024

#: Set on symlinks, junctions and every other Windows reparse point.
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


def is_link_like(path: str | os.PathLike[str]) -> bool:
    """True for a symlink, a junction, or any other reparse point.

    Lives here, in one place, because two different definitions of "is this a
    real directory" is exactly how the archive grew a hole: the function that
    *reported* on the archive refused to cross a link while the functions that
    *searched* and *deleted* crossed it happily.

    ``Path.is_symlink()`` alone is **not enough on Windows** — it returns False
    for a junction, which made a guard written for exactly that case unable to
    see it. ``Path.is_junction()`` says this in one call and arrived in 3.12;
    this project supports 3.11. ``st_file_attributes`` exists only on Windows,
    hence the ``AttributeError`` arm rather than a platform test.
    """
    target = Path(path)
    if target.is_symlink():
        return True
    try:
        return bool(os.lstat(target).st_file_attributes & _REPARSE_POINT)
    except (AttributeError, OSError):
        return False


def atomic_write_bytes(path: str | os.PathLike[str], data: bytes) -> Path:
    """Write *data* to *path* atomically: temp file in the same dir + replace.

    Same directory matters — ``os.replace`` is only atomic within one
    filesystem. The target is either the old content or the new content, never
    a truncated mixture.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    return target


def atomic_write_text(
    path: str | os.PathLike[str],
    text: str,
    *,
    encoding: str = "utf-8",
    newline: str = "\n",
) -> Path:
    """Atomically write *text*, UTF-8 and ``\\n`` by default on every platform."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        normalized = normalized.replace("\n", newline)
    return atomic_write_bytes(path, normalized.encode(encoding))


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Lowercase hex SHA-256 of a file's bytes — the content address."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_read_only(path: str | os.PathLike[str]) -> None:
    """Drop the write bit. Archived originals are immutable by intent.

    Best-effort: a failure here must never abort an ingest.
    """
    target = Path(path)
    try:
        mode = target.stat().st_mode
        target.chmod(mode & ~0o222)
    except OSError:  # pragma: no cover — exotic filesystems
        pass
