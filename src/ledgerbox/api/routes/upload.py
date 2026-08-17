# SPDX-License-Identifier: AGPL-3.0-or-later
"""``POST /api/upload`` — the only way into the ledger over HTTP.

This module does almost nothing, deliberately. It takes bytes, puts them in a
spool file and calls :func:`ledgerbox.ingest.pipeline.ingest_file`. Archiving,
identification, extraction, reconciliation and booking then happen for an
upload exactly as they happen for ``ledgerbox ingest`` — same order, same gate,
same refusals. **There is no second path into the ledger.** The moment this
module parses, checks or books anything of its own, "the ledger cannot hold a
number the reconciler disagreed with" is true for one caller and false for the
other, which is the same as it being false.

Three things it does own, because nothing upstream can:

* **the ceiling, enforced while the bytes are still arriving.** A limit checked
  once the write has finished is not a limit, it is a report on how much was
  written. The loop stops, the spool goes, the answer is 413.
* **the spool's lifetime.** ``incoming/`` is staging and nothing else:
  :func:`ledgerbox.ingest.archive.archive_file` has taken its own immutable
  copy by the time the pipeline returns, so a file left here afterwards is a
  second copy of a bank statement that no part of the system owns. It is
  removed in ``finally``, on every path.
* **the name.** ``file.filename`` is whatever the browser was handed by
  whoever named the file. It never becomes a path component — the spool is a
  :func:`uuid.uuid4` hex — and it travels back to the page as display data with
  its directory parts removed.

One limit of the ceiling, stated rather than implied: Starlette has already
parsed and buffered the whole multipart body before this function is entered,
so ``max_upload_bytes`` bounds what reaches the *data directory*, not what
reaches the machine. A wire-level cap belongs in front of the ASGI app. This is
the cap that keeps ``incoming/`` bounded, which is the one this layer can keep.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, cast
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from ...agent_runner import drain_jobs
from ...db import repo
from ...ingest import pipeline
from ...ingest.archive import MAGIC_WINDOW, PDF_MAGIC, pdf_header_offset
from ...reconcile.report import ReviewItem, verdict
from ..dependencies import AppState, get_state, ledger_rw
from ..schemas import (
    CheckOut,
    CheckStatus,
    ReviewItemOut,
    ReviewStatus,
    Severity,
    UploadResult,
    UploadStatus,
)

__all__ = ["router"]

# `reconcile.checks` and `ingest.pipeline` pass these vocabularies around as
# plain `str`; `schemas.py` narrows the same three to Literals so an unknown
# value is a validation error on the wire rather than an unstyled badge in the
# browser. The casts below assert that the two are one vocabulary, and are
# assertions only to the type checker: pydantic still validates every value at
# construction, so drift shows up loudly either way.

router = APIRouter(prefix="/api", tags=["upload"])

#: Read size. Small enough that the ceiling is enforced with 64 KiB of slack
#: rather than a whole file's worth, large enough that a 130 KB statement is
#: three reads.
_CHUNK = 64 * 1024

#: Written out rather than taken from ``fastapi.status``, whose name for it
#: changed: ``HTTP_413_REQUEST_ENTITY_TOO_LARGE`` (RFC 7231) is deprecated in
#: favour of ``HTTP_413_CONTENT_TOO_LARGE`` (RFC 9110), so either spelling ties
#: this module to a version range of Starlette. The number is the part the RFCs
#: kept.
TOO_LARGE = 413

#: The one-line story for the top of a result card. Display only — the page
#: branches on ``status``, never on this text.
_SUMMARY = {
    pipeline.DUPLICATE: "Already imported — nothing to do",
    pipeline.NEEDS_REVIEW: "Needs review — nothing was booked",
    pipeline.FAILED: "Could not read this file",
}


def _display_name(raw: str | None) -> str:
    """The submitted name with everything path-like removed.

    Kept at all because an operator who dropped thirteen files needs to know
    which card is which. Reduced to a bare name because the client chooses this
    string, and ``../../`` costs nothing to send. Backslashes are folded to
    slashes first: a Windows client's name is not a path on a POSIX host, where
    ``PurePosixPath`` would otherwise hand back the whole thing unchanged.
    """
    candidate = (raw or "").replace("\\", "/").strip()
    return PurePosixPath(candidate).name or "upload.pdf"


async def _spool(file: UploadFile, target: Path, *, limit: int) -> int:
    """Stream *file* into *target*, refusing the moment it would exceed *limit*."""
    total = 0
    with open(target, "wb") as sink:
        while chunk := await file.read(_CHUNK):
            total += len(chunk)
            if total > limit:
                # Raised before the write, not after it: *limit* is the number
                # of bytes this endpoint is willing to place on disk, not the
                # point at which it starts complaining about what it wrote.
                raise HTTPException(
                    TOO_LARGE,
                    f"That file is larger than the {limit} byte upload limit.",
                )
            sink.write(chunk)
    return total


def _require_pdf(spool: Path) -> None:
    """Refuse anything whose first five bytes are not ``%PDF-``.

    Read back off the disk, so the evidence is the bytes that actually landed
    rather than the ``Content-Type`` and the extension, both of which are
    claims by the sender. :func:`ledgerbox.ingest.archive.archive_file` asserts
    the same thing again and would refuse the file regardless; checking here is
    what makes the difference between "this was never a PDF" (415) and "this
    was read and then failed reconciliation" (200) visible to the browser.
    """
    with open(spool, "rb") as handle:
        head = handle.read(MAGIC_WINDOW)
    if pdf_header_offset(head) is not None:
        return

    # Say which of the two it is. "does not begin with '%PDF-'" was true of a
    # file whose header sat one newline in -- a real statement, which opens in
    # every reader -- and the operator was told their PDF was not a PDF while
    # the actual reason it was unusable (no parser recognises the layout) lived
    # one layer down and never reached them.
    found = head.find(PDF_MAGIC)
    detail = (
        f"{PDF_MAGIC.decode()!r} appears at byte {found}, but the bytes before it are not "
        f"whitespace, so this file is not a PDF."
        if found > 0
        else f"Not a PDF: {PDF_MAGIC.decode()!r} does not appear in the first "
        f"{MAGIC_WINDOW} bytes."
    )
    raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail)


def _ingest(state: AppState, spool: Path, filename: str) -> tuple[UploadResult, bool]:
    """The whole pipeline, on one connection, under the write lock.

    Called in a worker thread. Parsing a statement is seconds of CPU inside
    pdfplumber, and spending them on the event loop would stall every other
    request — including the health strip the page polls — for the duration.
    Opening and closing the connection inside that same thread is also what
    ``sqlite3``'s thread affinity requires.

    The response is assembled here, while that connection is still open, so that
    the queued items can be read back as the database actually stored them
    without a second connection or a second trip off the event loop.
    """
    with ledger_rw(state) as conn:
        outcome = pipeline.ingest_file(conn, state.paths, spool)
        return _result(outcome, filename, conn), outcome.agent_job_queued


def _checks(outcome: pipeline.IngestOutcome) -> list[CheckOut]:
    """Every assertion the reconciler ran, passes included.

    Empty when there is no report, which is not the same as "no checks failed":
    a file that could not be extracted never reached the reconciler at all, and
    the review item is where that is said.
    """
    if outcome.report is None:
        return []
    return [
        CheckOut(
            check_id=result.check_id,
            severity=cast(Severity, result.severity),
            status=cast(CheckStatus, result.status),
            message=result.message,
            detail=result.detail,
        )
        for result in outcome.report.results
    ]


def _review_item(
    item: ReviewItem,
    statement_month: str | None,
    stored: sqlite3.Row | None = None,
) -> ReviewItemOut:
    """One queued item, with its stored JSON split back into message and detail.

    *stored* is that item's row as the database now holds it, and it decides
    ``status`` and the timestamps. Hardcoding ``open`` here was a real defect:
    ``replace_review_items`` deliberately does not resurrect an item the user
    has already dismissed, so re-ingesting a dismissed statement produced an
    upload card listing two reasons as outstanding while the queue below it —
    reading the same database — said nothing was waiting. One screen, two
    answers, and the wrong one on top.

    The payload is written by :meth:`ReviewItem.from_result` and is therefore
    always valid JSON — but a decode error here would turn a completed ingest
    into a 500, telling the operator that an import failed after it had already
    been booked. An unreadable payload costs the message, not the response.
    """
    payload: dict[str, Any] = {}
    with contextlib.suppress(json.JSONDecodeError):
        decoded = json.loads(item.detail)
        if isinstance(decoded, dict):
            payload = decoded
    detail = payload.get("detail")
    return ReviewItemOut(
        id=item.id,
        source_file_id=item.source_file_id,
        status=cast(ReviewStatus, stored["status"]) if stored is not None else "open",
        severity=cast(Severity, item.severity),
        check_id=item.check_id,
        message=str(payload.get("message", "")),
        detail=detail if isinstance(detail, dict) else {},
        created_at=str(stored["created_at"]) if stored is not None else None,
        resolved_at=stored["resolved_at"] if stored is not None else None,
        statement_month=statement_month,
    )


def _summary(outcome: pipeline.IngestOutcome) -> str:
    if outcome.status != pipeline.IMPORTED:
        return _SUMMARY.get(outcome.status, "Could not read this file")
    counts = outcome.counts
    booked = counts.txns if counts is not None else 0
    head = f"Imported {outcome.statement_month} — {booked} transaction(s)"

    report = outcome.report
    if report is None:  # pragma: no cover - an import always carries a report
        return head

    # Only say "all checks passed" when all of them did. Reaching IMPORTED
    # proves the block-level checks passed and nothing more; a warn-level
    # failure, or a warn-level check that could not run, sits perfectly
    # comfortably behind that sentence. Summarising it as a clean import is the
    # small, sincere overstatement this project exists because of — the one line
    # everybody reads has to be the honest one, not the one `checks[]` corrects.
    # Named, not counted. "1 check(s) not run" tells the reader there is
    # something to look at and then gives them nowhere to look — and the page
    # deliberately renders `review[]` rather than the full check list, so a
    # pointer to "the checks below" would point at nothing. The ids are short
    # and they are the same ids `ledgerbox verify` prints.
    trailing = []
    if report.failures:
        trailing.append("warnings: " + ", ".join(r.check_id for r in report.failures))
    if report.skipped:
        trailing.append("not run: " + ", ".join(r.check_id for r in report.skipped))
    if trailing:
        return f"{head}; {'; '.join(trailing)}"
    return f"{head}, all {len(report.results)} checks passed"


def _result(
    outcome: pipeline.IngestOutcome,
    filename: str,
    conn: sqlite3.Connection | None = None,
) -> UploadResult:
    """The pipeline's outcome as the wire format, and nothing added.

    ``verdict`` comes from the report or is absent. There is no default: a file
    that was refused before reconciliation ran has no verdict, and "ok" is the
    one word that must never appear for a statement nothing checked.

    *conn* is used only to read each queued item's persisted state back, so the
    card and the queue below it cannot disagree about the same row. See
    :func:`_review_item`.
    """
    counts = outcome.counts if outcome.status == pipeline.IMPORTED else None
    stored = (
        {item.id: repo.get_review_item(conn, item.id) for item in outcome.review}
        if conn is not None
        else {}
    )
    return UploadResult(
        status=cast(UploadStatus, outcome.status),
        filename=filename,
        sha256=outcome.sha256,
        statement_month=outcome.statement_month,
        verdict=verdict(outcome.report) if outcome.report is not None else None,
        booked=counts.txns if counts is not None else 0,
        skipped_duplicates=counts.skipped_duplicates if counts is not None else 0,
        summary=_summary(outcome),
        checks=_checks(outcome),
        review=[
            _review_item(item, outcome.statement_month, stored.get(item.id))
            for item in outcome.review
        ],
        error=outcome.error,
    )


@router.post(
    "/upload",
    response_model=UploadResult,
    summary="Ingest one statement PDF",
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "No file part, or an empty filename"},
        TOO_LARGE: {"description": "Larger than the upload limit"},
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {"description": "The bytes are not a PDF"},
    },
)
async def upload_statement(
    background_tasks: BackgroundTasks,
    state: Annotated[AppState, Depends(get_state)],
    # `str` and `None` are in the annotation so that a malformed request is
    # this function's 400 rather than FastAPI's 422. A missing part arrives as
    # None; a part sent *without* a filename is not a file at all and the
    # multipart parser hands it over as text. Declaring only `UploadFile` would
    # make both a validation error, and the contract's status table says a
    # request with no usable file is a 400.
    file: Annotated[UploadFile | str | None, File()] = None,
) -> UploadResult:
    """Archive, reconcile and (only then) book one statement.

    Every *pipeline* outcome is a 200, ``needs_review`` and ``failed``
    included. A statement that fails reconciliation is a successful call
    reporting a refusal, and the refusal is the product; making it a 4xx would
    oblige the browser's error path to re-render the same result the success
    path already renders, in less detail, from a different code path.
    """
    # Tested against `str`/`None` rather than `isinstance(file, UploadFile)`:
    # what arrives is Starlette's ``UploadFile``, not the FastAPI subclass this
    # module imports, so an isinstance check on the imported name silently
    # rejects every real upload.
    if file is None or isinstance(file, str) or not (file.filename or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Send one PDF as a multipart form field named 'file', with a filename.",
        )

    filename = _display_name(file.filename)
    spool = state.paths.incoming / f"{uuid4().hex}.pdf"
    try:
        await _spool(file, spool, limit=state.max_upload_bytes)
        _require_pdf(spool)
        result, agent_job_queued = await run_in_threadpool(
            _ingest,
            state,
            spool,
            filename,
        )
    finally:
        # On every path: 413, 415, a parser crash, or success. The archive has
        # its own copy; anything still here is an unmanaged second one. A
        # removal that fails (a Windows handle held elsewhere) leaves the file
        # where the operator can see it, which beats failing an ingest that
        # already happened.
        with contextlib.suppress(OSError):
            spool.unlink(missing_ok=True)

    if agent_job_queued:
        background_tasks.add_task(drain_jobs, state.paths)
    return result
