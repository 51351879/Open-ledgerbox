# SPDX-License-Identifier: AGPL-3.0-or-later
"""Removing a statement: the inverse of :mod:`ledgerbox.ingest.pipeline`.

A statement that was uploaded by mistake, or refused and never booked, used to
have no way out of this ledger at all. ``verify`` stayed red on
``unbooked_statements`` for as long as the file existed, and the only button on
the page — Dismiss — deliberately books nothing and deletes nothing
(``docs/STATUS.md`` §5.13). This module is the missing direction.

**The standard deletion is held to.** Whatever is left afterwards must equal
what re-ingesting the *remaining* archive into an empty database would produce —
**over the eight statement-derived tables**. The qualification is load-bearing
and belongs here rather than only in the test: ``account``, ``category`` and
``commodity`` are reference rows created at ingest and idempotent
(``docs/STATUS.md`` §5.37), so forgetting the last statement leaves them
standing while a rebuild from an emptied archive would create neither. The
unqualified sentence is false, and ``tests/test_rebuild.py`` names both sets as
constants so the exclusion is asserted rather than described.

That standard is ``tests/test_rebuild.py``'s invariant applied to a smaller
archive, and it is what turns the three uncomfortable questions about deleting
from a double-entry ledger into answers:

* deleting a month in the middle leaves the later printed balances
  irreproducible. That is **correct** — the ledger really does have a hole, and
  a rebuild from the remaining archive has the same hole in the same places.
  What would not be correct is the operator discovering it afterwards, which is
  why :func:`plan_forget` *measures* the consequences before anything is written;
* an assertion on a day two statements share survives, with its provenance moved
  to the statement that still prints it (``docs/STATUS.md`` §5.7);
* the opening entry is re-derived, because it comes from the earliest surviving
  assertion (``docs/STATUS.md`` §5.5).

**Two things this refuses to do**, both reported as refusals rather than
absorbed:

* delete a statement whose period overlaps a surviving one. ``insert_entries``
  is check-then-insert, so a transaction two statements both report is booked
  once, under whichever was ingested first. Deleting that one takes a
  transaction the survivor also evidences, and nothing notices: the survivor
  still has its other identity rows, so ``unbooked_statements`` calls it booked.
  Re-pointing those identities at the survivor's ``raw_record`` would need
  "which payload is the same transaction" re-derived from the stored JSON — a
  second definition of the thing ``ledger.identity`` already defines, and
  ``docs/STATUS.md`` §5.29 is what a second definition costs. Refusing is the
  answer with a boundary that can be tested;
* delete a statement holding a transaction that supersedes one elsewhere.
  Unreachable today — nothing writes ``superseded_by`` — and named anyway,
  because the alternative is a foreign-key ``IntegrityError`` with no sentence
  attached to it.

**Database first, then the filesystem.** A crash between the two then leaves
bytes in ``archive/`` with no row, which ``archived_not_recorded`` reports and
which re-ingesting that same file repairs. The other order leaves a row whose
bytes are gone — ``recorded_not_archived``, whose documented repair is to
re-ingest the original file, which is the file that would just have been
deleted. One of these two failure modes has a way out.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..config import DataPaths
from ..db import repo
from ..db.connection import transaction
from ..reconcile.checks import CheckResult
from . import archive
from .pipeline import ARCHIVE_CHECK_IDS, verify_ledger

__all__ = [
    "ForgetPlan",
    "ForgetRefused",
    "ForgetResult",
    "forget_statement",
    "plan_forget",
]


class ForgetRefused(RuntimeError):
    """The deletion cannot be performed. Carries one reason per line."""

    def __init__(self, source_file_id: str, reasons: tuple[str, ...]) -> None:
        self.source_file_id = source_file_id
        self.reasons = reasons
        joined = "\n".join(f"  - {reason}" for reason in reasons)
        super().__init__(f"cannot forget {source_file_id[:12]}…:\n{joined}")


@dataclass(frozen=True, slots=True)
class ForgetPlan:
    """What deleting one statement would cost, measured rather than predicted.

    ``checks_after`` is the interesting field and it is not an estimate: the
    deletion is really performed, ``verify_ledger`` is really run against the
    result, and the transaction is then rolled back. The alternative — deriving
    "which printed balances would stop reproducing" with a second query — would
    be a second implementation of the replay that ``verify`` already does, free
    to disagree with it exactly when it matters.

    It holds **six** results, not nine. The three archive checks are left out
    because measuring them here would be a lie in either direction: the file is
    still on disk while this runs, so ``archived_not_recorded`` would fail on a
    statement that is about to be removed properly. :attr:`checks_note` is the
    sentence that says so, and it is meant to be shown, not just read here.
    """

    source_file_id: str
    facts: repo.DeletionFacts
    #: Non-empty means this will not be allowed to happen. See :class:`ForgetRefused`.
    refusals: tuple[str, ...] = ()
    #: Only measured when the deletion is allowed: a forecast for something that
    #: will not be permitted describes a ledger that will never exist.
    checks_after: tuple[CheckResult, ...] = ()
    #: ``None`` until the deletion has actually been performed and rolled back,
    #: for the same reason ``checks_after`` is empty until then: a forecast for
    #: something that will not be permitted describes a ledger that will never
    #: exist. It was an empty ``dict`` before, which read as "measured, and it
    #: came to nothing" — the same conflation ``balance_minor`` was carrying.
    #:
    #: And ``balance_minor`` inside them really can be ``None`` here: forgetting
    #: the last statement leaves no own-account posting at all, so afterwards
    #: the ledger has no balance to report rather than a balance of zero.
    totals_before: repo.LedgerTotals | None = None
    totals_after: repo.LedgerTotals | None = None
    #: The archived original, when it is still where the archive says it is.
    archive_path: Path | None = None
    #: The extraction cache. Rebuildable from the archive, and the single most
    #: disclosing file in the data directory (``docs/STATUS.md`` §5.31): it is
    #: the whole text layer, account number and address included.
    extracted_path: Path | None = None

    @property
    def allowed(self) -> bool:
        return not self.refusals

    @property
    def checks_note(self) -> str:
        return (
            f"measured on the ledger only; {len(ARCHIVE_CHECK_IDS)} archive checks "
            f"({', '.join(ARCHIVE_CHECK_IDS)}) are not simulated, because the "
            f"archived file is still on disk while this is measured"
        )

    @property
    def failing_after(self) -> tuple[CheckResult, ...]:
        return tuple(result for result in self.checks_after if result.failed)


@dataclass(frozen=True, slots=True)
class ForgetResult:
    """What actually happened. Every field is an observation after the fact."""

    source_file_id: str
    statement_month: str | None
    counts: repo.DeletionCounts
    #: Paths that are no longer there.
    removed_files: tuple[Path, ...] = ()
    #: Paths that could not be removed, with the reason. The ledger rows are
    #: already gone at this point, so anything here is bytes on disk that nothing
    #: accounts for. ``ledgerbox doctor`` reports both kinds and exits non-zero
    #: until they are gone.
    #:
    #: This used to name ``verify``'s ``archived_not_recorded``, which was true
    #: of a leftover archived statement and **false** of a leftover extraction
    #: cache — that check walks ``archive/`` and nothing else, so ``verify`` was
    #: green over a stranded ``.ndjson`` holding the entire text layer. See
    #: :func:`ledgerbox.ingest.pipeline.stranded_extractions`.
    unremoved_files: tuple[tuple[Path, str], ...] = ()
    #: All **nine** checks, run against the finished state. Unlike the plan's
    #: six, this one can include the archive: the disk has caught up.
    checks_after: tuple[CheckResult, ...] = ()

    @property
    def failing_after(self) -> tuple[CheckResult, ...]:
        return tuple(result for result in self.checks_after if result.failed)


def _ledger_checks(conn: sqlite3.Connection) -> tuple[CheckResult, ...]:
    """``verify_ledger`` minus the three questions that need the disk."""
    return tuple(
        result
        for result in verify_ledger(conn)
        if result.check_id not in ARCHIVE_CHECK_IDS
    )


def _refusals(conn: sqlite3.Connection, facts: repo.DeletionFacts) -> tuple[str, ...]:
    """Every reason this deletion will not be attempted. Empty means go ahead."""
    reasons: list[str] = []

    for row in repo.overlapping_statements(conn, facts.source_file_id):
        reasons.append(
            f"statement {str(row['source_file_id'])[:12]}… "
            f"({row['statement_month'] or 'period unread'}, "
            f"{row['period_start']} to {row['period_end']}) covers an overlapping "
            f"period. A transaction printed on both is booked once, under whichever "
            f"was ingested first, so deleting this one could remove a transaction "
            f"the other statement also reports — and nothing downstream would say so."
        )

    if facts.superseded_by_this:
        listed = ", ".join(txn_id[:12] + "…" for txn_id in facts.superseded_by_this[:5])
        reasons.append(
            f"{len(facts.superseded_by_this)} transaction(s) outside this statement are "
            f"marked as superseded by one inside it ({listed}). Removing it would leave "
            f"them pointing at nothing."
        )

    return tuple(reasons)


def plan_forget(
    conn: sqlite3.Connection, paths: DataPaths, source_file_id: str
) -> ForgetPlan:
    """Measure what deleting this statement would do. Changes nothing.

    Needs a connection that is both **writable and idle**, and the second half
    is not a formality: the measurement is a real deletion inside a transaction
    that is rolled back — the only way the forecast and the act can be the same
    code — so it issues its own ``BEGIN IMMEDIATE`` and calling it from inside
    :func:`~ledgerbox.db.connection.transaction` raises *cannot start a
    transaction within a transaction*. Wrapping this is the natural mistake for
    a caller to make, which is why it is written down here rather than left to
    be discovered.

    *source_file_id* must be a full id — use :func:`ledgerbox.db.repo.find_statement`
    to turn a prefix into one, so that "no such statement" and "that prefix is
    ambiguous" stay two different answers.
    """
    facts = repo.statement_deletion_facts(conn, source_file_id)
    refusals = _refusals(conn, facts)

    archived = archive.find_archived(paths, source_file_id)
    extracted = paths.extracted / f"{source_file_id}.ndjson"

    plan = ForgetPlan(
        source_file_id=source_file_id,
        facts=facts,
        refusals=refusals,
        archive_path=archived,
        extracted_path=extracted if extracted.is_file() else None,
    )
    if refusals:
        return plan

    totals_before = repo.ledger_totals(conn)

    # A real deletion, really verified, then really undone. `try/finally` rather
    # than the `transaction()` helper because the successful path has to roll
    # back too: this function's whole contract is that it leaves no trace.
    conn.execute("BEGIN IMMEDIATE")
    try:
        repo.delete_statement(conn, source_file_id)
        checks_after = _ledger_checks(conn)
        totals_after = repo.ledger_totals(conn)
    finally:
        conn.execute("ROLLBACK")

    return ForgetPlan(
        source_file_id=source_file_id,
        facts=facts,
        refusals=(),
        checks_after=checks_after,
        totals_before=totals_before,
        totals_after=totals_after,
        archive_path=archived,
        extracted_path=extracted if extracted.is_file() else None,
    )


def _remove(path: Path) -> str | None:
    """Delete one file. Returns None on success, or why it could not be deleted.

    Clears the read-only bit first: every archived original is chmod'd read-only
    on purpose, and on Windows that alone makes ``unlink`` fail.

    A file that is already gone counts as success. The goal state is "this is not
    on disk", and it holds — the same asymmetry, for the same reason, as
    :func:`ledgerbox.db.repo.clear_category_override` not raising for a
    transaction that has no override.
    """
    # Not being able to change the mode is not itself a failure; the unlink
    # below is the thing that decides.
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    try:
        path.unlink()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return str(exc)
    return None


def forget_statement(
    conn: sqlite3.Connection, paths: DataPaths, source_file_id: str
) -> ForgetResult:
    """Delete one statement from the ledger, the archive and the extraction cache.

    Re-checks the refusals rather than trusting a plan handed in from outside: a
    plan is a measurement of a moment, and the caller between the two is a
    browser. Raises :class:`ForgetRefused` if anything now says no.

    Empty shard directories are left behind on purpose. Removing
    ``archive/<YYYY>/<MM>`` because it happens to be empty races an ingest that
    has just created it, and an empty shard is not a finding —
    :func:`ledgerbox.ingest.pipeline.survey_archive` descends into it and reports
    nothing, which is the correct amount to say about an empty directory.
    """
    facts = repo.statement_deletion_facts(conn, source_file_id)
    refusals = _refusals(conn, facts)
    if refusals:
        raise ForgetRefused(source_file_id, refusals)

    archived = archive.find_archived(paths, source_file_id)
    extracted = paths.extracted / f"{source_file_id}.ndjson"

    with transaction(conn):
        counts = repo.delete_statement(conn, source_file_id)

    removed: list[Path] = []
    unremoved: list[tuple[Path, str]] = []
    for path in (archived, extracted):
        if path is None or not path.exists():
            continue
        problem = _remove(path)
        if problem is None:
            removed.append(path)
        else:
            unremoved.append((path, problem))

    return ForgetResult(
        source_file_id=source_file_id,
        statement_month=facts.statement_month,
        counts=counts,
        removed_files=tuple(removed),
        unremoved_files=tuple(unremoved),
        checks_after=tuple(verify_ledger(conn, paths)),
    )
