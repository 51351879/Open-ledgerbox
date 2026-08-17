# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bronze layer: immutable, content-addressed originals.

Archiving happens **before** identification and parsing, so this module knows
nothing about banks, periods or amounts — it cannot, the file has not been read
yet. Its single obligation is that the exact bytes behind any row in
``ledger.db`` can still be produced years from now, which is what makes the
rebuild invariant of ``docs/EXECUTION_PLAN.md`` §2 enforceable: *the database
must be reconstructible from* ``archive/`` *alone*.

Two consequences run through everything below:

* the destination is a pure function of the file's content and the ingest date
  — no counters, no random suffixes, no wall clock;
* the file's *name* carries no authority. A ``.pdf`` extension is a claim by
  whoever named it; the first five bytes are evidence.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..config import DataPaths
from ..fsutil import is_link_like, make_read_only, sha256_file

__all__ = [
    "MEDIA_TYPE_PDF",
    "MONTH_SHARD",
    "PDF_MAGIC",
    "SHA_NAME",
    "YEAR_SHARD",
    "ArchiveError",
    "ArchivedFile",
    "archive_file",
    "find_archived",
    "is_shard",
    "real_subdirectory",
]

MEDIA_TYPE_PDF = "application/pdf"

#: %PDF-1.x. Required at offset 0 by PDF 1.7 §7.5.2.
PDF_MAGIC = b"%PDF-"

#: How far in the header is looked for. Readers in the wild scan the first
#: kilobyte and use whatever they find; that is a repair heuristic and this
#: layer does not repair. What it does do is not mistake **whitespace** for
#: content — see :func:`pdf_header_offset`.
MAGIC_WINDOW = 1024

#: Streamed rather than slurped: 13 statements of ~130 KB is today's corpus,
#: not a licence to assume every original fits in memory.
_CHUNK = 1024 * 1024

_HEX = frozenset("0123456789abcdef")
_SHA256_HEX_LEN = 64

#: What the archive's own names look like. **One definition, used by everything
#: that walks this directory.**
#:
#: There used to be two, and the difference was load-bearing in the worst way.
#: ``verify``'s survey learned to reject Unicode digits and to refuse to cross a
#: junction; :func:`find_archived` kept ``str.isdigit()`` and kept following
#: links. So moving the archive to another drive and leaving a junction behind —
#: an entirely ordinary thing to do when a disk fills up — produced a ledger
#: that failed verification *and could not be repaired*: re-ingesting the
#: original PDFs, which is the remedy this project documents in four places,
#: found the files through the junction, reported ``duplicate``, and copied
#: nothing back.
#:
#: ``\Z`` rather than ``$``: ``$`` also matches before a trailing newline, and a
#: filename may legally contain one on POSIX.
YEAR_SHARD = re.compile(r"[0-9]{4}\Z")
MONTH_SHARD = re.compile(r"[0-9]{2}\Z")
SHA_NAME = re.compile(r"[0-9a-f]{64}\Z")


def is_shard(parts: tuple[str, ...]) -> bool:
    """``archive/<YYYY>/<MM>/`` — the only directories the archive creates.

    ASCII digits only. ``str.isdigit()`` is true for Arabic-Indic and fullwidth
    digits and a long tail of other code points, so a directory named with them
    passed as a shard and everything inside it became invisible.
    """
    if len(parts) == 1:
        return YEAR_SHARD.match(parts[0]) is not None
    if len(parts) == 2:
        return MONTH_SHARD.match(parts[1]) is not None
    return False


def real_subdirectory(path: Path) -> bool:
    """A directory that is really here, not a link to somewhere else."""
    return path.is_dir() and not is_link_like(path)


class ArchiveError(RuntimeError):
    """The file cannot be archived, or the archive itself is inconsistent."""


@dataclass(frozen=True, slots=True)
class ArchivedFile:
    """Where an original ended up, and whether this call put it there."""

    sha256: str
    #: Relative to ``archive/`` and always ``/``-separated: this string is
    #: persisted in the database and must mean the same thing after the data
    #: directory is copied from Windows to a Linux box.
    rel_path: str
    path: Path
    byte_len: int
    media_type: str
    #: True when the content was already on disk. The caller may then skip
    #: extraction entirely — re-ingest is a no-op, not a second copy.
    already_present: bool


def _shard(ingested_on: date) -> tuple[str, str]:
    """``(YYYY, MM)`` of the ingest date.

    The *statement* period would be the more meaningful shard, but it is not
    known yet — parsing is two steps away, and the whole point of archiving
    first is that a file which cannot be parsed is still preserved.
    """
    return f"{ingested_on.year:04d}", f"{ingested_on.month:02d}"


def find_archived(paths: DataPaths, sha256: str) -> Path | None:
    """The archived copy of *sha256*, looked up across **every** shard.

    The shard is a filing convention; identity is the content hash. So the same
    statement ingested in August and again in January must resolve to the one
    copy already on disk, not gain a second under a different month — otherwise
    "content-addressed" would only hold within a month, and a rebuild would see
    the same document twice.

    Implemented as a scan rather than an index, deliberately. An index answers
    in O(1) but is mutable state that the archive's own bytes cannot regenerate;
    when it disagreed with the directory, the invariant "``archive/`` is
    sufficient" would already be false and nothing would have noticed. The scan
    cannot disagree with the filesystem because it *is* the filesystem. Its cost
    is one listing per year plus one stat per month — at a statement a month,
    a few hundred syscalls after a decade of use.
    """
    if len(sha256) != _SHA256_HEX_LEN or not _HEX.issuperset(sha256):
        raise ArchiveError(f"not a lowercase sha-256 hex digest: {sha256!r}")

    root = paths.archive
    if not root.is_dir():
        return None

    if is_link_like(root):
        # The archive root pointing elsewhere puts every statement outside the
        # directory the guard was given. Refusing to resolve through it is what
        # lets `verify`'s complaint be cleared by re-ingesting.
        return None

    filename = f"{sha256}.pdf"
    # sorted() so a (pathological) duplicate resolves to the same shard on every
    # host: iteration order of a directory is not part of any guarantee.
    for year in sorted(root.iterdir()):
        if not YEAR_SHARD.match(year.name) or not real_subdirectory(year):
            continue
        for month in sorted(year.iterdir()):
            if not MONTH_SHARD.match(month.name) or not real_subdirectory(month):
                continue
            candidate = month / filename
            if candidate.is_file() and not is_link_like(candidate):
                return candidate
    return None


def pdf_header_offset(head: bytes) -> int | None:
    """Where ``%PDF-`` starts, or ``None`` if this is not a PDF we will accept.

    Offset 0 is what the spec requires. **One newline in front of it is what a
    real bank actually served**, and refusing that file as "not a PDF" was a
    confident, wrong diagnosis: it opens in every reader, pdfplumber parses it,
    and the true reason it could not be used was that no parser recognises the
    layout — which is a different sentence, from a different layer, that the
    operator never got to see.

    So leading **whitespace** is accepted, because whitespace is not content.
    Leading anything else is still refused. This is not the scan-for-the-header
    repair that readers do: nothing is skipped or rewritten, the archived bytes
    stay byte-for-byte what arrived, and the content hash covers them all.
    """
    offset = head.find(PDF_MAGIC)
    if offset < 0:
        return None
    return offset if head[:offset].strip() == b"" else None


def _reject_unless_pdf(source: Path) -> int:
    """Validate *source* is a readable, non-empty PDF; return its byte length."""
    if source.is_dir():
        raise ArchiveError(f"cannot archive {source.name}: it is a directory ({source})")
    if not source.is_file():
        raise ArchiveError(f"cannot archive {source.name}: no such file ({source})")

    try:
        byte_len = source.stat().st_size
        with open(source, "rb") as handle:
            head = handle.read(MAGIC_WINDOW)
    except OSError as exc:
        raise ArchiveError(f"cannot read {source.name}: {exc}") from exc

    if byte_len == 0:
        raise ArchiveError(f"cannot archive {source.name}: the file is empty")

    if pdf_header_offset(head) is None:
        found = head.find(PDF_MAGIC)
        detail = (
            f"{PDF_MAGIC.decode()!r} appears at byte {found}, but the {found} byte(s) before "
            f"it are not whitespace"
            if found > 0
            else f"the first bytes are {head[: len(PDF_MAGIC)]!r}"
        )
        raise ArchiveError(
            f"cannot archive {source.name}: this is not a PDF — {detail}. "
            f"The extension is a claim, not evidence."
        )
    return byte_len


def _copy_into_place(source: Path, target: Path, *, expected_sha: str) -> int:
    """Copy *source* to *target* atomically; return the number of bytes written.

    Not :func:`ledgerbox.fsutil.atomic_write_bytes` only because that one takes
    the content as a ``bytes`` object; the temp-file-plus-``os.replace`` shape
    is the same, and for the same reason — a reader must never observe a half
    written original.

    The hash is recomputed from the bytes that actually reach the disk. Hashing
    the source and then copying it is two reads of a file that another process
    is free to modify in between, and an archive whose name did not match its
    content would be a silent lie of exactly the kind this project exists to
    prevent.
    """
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    digest = hashlib.sha256()
    written = 0
    try:
        # fdopen first: on Windows the pending unlink in the failure path cannot
        # remove a file that still has an open handle.
        with os.fdopen(fd, "wb") as writer:
            with open(source, "rb") as reader:
                while chunk := reader.read(_CHUNK):
                    digest.update(chunk)
                    writer.write(chunk)
                    written += len(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        if digest.hexdigest() != expected_sha:
            raise ArchiveError(
                f"{source.name} changed while it was being archived "
                f"(expected {expected_sha}, copied {digest.hexdigest()}); nothing was written"
            )
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):  # already replaced, or never created
            os.unlink(tmp_name)
        raise
    return written


def archive_file(
    paths: DataPaths,
    source: str | os.PathLike[str],
    *,
    ingested_on: date,
) -> ArchivedFile:
    """Copy *source* into ``archive/<YYYY>/<MM>/<sha256>.pdf``, once, ever.

    Re-ingesting content that is already archived — same path, another
    directory, another month — returns ``already_present=True`` and touches
    nothing on disk. Raises :class:`ArchiveError` for anything that is not a
    readable, non-empty PDF.
    """
    src = Path(source)
    byte_len = _reject_unless_pdf(src)
    digest = sha256_file(src)

    existing = find_archived(paths, digest)
    if existing is not None:
        # The archive claims this content; make it prove it. One read of one
        # file per duplicate ingest buys bit-rot detection at the only moment
        # the original is still around to compare against.
        actual = sha256_file(existing)
        if actual != digest:
            raise ArchiveError(
                f"archive corruption: {existing} does not hash to its own name "
                f"(content hashes to {actual}). Refusing to report {src.name} as archived. "
                f"Delete that file from the archive and offer this one again — the copy will "
                f"be rewritten from the bytes you just supplied."
            )
        return ArchivedFile(
            sha256=digest,
            rel_path=existing.relative_to(paths.archive).as_posix(),
            path=existing,
            byte_len=existing.stat().st_size,
            media_type=MEDIA_TYPE_PDF,
            already_present=True,
        )

    year, month = _shard(ingested_on)
    # Refuse to write *through* a link rather than quietly doing it. Replacing a
    # shard with a junction is an ordinary thing to do when a disk fills up, and
    # writing through it would put the statement outside the directory the guard
    # was given — while `verify` correctly reports the ledger as broken and the
    # documented repair (re-ingest the originals) appears to succeed and fixes
    # nothing. Refusing here turns a dead end into an instruction.
    # `is_link_like` first, and no `exists()` guard: `exists()` follows the link,
    # so a *dangling* one answered False and the case most in need of the
    # instruction below got a bare "[WinError 183] Cannot create a file when
    # that file already exists" instead. `is_link_like` uses `lstat`, which sees
    # the link whether or not its target is still there.
    for component in (paths.archive, paths.archive / year, paths.archive / year / month):
        if is_link_like(component):
            raise ArchiveError(
                f"cannot archive {src.name}: {component} is a link, not a real directory. "
                f"The archive has to hold real files inside the data directory — otherwise "
                f"statements end up somewhere ledgerbox never checked, and `verify` cannot be "
                f"satisfied by re-ingesting them. Replace the link with a directory (moving the "
                f"files back into it) and try again."
            )

    target = paths.archive / year / month / f"{digest}.pdf"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        written = _copy_into_place(src, target, expected_sha=digest)
    except OSError as exc:
        raise ArchiveError(f"cannot archive {src.name} to {target}: {exc}") from exc

    # Immutability is intent made mechanical; it is also why the duplicate path
    # above never opens the target for writing.
    make_read_only(target)

    if written != byte_len:  # pragma: no cover — the hash would have caught it first
        raise ArchiveError(f"{src.name}: copied {written} bytes, expected {byte_len}")

    return ArchivedFile(
        sha256=digest,
        rel_path=f"{year}/{month}/{digest}.pdf",
        path=target,
        byte_len=written,
        media_type=MEDIA_TYPE_PDF,
        already_present=False,
    )
