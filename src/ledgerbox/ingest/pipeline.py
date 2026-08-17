# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ingest orchestration: archive, identify, extract, reconcile, book.

The order is not negotiable and neither is the gate:

    1. archive      SHA-256 -> already known? return "duplicate", no side effects
    2. identify     /Producer + page markers -> a versioned layout, or refuse
    3. extract      positioned words, with page/bbox provenance kept
    4. reconcile    every check in reconcile.checks; ANY block failure stops here
    5. book         idempotency key -> dedupe -> single-entry to double-entry

Nothing is written to the ledger until step 4 passes. A statement that fails
lands in the review queue with the failing check attached, and the numbers you
already had stay the numbers you had.

Every file is processed in its own try/except: thirteen statements where the
seventh is corrupt must yield twelve booked statements and one review item, not
an empty database and a traceback. ``SystemExit`` deliberately escapes — the
data-directory guard raises it, and swallowing that would be swallowing the one
error the guard exists to raise.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..agent_jobs import enqueue_import_job_in_transaction
from ..analytics.categorize import assign_categories, default_rules, matches_transfer
from ..config import DataPaths
from ..db import repo
from ..db.connection import transaction
from ..fsutil import atomic_write_text, is_link_like, sha256_file
from ..learning import apply_learned_rules
from ..ledger import posting as posting_builder
from ..ledger.identity import review_item_id
from ..money import format_minor
from ..reconcile.checks import (
    BLOCK,
    FAIL,
    SKIP,
    CheckResult,
    ReconciliationReport,
    check_double_entry,
    run_statement_checks,
)
from ..reconcile.report import ReviewItem, review_items
from . import archive
from .extract import ExtractionError, extract_spans
from .parsers.base import ParsedStatement, ParseError
from .registry import UnknownLayout, identify_or_raise

#: Outcome values. `duplicate` means the archive already had this exact file
#: *and* the ledger has nothing outstanding for it.
IMPORTED = "imported"
DUPLICATE = "duplicate"
NEEDS_REVIEW = "needs_review"
FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IngestOutcome:
    source: Path
    status: str
    sha256: str | None = None
    statement_month: str | None = None
    report: ReconciliationReport | None = None
    counts: repo.WriteCounts | None = None
    review: tuple[ReviewItem, ...] = ()
    error: str | None = None
    #: A new durable A7.4 outbox job was committed with this import.
    agent_job_queued: bool = False
    #: True when the archived copy was missing and this ingest put it back. The
    #: ledger is unchanged; the bytes it depends on are not.
    restored_archive: bool = False

    @property
    def ok(self) -> bool:
        return self.status in (IMPORTED, DUPLICATE)

    def summary_line(self) -> str:
        name = self.source.name
        if self.status == IMPORTED:
            counts = self.counts
            booked = counts.txns if counts else 0
            skipped = counts.skipped_duplicates if counts else 0
            tail = f", {skipped} already known" if skipped else ""
            if self.restored_archive:
                tail += "; archived copy restored"
            return f"{name}: imported {self.statement_month} - {booked} transaction(s){tail}"
        if self.status == DUPLICATE:
            # "nothing to do" is not always true. Re-offering a statement whose
            # archived copy had been deleted puts the bytes back — that is the
            # documented repair for `recorded_not_archived`, and saying nothing
            # happened would leave the operator unsure whether it worked.
            if self.restored_archive:
                return f"{name}: already imported; archived copy restored"
            return f"{name}: already imported, nothing to do"
        if self.status == NEEDS_REVIEW:
            restored = "; archived copy restored" if self.restored_archive else ""
            reasons = "; ".join(item.check_id for item in self.review) or "unknown"
            # `self.error` holds why a refusal happened ("Unexpected EOF"), and
            # the check_id alone ("extraction") tells the operator nothing they
            # can act on. It was already recorded in the review queue; leaving
            # it out of the line they actually read helps no one.
            detail = f" ({self.error})" if self.error else ""
            return f"{name}: NEEDS REVIEW - {reasons}{detail}{restored}"
        return f"{name}: FAILED - {self.error}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _today() -> date:
    return datetime.now(UTC).date()


def transfer_flags(entries: Iterable[Any]) -> dict[str, bool]:
    """``{txn id: is this a transfer}`` for a batch, from the rules alone.

    Reads ``txn_identity.raw_descriptor`` -- the bank's bytes, verbatim -- for
    the same reason :func:`~ledgerbox.analytics.categorize.assign_categories`
    does: it is the column ``v_transaction`` exposes, so ingest and any later
    re-run are looking at one string rather than two.

    Every entry appears, including the ones the rules do not claim. Writing
    only the True ones would leave a stale flag behind when a rule is narrowed,
    and a transfer nobody can un-flag is money missing from the totals with no
    way back.
    """
    return {entry.txn_id: matches_transfer(entry.identity.raw_descriptor) is not None
            for entry in entries}


def _raw_payload(statement: ParsedStatement, index: int) -> str:
    """Verbatim row, with the coordinates that produced it.

    Provenance is the difference between "the ledger says -12.44" and "page 2,
    box (410, 300)-(433, 300) of the archived PDF says -12.44". Both figures
    here are invented: an example in a docstring is written while looking at
    debugging output, which is the moment docs/STATUS.md §6.5 keeps recording.
    """
    txn = statement.transactions[index]
    return json.dumps(
        {
            "posted_date": txn.posted_date.isoformat(),
            "description": txn.description,
            "amount_minor": txn.amount_minor,
            "balance_minor": txn.balance_minor,
            "amount_source": txn.amount_source,
            "row_index": txn.row_index,
            "page": txn.provenance.page,
            "bbox": list(txn.provenance.as_bbox()),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _extracted_cache(paths: DataPaths, sha256: str, statement: ParsedStatement) -> Path:
    """NDJSON mirror of the parsed rows, rebuildable from archive/ at any time."""
    lines = [_raw_payload(statement, index) for index in range(len(statement.transactions))]
    target = paths.extracted / f"{sha256}.ndjson"
    atomic_write_text(target, "".join(f"{line}\n" for line in lines))
    return target


def _refusal_item(source_file_id: str, check_id: str, message: str) -> ReviewItem:
    return ReviewItem(
        id=review_item_id(source_file_id, check_id, BLOCK),
        source_file_id=source_file_id,
        severity=BLOCK,
        check_id=check_id,
        detail=json.dumps({"message": message, "detail": {}}, ensure_ascii=False, sort_keys=True),
    )


def _is_booked(conn: sqlite3.Connection, source_file_id: str) -> bool:
    """Did anything from this file actually reach the ledger?

    This is what decides whether re-offering the same bytes is a no-op or a
    retry, and it asks the **ledger**, never the review queue.

    It used to ask the queue -- "are there open blocking items for this file?"
    -- and that was a real bug. P1 lets a person dismiss a blocking item, which
    closes it, which made this predicate true, which made every later ingest of
    those bytes short-circuit to ``duplicate``. The pipeline would never run
    again. Fixing the parser would change nothing, because the parser would not
    be called -- and "fix the parser and re-ingest the archived file" is exactly
    what the 409 tells the user at the moment they press Dismiss.

    Phrased against ``txn_identity`` so that it and
    :func:`ledgerbox.db.repo.count_unbooked_statements` cannot disagree: the
    condition that makes ``verify`` report a statement as missing is the same
    condition that makes this pipeline willing to try it again.
    """
    row = conn.execute(
        "SELECT 1 FROM raw_record rr JOIN txn_identity ti ON ti.raw_record_id = rr.id "
        "WHERE rr.source_file_id = ? LIMIT 1",
        (source_file_id,),
    ).fetchone()
    return row is not None


def _remove_unrecorded_new_archive(
    conn: sqlite3.Connection, archived: archive.ArchivedFile
) -> None:
    """Undo the filesystem half of a booking transaction that rolled back."""
    if archived.already_present or repo.find_source_file(conn, archived.sha256) is not None:
        return
    if not archived.path.exists():
        return
    archived.path.chmod(0o600)
    archived.path.unlink()


@contextmanager
def _booking_transaction(
    conn: sqlite3.Connection, archived: archive.ArchivedFile
) -> Iterator[None]:
    """Keep a newly-created archive and its source row at one failure boundary."""
    try:
        with transaction(conn):
            yield
    except BaseException:
        _remove_unrecorded_new_archive(conn, archived)
        raise


def ingest_file(
    conn: sqlite3.Connection,
    paths: DataPaths,
    source: str | Path,
    *,
    ingested_on: date | None = None,
    ingested_at: str | None = None,
) -> IngestOutcome:
    """Run one file through the pipeline. Never raises for bad input."""
    origin = Path(source)
    ingested_on = ingested_on or _today()
    ingested_at = ingested_at or _now_iso()

    try:
        archived = archive.archive_file(paths, origin, ingested_on=ingested_on)
    except Exception as exc:  # unreadable, not a PDF, empty…
        # No archived bytes means no source_file row, and review_item has a
        # foreign key to source_file — so this one can only be reported, not
        # queued. Saying which file and why is the whole job here.
        return IngestOutcome(source=origin, status=FAILED, error=str(exc))

    existing = repo.find_source_file(conn, archived.sha256)

    # `archive_file` runs before any of this, so a file we already had a row for
    # whose archived copy had gone missing has just had it written back. That is
    # the whole repair path for `recorded_not_archived`.
    #
    # Computed here rather than in the duplicate branch alone: a *refused*
    # statement can lose its archived copy too, and re-offering it is how that
    # gets repaired. Reporting the repair only on the path where the ledger was
    # already complete would stay silent on the path where the operator is more
    # likely to be trying things and needs to know which of them worked.
    restored = existing is not None and not archived.already_present

    if existing is not None and _is_booked(conn, archived.sha256):
        return IngestOutcome(
            source=origin,
            status=DUPLICATE,
            sha256=archived.sha256,
            statement_month=(existing["period_end"] or "")[:7] or None,
            restored_archive=restored,
        )

    try:
        document = extract_spans(archived.path)
        parser = identify_or_raise(document)
        statement = parser.parse(document)
    except (ExtractionError, UnknownLayout, ParseError) as exc:
        check_id = {
            ExtractionError: "extraction",
            UnknownLayout: "unknown_layout",
            ParseError: "parse",
        }[type(exc)]
        return _queue_refusal(
            conn, paths, origin, archived, check_id, str(exc), ingested_at, restored
        )
    except Exception as exc:  # pragma: no cover - defence in depth
        return _queue_refusal(
            conn, paths, origin, archived, "parse", repr(exc), ingested_at, restored
        )

    report = run_statement_checks(statement)
    entries = posting_builder.build_entries(statement)

    # Structural check 0 runs on what is about to be written, not on what was
    # written: an unbalanced transaction should never reach the database.
    zero_sum = check_double_entry(
        (entry.txn_id, row.amount_minor, row.currency)
        for entry in entries.entries
        for row in entry.postings
    )
    results = (zero_sum, *report.results)
    report = ReconciliationReport(statement_month=statement.statement_month, results=results)

    queued = review_items(archived.sha256, report)

    agent_job_queued = False
    with _booking_transaction(conn, archived):
        repo.insert_source_file(
            conn,
            sha256=archived.sha256,
            rel_path=archived.rel_path,
            media_type=archived.media_type,
            byte_len=archived.byte_len,
            institution=statement.institution,
            period_start=statement.period_start.isoformat(),
            period_end=statement.period_end.isoformat(),
            ingested_at=ingested_at,
        )
        repo.replace_review_items(conn, source_file_id=archived.sha256, items=queued)

        if report.blocked:
            # Deliberately no rows: the gate is the product. The archived PDF
            # and the review items stay, so a fixed parser can be re-run over
            # exactly the same bytes.
            return IngestOutcome(
                source=origin,
                status=NEEDS_REVIEW,
                sha256=archived.sha256,
                statement_month=statement.statement_month,
                report=report,
                review=tuple(queued),
                restored_archive=restored,
            )

        repo.ensure_account(
            conn,
            account_id=entries.account_id,
            name=entries.account_name,
            kind="asset",
            subtype=entries.subtype,
            currency=entries.currency,
            institution=entries.institution,
            mask=entries.mask,
        )
        # Before the postings that will reference them. The rules file is the
        # definition; this table is its mirror, present so the foreign key on
        # posting.category_id has something to point at.
        repo.ensure_categories(conn, rows=list(default_rules().rows()))
        repo.insert_raw_records(
            conn,
            source_file_id=archived.sha256,
            payloads=[
                (index, "stmttrn", _raw_payload(statement, index))
                for index in range(len(statement.transactions))
            ],
            parser_id=statement.parser_id,
            parser_version=statement.parser_version,
        )
        counts = repo.insert_entries(
            conn, source_file_id=archived.sha256, entries=list(entries.entries)
        )
        # Categorisation runs here, inside the same transaction, rather than as
        # a pass over the ledger afterwards. It is a pure function of the
        # descriptor and the shipped rules file, so re-ingesting every archived
        # PDF into an empty database reproduces the same category on the same
        # posting -- which is what keeps the rebuild invariant an equality
        # rather than an equality with an exception for one column.
        repo.set_posting_categories(
            conn, assignments=assign_categories(entries.entries)
        )
        # The rules' answer about transfers, written next to the rules' answer
        # about categories and for the same reason: both are pure functions of
        # the descriptor and the shipped rules file, so re-ingesting the archive
        # reproduces them and the rebuild invariant needs no exception.
        #
        # Only `txn.is_transfer` is touched. A person's decision lives in
        # `category_override` and is folded in by `v_txn_transfer`, so this can
        # run over an existing ledger without overwriting anybody.
        repo.set_transfer_flags(conn, assignments=transfer_flags(entries.entries))
        repo.upsert_balance_assertions(
            conn, source_file_id=archived.sha256, rows=list(entries.balance_assertions)
        )
        # After the assertions, because it is derived from the earliest of them.
        repo.sync_opening_entry(
            conn, account_id=entries.account_id, currency=entries.currency
        )
        # This is an outbox write, not a process launch. Keeping it in the same
        # transaction means a successful import cannot be committed without
        # What earlier decisions taught claims the new lines it recognises, in
        # the same transaction as the booking: a merchant answered last month
        # never reaches the Agent queue or the review pile again. Learned
        # answers live in category_override, not posting.category_id, so the
        # rebuild invariant above is untouched.
        apply_learned_rules(conn, now=ingested_at)
        # its one durable trigger, and a failed/refused/duplicate import cannot
        # leave a classification job behind. The policy snapshot decides
        # whether there is anything to queue.
        queued_job = enqueue_import_job_in_transaction(
            conn,
            source_file_id=archived.sha256,
            now=ingested_at,
        )
        agent_job_queued = queued_job is not None and queued_job.created

    _extracted_cache(paths, archived.sha256, statement)

    return IngestOutcome(
        source=origin,
        status=IMPORTED,
        sha256=archived.sha256,
        statement_month=statement.statement_month,
        report=report,
        counts=counts,
        review=tuple(queued),
        restored_archive=restored,
        agent_job_queued=agent_job_queued,
    )


def _queue_refusal(
    conn: sqlite3.Connection,
    paths: DataPaths,
    origin: Path,
    archived: archive.ArchivedFile,
    check_id: str,
    message: str,
    ingested_at: str,
    restored_archive: bool = False,
) -> IngestOutcome:
    """A file we archived but could not read. It goes to the queue, not the bin."""
    item = _refusal_item(archived.sha256, check_id, message)
    with transaction(conn):
        repo.insert_source_file(
            conn,
            sha256=archived.sha256,
            rel_path=archived.rel_path,
            media_type=archived.media_type,
            byte_len=archived.byte_len,
            institution=None,
            period_start=None,
            period_end=None,
            ingested_at=ingested_at,
        )
        repo.replace_review_items(conn, source_file_id=archived.sha256, items=[item])
    return IngestOutcome(
        source=origin,
        status=NEEDS_REVIEW,
        sha256=archived.sha256,
        review=(item,),
        error=message,
        restored_archive=restored_archive,
    )


def collect_pdfs(sources: Iterable[str | Path]) -> list[Path]:
    """Expand directories to the PDFs inside them, sorted for reproducibility."""
    found: list[Path] = []
    for source in sources:
        path = Path(source)
        if path.is_dir():
            found.extend(sorted(p for p in path.rglob("*.pdf") if p.is_file()))
        else:
            found.append(path)
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def ingest_paths(
    conn: sqlite3.Connection,
    paths: DataPaths,
    sources: Sequence[str | Path],
    *,
    ingested_on: date | None = None,
) -> list[IngestOutcome]:
    """One try/except per file. A bad seventh statement costs you one statement."""
    outcomes: list[IngestOutcome] = []
    for source in collect_pdfs(sources):
        try:
            outcomes.append(ingest_file(conn, paths, source, ingested_on=ingested_on))
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            outcomes.append(IngestOutcome(source=Path(source), status=FAILED, error=repr(exc)))
    return outcomes


# ---------------------------------------------------------------------------
# Ledger-wide verification (the `verify` command)
# ---------------------------------------------------------------------------


# The archive's naming rules and the link test both live where they belong --
# `ingest.archive` owns the layout, `fsutil` owns the filesystem question -- and
# are imported here rather than restated. Two definitions of "is this a shard"
# is precisely how this went wrong: the survey learned to reject Unicode digits
# and to refuse to cross a junction while `find_archived` did neither, so a
# ledger could fail verification in a way that re-ingesting could not repair.


@dataclass(frozen=True, slots=True)
class ArchiveSurvey:
    """What is actually sitting in ``archive/``, and whether it is what it claims.

    Five separate facts, because they fail separately and a single "the archive
    is fine" boolean would hide four of them:

    * ``shas`` — the files named as the archive names things, which is the set
      the database can be compared against;
    * ``corrupt`` — files whose bytes do not hash to their own name. The name of
      an archived file *is* its checksum, so this is decidable at any time and is
      the only thing standing between "the rebuild produces the same ledger" and
      "the rebuild produces a different one, silently";
    * ``unreadable`` — files that could not be opened. **Not the same as
      corrupt, and not the same as fine.** A statement open in a PDF reader, or
      mid-scan by antivirus, or being copied by a sync client, is exclusively
      locked on Windows for a few seconds at a time. Nothing is wrong with it;
      we simply do not know yet, and that is a third answer;
    * ``stale_temp`` — ``.<name>.<rand>.tmp`` left by an interrupted archive
      write. This program *does* write those, so calling them unexpected would
      be blaming a crash on a stranger;
    * ``unexpected`` — everything else under ``archive/``, files and non-files
      alike. A directory, a junction or a dangling link here is not a statement
      and did not come from this program; a junction in particular quietly
      extends the archive outside the data directory, and therefore outside the
      reach of the guard that keeps financial data out of git repositories.
    """

    shas: frozenset[str]
    corrupt: tuple[str, ...]
    unreadable: tuple[str, ...]
    stale_temp: tuple[str, ...]
    unexpected: tuple[str, ...]


def survey_archive(paths: DataPaths, *, verify_bytes: bool = True) -> ArchiveSurvey:
    """Walk ``archive/`` and check it against its own naming contract.

    Re-hashing every file sounds expensive and is not: measured on this corpus,
    2.2 ms for thirteen statements, and about 930 MB/s thereafter — a decade of
    ten accounts is a quarter of a second, once, when someone asked to be
    reassured. ``ingest.archive`` already refuses content that does not hash to
    its name; this is the same question asked of files written long ago, which
    is when bit rot and well-meaning file managers happen.

    Never raises for anything it finds. An unreadable file is a *finding*, not
    an error: letting ``PermissionError`` out of here aborted ``verify`` before
    it printed a single result, so a statement someone had open in a PDF reader
    cost the operator every conclusion about their whole ledger — intermittently,
    and with a traceback, and under an exit code this CLI defines as "a statement
    needs review".
    """
    root = paths.archive
    if not root.is_dir():
        return ArchiveSurvey(frozenset(), (), (), (), ())
    if is_link_like(root):
        # The archive root itself pointing elsewhere puts every statement
        # outside the directory the guard was given.
        return ArchiveSurvey(frozenset(), (), (), (), ("<archive root is a link>",))

    shas: set[str] = set()
    corrupt: list[str] = []
    unreadable: list[str] = []
    stale_temp: list[str] = []
    unexpected: list[str] = []

    # Walked by hand rather than with `rglob`, and descending **only** into real
    # shard directories. `rglob` follows junctions and enumerates everything
    # under anything, which meant the files reached through a junction were
    # still counted as archived: the link got reported, and in the same breath
    # `recorded_not_archived` confirmed every statement was present — while they
    # were physically somewhere else entirely. Same for a hand-made directory:
    # `archive/junk/08/<sha>.pdf` counted as an archived statement.
    #
    # Not descending also means a stray directory is reported once, by its top,
    # instead of once per file inside it.
    stack: list[tuple[Path, tuple[str, ...]]] = [(root, ())]
    while stack:
        directory, prefix = stack.pop()
        try:
            children = sorted(directory.iterdir())
        except OSError:
            unreadable.append("/".join(prefix) or ".")
            continue

        for path in children:
            parts = (*prefix, path.name)
            relative = "/".join(parts)

            # Links first, and by reparse point rather than by `is_symlink()` —
            # see :func:`ledgerbox.fsutil.is_link_like`. Both `is_dir()` and
            # `is_file()` follow a link, so an entry resolving elsewhere would
            # otherwise be walked into or hashed and accepted. The archive holds
            # bytes, not pointers to bytes, and a link is how it stops being
            # confined to the data directory the guard checked.
            if is_link_like(path):
                unexpected.append(relative)
                continue

            if path.is_dir():
                if archive.is_shard(parts):
                    stack.append((path, parts))
                else:
                    unexpected.append(relative)
                continue

            if not path.is_file():  # device node, socket, dangling entry
                unexpected.append(relative)
                continue

            if path.name.startswith(".") and path.name.endswith(".tmp"):
                stale_temp.append(relative)
                continue

            # Depth matters, not just the name. A correctly-named statement at
            # `archive/<sha>.pdf` used to satisfy every check while being
            # invisible to `find_archived`, which only scans `<YYYY>/<MM>` — so
            # the next re-ingest wrote a *second* physical copy of the same bank
            # statement and all eight checks stayed green. An unmanaged second
            # copy is exactly what `incoming/` is swept to prevent.
            if (
                len(parts) != 3
                or path.suffix.lower() != ".pdf"
                or not archive.SHA_NAME.match(path.stem)
            ):
                unexpected.append(relative)
                continue

            shas.add(path.stem)
            if not verify_bytes:
                continue
            try:
                digest = sha256_file(path)
            except OSError:
                unreadable.append(relative)
                continue
            if digest != path.stem:
                corrupt.append(relative)

    return ArchiveSurvey(
        frozenset(shas),
        tuple(sorted(corrupt)),
        tuple(sorted(unreadable)),
        tuple(sorted(stale_temp)),
        tuple(sorted(unexpected)),
    )


def archived_shas(paths: DataPaths) -> set[str]:
    """Every sha256 in ``archive/``, by filename only. Does not read the bytes."""
    return set(survey_archive(paths, verify_bytes=False).shas)


def stranded_extractions(conn: sqlite3.Connection, paths: DataPaths) -> list[str]:
    """Files in ``extracted/`` with no ``source_file`` row behind them.

    Reported by ``doctor`` and **not** by ``verify``, and the split is the point
    rather than an oversight. ``verify``'s nine checks answer one question — can
    these numbers be trusted, and can this ledger be rebuilt — and a stranded
    extraction cache affects neither: it is a mirror of the archive, regenerated
    on rebuild, authoritative for nothing. Making it a block-level check would
    have ``verify`` report a ledger as unverified over a file that has no bearing
    on it, which is an overstatement in the other direction. ``doctor`` is where
    the state of the data directory is reported, and it already reports
    ``incoming/`` for exactly this reason (``docs/STATUS.md`` §5.24).

    Nothing was watching this directory at all, which mattered once deletion
    existed: a file here that could not be removed left every check green while
    the most disclosing single file in the data directory stayed on disk — the
    whole text layer, account number and address included. ``incoming/`` is at
    least swept on startup; ``extracted/`` never has been, so an orphan here does
    not clear itself and a report with no exit code behind it would not be read.

    Only files this program writes are considered, matched by the name it writes
    them under. A ``.ndjson`` whose stem is not a recorded ``source_file`` id is
    stranded whether or not it looks like a hash.
    """
    if not paths.extracted.is_dir() or is_link_like(paths.extracted):
        return []
    recorded = {str(row["id"]) for row in conn.execute("SELECT id FROM source_file")}
    stranded: list[str] = []
    try:
        children = sorted(paths.extracted.iterdir())
    except OSError:
        return []
    for path in children:
        if is_link_like(path) or path.suffix.lower() != ".ndjson":
            continue
        try:
            if not path.is_file():
                continue
        except OSError:  # pragma: no cover - a vanishing entry is not a finding
            continue
        if path.stem not in recorded:
            stranded.append(path.name)
    return stranded


_CASHFLOW_VIEW_SQL = """
SELECT COALESCE(SUM(inflow_minor), 0)  AS inflow_minor,
       COALESCE(SUM(outflow_minor), 0) AS outflow_minor,
       COALESCE(SUM(txn_count), 0)     AS txn_count
FROM v_cashflow_monthly
"""

#: The category breakdown as SQL states it. Deliberately not
#: ``repo.category_spend``: see the comment at its use site.
_CATEGORY_VIEW_TOTAL_SQL = """
SELECT COALESCE(SUM(spend_minor), 0) AS total_minor FROM v_category_spend
"""


def cashflow_disagreements(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Where the aggregations that report money differ from each other. Empty is good.

    One function rather than one query per caller. ``verify`` reports this as a
    check and ``doctor`` folds it into an exit code, and if each asked the
    question in its own words they would eventually answer differently — which
    is how ``doctor`` came to exit 0 over a ledger missing four statements out
    of five while ``verify`` was red (``docs/STATUS.md`` §5.22).

    **What is compared is listed rather than counted.** This docstring said
    "three" while the function did four, because P2 M6 added an arm and left
    the number alone; the same stale count reached ``schemas.py``,
    ``ARCHITECTURE.md`` and ``STATUS.md`` in one go. A count is a fact about
    the code kept in prose, which is the one kind of sentence this project has
    never managed to keep true (§5.69). So:

    ===============================  ====================================
    comparison                       what can pull it apart
    ===============================  ====================================
    ``ledger_totals`` ×              a **shape** — a transaction one side
    ``v_cashflow_monthly``           structurally cannot see. Both such
                                     shapes are negative cases in
                                     ``tests/test_pipeline.py``
    ``ledger_totals`` ×              an edit that changes **what either sums
    ``v_category_spend``             to**
    ``ledger_totals`` ×              likewise
    ``repo.category_spend``
    ``ledger_totals`` ×              likewise, on all four figures
    ``repo.monthly_cashflow``
    the last two again, through      an edit that **drops the date bound**
    one derived date bound           (see :func:`_scoped_disagreements`)
    ===============================  ====================================

    Only the first can be broken by data. The rest read ``v_cashflow_line``
    under the same predicates and differ only in how they group it, and a
    grouping does not change a sum, so **no data can pull them apart**.

    **What they catch is narrower than "an edit", and saying "an edit" was
    itself an over-promise** — made, of all places, in the paragraph rewritten
    to end the previous one (§5.45, §5.69). They compare *sums*. An edit that
    leaves the sum alone is invisible to them: an acceptance round pointed
    every wedge at one category id and collapsed thirteen month buckets into
    one, and both passed here, because the total was still the total. The
    grouping keys are what the *charts* are, and nothing in this function looks
    at them; ``tests/test_analytics.py`` does.

    (An earlier version of this paragraph also said the three read
    ``v_cashflow_line`` "through a join that emits one row per transaction".
    None of them joins anything, and the view is one row per income-or-expense
    **leg**. The conclusion held; the mechanism given for it was left over from
    migration 0007, when ``v_category_spend`` really did join.)

    **Both expressions of the category breakdown are compared, and leaving one
    out was a real hole.** Until an acceptance round constructed it, only the
    SQL view was checked — while the donut is drawn from
    :func:`repo.category_spend`, a *different* text. Editing that one to drop
    the unclaimed lines left the chart summing to a twelfth of the figure
    printed above it and **all nine checks green**. The argument for reading
    the view (a check that calls the code it checks proves less) was right
    about the view and silently exempted the query that reaches the page. Both
    are read now; neither substitutes for the other.

    It is checked here at all, rather than left to the test suite, because the
    test suite does not run on the operator's machine and every wedge of that
    chart claims to be part of ``outflow_minor``. A breakdown that has quietly
    stopped being a breakdown should be a red line on ``verify``, not a
    plausible picture.
    """
    ledger = repo.ledger_totals(conn)
    view = conn.execute(_CASHFLOW_VIEW_SQL).fetchone()

    found = {
        field: {"ledger_totals_minor": int(ledger[field]), "cashflow_view_minor": int(view[field])}
        for field in ("inflow_minor", "outflow_minor")
        if int(ledger[field]) != int(view[field])
    }
    if int(ledger["txn_count"]) != int(view["txn_count"]):
        # No `_minor` suffix, because it is not money. How many transactions
        # each side can see is what separates "one of us is missing rows" from
        # "one of us is adding them up wrong".
        found["txn_count"] = {
            "ledger_totals": int(ledger["txn_count"]),
            "cashflow_view": int(view["txn_count"]),
        }

    # The breakdown has to add up to the thing it claims to break down. Compared
    # against `outflow_minor` and nothing else: the breakdown reads expense legs
    # only, so it has no opinion about income and must not be made to look as
    # though it does.
    #
    # The breakdown has to add up to the thing it claims to break down, and
    # **both** expressions of it are asked, because there are two.
    #
    # `v_category_spend` is the projection held in SQL; `repo.category_spend` is
    # the one the donut is actually drawn from. Reading only the view was the
    # earlier shape and the reasoning for it was sound as far as it went -- a
    # check that calls the function it checks proves less about that function.
    # What it missed is that the argument says nothing about the *other* text.
    # An acceptance round edited `_CATEGORY_SPEND_SQL` alone, and the chart
    # summed to a twelfth of the Out above it while all nine checks passed.
    #
    # So the view is still read by a path independent of the repository, and the
    # query that reaches the page is read as well. Two keys, because "which of
    # them disagrees" is the first thing the operator needs and a single key
    # would make them one fact.
    outflow = int(ledger["outflow_minor"])
    view_total = int(conn.execute(_CATEGORY_VIEW_TOTAL_SQL).fetchone()["total_minor"])
    if view_total != outflow:
        found["category_breakdown_minor"] = {
            "ledger_totals_minor": outflow,
            "category_spend_minor": view_total,
        }

    drawn_total = int(repo.category_spend(conn).total_minor)
    if drawn_total != outflow:
        found["category_query_minor"] = {
            "ledger_totals_minor": outflow,
            "category_spend_query_minor": drawn_total,
        }

    # And the monthly decomposition. The bars claim to be these figures split by
    # month; a split that no longer adds up is a chart quietly describing a
    # different ledger.
    #
    # **`net_minor` is compared, and the sentence that used to exempt it was
    # wrong.** It said the two sides "both derive it as in + out, so it cannot
    # disagree while these do not". They do both derive it that way — in two
    # different places, `ledger_totals` in one expression and `CashflowMonth`
    # in another — and *two expressions of one sum* is precisely what these
    # comparisons exist to watch. An acceptance round changed one `+` to a `-`:
    # every Net in the bar chart and its total row went wrong, the Net at the
    # top of the page stayed right, both were on screen together, and all nine
    # checks passed. The exemption was an argument about what the source
    # currently says, offered in the one place whose job is to not depend on
    # that.
    months = repo.monthly_cashflow(conn)
    for field in ("inflow_minor", "outflow_minor", "net_minor"):
        if int(getattr(months, field)) != int(ledger[field]):
            found[f"monthly_{field}"] = {
                "ledger_totals_minor": int(ledger[field]),
                "monthly_sum_minor": int(getattr(months, field)),
            }
    if int(months.txn_count) != int(ledger["txn_count"]):
        # Not money, so no `_minor`, and the same reason the first comparison
        # carries a count: it separates "one of us is missing rows" from "one of
        # us is adding them up wrong".
        found["monthly_txn_count"] = {
            "ledger_totals": int(ledger["txn_count"]),
            "monthly_sum": int(months.txn_count),
        }

    found.update(_scoped_disagreements(conn))
    return found


def _scoped_disagreements(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """The same equalities, asked once through a **date bound**.

    Everything above is unscoped, and unscoped is the only window ``verify``
    may be judged on: a check that could be made green by choosing a window is
    not a check. But it left a hole the size of P2 M6. An acceptance round made
    ``category_spend`` and ``monthly_cashflow`` ignore their ``span`` argument
    entirely — one line each — and every check stayed green while the page,
    under any date range, showed a headline for the window beside a chart for
    the whole ledger. The identity the page rests on is "for **any** window",
    and nothing was asking about any window but one.

    So one more window is asked about, and it is **derived from the ledger
    rather than chosen**: everything dated on or after the latest transaction.
    A bound that selects a strict subset is all this needs — a query that
    drops its bound answers about the whole ledger and disagrees immediately —
    and deriving it means there is no window policy here to argue with or to
    tune until it passes.

    Empty ledger, or a ``txn.date`` that is not a date: nothing is compared and
    nothing is claimed. The second is reachable because the column has no CHECK
    constraint (``docs/STATUS.md`` §7); reporting it as a cashflow disagreement
    would name the wrong problem.
    """
    latest = conn.execute(
        "SELECT MAX(date) AS latest FROM txn WHERE superseded_by IS NULL"
    ).fetchone()["latest"]
    if latest is None:
        return {}
    try:
        span = repo.DateSpan(since=str(latest))
    except ValueError:
        return {}

    scoped = repo.ledger_totals(conn, span)
    found: dict[str, dict[str, int]] = {}

    breakdown = int(repo.category_spend(conn, span).total_minor)
    if breakdown != int(scoped["outflow_minor"]):
        found["scoped_category_minor"] = {
            "ledger_totals_minor": int(scoped["outflow_minor"]),
            "category_spend_query_minor": breakdown,
        }

    windowed = repo.monthly_cashflow(conn, span)
    for field in ("inflow_minor", "outflow_minor", "net_minor"):
        if int(getattr(windowed, field)) != int(scoped[field]):
            found[f"scoped_monthly_{field}"] = {
                "ledger_totals_minor": int(scoped[field]),
                "monthly_sum_minor": int(getattr(windowed, field)),
            }
    return found


#: The three checks that cannot be answered from the database alone. Named once
#: so that a caller who has to leave them out — :mod:`ledgerbox.ingest.forget`
#: measures the other six against a deletion that has not touched the disk yet —
#: takes the list from here rather than writing its own copy of it. Two
#: definitions of which checks are which is how the archive grew a hole
#: (``docs/STATUS.md`` §5.29).
ARCHIVE_CHECK_IDS = ("archived_not_recorded", "recorded_not_archived", "archive_integrity")


def verify_ledger(
    conn: sqlite3.Connection, paths: DataPaths | None = None
) -> list[CheckResult]:
    """Re-check what is already booked, without re-reading any PDF.

    Most checks read the database only, so they stay honest even if the parser
    has changed since the rows were written. The last three need *paths*, because
    the questions they ask are about the archive: is there a statement on disk
    the database has never heard of, is there a row whose bytes are gone, and do
    the bytes still hash to the names they are filed under. None of the three can
    be asked of the database alone.

    Omitting *paths* does not skip them quietly. They report ``skip`` at
    block level, which reads as ``UNVERIFIED`` rather than as a pass, for the
    same reason as everywhere else here: a check that could not run has not
    established anything.
    """
    results: list[CheckResult] = []

    unbalanced = conn.execute("SELECT COUNT(*) FROM v_unbalanced_txn").fetchone()[0]
    results.append(
        CheckResult(
            "double_entry",
            BLOCK,
            "pass" if unbalanced == 0 else FAIL,
            f"{unbalanced} transaction(s) do not sum to zero",
            {"unbalanced": unbalanced},
        )
    )

    orphans = conn.execute("SELECT COUNT(*) FROM v_identity_without_source").fetchone()[0]
    results.append(
        CheckResult(
            "provenance",
            BLOCK,
            "pass" if orphans == 0 else FAIL,
            f"{orphans} booked transaction(s) have no source record",
            {"orphans": orphans},
        )
    )

    # Every assertion is now checkable against a plain replay, because the
    # opening entry puts the account's starting balance *in* the ledger. The
    # earlier version had to skip the first assertion and borrow its value as
    # an anchor, which also meant a second account with no assertion on that
    # exact day was silently replayed from zero.
    rows = conn.execute(
        """
        SELECT ba.account_id, ba.as_of, ba.amount_minor AS declared_minor,
               COALESCE((
                 SELECT SUM(p.amount_minor) FROM posting p
                 JOIN txn t ON t.id = p.txn_id
                 WHERE p.account_id = ba.account_id AND t.date <= ba.as_of
                       AND t.superseded_by IS NULL
               ), 0) AS replayed_minor
        FROM balance_assertion ba
        WHERE ba.amount_minor IS NOT NULL
        ORDER BY ba.account_id, ba.as_of
        """
    ).fetchall()

    checked = len(rows)
    broken: list[dict[str, Any]] = [
        {
            "account_id": row["account_id"],
            "as_of": row["as_of"],
            "declared_minor": row["declared_minor"],
            "replayed_minor": row["replayed_minor"],
            "diff_minor": row["replayed_minor"] - row["declared_minor"],
        }
        for row in rows
        if row["replayed_minor"] != row["declared_minor"]
    ]

    results.append(
        CheckResult(
            "balance_assertions",
            BLOCK,
            "pass" if not broken else FAIL,
            (
                f"{checked} printed balance(s) reproduced by replaying the ledger"
                if not broken
                else f"{len(broken)} printed balance(s) disagree with the replayed ledger"
            ),
            {"checked": checked, "broken": broken[:20]},
        )
    )

    open_reviews = conn.execute(
        "SELECT COUNT(*) FROM review_item WHERE status = 'open' AND severity = 'block'"
    ).fetchone()[0]
    results.append(
        CheckResult(
            "review_queue",
            BLOCK,
            "pass" if open_reviews == 0 else FAIL,
            f"{open_reviews} blocking review item(s) still open",
            {"open": open_reviews},
        )
    )

    # Independent of the queue, and that is the entire point. P1 lets a person
    # dismiss a blocking review item; recording that decision is right, but if
    # it were the *only* record, dismissing one would turn `verify` green over a
    # statement whose transactions were never booked — a clean exit code on an
    # incomplete ledger, which is the failure this project exists to prevent.
    # This check asks the ledger instead of the queue: an archived statement
    # with no money behind it is missing, whoever clicked what.
    unbooked = repo.count_unbooked_statements(conn)
    archived = int(conn.execute("SELECT COUNT(*) FROM source_file").fetchone()[0])
    results.append(
        CheckResult(
            "unbooked_statements",
            BLOCK,
            "pass" if not unbooked else FAIL,
            (
                f"{archived} archived statement(s), all booked"
                if not unbooked
                else f"{len(unbooked)} of {archived} archived statement(s) "
                f"have no transactions booked"
            ),
            {
                "archived": archived,
                "unbooked": [
                    {
                        "source_file_id": row["source_file_id"],
                        "statement_month": row["statement_month"],
                    }
                    for row in unbooked[:20]
                ],
            },
        )
    )

    # This project reports what was earned and spent through two queries that
    # sum different postings of different row sets: `ledger_totals` adds up the
    # income and expense legs of every non-transfer transaction, while
    # `v_cashflow_monthly` adds up the own-account leg of the transactions that
    # carry a `txn_identity` row. They agree today because one function produces
    # every row either of them counts -- and that sentence was written four
    # times, refuted by construction three times, before it was accurate.
    #
    # A paragraph that took four attempts is not a guarantee, it is a hope with
    # references. This is the assertion that replaces it. Whichever of the two
    # numbers reaches the operator, the other one is checking it.
    #
    # P2 M5 and M6 added more parties: the category breakdown, whose slices are
    # drawn as a chart and each claim to be part of `outflow_minor`, and the
    # monthly split, whose bars claim to be all four figures divided by month.
    # `cashflow_disagreements` lists what is compared and what each comparison
    # can catch -- and lists rather than counts them, because the count in this
    # comment was wrong for the whole of M6.
    ledger = repo.ledger_totals(conn)
    disagreements = cashflow_disagreements(conn)
    results.append(
        CheckResult(
            "cashflow_agreement",
            BLOCK,
            "pass" if not disagreements else FAIL,
            (
                f"the cashflow aggregations agree: {format_minor(ledger['inflow_minor'])} in "
                f"and {format_minor(ledger['outflow_minor'])} out over "
                f"{ledger['txn_count']} transaction(s); both readings of the category "
                f"breakdown account for all of the out, and the monthly split for all "
                f"four figures, scoped and unscoped"
                if not disagreements
                else f"the cashflow aggregations disagree on "
                f"{', '.join(sorted(disagreements))}"
            ),
            {"disagreements": disagreements},
        )
    )

    # The other direction, and it needs the disk. `unbooked_statements` compares
    # source_file to txn_identity, so a statement the database has no row for at
    # all is invisible to it -- an empty ledger next to a full archive reports
    # "0 archived statement(s), all booked" and exits 0.
    #
    # That state is reachable without anyone deleting anything: archive_file
    # writes the bytes *before* the transaction that writes the source_file row,
    # so a Ctrl-C, a power cut or a failed write in between leaves exactly this.
    # The archive is the thing the rebuild invariant depends on; it not matching
    # the database is the one discrepancy that must never be quiet.
    if paths is None:
        for check_id in ARCHIVE_CHECK_IDS:
            results.append(
                CheckResult(
                    check_id,
                    BLOCK,
                    SKIP,
                    "no data directory was supplied, so archive/ could not be examined",
                    {},
                )
            )
        return results

    survey = survey_archive(paths)
    recorded = {str(row["id"]) for row in conn.execute("SELECT id FROM source_file")}

    orphaned = sorted(survey.shas - recorded)
    results.append(
        CheckResult(
            "archived_not_recorded",
            BLOCK,
            "pass" if not orphaned else FAIL,
            (
                f"every archived file has a source_file row ({len(recorded)} recorded)"
                if not orphaned
                else f"{len(orphaned)} archived file(s) have no source_file row; "
                f"the ledger does not know they exist"
            ),
            {"orphaned": orphaned[:20], "recorded": len(recorded)},
        )
    )

    # The mirror of the check above, and the more important direction of the two.
    # Everything this project promises rests on archive/ still being there: the
    # rebuild invariant, and "fix the parser and re-ingest the archived file",
    # which is the only route a refused statement has into the ledger. Deleting
    # an archived PDF while leaving its source_file row used to leave every check
    # green -- so did deleting archive/ entirely, because an empty directory
    # yields an empty orphan set. A tidy-up, a partial restore or a sync tool
    # that dropped a file is enough; nothing has to go wrong on purpose.
    missing = sorted(recorded - survey.shas)
    results.append(
        CheckResult(
            "recorded_not_archived",
            BLOCK,
            "pass" if not missing else FAIL,
            (
                f"every source_file row still has its bytes in archive/ ({len(survey.shas)} files)"
                if not missing
                else f"{len(missing)} recorded statement(s) are no longer in archive/; "
                f"the ledger cannot be rebuilt and they cannot be re-ingested"
            ),
            {"missing": missing[:20], "archived": len(survey.shas)},
        )
    )

    # An archived file's name *is* the sha256 of its contents, so whether the
    # two still agree is decidable at any moment -- and nothing was asking.
    # Rewriting one archived file, or swapping the contents of two, left every
    # check passing while a future rebuild would produce a different ledger and
    # call it the same one. ingest.archive already refuses content that does not
    # hash to its name; this asks the same question of files written long ago,
    # which is when bit rot and helpful file managers happen.
    #
    # Three outcomes, not two. Evidence of damage outranks absence of evidence:
    # if anything is corrupt or does not belong, that is a failure whatever else
    # is going on. But a file that could not be opened has not been checked, and
    # reporting the whole archive as intact on the strength of the files that
    # happened to be readable is the shape of statement this project exists to
    # not make. It reads as `skip`, which is `UNVERIFIED` — see §5.8.
    damaged = list(survey.corrupt) + list(survey.unexpected)
    if damaged:
        integrity_status = FAIL
        integrity_message = (
            f"{len(survey.corrupt)} archived file(s) do not hash to their own names, "
            f"{len(survey.unexpected)} unexpected entr(ies) present"
        )
        if survey.corrupt:
            # The repair exists and works; nothing was telling anyone what it is.
            # A refusal that leaves the operator to invent the next step is the
            # shape this project keeps having to fix.
            integrity_message += (
                ". Delete each corrupted file from archive/ and re-ingest that statement"
            )
        if survey.unreadable:
            # Without this the one line reads as "one problem, and everything
            # else was checked" -- while some of it was not checked at all.
            integrity_message += (
                f"; a further {len(survey.unreadable)} file(s) could not be read "
                f"and were not checked"
            )
    elif survey.unreadable:
        integrity_status = SKIP
        # "file(s) ... try again" was wrong whenever the thing that could not be
        # read was a *directory*: that is usually a permission, and retrying
        # does not fix a permission.
        integrity_message = (
            f"{len(survey.unreadable)} archived path(s) could not be read, so the archive "
            f"was not verified (a lock will clear on its own; a permission will not)"
        )
    else:
        integrity_status = "pass"
        integrity_message = f"all {len(survey.shas)} archived file(s) hash to their own names"

    results.append(
        CheckResult(
            "archive_integrity",
            BLOCK,
            integrity_status,
            integrity_message,
            {
                "corrupt": list(survey.corrupt[:20]),
                "unreadable": list(survey.unreadable[:20]),
                "unexpected": list(survey.unexpected[:20]),
                # Debris from an interrupted write, not damage. Reported so the
                # count is visible; never on its own a reason to fail.
                "stale_temp": list(survey.stale_temp[:20]),
            },
        )
    )
    return results
