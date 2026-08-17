# SPDX-License-Identifier: AGPL-3.0-or-later
"""P2 M3: removing a statement, and the command line over it.

Almost all of this runs on CI, where there are no bank statements and never
will be. A ledger does not need a PDF to exist: :mod:`synth` builds a
Chase-shaped :class:`~ledgerbox.ingest.extract.Document` from coordinates, the
real parser reads it, ``build_entries`` turns it into transactions, and the
``repo.insert_*`` functions write them. :func:`book` does exactly that, and it
also archives a small file so that the statement has a genuine, genuinely
read-only archived original with a genuine sha256 — the id every deletion is
keyed on. The archived bytes are not the statement they stand for, which is
the one thing this file fakes and the reason it is said out loud here.

What that buys is that ``verify`` is *green* over these ledgers, all nine
checks, before anything is deleted. A deletion test on a ledger that was
already red would prove nothing about the checks going red afterwards.

One test needs a real PDF, because the extraction cache is only written for a
statement that could actually be parsed. It takes the ``real_statements``
fixture, which **skips** rather than fails when the corpus is absent — 43 of
the 44 tests here run without it. (The deletion invariant against a rebuild
needs the whole corpus and lives in ``tests/test_rebuild.py``, which is
real-gated in its entirety.)
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from synth import Row, simple_statement

from ledgerbox.cli import main
from ledgerbox.config import DataPaths
from ledgerbox.db import repo
from ledgerbox.db.connection import transaction
from ledgerbox.db.migrate import open_ledger
from ledgerbox.ingest import archive, pipeline
from ledgerbox.ingest.forget import ForgetRefused, forget_statement, plan_forget
from ledgerbox.ingest.registry import identify_or_raise
from ledgerbox.ledger import posting as posting_builder
from ledgerbox.ledger.identity import review_item_id
from ledgerbox.reconcile.report import ReviewItem

#: The shard every synthetic statement is filed under. Fixed so that a test can
#: name a path; the shard is the *ingest* date and has nothing to do with the
#: statement's period.
INGESTED_ON = date(2026, 2, 1)
INGESTED_AT = "2026-02-01T00:00:00+00:00"

BANK = "assets:chase:checking:1234"
OPENING_EQUITY = "equity:opening-balances"

#: Invented, and unlike anything a real statement prints. Every figure below is
#: chosen so the three months chain: each month's ending balance is the next
#: month's beginning balance, which is what makes the shared boundary day
#: (docs/STATUS.md §5.7) exist at all.
JANUARY_OPENING = "$700.00"
JANUARY_CLOSING = "$718.75"
FEBRUARY_CLOSING = "$700.00"
MARCH_CLOSING = "$675.00"


@dataclass(frozen=True, slots=True)
class Booked:
    """One statement, as it landed."""

    sha256: str
    month: str
    period_start: str
    period_end: str
    txn_ids: tuple[str, ...]


@pytest.fixture
def ledger(git_free_tmp: Path) -> Iterator[tuple[DataPaths, sqlite3.Connection]]:
    paths = DataPaths.resolve(git_free_tmp / "data")
    conn = open_ledger(paths.db)
    try:
        yield paths, conn
    finally:
        conn.close()


def book(
    conn: sqlite3.Connection,
    paths: DataPaths,
    document: Any,
    *,
    filler: str,
    ingested_on: date = INGESTED_ON,
) -> Booked:
    """Archive a placeholder original, then book a parsed statement against it.

    This is :func:`ledgerbox.ingest.pipeline.ingest_file` with the two steps
    that need a real PDF taken out — extraction and the reconciliation gate —
    and nothing else changed: same parser, same ``build_entries``, same writes,
    same order, including ``sync_opening_entry`` last because it is derived from
    the assertions written just above it.

    *filler* only has to differ between statements: it is what makes the
    archived bytes, and therefore the sha256 the whole deletion is keyed on,
    distinct.
    """
    statement = identify_or_raise(document).parse(document)
    entries = posting_builder.build_entries(statement)

    spool = paths.incoming / f"{filler}.pdf"
    spool.write_bytes(b"%PDF-1.7\n% placeholder for the synthetic statement " + filler.encode())
    archived = archive.archive_file(paths, spool, ingested_on=ingested_on)
    spool.unlink()

    with transaction(conn):
        repo.insert_source_file(
            conn,
            sha256=archived.sha256,
            rel_path=archived.rel_path,
            media_type=archived.media_type,
            byte_len=archived.byte_len,
            institution=statement.institution,
            period_start=statement.period_start.isoformat(),
            period_end=statement.period_end.isoformat(),
            ingested_at=INGESTED_AT,
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
        repo.ensure_categories(
            conn, rows=[("dining", None, "expense"), ("transfer", None, "transfer")]
        )
        repo.insert_raw_records(
            conn,
            source_file_id=archived.sha256,
            payloads=[(index, "stmttrn", "{}") for index in range(len(statement.transactions))],
            parser_id=statement.parser_id,
            parser_version=statement.parser_version,
        )
        repo.insert_entries(
            conn, source_file_id=archived.sha256, entries=list(entries.entries)
        )
        repo.upsert_balance_assertions(
            conn, source_file_id=archived.sha256, rows=list(entries.balance_assertions)
        )
        repo.sync_opening_entry(
            conn, account_id=entries.account_id, currency=entries.currency
        )

    return Booked(
        sha256=archived.sha256,
        month=statement.statement_month,
        period_start=statement.period_start.isoformat(),
        period_end=statement.period_end.isoformat(),
        txn_ids=tuple(entry.txn_id for entry in entries.entries),
    )


def january() -> Any:
    return simple_statement(
        period="January 01, 2025 through January 31, 2025",
        beginning=JANUARY_OPENING,
        ending=JANUARY_CLOSING,
        rows=[
            Row("01/09", "Zelle Payment From A Friend", "60.00", "760.00"),
            Row("01/22", "Card Purchase 01/21 Corner Store CA", "-41.25", "718.75"),
        ],
    )


def february() -> Any:
    return simple_statement(
        period="February 01, 2025 through February 28, 2025",
        beginning=JANUARY_CLOSING,
        ending=FEBRUARY_CLOSING,
        rows=[Row("02/11", "Card Purchase 02/10 Corner Store CA", "-18.75", "700.00")],
    )


def march() -> Any:
    return simple_statement(
        period="March 01, 2025 through March 31, 2025",
        beginning=FEBRUARY_CLOSING,
        ending=MARCH_CLOSING,
        rows=[Row("03/06", "Card Purchase 03/05 Corner Store CA", "-25.00", "675.00")],
    )


def three_months(conn: sqlite3.Connection, paths: DataPaths) -> tuple[Booked, Booked, Booked]:
    """January, February, March — consecutive, chained, and fully verified."""
    booked = (
        book(conn, paths, january(), filler="jan"),
        book(conn, paths, february(), filler="feb"),
        book(conn, paths, march(), filler="mar"),
    )
    assert failing(conn, paths) == [], "the fixture itself has to be a ledger that verifies"
    return booked


def failing(conn: sqlite3.Connection, paths: DataPaths | None = None) -> list[str]:
    return [r.check_id for r in pipeline.verify_ledger(conn, paths) if r.status != "pass"]


def assertion_rows(conn: sqlite3.Connection) -> dict[str, tuple[int, str | None]]:
    """``{as_of: (amount_minor, owning source_file_id)}``."""
    return {
        str(row["as_of"]): (int(row["amount_minor"]), row["source_file_id"])
        for row in conn.execute("SELECT as_of, amount_minor, source_file_id FROM balance_assertion")
    }


def opening_entries(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """``[(date, amount on the bank leg)]`` for every opening entry that exists."""
    return [
        (str(row["date"]), int(row["amount_minor"]))
        for row in conn.execute(
            "SELECT t.date, p.amount_minor FROM txn t "
            "JOIN posting p ON p.txn_id = t.id AND p.account_id = ? "
            "WHERE t.id IN (SELECT txn_id FROM posting WHERE account_id = ?) "
            "ORDER BY t.date",
            (BANK, OPENING_EQUITY),
        )
    ]


def archive_something_unreadable(paths: DataPaths, conn: sqlite3.Connection) -> str:
    """The product owner's actual situation: a statement that was refused.

    ``%PDF-`` is enough to be archived and not enough to be read, so this goes
    through the real pipeline and comes out the far side as a ``source_file``
    row, a blocking ``review_item`` and an archived file with no transactions —
    which is the state ``unbooked_statements`` exists to keep red.
    """
    spool = paths.incoming / "refused.pdf"
    spool.write_bytes(b"%PDF-1.7\n% enough to archive, not enough to read\n")
    outcomes = pipeline.ingest_paths(conn, paths, [spool])
    spool.unlink(missing_ok=True)
    assert [o.status for o in outcomes] == [pipeline.NEEDS_REVIEW]
    sha = outcomes[0].sha256
    assert sha is not None
    return sha


# ---------------------------------------------------------------------------
# 1. the refused statement — the case the product owner is stuck on
# ---------------------------------------------------------------------------


def test_a_refused_statement_can_be_forgotten_and_verify_goes_green(ledger) -> None:
    """docs/STATUS.md §2.5 point 5, end to end.

    Before: two block-level checks are red and no supported action clears them.
    Dismissing the review item empties the queue and leaves
    ``unbooked_statements`` exactly as red (`test_pipeline.py` pins that). The
    only way out is to remove the statement, and until M3 there was none.
    """
    paths, conn = ledger
    sha = archive_something_unreadable(paths, conn)

    assert sorted(failing(conn, paths)) == ["review_queue", "unbooked_statements"]

    result = forget_statement(conn, paths, sha)

    assert result.counts.review_items == 1
    assert result.counts.txns == 0, "there was never anything booked to remove"
    assert [check.check_id for check in result.failing_after] == []
    assert all(check.status == "pass" for check in result.checks_after)
    assert len(result.checks_after) == 9, "the real thing measures the archive too"
    assert failing(conn, paths) == []
    assert repo.row_counts(conn)["source_file"] == 0


def test_the_plan_for_a_refused_statement_measures_six_checks_not_nine(ledger) -> None:
    """The other half of the same case: the forecast admits what it left out.

    The archived file is still on disk while a plan is measured, so simulating
    ``archived_not_recorded`` there would fail on a statement that is about to
    be removed properly — a forecast that is wrong in the reassuring direction.
    """
    paths, conn = ledger
    sha = archive_something_unreadable(paths, conn)

    plan = plan_forget(conn, paths, sha)

    assert plan.allowed
    assert len(plan.checks_after) == 6
    assert [check.check_id for check in plan.failing_after] == []
    for check_id in pipeline.ARCHIVE_CHECK_IDS:
        assert check_id not in {check.check_id for check in plan.checks_after}
        assert check_id in plan.checks_note
    # And the ledger is still exactly as broken as it was: a plan is not a fix.
    assert sorted(failing(conn, paths)) == ["review_queue", "unbooked_statements"]


# ---------------------------------------------------------------------------
# 2. a plan leaves no trace
# ---------------------------------------------------------------------------


def test_planning_a_deletion_changes_not_one_row(ledger) -> None:
    """The measurement is a real deletion inside a transaction that is rolled back.

    That is the only way the forecast and the act can be the same code, and it
    is also the only way this can be got wrong: the rollback is load-bearing,
    so it needs an assertion rather than a comment.
    """
    paths, conn = ledger
    jan, feb, mar = three_months(conn, paths)

    before_counts = repo.row_counts(conn)
    before_totals = repo.ledger_totals(conn)
    before_assertions = assertion_rows(conn)

    for target in (jan, feb, mar):
        plan = plan_forget(conn, paths, target.sha256)
        assert plan.allowed
        assert repo.row_counts(conn) == before_counts
        assert repo.ledger_totals(conn) == before_totals
        assert assertion_rows(conn) == before_assertions

    # Including the files, which a plan only ever reports on.
    assert plan.archive_path is not None and plan.archive_path.is_file()
    assert failing(conn, paths) == []


def test_the_plan_reports_the_totals_the_deletion_would_leave(ledger) -> None:
    """Measured on the rolled-back result, so it cannot drift from the real one."""
    paths, conn = ledger
    _, _, mar = three_months(conn, paths)

    plan = plan_forget(conn, paths, mar.sha256)
    assert plan.totals_before == repo.ledger_totals(conn)
    assert plan.totals_after != plan.totals_before

    forget_statement(conn, paths, mar.sha256)

    assert repo.ledger_totals(conn) == plan.totals_after


def test_the_plan_counts_what_the_deletion_then_removes(ledger) -> None:
    """``DeletionFacts`` and ``DeletionCounts`` are two objects; one arithmetic."""
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)

    facts = plan_forget(conn, paths, jan.sha256).facts
    counts = forget_statement(conn, paths, jan.sha256).counts

    assert (counts.txns, counts.postings, counts.identities) == (
        facts.txns,
        facts.postings,
        facts.identities,
    )
    assert counts.raw_records == facts.raw_records
    assert counts.review_items == facts.review_items
    assert counts.balance_assertions_reassigned == facts.balance_assertions_shared
    assert (
        counts.balance_assertions_removed
        == facts.balance_assertions - facts.balance_assertions_shared
    )


# ---------------------------------------------------------------------------
# 4. the shared boundary day (docs/STATUS.md §5.7)
# ---------------------------------------------------------------------------


def test_the_assertion_on_a_shared_day_survives_with_its_provenance_moved(ledger) -> None:
    """January's closing balance is February's opening balance: one row, two claims.

    Deleting January must not take it, because February still prints it — and
    ingesting February alone into an empty database would produce exactly this
    row. What has to move is the provenance, or the surviving row would point at
    a ``source_file`` that no longer exists.
    """
    paths, conn = ledger
    jan, feb, _ = three_months(conn, paths)

    seam = jan.period_end
    before = assertion_rows(conn)
    assert before[seam][1] == jan.sha256, "the statement that *closes* on the day owns it (§5.7)"

    forget_statement(conn, paths, jan.sha256)

    after = assertion_rows(conn)
    assert seam in after, "February still prints this balance, so the fact survives"
    assert after[seam][0] == before[seam][0], "and it is the same number"
    assert after[seam][1] == feb.sha256, "with provenance moved to the statement that prints it"
    assert failing(conn, paths) == []


def test_the_assertion_nobody_else_prints_is_removed(ledger) -> None:
    """The failing half of the same rule.

    January's *opening* balance is dated the day before its period starts. No
    surviving statement asserts anything about that day, so keeping the row
    would leave the ledger claiming a balance no document in it evidences.
    """
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)

    orphan_day = "2024-12-31"
    before = assertion_rows(conn)
    assert before[orphan_day][1] == jan.sha256

    result = forget_statement(conn, paths, jan.sha256)

    assert orphan_day not in assertion_rows(conn)
    assert result.counts.balance_assertions_removed == 1
    assert result.counts.balance_assertions_reassigned == 1


# ---------------------------------------------------------------------------
# 5. the opening entry (docs/STATUS.md §5.5)
# ---------------------------------------------------------------------------


def test_deleting_the_earliest_statement_moves_the_opening_entry(ledger) -> None:
    """It is derived from the earliest surviving assertion, and that day just moved."""
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)

    assert opening_entries(conn) == [("2024-12-31", 700_00)]

    result = forget_statement(conn, paths, jan.sha256)

    assert opening_entries(conn) == [("2025-01-31", 718_75)]
    assert result.counts.opening_txn_ids, "there is still an assertion to derive one from"
    assert failing(conn, paths) == []
    # The replay still lands on every surviving printed balance, which is the
    # only reason moving it is right rather than merely tidy.
    assert repo.ledger_totals(conn)["balance_minor"] == 675_00


def test_deleting_the_last_statement_leaves_no_opening_entry_at_all(ledger) -> None:
    """The case ``sync_opening_entry`` was fixed for, written as its regression.

    Before the fix the function returned early when no assertion was left, and
    the opening entry stayed: an equity leg asserting a balance that no
    surviving document claims. Every check still passed — ``balance_assertions``
    has nothing left to check, ``double_entry`` is happy because the orphan sums
    to zero — while ``balance_minor`` reported money that is not there, and
    re-ingesting the (now empty) archive produced no such row. That gap is the
    whole of what a deletion must not do.
    """
    paths, conn = ledger
    only = book(conn, paths, january(), filler="jan")
    assert failing(conn, paths) == []
    assert opening_entries(conn) == [("2024-12-31", 700_00)]

    result = forget_statement(conn, paths, only.sha256)

    assert opening_entries(conn) == []
    assert result.counts.opening_txn_ids == ()
    assert conn.execute(
        "SELECT COUNT(*) FROM posting WHERE account_id = ?", (OPENING_EQUITY,)
    ).fetchone()[0] == 0
    assert repo.ledger_totals(conn)["balance_minor"] is None, (
        "the ledger must not report a balance no document behind it claims -- and "
        "$0.00 was the nearest the old shape could come to saying that, while being "
        "itself a claim that the account held nothing"
    )
    counts = repo.row_counts(conn)
    assert (counts["txn"], counts["posting"], counts["balance_assertion"]) == (0, 0, 0)
    assert failing(conn, paths) == []


# ---------------------------------------------------------------------------
# 6. a middle month goes red, and that is correct
# ---------------------------------------------------------------------------


def test_the_plan_says_the_middle_month_goes_red_before_anything_is_written(ledger) -> None:
    """docs/STATUS.md §2.5 point 1.

    Deleting February leaves the ledger unable to reproduce two printed
    balances — February's closing figure and March's — because the money that
    got there is gone. That is the ledger being *honest about a hole*, not a
    bug: a rebuild from the remaining archive has the same hole in the same
    places. What would be wrong is the operator finding out afterwards, so the
    assertion here is on the **plan**: the numbers are in front of them before
    they type --yes.
    """
    paths, conn = ledger
    _, feb, _ = three_months(conn, paths)

    plan = plan_forget(conn, paths, feb.sha256)

    assert plan.allowed, "this is permitted; it is merely consequential"
    failed = {check.check_id: check for check in plan.failing_after}
    assert "balance_assertions" in failed
    broken = failed["balance_assertions"].detail["broken"]
    assert [row["as_of"] for row in broken] == ["2025-02-28", "2025-03-31"]
    assert all(row["diff_minor"] == 18_75 for row in broken), (
        "every later balance is short by exactly the money February moved"
    )
    # Nothing else went wrong, and nothing has happened yet.
    assert {check.check_id for check in plan.failing_after} == {"balance_assertions"}
    assert failing(conn, paths) == []

    forget_statement(conn, paths, feb.sha256)
    assert failing(conn, paths) == ["balance_assertions"], (
        "the plan predicted this exactly, which is the point of measuring it"
    )


def test_deleting_the_newest_month_leaves_every_check_green(ledger) -> None:
    """The contrast case. Nothing later depends on March, so nothing goes red."""
    paths, conn = ledger
    _, _, mar = three_months(conn, paths)

    plan = plan_forget(conn, paths, mar.sha256)
    assert [check.check_id for check in plan.failing_after] == []

    result = forget_statement(conn, paths, mar.sha256)
    assert [check.check_id for check in result.failing_after] == []
    assert failing(conn, paths) == []
    assert repo.ledger_totals(conn)["balance_minor"] == 700_00


# ---------------------------------------------------------------------------
# 7. the overlap refusal (docs/STATUS.md §2.5 point 4)
# ---------------------------------------------------------------------------

#: Printed by both statements below, identically: same date, same amount, same
#: description. That is what makes it one transaction rather than two.
SHARED_ROW = Row("01/20", "Card Purchase 01/19 Corner Store CA", "-10.00", "690.00")


def overlapping_pair(conn: sqlite3.Connection, paths: DataPaths) -> tuple[Booked, Booked]:
    """Two statements whose periods overlap, sharing one printed transaction.

    §2.5 point 4 says of this shape: "13 real Chase statements do not overlap,
    **but this hole was reasoned about and never constructed**". This is the
    construction. Both statements reconcile internally and the ledger they make
    passes all nine checks — the hazard is invisible to every one of them.
    """
    first = book(
        conn,
        paths,
        simple_statement(
            period="January 01, 2025 through January 31, 2025",
            beginning=JANUARY_OPENING,
            ending="$690.00",
            rows=[SHARED_ROW],
        ),
        filler="overlap-first",
    )
    second = book(
        conn,
        paths,
        simple_statement(
            period="January 15, 2025 through February 14, 2025",
            beginning=JANUARY_OPENING,
            ending="$685.00",
            rows=[
                SHARED_ROW,
                Row("02/01", "Card Purchase 01/31 Corner Store CA", "-5.00", "685.00"),
            ],
        ),
        filler="overlap-second",
    )
    return first, second


def test_a_transaction_two_statements_print_is_booked_once_under_the_first(ledger) -> None:
    """The latent hazard itself, made visible.

    ``insert_entries`` is check-then-insert on the natural key, so the second
    statement's copy of the shared row is skipped: it has a ``raw_record`` for
    that line and no ``txn_identity`` pointing at it. Nothing anywhere reports
    this, and nothing should — the ledger is *correct*, the transaction really
    did happen once. It is only deleting the owner that turns it into a problem.
    """
    paths, conn = ledger
    first, second = overlapping_pair(conn, paths)

    assert set(first.txn_ids) & set(second.txn_ids), "the two statements share a transaction id"
    shared = (set(first.txn_ids) & set(second.txn_ids)).pop()

    assert conn.execute("SELECT COUNT(*) FROM txn WHERE id = ?", (shared,)).fetchone()[0] == 1
    owner = conn.execute(
        "SELECT rr.source_file_id FROM txn_identity ti "
        "JOIN raw_record rr ON rr.id = ti.raw_record_id WHERE ti.txn_id = ?",
        (shared,),
    ).fetchone()
    assert owner[0] == first.sha256, "booked under whichever was ingested first"

    # And every check is green over it. That is the whole difficulty.
    assert failing(conn, paths) == []
    assert repo.count_unbooked_statements(conn) == []


def test_an_overlapping_period_refuses_the_deletion_in_both_directions(ledger) -> None:
    """What would go wrong without the refusal, stated as the reason for it.

    Deleting the first statement removes the shared transaction — the survivor
    still prints it, but the row is gone. Nothing downstream notices:
    ``unbooked_statements`` looks for *any* ``txn_identity`` behind a statement
    and the survivor still has one for its other line, so it goes on calling the
    survivor booked while a transaction it also reports has silently left the
    ledger. Re-pointing the identity at the survivor's ``raw_record`` would need
    "which payload is the same transaction" derived a second time from stored
    JSON — a second definition of what ``ledger.identity`` already defines, and
    §5.29 is what a second definition costs. Refusing is the answer with a
    boundary that can be tested; this is the test.
    """
    paths, conn = ledger
    first, second = overlapping_pair(conn, paths)
    before = repo.row_counts(conn)

    for target, other in ((first, second), (second, first)):
        plan = plan_forget(conn, paths, target.sha256)
        assert plan.allowed is False
        assert len(plan.refusals) == 1
        assert other.sha256[:12] in plan.refusals[0]
        assert "overlapping period" in plan.refusals[0]
        # A refused plan measures nothing: a forecast for a ledger that will
        # never exist describes nothing.
        assert plan.checks_after == ()
        # `None` rather than an empty mapping, which read as "measured, and it
        # came to nothing" -- the same conflation the balance itself was making.
        assert plan.totals_after is None

        with pytest.raises(ForgetRefused) as excinfo:
            forget_statement(conn, paths, target.sha256)
        assert excinfo.value.reasons == plan.refusals
        assert excinfo.value.source_file_id == target.sha256

    assert repo.row_counts(conn) == before, "a refusal writes nothing and deletes nothing"
    assert plan.archive_path is not None and plan.archive_path.is_file()


def test_a_statement_with_no_period_can_neither_overlap_nor_be_overlapped(ledger) -> None:
    """The passing half. A refused statement has no period and no transactions.

    If "no period" were treated as "overlaps everything", the one deletion this
    milestone exists for would be the one it refused.
    """
    paths, conn = ledger
    three_months(conn, paths)
    refused = archive_something_unreadable(paths, conn)

    assert repo.overlapping_statements(conn, refused) == []
    assert plan_forget(conn, paths, refused).allowed is True


def test_a_transaction_superseded_from_outside_refuses_the_deletion(ledger) -> None:
    """The second refusal, which nothing in the product can reach yet.

    Nothing writes ``superseded_by`` today, so this state is planted by hand.
    That is the reason to test it rather than a reason not to: the branch exists
    so the failure is a sentence instead of a foreign-key ``IntegrityError``
    with nothing attached to it, and a branch nobody has watched fire is not a
    branch anyone can rely on.
    """
    paths, conn = ledger
    jan, feb, _ = three_months(conn, paths)

    with transaction(conn):
        conn.execute(
            "UPDATE txn SET superseded_by = ? WHERE id = ?", (feb.txn_ids[0], jan.txn_ids[0])
        )

    plan = plan_forget(conn, paths, feb.sha256)

    assert plan.allowed is False
    assert plan.facts.superseded_by_this == (jan.txn_ids[0],)
    assert "superseded" in plan.refusals[0]
    with pytest.raises(ForgetRefused):
        forget_statement(conn, paths, feb.sha256)

    # January itself is still deletable: it holds the superseded row, not the
    # one doing the superseding.
    assert plan_forget(conn, paths, jan.sha256).allowed is True


# ---------------------------------------------------------------------------
# 8. category_override — the one thing a deletion destroys
# ---------------------------------------------------------------------------


def test_a_category_override_is_counted_before_it_is_destroyed(ledger) -> None:
    """docs/STATUS.md §5.49: the only table ``archive/`` cannot reproduce.

    Everything else a deletion removes comes back if the same PDF is ingested
    again. This does not, so the number has to be in front of the person before
    they confirm — and it has to be true afterwards.
    """
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    with transaction(conn):
        repo.set_category_override(conn, txn_id=jan.txn_ids[1], category_id="dining")
    assert len(repo.list_category_overrides(conn)) == 1

    plan = plan_forget(conn, paths, jan.sha256)
    assert plan.facts.category_overrides == 1

    result = forget_statement(conn, paths, jan.sha256)

    assert result.counts.category_overrides == 1
    assert repo.list_category_overrides(conn) == []
    assert repo.row_counts(conn)["category_override"] == 0
    # The category itself is reference data and stays: it is the rules file's
    # mirror (§5.37), not a record of anything the deleted statement said.
    assert repo.row_counts(conn)["category"] > 0


def test_an_override_on_another_statement_is_not_counted_or_touched(ledger) -> None:
    """The negative case: the count is of *this* statement's overrides."""
    paths, conn = ledger
    jan, feb, _ = three_months(conn, paths)
    with transaction(conn):
        repo.set_category_override(conn, txn_id=feb.txn_ids[0], category_id="dining")

    plan = plan_forget(conn, paths, jan.sha256)
    assert plan.facts.category_overrides == 0

    result = forget_statement(conn, paths, jan.sha256)

    assert result.counts.category_overrides == 0
    assert [row["txn_id"] for row in repo.list_category_overrides(conn)] == [feb.txn_ids[0]]


# ---------------------------------------------------------------------------
# 9. find_statement — "no match" and "several matches" are different mistakes
# ---------------------------------------------------------------------------


def plant_bare_statement(conn: sqlite3.Connection, source_file_id: str) -> None:
    """A ``source_file`` row and nothing else, to make a prefix ambiguous.

    Two archived files cannot be made to share eight leading hex characters
    without a sha-256 collision, so the rows are written directly. Nothing here
    tests archiving; :func:`repo.find_statement` reads ``v_statement``, which is
    ``source_file``.
    """
    with transaction(conn):
        repo.insert_source_file(
            conn,
            sha256=source_file_id,
            rel_path=f"2026/02/{source_file_id}.pdf",
            media_type="application/pdf",
            byte_len=1,
            institution="chase",
            period_start="2025-05-01",
            period_end="2025-05-31",
            ingested_at=INGESTED_AT,
        )


#: 64 hex characters each, sharing a 12-character prefix and differing in the
#: last one. Letters are interleaved so these carry no long run of digits — the
#: repository's own leak guard reads shapes, not intentions.
TWIN_A = "abcdef012345" + "ab" * 25 + "ac"
TWIN_B = "abcdef012345" + "ab" * 25 + "ad"


def test_find_statement_accepts_the_full_id(ledger) -> None:
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    assert repo.find_statement(conn, jan.sha256)["source_file_id"] == jan.sha256


def test_find_statement_accepts_an_unambiguous_prefix(ledger) -> None:
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    found = repo.find_statement(conn, jan.sha256[:8])
    assert found["source_file_id"] == jan.sha256
    # Upper case and surrounding whitespace are how an id arrives off a screen.
    messy = repo.find_statement(conn, f"  {jan.sha256[:10].upper()} ")
    assert messy["source_file_id"] == jan.sha256


@pytest.mark.parametrize(
    ("needle", "why"),
    [
        ("abc", "shorter than the minimum prefix"),
        ("abcdef0", "one character short of the minimum"),
        ("zzzzzzzz", "the right length and not hex"),
        ("abcdef01g", "hex with one character that is not"),
        ("abcdef 01", "a space is not a hex digit"),
        ("", "nothing at all"),
    ],
)
def test_find_statement_refuses_a_needle_that_is_not_a_statement_id(
    ledger, needle: str, why: str
) -> None:
    """These fail before any lookup, and say what a statement id looks like."""
    paths, conn = ledger
    three_months(conn, paths)
    with pytest.raises(repo.StatementNotFound, match="is not a statement id"):
        repo.find_statement(conn, needle)


def test_find_statement_refuses_a_well_formed_prefix_that_matches_nothing(ledger) -> None:
    paths, conn = ledger
    three_months(conn, paths)
    with pytest.raises(repo.StatementNotFound, match="no archived statement"):
        repo.find_statement(conn, "0123456789ab")


def test_an_ambiguous_prefix_is_refused_and_names_its_candidates(ledger) -> None:
    """Never resolved to the first match: the two are different statements."""
    _, conn = ledger
    plant_bare_statement(conn, TWIN_A)
    plant_bare_statement(conn, TWIN_B)

    with pytest.raises(repo.AmbiguousStatement) as excinfo:
        repo.find_statement(conn, "abcdef01")

    assert excinfo.value.candidates == (TWIN_A, TWIN_B)
    assert TWIN_A in str(excinfo.value) and TWIN_B in str(excinfo.value)
    assert "Give more of the id" in str(excinfo.value)
    # One more character still is not enough; the whole thing is.
    with pytest.raises(repo.AmbiguousStatement):
        repo.find_statement(conn, "abcdef012345")
    assert repo.find_statement(conn, TWIN_A)["source_file_id"] == TWIN_A


# ---------------------------------------------------------------------------
# 10. the files
# ---------------------------------------------------------------------------


def test_the_archived_original_and_the_extraction_cache_are_both_gone(ledger) -> None:
    """The archived PDF is chmod'd read-only on purpose; the cache is the text layer.

    ``extracted/<sha>.ndjson`` holds the *whole* extracted text of the
    statement — account number, name, address, every counterparty
    (docs/STATUS.md §5.31). A deletion that removed the rows and left that file
    would leave the single most disclosing artefact in the data directory
    exactly where it was.

    The cache is written by hand here because only a genuinely parseable PDF
    produces one; the archived original next to it is real, and really
    read-only.
    """
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)

    archived = archive.find_archived(paths, jan.sha256)
    assert archived is not None and archived.is_file()
    assert not (archived.stat().st_mode & 0o200), "the archive is written read-only"
    cache = paths.extracted / f"{jan.sha256}.ndjson"
    cache.write_text('{"page": 1, "spans": []}\n', encoding="utf-8")

    plan = plan_forget(conn, paths, jan.sha256)
    assert plan.archive_path == archived
    assert plan.extracted_path == cache

    result = forget_statement(conn, paths, jan.sha256)

    assert set(result.removed_files) == {archived, cache}
    assert result.unremoved_files == ()
    assert not archived.exists(), "read-only is not a reason for a deletion to fail"
    assert not cache.exists()
    assert archived.parent.is_dir(), "the empty shard is left alone on purpose"


def test_a_missing_cache_is_not_reported_as_removed(ledger) -> None:
    """The other half: only files that were there are claimed to have gone."""
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    assert not (paths.extracted / f"{jan.sha256}.ndjson").exists()

    plan = plan_forget(conn, paths, jan.sha256)
    assert plan.extracted_path is None

    result = forget_statement(conn, paths, jan.sha256)
    assert [path.suffix for path in result.removed_files] == [".pdf"]


def skip_unless_an_open_file_cannot_be_unlinked(paths: DataPaths) -> None:
    """POSIX unlinks a file that is open; Windows refuses. Only one is testable.

    Asked of this host rather than of ``os.name``, because what matters is
    whether the condition can be produced at all.
    """
    probe = paths.incoming / "unlink-probe.bin"
    probe.write_bytes(b"x")
    with probe.open("rb"):
        try:
            probe.unlink()
        except OSError:
            return
    probe.unlink(missing_ok=True)
    pytest.skip("this host lets an open file be unlinked; the failure cannot be produced here")


def test_a_file_that_cannot_be_deleted_is_reported_rather_than_assumed_gone(ledger) -> None:
    """The rows are already gone by then, so silence here would be the worst answer.

    ``verify`` will report ``archived_not_recorded`` for as long as the file is
    there, and the only repair is a person deleting it — which they can only do
    if they are told. Held open rather than merely read-only, because read-only
    is cleared and unlinked successfully (the test above pins that).
    """
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    archived = archive.find_archived(paths, jan.sha256)
    assert archived is not None
    skip_unless_an_open_file_cannot_be_unlinked(paths)

    handle = archived.open("rb")
    try:
        result = forget_statement(conn, paths, jan.sha256)
    finally:
        handle.close()

    assert [path for path, _ in result.unremoved_files] == [archived]
    assert result.removed_files == ()
    assert archived.exists()
    # And the consequence is reported, not smoothed over: the rows are gone, so
    # the archive now holds bytes nothing in the database accounts for.
    failed = {check.check_id for check in result.failing_after}
    assert "archived_not_recorded" in failed

    archived.chmod(0o600)
    archived.unlink()


def test_a_stranded_extraction_cache_is_invisible_to_verify(ledger) -> None:
    """The other kind of leftover, and ``verify`` cannot see it. That is the point.

    The sibling test above holds the archived PDF open and ``verify`` goes red on
    ``archived_not_recorded``. Hold the *extraction cache* open instead and all
    nine checks come back green — that check walks ``archive/`` and nothing else.

    Four places used to tell the operator that ``verify`` would keep reporting a
    file that could not be deleted. It was true of the PDF and false of the
    cache, and the cache is the file holding the whole text layer: account
    number, name, address, every counterparty (docs/STATUS.md §5.31, §5.62). A
    person following that instruction ran ``verify``, saw nine green checks, and
    concluded the file was gone.

    This test asserts the *absence* deliberately. If a later change makes
    ``verify`` notice, this goes red and the sentences that now point at
    ``doctor`` can point at either.
    """
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    cache = paths.extracted / f"{jan.sha256}.ndjson"
    cache.write_text('{"page": 1, "spans": []}\n', encoding="utf-8")
    skip_unless_an_open_file_cannot_be_unlinked(paths)

    handle = cache.open("rb")
    try:
        result = forget_statement(conn, paths, jan.sha256)
    finally:
        handle.close()

    assert [path for path, _ in result.unremoved_files] == [cache]
    assert cache.exists()
    assert result.failing_after == (), "verify does not look in extracted/, and that is the bug"

    # What does see it, and what the messages now name.
    assert pipeline.stranded_extractions(conn, paths) == [cache.name]
    assert main(["--data-dir", str(paths.root), "doctor"]) != 0

    cache.unlink()
    assert pipeline.stranded_extractions(conn, paths) == []
    assert main(["--data-dir", str(paths.root), "doctor"]) == 0


def test_a_data_directory_with_nothing_stranded_says_so(ledger) -> None:
    """The negative half: a cache that still has its statement is not stranded.

    Without this the check could be ``return sorted(os.listdir(...))`` and every
    assertion above would still pass.
    """
    paths, conn = ledger
    jan, feb, _ = three_months(conn, paths)
    for sha in (jan.sha256, feb.sha256):
        (paths.extracted / f"{sha}.ndjson").write_text("{}\n", encoding="utf-8")

    assert pipeline.stranded_extractions(conn, paths) == []
    assert main(["--data-dir", str(paths.root), "doctor"]) == 0

    # Only what this program writes is judged, and only by the name it writes it
    # under: a file it did not put there is not its business to call stranded.
    (paths.extracted / "notes.txt").write_text("mine", encoding="utf-8")
    assert pipeline.stranded_extractions(conn, paths) == []

    # Forgetting February strands February's cache and nothing else.
    forget_statement(conn, paths, feb.sha256)
    assert pipeline.stranded_extractions(conn, paths) == []

    # And a cache whose statement was never recorded at all is stranded, whether
    # or not its name looks like a hash.
    (paths.extracted / "deadbeef.ndjson").write_text("{}\n", encoding="utf-8")
    assert pipeline.stranded_extractions(conn, paths) == ["deadbeef.ndjson"]


def test_forgetting_a_real_statement_removes_both_of_its_files(
    git_free_tmp: Path, real_statements: list[Path]
) -> None:
    """The same claim over a statement that was really parsed from a real PDF.

    Skips without the corpus. Everything above builds its ledger in code; this
    one exists so that the extraction cache being deleted is asserted at least
    once about a cache this program actually wrote.
    """
    paths = DataPaths.resolve(git_free_tmp / "real")
    conn = open_ledger(paths.db)
    try:
        outcome = pipeline.ingest_file(conn, paths, real_statements[0])
        assert outcome.status == pipeline.IMPORTED
        sha = outcome.sha256
        assert sha is not None

        cache = paths.extracted / f"{sha}.ndjson"
        archived = archive.find_archived(paths, sha)
        assert cache.is_file() and archived is not None and archived.is_file()

        result = forget_statement(conn, paths, sha)

        assert set(result.removed_files) == {archived, cache}
        assert not cache.exists() and not archived.exists()
        assert failing(conn, paths) == []
        assert repo.row_counts(conn)["txn"] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 11. the command line
# ---------------------------------------------------------------------------


def cli(git_free_tmp: Path, *argv: str) -> int:
    return main(["--data-dir", str(git_free_tmp / "data"), "forget", *argv])


def test_forget_without_yes_deletes_nothing_and_exits_two(ledger, git_free_tmp, capsys) -> None:
    """A command called ``forget`` that deleted nothing must not report success.

    Exit 0 in this CLI means "everything imported and verified" and this thing
    ends up in cron. So the run that changed nothing exits 2 and the last line
    says what to add.
    """
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    before = repo.row_counts(conn)
    conn.close()

    assert cli(git_free_tmp, jan.sha256[:12]) == 2

    out = capsys.readouterr().out
    assert "nothing has been deleted" in out
    assert "--yes" in out
    assert jan.sha256[:12] in out
    assert "2025-01" in out and "2025-01-01 to 2025-01-31" in out

    conn = open_ledger(paths.db)
    try:
        assert repo.row_counts(conn) == before
    finally:
        conn.close()
    assert archive.find_archived(paths, jan.sha256) is not None


def test_forget_with_yes_deletes_and_exits_zero(ledger, git_free_tmp, capsys) -> None:
    paths, conn = ledger
    jan, _, mar = three_months(conn, paths)
    conn.close()

    assert cli(git_free_tmp, mar.sha256[:8], "--yes") == 0

    out = capsys.readouterr().out
    assert "forgot" in out and mar.sha256[:12] in out
    assert "all 9 check(s) pass." in out

    conn = open_ledger(paths.db)
    try:
        assert repo.row_counts(conn)["source_file"] == 2
        assert failing(conn, paths) == []
        assert archive.find_archived(paths, mar.sha256) is None
        assert archive.find_archived(paths, jan.sha256) is not None
    finally:
        conn.close()


def test_forget_with_an_unknown_id_exits_two_with_one_sentence(git_free_tmp, capsys) -> None:
    """No traceback. There is nothing here a stack trace would tell anyone."""
    assert cli(git_free_tmp, "0123456789ab") == 2

    captured = capsys.readouterr()
    assert "no archived statement" in captured.err
    assert "Traceback" not in captured.err + captured.out
    assert len(captured.err.strip().splitlines()) == 1


def test_forget_with_a_needle_that_is_not_an_id_says_what_an_id_looks_like(
    git_free_tmp, capsys
) -> None:
    assert cli(git_free_tmp, "nope") == 2
    err = capsys.readouterr().err
    assert "is not a statement id" in err
    assert "Traceback" not in err


def test_forget_with_an_ambiguous_prefix_lists_the_candidates(ledger, git_free_tmp, capsys) -> None:
    paths, conn = ledger
    plant_bare_statement(conn, TWIN_A)
    plant_bare_statement(conn, TWIN_B)
    conn.close()

    assert cli(git_free_tmp, "abcdef01") == 2

    err = capsys.readouterr().err
    assert TWIN_A in err and TWIN_B in err
    assert "Give more of the id" in err
    assert "Traceback" not in err


def test_the_plan_prints_the_numbers_of_a_check_that_would_fail(
    ledger, git_free_tmp, capsys
) -> None:
    """docs/STATUS.md §5.45: a check whose job is to show a number, showing none.

    ``_detail_lines`` walks both shapes a check's ``detail`` can take, and the
    plan has to use it. "2 printed balance(s) disagree" is a fact nobody can act
    on; which days, and by how much, is the report.
    """
    paths, conn = ledger
    _, feb, _ = three_months(conn, paths)
    conn.close()

    assert cli(git_free_tmp, feb.sha256[:12]) == 2

    out = capsys.readouterr().out
    assert "would" in out and "not pass afterwards" in out
    assert "FAIL [block] balance_assertions" in out
    assert "as_of=2025-02-28" in out and "as_of=2025-03-31" in out
    assert "diff_minor=$18.75" in out, "rendered as money, not as a raw integer"
    assert "declared_minor=$700.00" in out


def test_the_plan_never_claims_a_pass_it_did_not_measure(ledger, git_free_tmp, capsys) -> None:
    """docs/STATUS.md §5.19 and discipline rule 11, applied to this command.

    Two claims are checked. On a deletion with consequences the summary line
    must not say anything passed; and on one without, the word is "measured",
    with the note naming the three checks that were not — verbatim, because a
    paraphrase of it is free to be stronger than it is.
    """
    paths, conn = ledger
    _, feb, mar = three_months(conn, paths)
    note = plan_forget(conn, paths, mar.sha256).checks_note
    conn.close()

    assert cli(git_free_tmp, feb.sha256[:12]) == 2
    red = capsys.readouterr().out
    assert "would still pass" not in red
    assert "1 of 6 measured check(s) would not pass afterwards" in red

    assert cli(git_free_tmp, mar.sha256[:12]) == 2
    green = capsys.readouterr().out
    assert "all 6 measured check(s) would still pass" in green
    assert note in green, "the note is shown verbatim, never paraphrased"
    for check_id in pipeline.ARCHIVE_CHECK_IDS:
        assert check_id in green


def test_the_plan_puts_hand_made_decisions_on_their_own_line(ledger, git_free_tmp, capsys) -> None:
    """Both kinds of decision, each on its own line, and silence when there are none.

    A category somebody set and a review item somebody resolved or dismissed are
    the two things ``archive/`` cannot give back: it holds documents, not what a
    person decided about them. This output named only the first and called it
    the only one, until an acceptance run dismissed an item, deleted the
    statement, re-ingested the identical bytes and watched the dismissal come
    back as ``open`` (docs/STATUS.md §5.65).
    """
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    conn.close()

    assert cli(git_free_tmp, jan.sha256[:12]) == 2
    without = capsys.readouterr().out
    assert "category override" not in without
    # And it says so, rather than leaving the line out. The 409 says it in both
    # directions and this is the same sentence to the same person at the same
    # moment; an absent line only answers the reader who knows the line exists.
    assert "destroys nothing irreversible" in without

    conn = open_ledger(paths.db)
    try:
        with transaction(conn):
            repo.set_category_override(conn, txn_id=jan.txn_ids[0], category_id="dining")
    finally:
        conn.close()

    assert cli(git_free_tmp, jan.sha256[:12]) == 2
    with_one = capsys.readouterr().out
    assert "1 category override(s)" in with_one
    assert "a category somebody set by hand" in with_one
    assert "restores the transactions, not these" in with_one
    assert "review item(s) somebody had already" not in with_one, "there are none yet"

    # Now the other kind. `three_months` books cleanly, so there is no queue item
    # to decide -- one is put there and then decided, which is the state a warn
    # that somebody looked at leaves behind. Constructed rather than skipped: a
    # test that skips when the fixture is tidy is a test that never runs.
    conn = open_ledger(paths.db)
    try:
        with transaction(conn):
            repo.replace_review_items(
                conn,
                source_file_id=jan.sha256,
                items=[
                    ReviewItem(
                        id=review_item_id(jan.sha256, "transaction_count", "warn"),
                        source_file_id=jan.sha256,
                        severity="warn",
                        check_id="transaction_count",
                        detail='{"message": "no count printed", "detail": {}}',
                    )
                ],
            )
            repo.set_review_status(
                conn,
                item_id=review_item_id(jan.sha256, "transaction_count", "warn"),
                status="dismissed",
                resolved_at="2026-02-01T00:00:00+00:00",
            )
    finally:
        conn.close()

    assert cli(git_free_tmp, jan.sha256[:12]) == 2
    with_both = capsys.readouterr().out
    assert "1 review item(s) somebody had already resolved or dismissed" in with_both


def test_the_plan_names_the_files_it_would_delete(ledger, git_free_tmp, capsys) -> None:
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    cache = paths.extracted / f"{jan.sha256}.ndjson"
    cache.write_text("{}\n", encoding="utf-8")
    conn.close()

    assert cli(git_free_tmp, jan.sha256[:12]) == 2

    out = capsys.readouterr().out
    assert str(cache) in out
    assert f"{jan.sha256}.pdf" in out
    assert cache.is_file(), "and still deleted nothing"


def test_a_refused_deletion_reaches_the_cli_with_its_reason(ledger, git_free_tmp, capsys) -> None:
    paths, conn = ledger
    first, second = overlapping_pair(conn, paths)
    before = repo.row_counts(conn)
    conn.close()

    # Even with --yes: a refusal is not a confirmation problem.
    assert cli(git_free_tmp, first.sha256[:12], "--yes") == 2

    out = capsys.readouterr().out
    assert "this deletion is refused" in out
    assert "overlapping period" in out
    assert second.sha256[:12] in out
    assert "nothing has been deleted" not in out, "it was refused, not merely not confirmed"

    conn = open_ledger(paths.db)
    try:
        assert repo.row_counts(conn) == before
    finally:
        conn.close()


def test_the_cli_forgets_a_refused_statement_and_exits_zero(ledger, git_free_tmp, capsys) -> None:
    """The product owner's case, from the command line, all the way to exit 0."""
    paths, conn = ledger
    sha = archive_something_unreadable(paths, conn)
    conn.close()

    assert main(["--data-dir", str(git_free_tmp / "data"), "verify"]) == 2
    capsys.readouterr()

    assert cli(git_free_tmp, sha[:12], "--yes") == 0
    out = capsys.readouterr().out
    assert "1 review item(s)" in out
    assert "month unknown" in out, "it was never parsed, so it has no month to name"

    assert main(["--data-dir", str(git_free_tmp / "data"), "verify"]) == 0


def test_a_file_that_could_not_be_deleted_is_named_on_stderr(
    ledger, git_free_tmp, capsys
) -> None:
    """The one claim in this output a person has to act on personally.

    The rows are gone and the bytes are not. Saying "deleted" for a file still
    on disk, or saying nothing, both leave them unable to clear it.

    The follow-up sentence names ``ledgerbox doctor`` and this test asserts that
    rather than the check id it used to name. ``archived_not_recorded`` is right
    *here* — the leftover is an archived PDF — and wrong for the other leftover
    this command can produce, and an instruction that holds for one of the two
    cases is the shape docs/STATUS.md §5.62 is about. ``doctor`` reports both.
    The failing check is still named in the check list on stdout, which is where
    a check id belongs.
    """
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    archived = archive.find_archived(paths, jan.sha256)
    assert archived is not None
    skip_unless_an_open_file_cannot_be_unlinked(paths)
    conn.close()

    handle = archived.open("rb")
    try:
        assert cli(git_free_tmp, jan.sha256[:12], "--yes") == 2
    finally:
        handle.close()

    captured = capsys.readouterr()
    assert "COULD NOT DELETE" in captured.err
    assert str(archived) in captured.err
    assert "doctor" in captured.err, "the one command that reports both kinds of leftover"
    assert "archived_not_recorded" not in captured.err, (
        "naming that check here would be right for this leftover and wrong for a "
        "stranded extraction cache, which verify never looks at (§5.62)"
    )
    assert f"deleted {archived}" not in captured.out, "it was not deleted, so do not say so"
    assert "archived_not_recorded" in captured.out, "the check list still names what failed"

    archived.chmod(0o600)
    archived.unlink()


def test_a_stranded_cache_makes_the_command_fail_even_though_verify_passes(
    ledger, git_free_tmp, capsys
) -> None:
    """The exit code has to cover the leak `verify` deliberately cannot see.

    The nine checks do not look in ``extracted/`` on purpose, so a deletion that
    left the entire text layer on disk passed all nine and this command exited
    **0** with the warning printed underneath it. ``cmd_doctor`` in the same file
    argues that a line printed under a zero exit code is a line nobody reads;
    that applies here first, and to a cron job it is the only part read at all.

    The archived-PDF half of this already exited 2, because ``verify`` goes red
    on it. The two leaks are the same leak and now have the same exit code.
    """
    paths, conn = ledger
    jan, _, _ = three_months(conn, paths)
    cache = paths.extracted / f"{jan.sha256}.ndjson"
    cache.write_text('{"page": 1, "spans": []}\n', encoding="utf-8")
    skip_unless_an_open_file_cannot_be_unlinked(paths)
    conn.close()

    handle = cache.open("rb")
    try:
        code = cli(git_free_tmp, jan.sha256[:12], "--yes")
    finally:
        handle.close()

    captured = capsys.readouterr()
    assert "all 9 check(s) pass." in captured.out, "verify really is green; that is the premise"
    assert code == 2, "and the command still failed, because a file it meant to remove is there"
    assert "COULD NOT DELETE" in captured.err
    assert cache.exists()

    cache.unlink()


def test_a_deletion_that_leaves_a_failing_check_exits_two(ledger, git_free_tmp, capsys) -> None:
    """Deleting the middle month is allowed, happens, and still exits 2.

    The exit code follows the ledger, not the command: the deletion succeeded
    and the ledger has a hole in it, and cron has to hear about the second one.
    """
    paths, conn = ledger
    _, feb, _ = three_months(conn, paths)
    conn.close()

    assert cli(git_free_tmp, feb.sha256[:12], "--yes") == 2

    out = capsys.readouterr().out
    assert "forgot" in out
    assert "1 of 9 check(s) do not pass" in out
    assert "all 9 check(s) pass." not in out

    conn = open_ledger(paths.db)
    try:
        assert repo.row_counts(conn)["source_file"] == 2, "it really was deleted"
        assert failing(conn, paths) == ["balance_assertions"]
    finally:
        conn.close()
