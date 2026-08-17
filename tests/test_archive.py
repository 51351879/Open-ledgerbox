# SPDX-License-Identifier: AGPL-3.0-or-later
"""M6: content-addressed archive of the originals."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from ledgerbox.config import DataPaths
from ledgerbox.fsutil import sha256_bytes, sha256_file
from ledgerbox.ingest import archive as archive_mod
from ledgerbox.ingest.archive import (
    MEDIA_TYPE_PDF,
    ArchivedFile,
    ArchiveError,
    archive_file,
    find_archived,
)

AUG = date(2026, 8, 15)
JAN = date(2027, 1, 3)


def pdf_bytes(marker: bytes = b"one") -> bytes:
    """Minimal bytes that pass the magic-byte gate.

    This layer never opens the PDF — it hashes and copies it — so a real
    document would only make the tests slower and the fixtures unreadable.
    """
    return b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + marker + b"\ntrailer\n%%EOF\n"


def write_pdf(path: Path, marker: bytes = b"one") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf_bytes(marker))
    return path


def archived_files(paths: DataPaths) -> list[Path]:
    return sorted(p for p in paths.archive.rglob("*") if p.is_file())


@pytest.fixture
def paths(git_free_tmp: Path) -> Iterator[DataPaths]:
    """A data directory outside any git repository.

    ``tmp_path`` is unusable here: the guard in :class:`DataPaths` rejects any
    path under a ``.git``, and on this host the system temp directory is one.
    """
    data = DataPaths(git_free_tmp / "data").ensure()
    yield data
    # Archived files are read-only; on Windows rmtree cannot delete those, and
    # the session fixture cleans up with ignore_errors=True — i.e. silently
    # leaving them behind. Put the write bit back so teardown actually works.
    for entry in data.archive.rglob("*"):
        with contextlib.suppress(OSError):
            entry.chmod(0o700)


# --------------------------------------------------------------------------
# the happy path
# --------------------------------------------------------------------------


def test_archives_a_pdf_under_the_ingest_month_shard(paths: DataPaths) -> None:
    source = write_pdf(paths.root.parent / "inbox" / "statement.pdf")
    data = pdf_bytes()

    result = archive_file(paths, source, ingested_on=AUG)

    assert isinstance(result, ArchivedFile)
    assert result.sha256 == sha256_bytes(data)
    assert result.rel_path == f"2026/08/{result.sha256}.pdf"
    assert result.path == paths.archive / "2026" / "08" / f"{result.sha256}.pdf"
    assert result.byte_len == len(data)
    assert result.media_type == MEDIA_TYPE_PDF
    assert result.already_present is False
    assert result.path.read_bytes() == data
    assert source.read_bytes() == data  # the original is copied, never moved


def test_rel_path_uses_forward_slashes_on_every_platform(paths: DataPaths) -> None:
    source = write_pdf(paths.root.parent / "in" / "s.pdf")
    result = archive_file(paths, source, ingested_on=AUG)

    assert "\\" not in result.rel_path
    assert result.rel_path.split("/") == ["2026", "08", f"{result.sha256}.pdf"]
    # rel_path is what the database stores; it must round-trip to the real file.
    assert (paths.archive / result.rel_path).read_bytes() == source.read_bytes()


def test_different_content_gets_different_addresses(paths: DataPaths) -> None:
    first = archive_file(paths, write_pdf(paths.root.parent / "a.pdf", b"a"), ingested_on=AUG)
    second = archive_file(paths, write_pdf(paths.root.parent / "b.pdf", b"b"), ingested_on=AUG)

    assert first.sha256 != second.sha256
    assert len(archived_files(paths)) == 2


def test_the_destination_depends_only_on_content_and_ingest_date(git_free_tmp: Path) -> None:
    """No clock, no counter, no randomness: the rebuild invariant needs this."""
    source = write_pdf(git_free_tmp / "src.pdf")
    one = DataPaths(git_free_tmp / "one").ensure()
    two = DataPaths(git_free_tmp / "two").ensure()

    first = archive_file(one, source, ingested_on=AUG)
    second = archive_file(two, source, ingested_on=AUG)

    assert first.rel_path == second.rel_path
    assert first.sha256 == second.sha256
    for data in (one, two):
        for entry in data.archive.rglob("*"):
            entry.chmod(0o700)


# --------------------------------------------------------------------------
# idempotence — the P0 acceptance criterion
# --------------------------------------------------------------------------


def test_reingesting_the_same_path_is_a_no_op(paths: DataPaths) -> None:
    source = write_pdf(paths.root.parent / "statement.pdf")

    first = archive_file(paths, source, ingested_on=AUG)
    before = first.path.stat()
    second = archive_file(paths, source, ingested_on=AUG)

    assert first.already_present is False
    assert second.already_present is True
    assert second.path == first.path
    assert second.rel_path == first.rel_path
    assert second.sha256 == first.sha256
    assert second.byte_len == first.byte_len
    assert first.path.stat().st_mtime_ns == before.st_mtime_ns  # not rewritten
    assert len(archived_files(paths)) == 1


def test_same_content_from_two_directories_is_archived_once(paths: DataPaths) -> None:
    """The predecessor's "already imported?" check was the file *name*."""
    inbox = write_pdf(paths.root.parent / "inbox" / "20260801-statement.pdf")
    downloads = write_pdf(paths.root.parent / "downloads" / "statement (1).pdf")

    first = archive_file(paths, inbox, ingested_on=AUG)
    second = archive_file(paths, downloads, ingested_on=AUG)

    assert second.already_present is True
    assert second.path == first.path
    assert archived_files(paths) == [first.path]
    assert first.path.read_bytes() == pdf_bytes()


def test_same_content_in_a_later_month_reuses_the_original_shard(paths: DataPaths) -> None:
    """Sharding is filing; identity is the hash. A new month is not a new file."""
    source = write_pdf(paths.root.parent / "statement.pdf")

    first = archive_file(paths, source, ingested_on=AUG)
    second = archive_file(paths, source, ingested_on=JAN)

    assert second.already_present is True
    assert second.rel_path == first.rel_path == f"2026/08/{first.sha256}.pdf"
    assert not (paths.archive / "2027").exists()
    assert len(archived_files(paths)) == 1


def test_find_archived_locates_a_copy_in_any_shard(paths: DataPaths) -> None:
    result = archive_file(paths, write_pdf(paths.root.parent / "s.pdf"), ingested_on=AUG)

    assert find_archived(paths, result.sha256) == result.path
    assert find_archived(paths, sha256_bytes(b"never archived")) is None


def test_find_archived_refuses_a_malformed_digest(paths: DataPaths) -> None:
    with pytest.raises(ArchiveError):
        find_archived(paths, "deadbeef")
    with pytest.raises(ArchiveError):
        find_archived(paths, "A" * 64)  # uppercase is not the canonical form


# --------------------------------------------------------------------------
# immutability
# --------------------------------------------------------------------------


def test_the_archived_copy_is_read_only(paths: DataPaths) -> None:
    result = archive_file(paths, write_pdf(paths.root.parent / "s.pdf"), ingested_on=AUG)

    assert result.path.stat().st_mode & 0o222 == 0
    assert not os.access(result.path, os.W_OK)


def test_reingest_does_not_trip_over_the_read_only_target(paths: DataPaths) -> None:
    """Copying onto a read-only file would raise PermissionError on Windows."""
    source = write_pdf(paths.root.parent / "s.pdf")
    archive_file(paths, source, ingested_on=AUG)

    again = archive_file(paths, source, ingested_on=AUG)

    assert again.already_present is True
    assert again.path.stat().st_mode & 0o222 == 0


def test_corruption_of_an_archived_file_is_reported_not_ignored(paths: DataPaths) -> None:
    source = write_pdf(paths.root.parent / "s.pdf")
    result = archive_file(paths, source, ingested_on=AUG)
    result.path.chmod(0o700)
    result.path.write_bytes(pdf_bytes(b"tampered"))

    with pytest.raises(ArchiveError, match="corruption"):
        archive_file(paths, source, ingested_on=AUG)


# --------------------------------------------------------------------------
# refusal — unknown input is never guessed at
# --------------------------------------------------------------------------


def test_a_file_without_pdf_magic_bytes_is_refused(paths: DataPaths) -> None:
    impostor = paths.root.parent / "statement.pdf"
    impostor.parent.mkdir(parents=True, exist_ok=True)
    impostor.write_bytes(b"PK\x03\x04 this is a zip wearing a pdf extension")

    with pytest.raises(ArchiveError, match="statement.pdf"):
        archive_file(paths, impostor, ingested_on=AUG)
    assert archived_files(paths) == []


def test_leading_whitespace_before_the_header_is_accepted(paths: DataPaths) -> None:
    """This test used to assert the opposite, and the opposite was wrong.

    A real bank served a statement with a single newline before ``%PDF-``. It
    opens in every reader and pdfplumber parses it, and ledgerbox told the
    operator their PDF was not a PDF — while the actual reason it was unusable
    (no parser recognises that layout) lived one layer down and never reached
    them. A confident, wrong diagnosis is the failure mode this project exists
    to prevent, and it does not stop being one because the subject is a file
    header rather than a number.

    Nothing is repaired: the bytes go into the archive exactly as they arrived,
    newline included, and the content hash covers them.
    """
    served = paths.root.parent / "served-with-a-newline.pdf"
    served.parent.mkdir(parents=True, exist_ok=True)
    original = b"\n\n" + pdf_bytes()
    served.write_bytes(original)

    result = archive_file(paths, served, ingested_on=AUG)

    assert result.path.read_bytes() == original, "archived verbatim, not trimmed"
    assert result.sha256 == sha256_bytes(original)


def test_leading_junk_before_the_header_is_refused(paths: DataPaths) -> None:
    """Whitespace is not content; an HTML login page is. This does not repair."""
    sneaky = paths.root.parent / "sneaky.pdf"
    sneaky.parent.mkdir(parents=True, exist_ok=True)
    sneaky.write_bytes(b"<html>please sign in</html>" + pdf_bytes())

    with pytest.raises(ArchiveError, match="not whitespace"):
        archive_file(paths, sneaky, ingested_on=AUG)
    assert archived_files(paths) == []


def test_an_empty_file_is_refused(paths: DataPaths) -> None:
    empty = paths.root.parent / "empty.pdf"
    empty.parent.mkdir(parents=True, exist_ok=True)
    empty.write_bytes(b"")

    with pytest.raises(ArchiveError, match="empty.pdf"):
        archive_file(paths, empty, ingested_on=AUG)
    assert archived_files(paths) == []


def test_a_missing_file_is_refused_by_name(paths: DataPaths) -> None:
    missing = paths.root.parent / "nope.pdf"

    with pytest.raises(ArchiveError, match="nope.pdf"):
        archive_file(paths, missing, ingested_on=AUG)


def test_a_directory_is_refused(paths: DataPaths) -> None:
    folder = paths.root.parent / "a-folder.pdf"
    folder.mkdir(parents=True)

    with pytest.raises(ArchiveError, match="a-folder.pdf"):
        archive_file(paths, folder, ingested_on=AUG)
    assert archived_files(paths) == []


# --------------------------------------------------------------------------
# non-ASCII paths — the predecessor died here before reading a single PDF
# --------------------------------------------------------------------------


def test_chinese_directory_and_file_names_work(paths: DataPaths) -> None:
    source = write_pdf(paths.root.parent / "中文 目录" / "对账单 2026年8月.pdf")

    result = archive_file(paths, source, ingested_on=AUG)

    assert result.path.read_bytes() == pdf_bytes()
    assert result.rel_path == f"2026/08/{result.sha256}.pdf"
    assert archive_file(paths, source, ingested_on=AUG).already_present is True


def test_a_chinese_named_file_that_is_not_a_pdf_reports_its_name(paths: DataPaths) -> None:
    bogus = paths.root.parent / "中文 目录" / "不是 pdf.pdf"
    bogus.parent.mkdir(parents=True, exist_ok=True)
    bogus.write_bytes(b"not a pdf")

    with pytest.raises(ArchiveError) as excinfo:
        archive_file(paths, bogus, ingested_on=AUG)
    assert "不是 pdf.pdf" in str(excinfo.value)


# --------------------------------------------------------------------------
# atomicity
# --------------------------------------------------------------------------


def test_a_failed_replace_leaves_no_debris(
    paths: DataPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = write_pdf(paths.root.parent / "s.pdf")

    def boom(src: object, dst: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(ArchiveError):
        archive_file(paths, source, ingested_on=AUG)
    monkeypatch.undo()

    assert archived_files(paths) == []  # no target, and no .tmp either
    # and the archive is still usable afterwards
    assert archive_file(paths, source, ingested_on=AUG).already_present is False


def test_content_changing_mid_copy_aborts_without_writing(
    paths: DataPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The name must address the bytes on disk, not the bytes we hashed first."""
    source = write_pdf(paths.root.parent / "s.pdf")

    monkeypatch.setattr(archive_mod, "sha256_file", lambda _path: "0" * 64)
    with pytest.raises(ArchiveError, match="changed while"):
        archive_file(paths, source, ingested_on=AUG)

    assert archived_files(paths) == []


# --------------------------------------------------------------------------
# the real corpus
# --------------------------------------------------------------------------


def test_real_statements_archive_once_each(paths: DataPaths, real_statements: list[Path]) -> None:
    """Read-only use of the real PDFs: they are hashed and copied, never edited."""
    first_pass = [archive_file(paths, pdf, ingested_on=AUG) for pdf in real_statements]

    assert all(entry.already_present is False for entry in first_pass)
    assert len({entry.sha256 for entry in first_pass}) == len(real_statements)
    for entry, pdf in zip(first_pass, real_statements, strict=True):
        assert entry.sha256 == sha256_file(pdf)
        assert entry.byte_len == pdf.stat().st_size

    # A second run, months later, must add nothing.
    second_pass = [archive_file(paths, pdf, ingested_on=JAN) for pdf in real_statements]
    assert all(entry.already_present is True for entry in second_pass)
    assert [e.rel_path for e in second_pass] == [e.rel_path for e in first_pass]
    assert len(archived_files(paths)) == len(real_statements)
