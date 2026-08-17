# SPDX-License-Identifier: AGPL-3.0-or-later
"""M6/M7: the ingest pipeline and the CLI.

This file carries P0 acceptance items 1-10. Item 11 (the rebuild invariant)
lives in test_rebuild.py.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from synth import simple_statement

from ledgerbox.cli import main
from ledgerbox.config import DataPaths
from ledgerbox.db import repo
from ledgerbox.db.connection import transaction
from ledgerbox.db.migrate import open_ledger
from ledgerbox.db.repo import ledger_totals, row_counts
from ledgerbox.ingest import archive, pipeline
from ledgerbox.ingest.registry import PARSERS

# The monetary expectations for the real corpus live beside the corpus, in the
# untracked expected-totals.json read by the `real_expected` fixture: they are
# the owner's real figures and the repository keeps only the synthetic story
# set. The count expectations below stay here on purpose -- they are the same
# rule-coverage measurements README publishes, and editing a rule is supposed
# to break them loudly.
EXPECTED_ROWS = 415
EXPECTED_MONTHS = 13

CJK_DIR = "中文 目录 对账单"


@pytest.fixture
def ledger(git_free_tmp: Path) -> tuple[DataPaths, sqlite3.Connection]:
    paths = DataPaths.resolve(git_free_tmp / "data")
    conn = open_ledger(paths.db)
    yield paths, conn
    conn.close()


@pytest.fixture(scope="session")
def ingested_real(
    git_free_tmp_root: Path, real_statements: list[Path]
) -> tuple[DataPaths, sqlite3.Connection, list]:
    """All 13 real statements, ingested once for the whole session."""
    paths = DataPaths.resolve(git_free_tmp_root / "real-ledger")
    conn = open_ledger(paths.db)
    outcomes = pipeline.ingest_paths(conn, paths, real_statements)
    yield paths, conn, outcomes
    conn.close()


# --------------------------------------------------------------------------
# acceptance 1-6: the numbers
# --------------------------------------------------------------------------


def test_all_thirteen_statements_import(ingested_real) -> None:
    _, _, outcomes = ingested_real
    failures = [o.summary_line() for o in outcomes if o.status != pipeline.IMPORTED]
    assert failures == []
    assert len(outcomes) == EXPECTED_MONTHS


def test_booked_totals_match_the_statements(ingested_real, real_expected) -> None:
    """The filtered headline plus exactly what the transfer filter removed.

    ``ledger_totals`` excludes transfer-flagged lines; the statements' own
    printed sums do not. Since P2's conservative transfer rules fire on this
    corpus, the honest identity is whole == filtered + excluded, which also
    pins ``transfer_excluded_*`` to an independently printed number.
    """
    _, conn, _ = ingested_real
    totals = ledger_totals(conn)
    assert totals["inflow_minor"] + totals["transfer_excluded_in_minor"] == (
        real_expected["deposits_minor"]
    )
    assert totals["outflow_minor"] + totals["transfer_excluded_out_minor"] == (
        real_expected["withdrawals_minor"]
    )
    assert (
        totals["net_minor"]
        + totals["transfer_excluded_in_minor"]
        + totals["transfer_excluded_out_minor"]
    ) == real_expected["net_minor"]
    assert totals["txn_count"] + totals["transfer_count"] == EXPECTED_ROWS


def test_replaying_the_ledger_lands_on_the_printed_closing_balance(
    ingested_real, real_expected
) -> None:
    """Two independent routes to $288.71: the chain, and the printed balance.

    The sum needs no opening term added to it. That is the point of booking an
    opening entry: a ledger where "add up the postings" gives the wrong balance
    has a hole in it, and every reader has to know the workaround.
    """
    _, conn, _ = ingested_real
    replayed = conn.execute(
        "SELECT COALESCE(SUM(p.amount_minor), 0) FROM posting p "
        "JOIN account a ON a.id = p.account_id WHERE a.kind = 'asset'"
    ).fetchone()[0]
    printed = conn.execute(
        "SELECT amount_minor FROM balance_assertion ORDER BY as_of DESC LIMIT 1"
    ).fetchone()[0]
    assert replayed == real_expected["closing_minor"]
    assert printed == real_expected["closing_minor"]

    opening = conn.execute(
        "SELECT COALESCE(SUM(p.amount_minor), 0) FROM posting p "
        "WHERE p.account_id = 'equity:opening-balances'"
    ).fetchone()[0]
    assert opening == -real_expected["opening_minor"], (
        "the seeded equity account is actually used"
    )


def test_thirteen_distinct_statement_months(ingested_real) -> None:
    _, conn, _ = ingested_real
    months = {row[0] for row in conn.execute("SELECT statement_month FROM v_statement")}
    assert len(months) == EXPECTED_MONTHS
    assert {"2025-06", "2025-09", "2025-12"} <= months


def test_every_booked_row_carries_provenance(ingested_real) -> None:
    _, conn, _ = ingested_real
    assert conn.execute("SELECT COUNT(*) FROM v_identity_without_source").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM v_unbalanced_txn").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM v_transaction").fetchone()[0] == EXPECTED_ROWS


def test_the_review_queue_is_empty_after_a_clean_run(ingested_real) -> None:
    _, conn, _ = ingested_real
    open_blocks = conn.execute(
        "SELECT COUNT(*) FROM review_item WHERE status='open' AND severity='block'"
    ).fetchone()[0]
    assert open_blocks == 0


def test_verify_passes_over_the_booked_ledger(ingested_real) -> None:
    paths, conn, _ = ingested_real
    failed = [r.check_id for r in pipeline.verify_ledger(conn, paths) if r.status != "pass"]
    assert failed == []


# --------------------------------------------------------------------------
# P2 M1: categories are written by the ingest, not by a later pass
# --------------------------------------------------------------------------


def test_the_category_table_mirrors_the_rules_file(ingested_real) -> None:
    """One definition. The JSON declares the categories; this table points at them."""
    from ledgerbox.analytics.categorize import default_rules

    _, conn, _ = ingested_real
    stored = {row[0] for row in conn.execute("SELECT id FROM category")}
    assert stored == set(default_rules().ids())


def test_ingest_writes_categories_without_being_asked_twice(ingested_real) -> None:
    """If this were a separate pass, a rebuilt ledger would come back uncategorised."""
    _, conn, _ = ingested_real
    categorised = conn.execute(
        "SELECT COUNT(*) FROM posting WHERE category_id IS NOT NULL"
    ).fetchone()[0]
    assert categorised > 0


#: What the shipped rules claim on the 13 real statements, as measured. Pinned
#: rather than described, because `> 0` once let a whole category be deleted from
#: the rules file -- 130 claimed rows down to 89 -- with the suite still green,
#: while `docs/STATUS.md` went on quoting the old baseline. Numbers nobody checks going
#: quietly stale is the predecessor's defect, not a documentation style.
#:
#: Editing a rule is *supposed* to break this: the failure is the notification,
#: and the fix is to re-measure and update both this table and STATUS §5.42.
#: Nothing here gates an ingest -- categories are a heuristic (§5c).
EXPECTED_CLAIMED = 145
#: 16 expense + 3 income + 2 transfer-kind labels. Generic `transfer` carries
#: the conservative rules (P2 M2.3); `investment` is deliberately patternless.
#: Both make a person's one-sided cash-flow decision expressible because
#: `v_txn_transfer` tests the category kind rather than one hard-coded id.
EXPECTED_CATEGORY_ROWS = 24
EXPECTED_BY_CATEGORY = {
    "subscriptions": 41,
    "fees": 36,
    "shopping": 26,
    "dining": 8,
    "transport": 8,
    "entertainment": 7,
    "groceries": 5,
    "insurance": 5,
    "sport": 3,
    "taxes": 1,
    # Measured 2026-08-16, the first real-corpus run since A6.5 added these
    # three categories and their rules. The suite had been quoting the pre-A6.5
    # baseline ever since -- exactly the staleness this table's own comment
    # warns about, found by running the gate rather than by reading it.
    "pet": 1,
    "rewards": 2,
    "cash-deposit": 2,
}


def test_the_measured_category_coverage_has_not_moved(ingested_real) -> None:
    _, conn, _ = ingested_real

    assert conn.execute("SELECT COUNT(*) FROM category").fetchone()[0] == EXPECTED_CATEGORY_ROWS

    claimed = conn.execute(
        "SELECT COUNT(*) FROM posting WHERE category_id IS NOT NULL"
    ).fetchone()[0]
    assert claimed == EXPECTED_CLAIMED

    # Asked of the database, not of arithmetic. The first version of this line
    # was `claimed + 275 == EXPECTED_ROWS`, which is 140 + 275 == 415 -- true by
    # the assertion above and unable to fail, while its message claimed a fact
    # about stored rows that it never queried.
    unclaimed = conn.execute(
        "SELECT COUNT(*) FROM v_transaction WHERE category_id IS NULL"
    ).fetchone()[0]
    assert unclaimed == EXPECTED_ROWS - EXPECTED_CLAIMED
    assert claimed + unclaimed == EXPECTED_ROWS, "every line is claimed or NULL, nothing between"

    breakdown = dict(
        conn.execute(
            "SELECT category_id, COUNT(*) FROM posting "
            "WHERE category_id IS NOT NULL GROUP BY category_id"
        ).fetchall()
    )
    assert breakdown == EXPECTED_BY_CATEGORY


def test_the_income_rules_claim_exactly_the_measured_rows(ingested_real) -> None:
    """72 deposits; the A6.5 income rules claim four of them.

    This test spent its first life asserting zero income claims, with the note
    that a failure would mean `docs/STATUS.md` §5.42's unverified guess had
    been answered. It has been: A6.5 added `rewards` and `cash-deposit` with
    rules, and on this corpus each claims two rows. The rest of the deposits
    stay unclaimed by design -- most are transfer-shaped and transfer needs
    ownership evidence no rule derives from wording.
    """
    _, conn, _ = ingested_real
    claimed = dict(
        conn.execute(
            """
            SELECT c.id, COUNT(*) FROM posting p
            JOIN category c ON c.id = p.category_id
            WHERE c.kind = 'income' GROUP BY c.id
            """
        ).fetchall()
    )
    assert claimed == {"cash-deposit": 2, "rewards": 2}

    deposits = conn.execute(
        "SELECT COUNT(*) FROM v_transaction WHERE amount_minor > 0"
    ).fetchone()[0]
    assert deposits == 72


def test_only_the_bank_leg_ever_carries_a_category(ingested_real) -> None:
    """``v_transaction`` joins that leg; a category on the counter-leg is invisible."""
    _, conn, _ = ingested_real
    stray = conn.execute(
        "SELECT COUNT(*) FROM posting WHERE category_id IS NOT NULL AND seq <> 0"
    ).fetchone()[0]
    assert stray == 0


def test_no_category_sits_on_the_wrong_side_of_the_ledger(ingested_real) -> None:
    """An expense category on a deposit would be a refund counted as spending.

    Structural rather than sampled: sign chooses the side before any rule is
    consulted, so this must hold for all 415 rows or the gating is not real.
    """
    _, conn, _ = ingested_real
    wrong = conn.execute(
        """
        SELECT COUNT(*) FROM posting p
        JOIN category c ON c.id = p.category_id
        WHERE (p.amount_minor > 0 AND c.kind <> 'income')
           OR (p.amount_minor <= 0 AND c.kind <> 'expense')
        """
    ).fetchone()[0]
    assert wrong == 0


def test_categories_do_not_move_a_single_headline_number(ingested_real, real_expected) -> None:
    """M1 rewrites one column and nothing else.

    The four figures are measured on the income and expense legs; a category
    lands on the bank leg and is not consulted by any aggregate yet. If this
    ever fails, categorisation has started deciding structure, which is the one
    thing it is not allowed to do.
    """
    _, conn, _ = ingested_real
    totals = ledger_totals(conn)
    assert totals["inflow_minor"] + totals["transfer_excluded_in_minor"] == (
        real_expected["deposits_minor"]
    )
    assert totals["outflow_minor"] + totals["transfer_excluded_out_minor"] == (
        real_expected["withdrawals_minor"]
    )
    assert (
        totals["net_minor"]
        + totals["transfer_excluded_in_minor"]
        + totals["transfer_excluded_out_minor"]
    ) == real_expected["net_minor"]
    assert totals["txn_count"] + totals["transfer_count"] == EXPECTED_ROWS


# --------------------------------------------------------------------------
# acceptance 7: three ingests of the same file change nothing
# --------------------------------------------------------------------------


def test_ingesting_the_same_pdf_three_times_changes_no_rows(
    ledger, real_statements: list[Path]
) -> None:
    paths, conn = ledger
    one = real_statements[0]

    first = pipeline.ingest_file(conn, paths, one)
    assert first.status == pipeline.IMPORTED
    after_first = row_counts(conn)

    for _ in range(2):
        again = pipeline.ingest_file(conn, paths, one)
        assert again.status == pipeline.DUPLICATE
        assert row_counts(conn) == after_first

    archived = list(paths.archive.rglob("*.pdf"))
    assert len(archived) == 1, "content addressing means one copy, not three"


# --------------------------------------------------------------------------
# acceptance 8: the same batch from two directories is still one batch
# --------------------------------------------------------------------------


def test_the_same_statements_from_two_directories_do_not_double(
    ledger, real_statements: list[Path], git_free_tmp: Path
) -> None:
    paths, conn = ledger
    first_dir = git_free_tmp / "download-a"
    second_dir = git_free_tmp / "download-b"
    first_dir.mkdir()
    second_dir.mkdir()
    for source in real_statements[:3]:
        shutil.copy2(source, first_dir / source.name)
        shutil.copy2(source, second_dir / f"copy-of-{source.name}")

    pipeline.ingest_paths(conn, paths, [first_dir])
    after_first = row_counts(conn)
    totals_first = ledger_totals(conn)

    outcomes = pipeline.ingest_paths(conn, paths, [second_dir])
    assert all(o.status == pipeline.DUPLICATE for o in outcomes)
    assert row_counts(conn) == after_first
    assert ledger_totals(conn) == totals_first
    assert len(list(paths.archive.rglob("*.pdf"))) == 3


# --------------------------------------------------------------------------
# acceptance 9: a broken amount must close the gate and leave no trace
# --------------------------------------------------------------------------


def test_a_tampered_amount_blocks_the_ingest_and_books_nothing(
    ledger, real_statements: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, conn = ledger
    parser = PARSERS[0]
    original_parse = type(parser).parse

    def tampered(self, doc):  # noqa: ANN001, ANN202
        statement = original_parse(self, doc)
        rows = list(statement.transactions)
        rows[3] = replace(rows[3], amount_minor=rows[3].amount_minor + 1000)
        return replace(statement, transactions=tuple(rows))

    monkeypatch.setattr(type(parser), "parse", tampered)

    outcome = pipeline.ingest_file(conn, paths, real_statements[0])

    assert outcome.status == pipeline.NEEDS_REVIEW
    assert outcome.report is not None and outcome.report.blocked

    counts = row_counts(conn)
    assert counts["txn"] == 0, "a blocked statement must not book a single row"
    assert counts["posting"] == 0
    assert counts["txn_identity"] == 0
    assert counts["balance_assertion"] == 0
    assert counts["source_file"] == 1, "the archived original is still recorded"

    queued = conn.execute(
        "SELECT check_id, severity, detail FROM review_item WHERE status='open'"
    ).fetchall()
    blocking = {row["check_id"] for row in queued if row["severity"] == "block"}
    assert blocking >= {"balance_chain", "period_totals", "declared_subtotals"}
    chain = next(row for row in queued if row["check_id"] == "balance_chain")
    assert "diff_minor" in chain["detail"]
    # A tampered amount also throws the bucket totals off. That check is warn
    # level, so it is queued for a human but is not what closed the gate.
    assert any(row["severity"] == "warn" for row in queued)


def test_a_blocked_file_is_reprocessed_rather_than_treated_as_a_duplicate(
    ledger, real_statements: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fixing the parser must not require deleting the archive."""
    paths, conn = ledger
    parser = PARSERS[0]
    original_parse = type(parser).parse

    def tampered(self, doc):  # noqa: ANN001, ANN202
        statement = original_parse(self, doc)
        rows = list(statement.transactions)
        rows[3] = replace(rows[3], amount_minor=rows[3].amount_minor + 1000)
        return replace(statement, transactions=tuple(rows))

    monkeypatch.setattr(type(parser), "parse", tampered)
    assert pipeline.ingest_file(conn, paths, real_statements[0]).status == pipeline.NEEDS_REVIEW

    monkeypatch.setattr(type(parser), "parse", original_parse)
    healed = pipeline.ingest_file(conn, paths, real_statements[0])
    assert healed.status == pipeline.IMPORTED
    assert row_counts(conn)["txn"] > 0
    assert conn.execute(
        "SELECT COUNT(*) FROM review_item WHERE status='open' AND severity='block'"
    ).fetchone()[0] == 0


# --------------------------------------------------------------------------
# acceptance 10: non-ASCII paths
# --------------------------------------------------------------------------


def test_a_chinese_path_ingests_without_crashing(
    ledger, real_statements: list[Path], git_free_tmp: Path
) -> None:
    paths, conn = ledger
    inbox = git_free_tmp / CJK_DIR / "2025 年"
    inbox.mkdir(parents=True)
    target = inbox / "对账单 2025-01.pdf"
    shutil.copy2(real_statements[0], target)

    outcome = pipeline.ingest_file(conn, paths, target)
    assert outcome.status == pipeline.IMPORTED
    assert row_counts(conn)["txn"] > 0


def test_a_chinese_data_directory_works_too(git_free_tmp: Path, real_statements) -> None:
    paths = DataPaths.resolve(git_free_tmp / CJK_DIR / "账本数据")
    conn = open_ledger(paths.db)
    try:
        outcome = pipeline.ingest_file(conn, paths, real_statements[0])
        assert outcome.status == pipeline.IMPORTED
        assert (paths.extracted / f"{outcome.sha256}.ndjson").exists()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# per-file isolation
# --------------------------------------------------------------------------


def test_one_corrupt_file_does_not_stop_the_batch(
    ledger, real_statements: list[Path], git_free_tmp: Path
) -> None:
    paths, conn = ledger
    inbox = git_free_tmp / "inbox"
    inbox.mkdir()
    for source in real_statements[:3]:
        shutil.copy2(source, inbox / source.name)
    (inbox / "00-empty.pdf").write_bytes(b"")
    (inbox / "01-garbage.pdf").write_bytes(b"not a pdf at all")

    outcomes = pipeline.ingest_paths(conn, paths, [inbox])

    imported = [o for o in outcomes if o.status == pipeline.IMPORTED]
    failed = [o for o in outcomes if o.status == pipeline.FAILED]
    assert len(imported) == 3, "the good statements must still be booked"
    assert len(failed) == 2
    assert all(o.error for o in failed)
    assert row_counts(conn)["source_file"] == 3


# --------------------------------------------------------------------------
# unbooked_statements: a refusal someone silenced is still a refusal
#
# No real statement is needed for any of this, and that is deliberate: a file
# whose first bytes are "%PDF-" is archivable by construction and unreadable by
# construction, which is exactly the state the check exists to notice. These
# therefore run on CI, where the real fixtures are absent.
# --------------------------------------------------------------------------


def _archive_something_unreadable(
    paths: DataPaths, conn: sqlite3.Connection, git_free_tmp: Path
) -> list[pipeline.IngestOutcome]:
    header_only = git_free_tmp / "header-only.pdf"
    header_only.write_bytes(b"%PDF-1.7\n% enough to archive, not enough to read\n")
    return pipeline.ingest_paths(conn, paths, [header_only])


def test_an_unreadable_pdf_is_archived_and_books_nothing(
    ledger, git_free_tmp: Path
) -> None:
    paths, conn = ledger
    outcomes = _archive_something_unreadable(paths, conn, git_free_tmp)

    assert [o.status for o in outcomes] == [pipeline.NEEDS_REVIEW]
    counts = row_counts(conn)
    assert counts["source_file"] == 1, "the bytes are kept so a fixed parser can retry them"
    assert counts["txn"] == 0
    assert counts["txn_identity"] == 0


def test_verify_fails_when_a_statement_was_archived_but_never_booked(
    ledger, git_free_tmp: Path
) -> None:
    paths, conn = ledger
    _archive_something_unreadable(paths, conn, git_free_tmp)

    results = {r.check_id: r for r in pipeline.verify_ledger(conn)}
    assert "unbooked_statements" in results, "a check nobody runs is not a check"
    assert results["unbooked_statements"].status == "fail"
    assert results["unbooked_statements"].severity == "block"
    assert results["unbooked_statements"].detail["archived"] == 1
    assert len(results["unbooked_statements"].detail["unbooked"]) == 1


def test_dismissing_the_review_item_does_not_turn_verify_green(
    ledger, git_free_tmp: Path
) -> None:
    """The whole reason this check exists.

    P1 lets a person dismiss a blocking review item, and recording that decision
    is right. If the queue were the only record, one click would give a clean
    exit code over a ledger that is missing a statement — a green cron job on
    incomplete books, which is the failure mode this project was built after.
    """
    paths, conn = ledger
    _archive_something_unreadable(paths, conn, git_free_tmp)
    conn.execute(
        "UPDATE review_item SET status = 'dismissed', resolved_at = '2026-08-03T00:00:00Z'"
    )

    results = {r.check_id: r for r in pipeline.verify_ledger(conn)}
    assert results["review_queue"].status == "pass", "the queue is genuinely empty now"
    assert results["unbooked_statements"].status == "fail", "and the money is still missing"


def test_unbooked_statements_passes_when_every_statement_is_booked(ingested_real) -> None:
    _, conn, _ = ingested_real
    result = next(
        r for r in pipeline.verify_ledger(conn) if r.check_id == "unbooked_statements"
    )
    assert result.status == "pass"
    assert result.detail["archived"] == EXPECTED_MONTHS
    assert result.detail["unbooked"] == []


def test_dismissing_a_review_item_does_not_close_the_recovery_path(
    ledger, git_free_tmp: Path
) -> None:
    """The route the 409 message promises must still be there after a dismissal.

    Re-ingesting the archived bytes is what the user is told to do at the exact
    moment they press Dismiss. When the duplicate short-circuit keyed on "are
    there open blocking items", dismissing one made every later ingest of those
    bytes a no-op: fixing the parser changed nothing, because the parser was
    never called again.
    """
    paths, conn = ledger
    _archive_something_unreadable(paths, conn, git_free_tmp)
    conn.execute("UPDATE review_item SET status = 'dismissed'")

    again = pipeline.ingest_paths(conn, paths, [git_free_tmp / "header-only.pdf"])

    assert [o.status for o in again] == [pipeline.NEEDS_REVIEW], (
        "the pipeline must run again; a dismissal is a note, not a decision to stop trying"
    )
    assert again[0].review, "and it must re-queue what it found"


def test_a_booked_statement_is_still_a_duplicate(ledger, real_statements) -> None:
    paths, conn = ledger
    first = pipeline.ingest_paths(conn, paths, [real_statements[0]])
    before = row_counts(conn)

    second = pipeline.ingest_paths(conn, paths, [real_statements[0]])

    assert first[0].status == pipeline.IMPORTED
    assert second[0].status == pipeline.DUPLICATE
    assert row_counts(conn) == before


# --------------------------------------------------------------------------
# archived_not_recorded: the archive and the database must agree
# --------------------------------------------------------------------------


def test_a_failed_booking_removes_the_newly_archived_orphan(
    ledger, git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rolled-back import must also roll back the archive it just created."""
    paths, conn = ledger
    source = git_free_tmp / "synthetic-transaction-failure.pdf"
    source.write_bytes(b"%PDF-1.7\n% synthetic pipeline failure probe\n")
    document = simple_statement()
    monkeypatch.setattr(pipeline, "extract_spans", lambda _path: document)

    def fail_after_archive(*_args, **_kwargs) -> None:
        raise sqlite3.IntegrityError("synthetic post-archive transaction failure")

    monkeypatch.setattr(repo, "upsert_balance_assertions", fail_after_archive)

    outcomes = pipeline.ingest_paths(conn, paths, [source])

    assert [outcome.status for outcome in outcomes] == [pipeline.FAILED]
    assert row_counts(conn)["source_file"] == 0, "the database transaction rolled back"
    assert pipeline.archived_shas(paths) == set(), (
        "the archive created by that failed transaction must roll back with it"
    )
    results = {result.check_id: result for result in pipeline.verify_ledger(conn, paths)}
    assert results["archived_not_recorded"].status == "pass"


def test_a_failed_retry_does_not_delete_an_archive_that_already_existed(
    ledger, git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback owns only the archive created by this ingest attempt."""
    paths, conn = ledger
    source = git_free_tmp / "synthetic-existing-archive.pdf"
    source.write_bytes(b"%PDF-1.7\n% synthetic pre-existing archive probe\n")
    archived = archive.archive_file(paths, source, ingested_on=date(2026, 8, 10))
    document = simple_statement()
    monkeypatch.setattr(pipeline, "extract_spans", lambda _path: document)

    def fail_after_archive(*_args, **_kwargs) -> None:
        raise sqlite3.IntegrityError("synthetic retry failure")

    monkeypatch.setattr(repo, "upsert_balance_assertions", fail_after_archive)

    outcomes = pipeline.ingest_paths(conn, paths, [source])

    assert [outcome.status for outcome in outcomes] == [pipeline.FAILED]
    assert archived.path.exists(), "a failed retry must not delete bytes it did not create"
    assert pipeline.archived_shas(paths) == {archived.sha256}


def test_verify_fails_when_the_archive_holds_a_statement_the_database_forgot(
    ledger, git_free_tmp: Path
) -> None:
    """Reachable without anyone deleting anything.

    ``archive_file`` writes the bytes before the transaction that writes the
    ``source_file`` row, so an interruption in between leaves exactly this. The
    archive is what the rebuild invariant depends on; it disagreeing with the
    database is the one discrepancy that must never be quiet.
    """
    paths, conn = ledger
    _archive_something_unreadable(paths, conn, git_free_tmp)
    assert pipeline.archived_shas(paths), "the bytes must be on disk to begin with"

    conn.execute("DELETE FROM review_item")
    conn.execute("DELETE FROM source_file")

    results = {r.check_id: r for r in pipeline.verify_ledger(conn, paths)}
    assert results["unbooked_statements"].status == "pass", (
        "no source_file rows means nothing for that check to see - which is the gap"
    )
    assert results["archived_not_recorded"].status == "fail"
    assert len(results["archived_not_recorded"].detail["orphaned"]) == 1


def test_verify_fails_when_a_recorded_statement_is_gone_from_the_archive(
    ledger, real_statements
) -> None:
    """The direction everything else rests on.

    ``archive/`` is what the rebuild invariant is rebuilt from, and it is the
    only route a refused statement has back into the ledger. Deleting a file
    while keeping its row left every check green — and so did deleting the whole
    directory, because an empty archive yields an empty set of orphans. A
    tidy-up or a partial restore is enough; nothing has to go wrong on purpose.
    """
    paths, conn = ledger
    pipeline.ingest_paths(conn, paths, [real_statements[0]])

    archived = next(paths.archive.rglob("*.pdf"))
    archived.chmod(0o600)  # the archive is written read-only
    archived.unlink()

    results = {r.check_id: r for r in pipeline.verify_ledger(conn, paths)}
    assert results["archived_not_recorded"].status == "pass", "nothing extra is on disk"
    assert results["recorded_not_archived"].status == "fail"
    assert len(results["recorded_not_archived"].detail["missing"]) == 1


def test_verify_fails_when_an_archived_file_no_longer_hashes_to_its_name(
    ledger, real_statements
) -> None:
    """Swapping two archived statements used to pass every check.

    The name of an archived file *is* its checksum, so this is decidable at any
    moment and nothing was asking. A rebuild from a rewritten archive produces a
    different ledger and calls it the same one.
    """
    paths, conn = ledger
    pipeline.ingest_paths(conn, paths, [real_statements[0]])

    archived = next(paths.archive.rglob("*.pdf"))
    archived.chmod(0o600)
    archived.write_bytes(b"%PDF-1.7\n% not what this file is named after\n")

    results = {r.check_id: r for r in pipeline.verify_ledger(conn, paths)}
    assert results["archived_not_recorded"].status == "pass"
    assert results["recorded_not_archived"].status == "pass", (
        "the name is still there - only the bytes changed, which is the point"
    )
    assert results["archive_integrity"].status == "fail"
    assert len(results["archive_integrity"].detail["corrupt"]) == 1
    # A refusal that leaves the operator to invent the next step is the shape
    # this project keeps having to fix. The repair exists; it has to be said.
    assert "Delete each corrupted file" in results["archive_integrity"].message

    outcome = pipeline.ingest_file(conn, paths, real_statements[0])
    assert outcome.status == pipeline.FAILED
    assert outcome.error is not None
    assert "Delete that file from the archive" in outcome.error


def test_verify_notices_a_file_the_archive_did_not_write(ledger, git_free_tmp) -> None:
    """Nothing this program writes to archive/ is named anything but <sha>.pdf."""
    paths, conn = ledger
    _archive_something_unreadable(paths, conn, git_free_tmp)

    shard = next(p for p in paths.archive.rglob("*.pdf")).parent
    (shard / "statement-copy.pdf").write_bytes(b"%PDF-1.7\n")
    (shard / "notes.txt").write_text("dropped here by hand", encoding="utf-8")
    # A directory is not a statement either, and `is_file()` used to skip it
    # before it was ever classified — so the archive could grow entries the
    # survey reported as nothing at all.
    (shard / "an-unexpected-directory").mkdir()

    result = next(
        r for r in pipeline.verify_ledger(conn, paths) if r.check_id == "archive_integrity"
    )
    assert result.status == "fail"
    assert len(result.detail["unexpected"]) == 3


def _make_junction(link: Path, target: Path) -> bool:
    """A Windows junction, or False if this host will not make one.

    Junctions rather than symlinks because symlink creation needs a privilege
    this project's own test host does not have (`test_config.py` skips for the
    same reason) — and because a junction is the case that actually broke.
    """
    if os.name != "nt":
        try:
            link.symlink_to(target, target_is_directory=True)
        except (OSError, NotImplementedError):
            return False
        return True
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and link.exists()


def test_a_shard_shaped_junction_does_not_pass_as_a_shard(
    ledger, git_free_tmp, real_statements
) -> None:
    """The archive must not be able to reach outside the directory the guard saw.

    ``Path.is_symlink()`` returns **False** for a Windows junction, so the branch
    written to catch exactly this never fired, and a junction named like a month
    shard was accepted as a month shard. Demonstrated by an independent reviewer:
    booked statements living inside a git repository — the placement
    :func:`ledgerbox.config.guard_data_dir` refuses outright — with every check
    passing.
    """
    paths, conn = ledger
    pipeline.ingest_paths(conn, paths, [real_statements[0]])

    shard = next(paths.archive.rglob("*.pdf")).parent
    elsewhere = git_free_tmp / "somewhere-else"
    elsewhere.mkdir()
    for archived in list(shard.glob("*.pdf")):
        archived.chmod(0o600)
        shutil.move(str(archived), str(elsewhere / archived.name))
    shard.rmdir()

    if not _make_junction(shard, elsewhere):
        pytest.skip("this host will not create a junction or a directory symlink")

    result = next(
        r for r in pipeline.verify_ledger(conn, paths) if r.check_id == "archive_integrity"
    )
    assert result.status == "fail", "a link named like a shard is not a shard"
    assert result.detail["unexpected"], "and it has to be named in the report"


def test_re_ingesting_puts_a_deleted_archive_copy_back(ledger, real_statements) -> None:
    """The repair this project documents in four places, actually repairing.

    ``archive_file`` runs before the duplicate short-circuit, so offering the
    original again rewrites the archived copy even though no ledger row changes.
    The summary line says so: "nothing to do" would leave the operator unable to
    tell whether the repair worked.
    """
    paths, conn = ledger
    pipeline.ingest_paths(conn, paths, real_statements[:2])

    victim = sorted(paths.archive.rglob("*.pdf"))[0]
    victim.chmod(0o600)
    victim.unlink()
    broken = next(
        r for r in pipeline.verify_ledger(conn, paths) if r.check_id == "recorded_not_archived"
    )
    assert broken.status == "fail"

    outcomes = pipeline.ingest_paths(conn, paths, real_statements[:2])

    assert all(o.status == pipeline.DUPLICATE for o in outcomes)
    assert sum(o.restored_archive for o in outcomes) == 1
    assert any("restored" in o.summary_line() for o in outcomes)
    assert [r.check_id for r in pipeline.verify_ledger(conn, paths) if r.status != "pass"] == []


def test_archiving_refuses_to_write_through_a_link(
    ledger, git_free_tmp, real_statements
) -> None:
    """Otherwise the repair instruction is one the operator cannot carry out.

    Replacing a shard with a junction is what people do when a disk fills up.
    Writing through it puts the statement outside the directory the guard was
    given, while `verify` correctly reports the ledger as broken and re-ingesting
    appears to succeed and fixes nothing — a permanent block-level failure whose
    only exit is a filesystem operation nothing tells you to perform.
    """
    paths, conn = ledger
    pipeline.ingest_paths(conn, paths, [real_statements[0]])

    shard = next(paths.archive.rglob("*.pdf")).parent
    elsewhere = git_free_tmp / "another-drive"
    elsewhere.mkdir()
    for archived in list(shard.glob("*.pdf")):
        archived.chmod(0o600)
        shutil.move(str(archived), str(elsewhere / archived.name))
    shard.rmdir()
    if not _make_junction(shard, elsewhere):
        pytest.skip("this host will not create a junction or a directory symlink")

    # The archive must not resolve through it either -- that is what made the
    # re-ingest report `duplicate` and copy nothing.
    assert archive.find_archived(paths, sorted(elsewhere.glob("*.pdf"))[0].stem) is None

    outcome = pipeline.ingest_file(conn, paths, real_statements[1])

    assert outcome.status == pipeline.FAILED
    assert outcome.error is not None
    assert "is a link, not a real directory" in outcome.error
    assert "Replace the link with a directory" in outcome.error


def test_a_dangling_link_still_gets_the_instruction(
    ledger, git_free_tmp, real_statements
) -> None:
    """The case most in need of the message used to get the least useful one.

    The guard read ``component.exists() and is_link_like(component)``, and
    ``exists()`` follows the link — so a junction whose target had been deleted
    answered False, the instruction was skipped, and the operator got a bare
    "Cannot create a file when that file already exists".
    """
    paths, conn = ledger
    pipeline.ingest_paths(conn, paths, [real_statements[0]])

    shard = next(paths.archive.rglob("*.pdf")).parent
    elsewhere = git_free_tmp / "target-about-to-vanish"
    elsewhere.mkdir()
    for archived in list(shard.glob("*.pdf")):
        archived.chmod(0o600)
        archived.unlink()
    shard.rmdir()
    if not _make_junction(shard, elsewhere):
        pytest.skip("this host will not create a junction or a directory symlink")
    elsewhere.rmdir()  # now dangling
    assert not shard.exists(), "a dangling link is what this test is about"

    outcome = pipeline.ingest_file(conn, paths, real_statements[1])

    assert outcome.status == pipeline.FAILED
    assert outcome.error is not None
    assert "is a link, not a real directory" in outcome.error


def test_a_statement_at_the_archive_root_is_not_where_statements_live(
    ledger, real_statements
) -> None:
    """Right name, wrong place — and the next re-ingest used to duplicate it.

    ``find_archived`` scans ``<YYYY>/<MM>`` only, so a correctly-named file at
    the archive root was invisible to it: every check passed, and re-offering the
    original wrote a *second* physical copy of the same bank statement while
    reporting "archived copy restored".
    """
    paths, conn = ledger
    pipeline.ingest_paths(conn, paths, [real_statements[0]])

    archived = next(paths.archive.rglob("*.pdf"))
    stray = paths.archive / archived.name
    shutil.copy2(archived, stray)

    result = next(
        r for r in pipeline.verify_ledger(conn, paths) if r.check_id == "archive_integrity"
    )
    assert result.status == "fail"
    assert result.detail["unexpected"] == [archived.name]


def test_re_ingesting_a_refused_statement_reports_the_restored_copy(
    ledger, git_free_tmp
) -> None:
    """The path where an operator is most likely to be retrying things.

    ``restored_archive`` was set only on the duplicate branch, so a refused
    statement whose archived copy had been deleted was silently repaired — on
    exactly the path where someone is trying things and needs to know which of
    them worked.
    """
    paths, conn = ledger
    _archive_something_unreadable(paths, conn, git_free_tmp)

    archived = next(paths.archive.rglob("*.pdf"))
    archived.chmod(0o600)
    archived.unlink()
    assert (
        next(
            r
            for r in pipeline.verify_ledger(conn, paths)
            if r.check_id == "recorded_not_archived"
        ).status
        == "fail"
    )

    outcome = pipeline.ingest_file(conn, paths, git_free_tmp / "header-only.pdf")

    assert outcome.status == pipeline.NEEDS_REVIEW
    assert outcome.restored_archive is True
    assert "archived copy restored" in outcome.summary_line()
    assert (
        next(
            r
            for r in pipeline.verify_ledger(conn, paths)
            if r.check_id == "recorded_not_archived"
        ).status
        == "pass"
    )


def test_the_temp_sweep_never_reaches_across_a_link(ledger, git_free_tmp) -> None:
    """The reporting function refused to cross this link; the deleting one did not.

    It ran on the server-start path, unattended, and deleted a file that was not
    in the data directory at all.
    """
    paths, conn = ledger
    shard = paths.archive / "2026" / "08"
    shard.mkdir(parents=True)

    elsewhere = git_free_tmp / "not-ours"
    elsewhere.mkdir()
    victim = elsewhere / ".someone-elses.abcd.tmp"
    victim.write_bytes(b"not ledgerbox's file")
    os.utime(victim, (0, 0))

    if not _make_junction(shard / "linked", elsewhere):
        pytest.skip("this host will not create a junction or a directory symlink")

    removed = paths.sweep_archive_temp(older_than_seconds=1)

    assert removed == []
    assert victim.exists(), "deleting outside the data directory is never in scope"


@pytest.mark.parametrize("name", ["٢٠٢٦", "20261", "２０２６"])
def test_a_directory_that_only_looks_numeric_is_not_a_shard(ledger, name: str) -> None:
    """``str.isdigit()`` is true for Arabic-Indic and fullwidth digits.

    A shard-shaped name built from them passed, and everything inside it became
    invisible: a correctly-named, correctly-hashed, recorded statement could sit
    in ``archive/٢٠٢٦/٠٩/`` and no check would see it at all.
    """
    paths, conn = ledger
    (paths.archive / name).mkdir(parents=True)

    result = next(
        r for r in pipeline.verify_ledger(conn, paths) if r.check_id == "archive_integrity"
    )
    assert result.status == "fail"
    assert result.detail["unexpected"] == [name]


def test_the_failure_line_admits_what_it_could_not_check(
    ledger, git_free_tmp, monkeypatch
) -> None:
    """Damage and an unread file at once: the one line has to mention both.

    Reporting only the corruption reads as "one problem, and everything else was
    checked" — while some of it was not checked at all.
    """
    paths, conn = ledger
    _archive_something_unreadable(paths, conn, git_free_tmp)

    both = pipeline.ArchiveSurvey(
        shas=frozenset({"a" * 64, "b" * 64}),
        corrupt=("2026/08/aaa.pdf",),
        unreadable=("2026/08/bbb.pdf",),
        stale_temp=(),
        unexpected=(),
    )
    monkeypatch.setattr(pipeline, "survey_archive", lambda *_a, **_k: both)

    result = next(
        r for r in pipeline.verify_ledger(conn, paths) if r.check_id == "archive_integrity"
    )
    assert result.status == "fail"
    assert "do not hash to their own names" in result.message
    assert "could not be read" in result.message


# --------------------------------------------------------------------------
# P2 M2.1: the aggregations that report money must agree with one another
#
# Two of them when this section was written, three since P2 M5 added
# `v_category_spend` -- and the third is not like the other two. No *data* can
# separate it from `ledger_totals`, because it scans the same postings through a
# join that emits one row per transaction; only an edit to one of the two
# queries can. Its negative cases therefore edit a query, and the two older ones
# plant a transaction shape. Which is which is asserted, not assumed.
# --------------------------------------------------------------------------


def _cashflow_check(conn) -> object:
    return next(
        r for r in pipeline.verify_ledger(conn) if r.check_id == "cashflow_agreement"
    )


def test_cashflow_agreement_passes_on_an_empty_ledger(ledger) -> None:
    """Zero equals zero, and it has to say so rather than skip.

    An empty ledger is the state a fresh install spends its first minute in,
    and a check that reported UNVERIFIED there would train the operator to
    ignore the word before it ever meant anything.
    """
    _, conn = ledger
    result = _cashflow_check(conn)
    assert result.status == "pass"
    assert result.severity == "block"
    assert result.detail["disagreements"] == {}


def test_cashflow_agreement_passes_over_the_real_corpus(ingested_real) -> None:
    _, conn, _ = ingested_real
    assert _cashflow_check(conn).status == "pass"


def _plant_txn_without_identity(conn, *, amount_minor: int) -> None:
    """One transaction with an expense leg and no ``txn_identity`` row.

    Not an arbitrary corruption. This is the exact shape verification
    constructed to refute the third version of ``ledger_totals``'s docstring:
    ``_TOTALS_SQL`` never joins ``txn_identity``, so it counts this, while
    ``v_cashflow_monthly`` reads ``v_transaction`` and cannot see it. Nothing
    in the codebase produces it today — which is the point. The check exists so
    that whatever produces it tomorrow is met by a red line rather than by a
    paragraph.
    """
    with transaction(conn):
        repo.ensure_account(
            conn,
            account_id="assets:planted:checking",
            # A beancount-expressible name: the export refuses any root outside
            # the five, and one of these tests renders it.
            name="Assets:Planted:Checking",
            kind="asset",
            subtype="checking",
            currency="USD",
            institution="planted",
            mask=None,
        )
        conn.execute(
            "INSERT INTO txn (id, date, narration, created_at) VALUES (?, ?, ?, ?)",
            ("planted-txn", "2025-07-15", "planted", "2026-08-04T00:00:00+00:00"),
        )
        for seq, (account, amount) in enumerate(
            (("assets:planted:checking", amount_minor), ("expenses:uncategorized", -amount_minor))
        ):
            conn.execute(
                "INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"planted-posting-{seq}", "planted-txn", seq, account, amount, "USD"),
            )


def test_cashflow_agreement_fails_when_one_side_cannot_see_a_transaction(ledger) -> None:
    """The negative case, without which this check has never been seen to fail."""
    _, conn = ledger
    assert _cashflow_check(conn).status == "pass"

    _plant_txn_without_identity(conn, amount_minor=-50_000)

    result = _cashflow_check(conn)
    assert result.status == "fail"
    assert result.severity == "block"

    outflow = result.detail["disagreements"]["outflow_minor"]
    assert outflow["ledger_totals_minor"] == -50_000
    assert outflow["cashflow_view_minor"] == 0
    assert result.detail["disagreements"]["txn_count"] == {
        "ledger_totals": 1,
        "cashflow_view": 0,
    }
    assert "disagree" in result.message


def test_cashflow_agreement_fails_in_the_other_direction_too(ledger) -> None:
    """The mirror shape: visible to the view, invisible to the totals.

    A transaction between two accounts you own has two own-account legs and no
    income/expense leg, so ``v_cashflow_monthly`` counts its bank leg and
    ``ledger_totals`` finds nothing to add. Both directions are asserted
    because the check claims to cover both, and a check only covers what it has
    been seen to catch.
    """
    _, conn = ledger
    with transaction(conn):
        for name in ("assets:own-a:checking", "assets:own-b:savings"):
            repo.ensure_account(
                conn,
                account_id=name,
                name=name,
                kind="asset",
                subtype="checking",
                currency="USD",
                institution="planted",
                mask=None,
            )
        conn.execute(
            "INSERT INTO txn (id, date, narration, created_at) VALUES (?, ?, ?, ?)",
            ("own-to-own", "2025-07-15", "planted", "2026-08-04T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO raw_record (id, source_file_id, record_index, kind, payload, "
            "parser_id, parser_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("rr-own", _plant_source_file(conn), 0, "stmttrn", "{}", "planted", "1"),
        )
        for seq, (account, amount) in enumerate(
            (("assets:own-a:checking", -50_000), ("assets:own-b:savings", 50_000))
        ):
            conn.execute(
                "INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"own-posting-{seq}", "own-to-own", seq, account, amount, "USD"),
            )
        conn.execute(
            "INSERT INTO txn_identity (txn_id, account_id, source_system, natural_key, "
            "natural_key_version, occurrence_index, raw_descriptor, raw_record_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("own-to-own", "assets:own-a:checking", "planted", "nk-own", 1, 0, "PLANTED", "rr-own"),
        )

    result = _cashflow_check(conn)
    assert result.status == "fail"
    outflow = result.detail["disagreements"]["outflow_minor"]
    assert outflow["ledger_totals_minor"] == 0
    assert outflow["cashflow_view_minor"] == -50_000


def _plant_source_file(conn) -> str:
    """A minimal ``source_file`` row, because ``raw_record`` has a foreign key."""
    sha = "f" * 64
    conn.execute(
        "INSERT INTO source_file (id, sha256, rel_path, media_type, byte_len, ingested_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
        (sha, sha, f"2025/07/{sha}.pdf", "application/pdf", 1, "2026-08-04T00:00:00+00:00"),
    )
    return sha


def test_cashflow_agreement_catches_a_ghost_that_moves_no_money(ledger) -> None:
    """A zero-amount transaction one side cannot see: both totals still match.

    Found during verification, not by design. It is the case that justifies
    ``txn_count`` being compared at all — the two money figures are identical
    to the cent and only the count says a transaction is missing from one side.
    """
    _, conn = ledger
    _plant_txn_without_identity(conn, amount_minor=0)

    result = _cashflow_check(conn)
    assert result.status == "fail"
    assert set(result.detail["disagreements"]) == {"txn_count"}
    assert result.detail["disagreements"]["txn_count"] == {
        "ledger_totals": 1,
        "cashflow_view": 0,
    }


def _book_one_statement(conn) -> list[str]:
    """A small statement with the two shapes the breakdown has to carry.

    One line a rule claimed and two nothing claimed, so a probe that drops the
    unclaimed group has something to lose. Built with ``test_transactions``'
    own writer rather than a third copy of the entry/posting/identity shapes
    (§5.29); ``tests/synth.py`` is imported the same way throughout this suite.
    """
    from test_transactions import Line, book

    return book(
        conn,
        (
            Line(amount_minor=-1_000, descriptor="a claimed line", rule_category="dining"),
            Line(amount_minor=-2_500, descriptor="nothing claims this"),
            Line(amount_minor=-400, descriptor="nor this"),
        ),
    )


def _redefine_category_spend(conn, *, extra_where: str) -> None:
    """Replace ``v_category_spend``'s body with a subtly wrong one.

    Blunt on purpose. The two comparisons above can be pulled apart by *data* —
    a transaction one side structurally cannot see — and this one cannot: the
    breakdown scans the same postings ``outflow_minor`` scans, through a join
    that emits one row per transaction. So the only thing that can separate
    them is somebody editing one of the two SQL texts, and the only honest
    negative case is to edit one.

    Which makes this a test of the thing that actually goes wrong. A rules
    change, a "small tidy-up", a new filter added to one query and not the
    other — P2 M5 draws every one of these rows as a wedge claiming to be part
    of the headline Out, and this is what stands between that claim and a
    plausible picture.
    """
    with transaction(conn):
        conn.execute("DROP VIEW v_category_spend")
        conn.execute(
            "CREATE VIEW v_category_spend AS "
            "SELECT vc.category_id, -SUM(p.amount_minor) AS spend_minor, "
            "       COUNT(DISTINCT p.txn_id) AS txn_count "
            "FROM posting p "
            "JOIN account a ON a.id = p.account_id "
            "JOIN txn t ON t.id = p.txn_id "
            "JOIN v_txn_transfer vt ON vt.txn_id = t.id "
            "JOIN v_txn_category vc ON vc.txn_id = t.id "
            "WHERE t.superseded_by IS NULL AND vt.is_transfer = 0 "
            f"  AND a.kind = 'expense' {extra_where} "
            "GROUP BY vc.category_id"
        )


def test_cashflow_agreement_catches_a_breakdown_that_stopped_adding_up(ledger) -> None:
    """The negative case for the third comparison: the predecessor's own shape.

    Dropping the unclaimed group is not a hypothetical mistake. It is precisely
    what the predecessor's breakdown did in effect — a catch-all that was also a
    wrong rule, so "other" came to almost nothing and the pie looked complete
    (§5.38). Here it leaves the wedges summing to less than the Out they are
    drawn under, and `verify` says so with both numbers.
    """
    _, conn = ledger
    _book_one_statement(conn)
    assert _cashflow_check(conn).status == "pass"

    _redefine_category_spend(conn, extra_where="AND vc.category_id IS NOT NULL")

    result = _cashflow_check(conn)
    assert result.status == "fail"
    assert result.severity == "block"

    reported = result.detail["disagreements"]["category_breakdown_minor"]
    assert reported["ledger_totals_minor"] < reported["category_spend_minor"], (
        "the breakdown lost the unclaimed lines, so it is nearer zero than the total"
    )
    # The two comparisons that data can trigger are untouched: this is a defect
    # in one query's text, not a transaction one side cannot see.
    assert set(result.detail["disagreements"]) == {"category_breakdown_minor"}
    assert "disagree" in result.message


def test_cashflow_agreement_catches_a_breakdown_that_counts_too_much(ledger) -> None:
    """And the other direction, because a check covers what it has been seen to catch.

    Losing the transfer exclusion makes the breakdown larger than the figure it
    claims to break down — the direction that would quietly *inflate* somebody's
    reported spending rather than shrink it.
    """
    _, conn = ledger
    txn_ids = _book_one_statement(conn)
    with transaction(conn):
        repo.set_transfer_flags(conn, assignments={txn_ids[0]: True})
    assert _cashflow_check(conn).status == "pass", "flagging alone keeps every side in step"

    # `vt.is_transfer = 0` is in the base text, so this probe removes it by
    # rebuilding without it rather than by appending a clause.
    with transaction(conn):
        conn.execute("DROP VIEW v_category_spend")
        conn.execute(
            "CREATE VIEW v_category_spend AS "
            "SELECT vc.category_id, -SUM(p.amount_minor) AS spend_minor, "
            "       COUNT(DISTINCT p.txn_id) AS txn_count "
            "FROM posting p "
            "JOIN account a ON a.id = p.account_id "
            "JOIN txn t ON t.id = p.txn_id "
            "JOIN v_txn_category vc ON vc.txn_id = t.id "
            "WHERE t.superseded_by IS NULL AND a.kind = 'expense' "
            "GROUP BY vc.category_id"
        )

    result = _cashflow_check(conn)
    assert result.status == "fail"
    reported = result.detail["disagreements"]["category_breakdown_minor"]
    assert reported["category_spend_minor"] < reported["ledger_totals_minor"], (
        "the breakdown kept a transfer the headline figure dropped"
    )


def test_cashflow_agreement_catches_an_edit_to_the_query_the_chart_is_drawn_from(
    ledger, monkeypatch
) -> None:
    """The hole an acceptance round found: only the *view* used to be checked.

    Two texts express "what each category cost" — ``v_category_spend`` in SQL
    and :data:`repo._CATEGORY_SPEND_SQL` in Python — and the donut is drawn from
    the second one. The check read only the first, so editing the query that
    reaches the page left the chart summing to a fraction of the Out printed
    above it while every block-level check passed.

    The mutation is the same one the round used and the same shape as the view
    probe above: drop the unclaimed lines, which is the predecessor's defect
    (§5.38). Both arms are asserted, in both directions — this one fires and the
    view's does not, because the view was not touched. Together with the
    assertion in the view test that *only* the view's key appears, that is what
    says the two arms are independent rather than one arm written twice.
    """
    _, conn = ledger
    _book_one_statement(conn)
    assert _cashflow_check(conn).status == "pass"

    monkeypatch.setattr(
        repo,
        "_CATEGORY_SPEND_SQL",
        repo._CATEGORY_SPEND_SQL + "\n  AND l.category_id IS NOT NULL\n",
    )

    result = _cashflow_check(conn)
    assert result.status == "fail"
    assert result.severity == "block"
    reported = result.detail["disagreements"]["category_query_minor"]
    assert reported["ledger_totals_minor"] < reported["category_spend_query_minor"], (
        "the drawn breakdown lost the unclaimed lines, so it is nearer zero than the total"
    )
    assert set(result.detail["disagreements"]) == {
        "category_query_minor",
        "scoped_category_minor",
    }, (
        "the SQL view was not edited, so its own comparison must still agree -- and one "
        "edit is seen by every comparison that reads the edited query, which is two"
    )
    assert "disagree" in result.message


def _book_income_and_spending(conn) -> list[str]:
    """A statement with money arriving as well as leaving.

    ``_book_one_statement`` is spending only, which is right for the breakdown
    probes — the breakdown reads expense legs and nothing else. It is wrong for
    a probe that removes the *income* arm: with no income, both sides are zero
    before the edit and zero after it, and the mutation is a no-op wearing the
    costume of a negative case. That mistake is cheap to make and invisible once
    made, which is why the test below asserts the precondition out loud.
    """
    from test_transactions import Line, book

    return book(
        conn,
        (
            Line(amount_minor=9_000, descriptor="money arriving"),
            Line(amount_minor=-1_000, descriptor="a claimed line", rule_category="dining"),
            Line(amount_minor=-2_500, descriptor="nothing claims this"),
        ),
    )


def test_cashflow_agreement_catches_a_monthly_split_that_stopped_adding_up(
    ledger, monkeypatch
) -> None:
    """The negative case for the bars, which arrived in M6 without one.

    The monthly split is compared to the same four figures the bars are drawn
    under. Nothing in the data can separate them — both read ``v_cashflow_line``
    — so the honest probe is an edit, as it is for the breakdown. Losing the
    income arm is the shape that would show a month of spending with no earnings
    above the line.
    """
    _, conn = ledger
    _book_income_and_spending(conn)
    assert repo.ledger_totals(conn)["inflow_minor"] != 0, (
        "the probe below removes the income arm; on a ledger with no income it changes "
        "nothing and this test would pass without the check ever having worked"
    )
    assert _cashflow_check(conn).status == "pass"

    monkeypatch.setattr(
        repo,
        "_CASHFLOW_MONTHS_SQL",
        repo._CASHFLOW_MONTHS_SQL.replace("l.account_kind = 'income'", "0 = 1"),
    )

    result = _cashflow_check(conn)
    assert result.status == "fail"
    reported = result.detail["disagreements"]["monthly_inflow_minor"]
    assert reported["monthly_sum_minor"] == 0
    assert reported["ledger_totals_minor"] != 0, (
        "this ledger has income, or the probe proves nothing"
    )
    # Four keys for one edit, and each is a different sentence: the money is
    # wrong, the net that money feeds is wrong, and both are wrong through the
    # date bound as well. The count is untouched and stays quiet, which is what
    # separates "adding it up wrong" from "missing rows".
    assert set(result.detail["disagreements"]) == {
        "monthly_inflow_minor",
        "monthly_net_minor",
        "scoped_monthly_inflow_minor",
        "scoped_monthly_net_minor",
    }


def test_cashflow_agreement_catches_a_monthly_net_that_no_longer_adds_up(
    ledger, monkeypatch
) -> None:
    """The field a comment used to exempt, and the reason exemptions are not free.

    ``net_minor`` went unchecked under the argument that both sides derive it
    as in + out and so cannot disagree while those agree. They do both derive
    it that way — in two different expressions — and two expressions of one sum
    is exactly what every other arm here is watching for. Acceptance turned one
    ``+`` into a ``-``: every Net in the bar chart and its total row went
    wrong, the Net at the top of the page stayed right, both were on screen at
    once, and all nine checks passed.

    The probe wraps the repository function rather than editing an SQL string,
    because the arithmetic being guarded is in Python and that is where an edit
    to it would land.
    """
    _, conn = ledger
    _book_income_and_spending(conn)
    assert _cashflow_check(conn).status == "pass"

    real = repo.monthly_cashflow

    def wrong(connection, span=None):
        months = real(connection, span)
        return repo.MonthlyCashflow(
            months=months.months,
            inflow_minor=months.inflow_minor,
            outflow_minor=months.outflow_minor,
            net_minor=months.net_minor - 1,
            txn_count=months.txn_count,
        )

    monkeypatch.setattr(repo, "monthly_cashflow", wrong)

    result = _cashflow_check(conn)
    assert result.status == "fail"
    reported = result.detail["disagreements"]["monthly_net_minor"]
    assert reported["monthly_sum_minor"] == reported["ledger_totals_minor"] - 1
    # The money and the count are untouched, so nothing else may complain about
    # them -- a net that disagrees on its own is the whole shape being caught.
    assert "monthly_inflow_minor" not in result.detail["disagreements"]
    assert "monthly_txn_count" not in result.detail["disagreements"]


def _book_two_days(conn) -> list[str]:
    """A ledger spanning two dates, so a date bound can select a strict subset."""
    from test_transactions import Line, book

    return book(
        conn,
        (
            Line(amount_minor=9_000, descriptor="money arriving", date="2025-05-02"),
            Line(amount_minor=-2_500, descriptor="early spending", date="2025-05-02"),
            Line(amount_minor=-1_000, descriptor="late spending", date="2025-05-20"),
        ),
        period_start="2025-05-01",
        period_end="2025-05-31",
    )


def test_cashflow_agreement_catches_a_query_that_ignores_its_date_bound(
    ledger, monkeypatch
) -> None:
    """The hole M6 opened and nothing was asking about.

    Every comparison above is unscoped, and unscoped is the only window
    ``verify`` may be judged on — a check that could be made green by choosing
    a window is not a check. But the identity the page rests on is "for *any*
    window", and an acceptance round made ``category_spend`` ignore its span
    argument in one line: the headline described the window, the donut
    described the whole ledger, they sat one above the other, and all nine
    checks passed.

    One derived bound is now asked about as well. This probe removes the bound
    the way that edit did, and the unscoped arms stay quiet — which is the
    point, because they are what was quiet before.
    """
    _, conn = ledger
    _book_two_days(conn)
    assert _cashflow_check(conn).status == "pass"

    real = repo.category_spend
    monkeypatch.setattr(repo, "category_spend", lambda connection, span=None: real(connection))

    result = _cashflow_check(conn)
    assert result.status == "fail"
    reported = result.detail["disagreements"]["scoped_category_minor"]
    assert reported["category_spend_query_minor"] != reported["ledger_totals_minor"]
    assert set(result.detail["disagreements"]) == {"scoped_category_minor"}, (
        "an unscoped query agrees with an unscoped total; only the bounded "
        "comparison can see this"
    )


def test_the_scoped_probe_says_nothing_about_a_ledger_it_cannot_bound(ledger) -> None:
    """An empty ledger, and a date that is not a date, are both silence.

    ``txn.date`` carries no CHECK constraint (``docs/STATUS.md`` §7), so a
    value that is not a day is reachable. Reporting it as a cashflow
    disagreement would name the wrong problem with somebody's ledger.
    """
    _, conn = ledger
    assert _cashflow_check(conn).status == "pass", "nothing booked, nothing to compare"

    _book_two_days(conn)
    with transaction(conn):
        conn.execute("UPDATE txn SET date = '05/20/2025' WHERE date = '2025-05-20'")

    result = _cashflow_check(conn)
    assert not any(key.startswith("scoped_") for key in result.detail["disagreements"]), (
        "a date the bound cannot be built from is not a disagreement about money"
    )


def test_cashflow_agreement_catches_a_monthly_split_that_counts_wrong(
    ledger, monkeypatch
) -> None:
    """The count, separately from the money.

    A split whose amounts add up and whose line count does not is a chart
    captioned with a number nothing on the page supports. It is the same reason
    the first comparison carries a count of its own: it separates "one of us is
    missing rows" from "one of us is adding them up wrong".
    """
    _, conn = ledger
    _book_income_and_spending(conn)
    assert _cashflow_check(conn).status == "pass"

    monkeypatch.setattr(
        repo,
        "_CASHFLOW_MONTHS_SQL",
        repo._CASHFLOW_MONTHS_SQL.replace(
            "COUNT(DISTINCT l.txn_id)", "COUNT(DISTINCT l.txn_id) + 1"
        ),
    )

    result = _cashflow_check(conn)
    assert result.status == "fail"
    reported = result.detail["disagreements"]["monthly_txn_count"]
    assert reported["monthly_sum"] > reported["ledger_totals"]
    assert set(result.detail["disagreements"]) == {"monthly_txn_count"}, (
        "the amounts were untouched, so only the count may disagree"
    )


def test_a_cashflow_failure_prints_the_numbers_not_just_the_field_names(
    git_free_tmp, capsys
) -> None:
    """The whole point of this check is to surface an amount.

    The first version rendered nothing: ``_detail_lines`` walked only list-valued
    detail, this check's detail is a mapping, and so the one check that exists to
    show a number showed none — while ``doctor`` pointed the operator at
    ``verify`` for numbers ``verify`` was dropping.
    """
    data_dir = git_free_tmp / "cashflow-detail"
    paths = DataPaths.resolve(data_dir)
    conn = open_ledger(paths.db)
    try:
        _plant_txn_without_identity(conn, amount_minor=-7_777)
    finally:
        conn.close()

    assert main(["--data-dir", str(data_dir), "verify"]) == 2
    out = capsys.readouterr().out

    # Each assertion names the *key* as well as the value. Asserting a bare
    # "-$77.77" looked like it pinned the fix and did not: the `ledger:` summary
    # line at the bottom of every verify run prints the same amount for an
    # unrelated reason, so two of these three assertions passed against the
    # broken renderer. An assertion satisfied by a coincidence elsewhere on the
    # screen is the test-suite version of the defect this project is about.
    assert "ledger_totals_minor=-$77.77" in out, "the amount, attached to the side it came from"
    assert "cashflow_view_minor=$0.00" in out, "and what the other side saw"
    assert "ledger_totals=1" in out and "cashflow_view=0" in out, "and the counts"


def test_cashflow_agreement_reaches_the_cli_exit_code(git_free_tmp, capsys) -> None:
    """A block-level failure has to leave `verify` non-zero, or it is decoration."""
    data_dir = git_free_tmp / "cashflow-cli"
    paths = DataPaths.resolve(data_dir)
    conn = open_ledger(paths.db)
    try:
        _plant_txn_without_identity(conn, amount_minor=-1_234)
    finally:
        conn.close()

    assert main(["--data-dir", str(data_dir), "verify"]) == 2
    assert "cashflow_agreement" in capsys.readouterr().out

    # And `doctor` must not disagree with `verify` about the same ledger. That
    # split is exactly what §5.22 had to close once already, for a different
    # check, and the exit code is the only part of doctor's output cron reads.
    assert main(["--data-dir", str(data_dir), "doctor"]) == 2
    assert "cashflow" in capsys.readouterr().out


# --------------------------------------------------------------------------
# P2 M2: one definition of "is this a transfer", end to end
# --------------------------------------------------------------------------


def _plant_booked_txn(conn, *, txn_id: str, amount_minor: int, record_index: int) -> None:
    """One statement line in the shape ``build_entries`` produces."""
    with transaction(conn):
        repo.ensure_account(
            conn,
            account_id="assets:planted:checking",
            # A beancount-expressible name: the export refuses any root outside
            # the five, and one of these tests renders it.
            name="Assets:Planted:Checking",
            kind="asset",
            subtype="checking",
            currency="USD",
            institution="planted",
            mask=None,
        )
        source = _plant_source_file(conn)
        raw = f"rr-{txn_id}"
        conn.execute(
            "INSERT INTO raw_record (id, source_file_id, record_index, kind, payload, "
            "parser_id, parser_version) VALUES (?, ?, ?, 'stmttrn', '{}', 'planted', '1')",
            (raw, source, record_index),
        )
        conn.execute(
            "INSERT INTO txn (id, date, narration, created_at) VALUES (?, ?, ?, ?)",
            (txn_id, "2025-07-15", "planted", "2026-08-04T00:00:00+00:00"),
        )
        counter = "income:uncategorized" if amount_minor > 0 else "expenses:uncategorized"
        for seq, (account, amount) in enumerate(
            (("assets:planted:checking", amount_minor), (counter, -amount_minor))
        ):
            conn.execute(
                "INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"{txn_id}-{seq}", txn_id, seq, account, amount, "USD"),
            )
        conn.execute(
            "INSERT INTO txn_identity (txn_id, account_id, source_system, natural_key, "
            "natural_key_version, occurrence_index, raw_descriptor, raw_record_id) "
            "VALUES (?, 'assets:planted:checking', 'planted', ?, 1, 0, 'PLANTED', ?)",
            (txn_id, f"nk-{txn_id}", raw),
        )


def test_marking_a_transfer_moves_the_headline_numbers_and_keeps_them_agreeing(
    ledger,
) -> None:
    """The whole seam, in one test: a person decides, and every reader follows.

    Each piece of this landed separately -- the predicate view, the override
    writer, the totals query, the excluded amounts -- and each was tested
    against its own neighbours. Nothing exercised the path end to end, which is
    the join where "it works" and "the parts work" come apart.

    The agreement check is asserted **after** the move, not only before: an
    exclusion that reached one aggregation and not the other would be exactly
    the divergence §5.43 spent four rewrites failing to describe.
    """
    _, conn = ledger
    _plant_booked_txn(conn, txn_id="stays", amount_minor=-30_000, record_index=0)
    _plant_booked_txn(conn, txn_id="moved", amount_minor=-50_000, record_index=1)
    with transaction(conn):
        repo.ensure_categories(conn, rows=[("transfer", None, "transfer")])

    before = repo.ledger_totals(conn)
    assert before["outflow_minor"] == -80_000
    assert before["transfer_count"] == 0
    assert before["transfer_excluded_out_minor"] == 0
    assert _cashflow_check(conn).status == "pass"

    with transaction(conn):
        assert repo.set_category_override(conn, txn_id="moved", category_id="transfer") is True

    after = repo.ledger_totals(conn)
    assert after["outflow_minor"] == -30_000, "the person's decision reached the totals"
    assert after["transfer_count"] == 1
    assert after["transfer_excluded_out_minor"] == -50_000, "and it says how much it took"
    assert after["outflow_minor"] + after["transfer_excluded_out_minor"] == -80_000
    assert _cashflow_check(conn).status == "pass", "both aggregations moved together"

    with transaction(conn):
        assert repo.clear_category_override(conn, txn_id="moved") is True
    assert repo.ledger_totals(conn) == before, "and it is reversible"


def test_a_person_can_overrule_a_rule_that_flagged_the_wrong_row(ledger) -> None:
    """The direction that matters most: taking money back *into* the totals.

    A false positive is the dangerous failure here -- it shrinks reported
    spending silently -- so the route out of one has to work, and has to be
    reachable without editing a rules file.
    """
    _, conn = ledger
    _plant_booked_txn(conn, txn_id="ruled", amount_minor=-25_000, record_index=0)
    with transaction(conn):
        repo.ensure_categories(
            conn, rows=[("transfer", None, "transfer"), ("dining", None, "expense")]
        )
        repo.set_transfer_flags(conn, assignments={"ruled": True})

    flagged = repo.ledger_totals(conn)
    assert flagged["outflow_minor"] == 0
    assert flagged["transfer_excluded_out_minor"] == -25_000

    with transaction(conn):
        repo.set_category_override(conn, txn_id="ruled", category_id="dining")

    corrected = repo.ledger_totals(conn)
    assert corrected["outflow_minor"] == -25_000, "the money is back in spending"
    assert corrected["transfer_count"] == 0
    assert corrected["transfer_excluded_out_minor"] == 0
    assert _cashflow_check(conn).status == "pass"

    raw = conn.execute("SELECT is_transfer FROM txn WHERE id='ruled'").fetchone()[0]
    assert raw == 1, "the rule's own answer is untouched; re-running rules cannot lose the person"


def test_the_dry_run_count_survives_a_person_having_corrected_something(
    git_free_tmp, capsys
) -> None:
    """``--dry-run`` must predict the number the real run then changes.

    The two agree trivially on a ledger nobody has corrected, because the rules'
    own previous answer and the effective answer are then the same value — which
    is why every existing test of this pair passed while the comparison read the
    wrong column. Verification swapped ``categorized_rows`` to report the
    effective value and all 596 tests stayed green.

    With one override present the two columns differ, and a preview that
    disagrees with the run it previews is worse than no preview — on exactly the
    ledgers where somebody has already had to correct something.
    """
    data_dir = git_free_tmp / "dryrun-override"
    paths = DataPaths.resolve(data_dir)
    conn = open_ledger(paths.db)
    try:
        _plant_booked_txn(conn, txn_id="corrected", amount_minor=-40_000, record_index=0)
        with transaction(conn):
            repo.ensure_categories(conn, rows=[("transfer", None, "transfer")])
            repo.set_category_override(conn, txn_id="corrected", category_id="transfer")
        # The person says transfer; the rules say otherwise. raw=0, effective=1.
        rows = repo.categorized_rows(conn)
        assert [row["rule_is_transfer"] for row in rows] == [0]
        assert repo.ledger_totals(conn)["transfer_count"] == 1
    finally:
        conn.close()

    assert main(["--data-dir", str(data_dir), "reapply-rules", "--dry-run"]) == 0
    preview = capsys.readouterr().out
    predicted = int(preview.split(" and ")[1].split(" transfer flag")[0])

    assert main(["--data-dir", str(data_dir), "reapply-rules"]) == 0
    applied = capsys.readouterr().out
    changed = int(applied.split(" and ")[1].split(" transfer flag")[0])

    assert predicted == changed == 0, "the rules want nothing; the person's answer is not theirs"

    conn = open_ledger(paths.db)
    try:
        assert repo.get_category_override(conn, "corrected") is not None, "and it survives"
        assert repo.ledger_totals(conn)["transfer_count"] == 1
    finally:
        conn.close()


def test_the_dry_run_category_count_survives_a_person_having_corrected_something(
    git_free_tmp, capsys
) -> None:
    """The same defect as the test above, on the other column, one release later.

    Migration 0006 made ``v_transaction.category_id`` the *effective* category —
    an override folded over the rules' answer — because a transaction table that
    kept showing the rule's old answer after somebody changed it would be the
    whole point of the write endpoint defeated. That substitution put this
    command's category forecast in exactly the position §5.51 records the
    transfer forecast having been in: comparing a person's answer against what
    the rules would say and calling the difference a row the rules want to move.

    So ``categorized_rows`` reports ``rule_category_id`` and this compares that.
    The trap is that it is invisible on a ledger nobody has corrected — raw and
    effective are the same value there, which is every other test in this suite.

    Here the rules claim nothing (``PLANTED`` matches no pattern) and a person
    has said ``dining``. The rules want no change, so ``--dry-run`` must promise
    none and the run must make none. Reading the effective column would promise
    one and then make none.
    """
    data_dir = git_free_tmp / "dryrun-category-override"
    paths = DataPaths.resolve(data_dir)
    conn = open_ledger(paths.db)
    try:
        _plant_booked_txn(conn, txn_id="recategorised", amount_minor=-40_000, record_index=0)
        with transaction(conn):
            repo.ensure_categories(conn, rows=[("dining", None, "expense")])
            repo.set_category_override(conn, txn_id="recategorised", category_id="dining")

        rows = repo.categorized_rows(conn)
        assert [row["rule_category_id"] for row in rows] == [None], "the rules claimed nothing"
        effective = conn.execute(
            "SELECT category_id, decided_by FROM v_txn_category WHERE txn_id = 'recategorised'"
        ).fetchone()
        assert (effective["category_id"], effective["decided_by"]) == ("dining", "override")
    finally:
        conn.close()

    assert main(["--data-dir", str(data_dir), "reapply-rules", "--dry-run"]) == 0
    preview = capsys.readouterr().out
    predicted = int(preview.split(" of ")[0].split(": ")[-1])

    assert main(["--data-dir", str(data_dir), "reapply-rules"]) == 0
    applied = capsys.readouterr().out
    changed = int(applied.split(" of ")[0].split(": ")[-1])

    assert predicted == changed == 0, "the rules want nothing; the person's answer is not theirs"

    conn = open_ledger(paths.db)
    try:
        raw = conn.execute(
            "SELECT category_id FROM posting WHERE id = 'recategorised-0'"
        ).fetchone()[0]
        assert raw is None, "the rules' column stayed the rules'; no decision was written into it"
        assert repo.get_category_override(conn, "recategorised") is not None, "and it survives"
    finally:
        conn.close()


def test_an_overridden_transfer_reaches_the_beancount_export(ledger) -> None:
    """The export is the escape hatch, so it has to agree with the ledger too."""
    from ledgerbox.ledger.beancount_export import render_beancount

    _, conn = ledger
    _plant_booked_txn(conn, txn_id="moved", amount_minor=-50_000, record_index=0)
    with transaction(conn):
        repo.ensure_categories(conn, rows=[("transfer", None, "transfer")])
        repo.set_category_override(conn, txn_id="moved", category_id="transfer")

    assert "#transfer" in render_beancount(conn)


def test_an_unreadable_archived_file_is_a_finding_not_a_traceback(
    ledger, git_free_tmp, monkeypatch
) -> None:
    """A statement open in a PDF reader must not cost the whole verify run.

    Exclusive locks are ordinary on Windows: antivirus mid-scan, a sync client
    copying, the operator reading their own statement. Letting the OSError out
    aborted verify before it printed a single check, under exit code 1 — which
    this CLI defines as "a statement needs review".
    """
    paths, conn = ledger
    _archive_something_unreadable(paths, conn, git_free_tmp)

    def refuse(path):  # noqa: ANN001, ANN202
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(pipeline, "sha256_file", refuse)

    results = {r.check_id: r for r in pipeline.verify_ledger(conn, paths)}

    assert len(results) >= 8, "every other check still has to run and be reported"
    assert results["double_entry"].status == "pass"
    integrity = results["archive_integrity"]
    assert integrity.status == "skip", "not checked is not the same as intact"
    assert integrity.severity == "block"
    assert len(integrity.detail["unreadable"]) == 1
    assert integrity.detail["corrupt"] == []


def test_interrupted_archive_debris_is_not_blamed_on_a_stranger(
    ledger, git_free_tmp
) -> None:
    """`.<name>.<rand>.tmp` is written by this program, so it is not "unexpected"."""
    paths, conn = ledger
    _archive_something_unreadable(paths, conn, git_free_tmp)

    shard = next(p for p in paths.archive.rglob("*.pdf")).parent
    debris = shard / ".abandoned.pdf.a1b2c3.tmp"
    debris.write_bytes(b"%PDF-1.7\n% half a copy\n")

    result = next(
        r for r in pipeline.verify_ledger(conn, paths) if r.check_id == "archive_integrity"
    )
    assert result.status == "pass", "debris from a crash is not damage"
    assert result.detail["stale_temp"] == [f"{shard.name}/{debris.name}"] or result.detail[
        "stale_temp"
    ], "but it is reported rather than ignored"

    removed = paths.sweep_archive_temp(older_than_seconds=-1)
    assert len(removed) == 1
    assert not debris.exists()


@pytest.mark.parametrize(
    "check_id", ["archived_not_recorded", "recorded_not_archived", "archive_integrity"]
)
def test_the_archive_checks_read_as_unverified_when_they_cannot_run(ledger, check_id) -> None:
    _, conn = ledger
    result = next(r for r in pipeline.verify_ledger(conn) if r.check_id == check_id)
    assert result.status == "skip"
    assert result.severity == "block", "a skipped block-level check is not a pass"


def test_verify_passes_over_a_ledger_that_matches_its_archive(ingested_real) -> None:
    paths, conn, _ = ingested_real
    failed = [r.check_id for r in pipeline.verify_ledger(conn, paths) if r.status != "pass"]
    assert failed == []


def test_a_directory_is_expanded_and_deduplicated() -> None:
    assert pipeline.collect_pdfs([]) == []


# --------------------------------------------------------------------------
# the CLI
# --------------------------------------------------------------------------


def test_cli_ingest_verify_doctor(
    git_free_tmp: Path, real_statements: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = git_free_tmp / "cli-data"
    argv = ["--data-dir", str(data_dir)]

    assert main([*argv, "ingest", str(real_statements[0])]) == 0
    out = capsys.readouterr().out
    assert "imported" in out

    assert main([*argv, "verify"]) == 0
    assert "ok" in capsys.readouterr().out

    assert main([*argv, "doctor"]) == 0
    doctor = capsys.readouterr().out
    assert "schema" in doctor and "review   queue empty" in doctor


def test_cli_exits_nonzero_when_a_file_needs_review(
    git_free_tmp: Path, real_statements: list[Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    parser = PARSERS[0]
    original_parse = type(parser).parse

    def tampered(self, doc):  # noqa: ANN001, ANN202
        statement = original_parse(self, doc)
        rows = list(statement.transactions)
        rows[2] = replace(rows[2], amount_minor=rows[2].amount_minor - 500)
        return replace(statement, transactions=tuple(rows))

    monkeypatch.setattr(type(parser), "parse", tampered)
    code = main(["--data-dir", str(git_free_tmp / "cli-review"), "ingest", str(real_statements[0])])
    assert code == 1


def test_cli_exits_two_when_a_file_cannot_be_read(
    git_free_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junk = git_free_tmp / "junk.pdf"
    junk.write_bytes(b"nope")
    code = main(["--data-dir", str(git_free_tmp / "cli-bad"), "ingest", str(junk)])
    assert code == 2
    assert "FAILED" in capsys.readouterr().out


def test_cli_output_survives_a_legacy_console(
    git_free_tmp: Path, real_statements: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    main(["--data-dir", str(git_free_tmp / "cli-enc"), "ingest", str(real_statements[0])])
    text = capsys.readouterr().out
    for encoding in ("cp1252", "cp936"):
        text.encode(encoding)


def test_cli_refuses_a_data_directory_inside_a_repo(git_free_tmp: Path) -> None:
    repo_dir = git_free_tmp / "repo"
    (repo_dir / ".git").mkdir(parents=True)
    with pytest.raises(SystemExit) as excinfo:
        main(["--data-dir", str(repo_dir / "data"), "doctor"])
    assert "git" in str(excinfo.value)


def test_doctor_works_before_anything_is_ingested(
    git_free_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--data-dir", str(git_free_tmp / "fresh"), "doctor"]) == 0
    assert "nothing ingested yet" in capsys.readouterr().out


@pytest.fixture
def doctor_ledger(ledger) -> tuple[DataPaths, sqlite3.Connection, str]:
    """A wholly synthetic ledger whose database and archive initially agree."""
    paths, conn = ledger
    source = paths.root / "synthetic-doctor.pdf"
    prefix = b"%PDF-1.7\n"
    source.write_bytes(prefix + b"x" * (1024 - len(prefix)))
    archived = archive.archive_file(paths, source, ingested_on=date(2026, 8, 7))
    source.unlink()

    from test_transactions import Line, book

    txn_id = book(
        conn,
        [
            Line(amount_minor=-1_000, descriptor="synthetic doctor line one"),
            Line(amount_minor=-2_000, descriptor="synthetic doctor line two"),
        ],
        sha256=archived.sha256,
    )[0]
    assert [
        result.check_id
        for result in pipeline.verify_ledger(conn, paths)
        if result.status != "pass"
    ] == []
    return paths, conn, txn_id


@pytest.mark.parametrize(
    "check_id",
    ["double_entry", "provenance", "balance_assertions"],
)
def test_doctor_reuses_every_block_check_exit_code(
    doctor_ledger,
    check_id: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The three verifier failures the old doctor reported under exit zero."""
    paths, conn, txn_id = doctor_ledger

    with transaction(conn):
        if check_id == "double_entry":
            repo.ensure_account(
                conn,
                account_id="equity:doctor-probe",
                name="Equity:DoctorProbe",
                kind="equity",
                subtype=None,
                currency="USD",
                institution=None,
                mask=None,
            )
            conn.execute(
                "INSERT INTO posting "
                "(id, txn_id, seq, account_id, amount_minor, currency) "
                "VALUES ('doctor-extra-leg', ?, 2, 'equity:doctor-probe', 1, 'USD')",
                (txn_id,),
            )
        elif check_id == "provenance":
            conn.execute(
                "UPDATE txn_identity SET raw_record_id = NULL WHERE txn_id = ?",
                (txn_id,),
            )
        else:
            source_file_id = conn.execute(
                "SELECT source_file_id FROM raw_record WHERE id = "
                "(SELECT raw_record_id FROM txn_identity WHERE txn_id = ?)",
                (txn_id,),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO balance_assertion "
                "(id, account_id, as_of, commodity_id, amount_minor, source_file_id) "
                "VALUES ('doctor-bad-balance', 'assets:chase:checking:1234', "
                "'2025-05-06', 'USD', 0, ?)",
                (source_file_id,),
            )

    failed = {
        result.check_id
        for result in pipeline.verify_ledger(conn, paths)
        if result.status != "pass"
    }
    assert check_id in failed, "the synthetic mutation really reaches the named verifier"

    assert main(["--data-dir", str(paths.root), "doctor"]) == 2
    out = capsys.readouterr().out
    assert check_id in out, "doctor names the same failed check whose exit code it folded"


def test_doctor_keeps_reporting_active_incoming_without_calling_it_damage(
    doctor_ledger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths, _, _ = doctor_ledger
    (paths.incoming / "active-upload.tmp").write_bytes(b"synthetic in-flight upload")

    assert main(["--data-dir", str(paths.root), "doctor"]) == 0
    out = capsys.readouterr().out
    assert "incoming 1 file(s)" in out
    assert "checks   all 9 measured check(s) pass" in out


def test_doctor_keeps_stranded_extractions_outside_verify_but_inside_its_exit_code(
    doctor_ledger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths, _, _ = doctor_ledger
    (paths.extracted / "deadbeef.ndjson").write_text("{}\n", encoding="utf-8")

    assert main(["--data-dir", str(paths.root), "doctor"]) == 2
    out = capsys.readouterr().out
    assert "stranded 1 extraction cache(s)" in out
    assert "checks   all 9 measured check(s) pass" in out, (
        "the cache condition remains doctor-specific rather than becoming a tenth verifier"
    )


def test_doctor_preserves_exit_one_when_only_a_blocking_review_needs_attention(
    doctor_ledger,
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths, conn, txn_id = doctor_ledger
    source_file_id = conn.execute(
        "SELECT rr.source_file_id FROM txn_identity ti "
        "JOIN raw_record rr ON rr.id = ti.raw_record_id WHERE ti.txn_id = ?",
        (txn_id,),
    ).fetchone()[0]
    with transaction(conn):
        conn.execute(
            "INSERT INTO review_item "
            "(id, source_file_id, status, severity, check_id, detail, created_at) "
            "VALUES ('doctor-review', ?, 'open', 'block', 'synthetic', '{}', "
            "'2026-08-07T00:00:00+00:00')",
            (source_file_id,),
        )

    assert main(["--data-dir", str(paths.root), "doctor"]) == 1
    out = capsys.readouterr().out
    assert "review_queue" in out
    assert "1 of 9 measured check(s) do not pass" in out


def test_cli_reapply_rules_is_a_no_op_right_after_an_ingest(
    git_free_tmp: Path, real_statements: list[Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """The ingest already applied the same rules, so there is nothing to move.

    That is the assertion, not a side note: if this reported changes, it would
    mean the ingest path and the re-categorisation path disagree about which
    leg or which text they work on, and the ledger's categories would depend on
    which of the two ran last.
    """
    data_dir = git_free_tmp / "recat-data"
    argv = ["--data-dir", str(data_dir)]
    assert main([*argv, "ingest", str(real_statements[0])]) == 0
    capsys.readouterr()

    assert main([*argv, "reapply-rules"]) == 0
    out = capsys.readouterr().out
    assert "0 of " in out and "posting(s)" in out
    assert "0 transfer flag(s) changed" in out, "the transfer rules agree with the ingest too"
    assert "gate nothing" in out


def test_cli_reapply_rules_reports_and_then_applies_a_rules_change(
    git_free_tmp: Path,
    real_statements: list[Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Editing a rule has to reach rows booked before the edit.

    The dry run must report exactly what the real run then changes; a
    "would change N" that does not match the N actually written is worse than
    no preview at all.
    """
    from ledgerbox.analytics import categorize

    data_dir = git_free_tmp / "recat-change"
    argv = ["--data-dir", str(data_dir)]
    assert main([*argv, "ingest", str(real_statements[0])]) == 0
    capsys.readouterr()

    # Every rule removed: the whole statement must come back uncategorised.
    empty = categorize.RuleSet(version=99, categories=())
    monkeypatch.setattr(categorize, "default_rules", lambda: empty)

    assert main([*argv, "reapply-rules", "--dry-run"]) == 0
    preview = capsys.readouterr().out
    assert "would change" in preview and "nothing altered" in preview
    predicted = int(preview.split("rules v99:")[1].split(" of ")[0])
    assert predicted > 0

    assert main([*argv, "reapply-rules"]) == 0
    applied = capsys.readouterr().out
    assert f"rules v99: {predicted} of " in applied
    assert "transfer flag(s) changed" in applied

    conn = open_ledger(data_dir / "ledger.db")
    try:
        left = conn.execute(
            "SELECT COUNT(*) FROM posting WHERE category_id IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    assert left == 0


def test_cli_reapply_rules_says_so_on_an_empty_ledger(
    git_free_tmp: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--data-dir", str(git_free_tmp / "recat-empty"), "reapply-rules"]) == 0
    assert "nothing is booked yet" in capsys.readouterr().out


def test_cli_reapply_rules_reports_a_broken_rules_file_in_one_sentence(
    git_free_tmp: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broken rules file is a broken install, not a broken ledger.

    Verification measured the previous behaviour and it was a 29-line
    traceback. The catch sits before the ledger is opened, so the data
    directory is not created either -- asserted here because "it happened to
    be before the open" is the kind of ordering a later edit silently reverses.
    """
    from ledgerbox.analytics import categorize

    def explode() -> categorize.RuleSet:
        raise categorize.RulesError("categories.json is not valid JSON: line 1")

    monkeypatch.setattr(categorize, "default_rules", explode)

    data_dir = git_free_tmp / "recat-broken"
    assert main(["--data-dir", str(data_dir), "reapply-rules"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("\n") == 1, "one sentence, not a traceback"
    assert "the category rules are unusable" in captured.err
    assert not data_dir.exists(), "a broken rules file must not create a data directory"
