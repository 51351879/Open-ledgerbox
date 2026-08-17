# SPDX-License-Identifier: AGPL-3.0-or-later
"""P0 acceptance item 11: the ledger must be rebuildable from archive/.

    Delete ledger.db, re-ingest everything in archive/, and the result must be
    row-for-row identical to what was there before.

This is the invariant that makes the database disposable and the archive
authoritative. It is also what forces every id in the system to be a pure
function of content — a single `uuid4()` anywhere would make it untestable, and
an untestable invariant is not an invariant.

Timestamps are excluded from the comparison and nothing else is: `ingested_at`
and `created_at` record when the work happened, which is genuinely different
the second time.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ledgerbox.config import DataPaths
from ledgerbox.db.migrate import open_ledger
from ledgerbox.db.repo import row_counts
from ledgerbox.ingest import pipeline
from ledgerbox.ingest.forget import forget_statement

#: Recorded at ingest time and legitimately different on a rebuild.
VOLATILE_COLUMNS = {"ingested_at", "created_at", "resolved_at", "reviewed_at"}

TABLES = (
    "source_file",
    "raw_record",
    "account",
    "commodity",
    "txn",
    "posting",
    "txn_identity",
    "balance_assertion",
    "review_item",
    "category",
    "category_override",
    "agent_proposal_run",
    "agent_category_proposal",
    "price",
    "lot",
    "corporate_action",
)


def _snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Every row of every table, sorted, with volatile columns removed."""
    snapshot: dict[str, list[tuple]] = {}
    for table in TABLES:
        columns = [
            row["name"]
            for row in conn.execute(f"PRAGMA table_info({table})")
            if row["name"] not in VOLATILE_COLUMNS
        ]
        selected = ", ".join(f'"{name}"' for name in columns)
        rows = conn.execute(f"SELECT {selected} FROM {table}").fetchall()
        snapshot[table] = sorted(tuple(row) for row in rows)
    return snapshot


@pytest.fixture
def rebuilt(git_free_tmp: Path, real_statements: list[Path]):
    paths = DataPaths.resolve(git_free_tmp / "data")

    conn = open_ledger(paths.db)
    outcomes = pipeline.ingest_paths(conn, paths, real_statements)
    assert all(o.status == pipeline.IMPORTED for o in outcomes)
    before = _snapshot(conn)
    conn.close()

    # Delete the database and everything derived from it. archive/ survives —
    # that is the point.
    for suffix in ("", "-wal", "-shm"):
        target = Path(str(paths.db) + suffix)
        if target.exists():
            target.unlink()
    for cached in paths.extracted.glob("*.ndjson"):
        cached.unlink()
    assert not paths.db.exists()

    archived = sorted(paths.archive.rglob("*.pdf"))
    assert len(archived) == len(real_statements)

    conn = open_ledger(paths.db)
    rebuild_outcomes = pipeline.ingest_paths(conn, paths, archived)
    after = _snapshot(conn)
    yield before, after, rebuild_outcomes, paths, conn
    conn.close()


def test_the_rebuild_imports_every_archived_statement(rebuilt) -> None:
    _, _, outcomes, _, _ = rebuilt
    assert [o.status for o in outcomes] == [pipeline.IMPORTED] * len(outcomes)


def test_the_rebuilt_ledger_is_row_for_row_identical(rebuilt) -> None:
    before, after, _, _, _ = rebuilt
    assert set(before) == set(after)
    for table in sorted(before):
        assert after[table] == before[table], f"{table} differs after rebuild"


def test_the_rebuild_reproduces_every_id_exactly(rebuilt) -> None:
    """Ids are content hashes; if any of them drifted the diff above would
    still pass for a table whose rows merely got renumbered."""
    before, after, _, _, _ = rebuilt
    for table in ("txn", "posting", "raw_record", "source_file", "balance_assertion"):
        assert [row[0] for row in after[table]] == [row[0] for row in before[table]]


def test_the_archive_is_untouched_by_the_rebuild(rebuilt) -> None:
    _, _, _, paths, _ = rebuilt
    archived = list(paths.archive.rglob("*.pdf"))
    assert len(archived) == 13, "re-ingesting from the archive must not copy it again"


def test_the_extracted_cache_is_regenerated(rebuilt) -> None:
    _, _, _, paths, _ = rebuilt
    assert len(list(paths.extracted.glob("*.ndjson"))) == 13


def test_the_rebuilt_ledger_still_verifies(rebuilt) -> None:
    _, _, _, paths, conn = rebuilt
    # With `paths`, so that `archived_not_recorded` runs: a rebuild is precisely
    # the operation whose whole claim is that the archive and the database agree.
    failed = [r.check_id for r in pipeline.verify_ledger(conn, paths) if r.status != "pass"]
    assert failed == []


# ---------------------------------------------------------------------------
# P2 M3: the same invariant, applied to a smaller archive
#
# Deleting a statement has to leave the ledger that ingesting the *remaining*
# archive into an empty database would produce. That is what turns "you can
# delete a month" from a convenience into something with a definition, and it
# is why `ingest.forget` exists as the inverse of the pipeline rather than as a
# handful of DELETE statements.
# ---------------------------------------------------------------------------

#: Everything a statement writes. The invariant is over exactly these, and the
#: comparison below covers all of them.
STATEMENT_DERIVED = (
    "source_file",
    "raw_record",
    "txn",
    "posting",
    "txn_identity",
    "balance_assertion",
    "review_item",
    "category_override",
)

#: Deliberately **not** compared — a stated property of the invariant rather
#: than a gap in the test.
#:
#: These three are reference data written at ingest time, not migration seeds
#: and not records of anything a statement said. ``ensure_account`` and
#: ``ensure_categories`` are idempotent, the ``category`` table is the rules
#: file's mirror (docs/STATUS.md §5.37), and an account row with no postings
#: left is not wrong. So deleting the *last* statement for an account leaves
#: its row standing while a rebuild from an emptied archive would create
#: neither it nor the categories — and deleting one of several shows no
#: discrepancy at all, because the survivors recreate both.
#:
#: The next test pins that half down rather than leaving it unexamined.
REFERENCE_TABLES = ("account", "category", "commodity")


def _ingest_all(paths: DataPaths, statements: list[Path]) -> sqlite3.Connection:
    conn = open_ledger(paths.db)
    outcomes = pipeline.ingest_paths(conn, paths, statements)
    assert [o.status for o in outcomes] == [pipeline.IMPORTED] * len(statements)
    return conn


def test_deleting_a_statement_leaves_what_ingesting_the_rest_would_produce(
    git_free_tmp: Path, real_statements: list[Path]
) -> None:
    """Delete one month here; ingest the other twelve into an empty database
    there; the statement-derived tables must be identical, row for row and id
    for id.

    The month deleted is one in the middle, which is the case that makes the
    ledger unable to reproduce the later printed balances. Both sides have that
    hole, in the same places — the deletion is not required to leave a *green*
    ledger, it is required to leave the *same* ledger.
    """
    paths = DataPaths.resolve(git_free_tmp / "deleted")
    conn = _ingest_all(paths, real_statements)
    ordered = [
        str(row["source_file_id"])
        for row in conn.execute("SELECT source_file_id FROM v_statement ORDER BY period_end")
    ]
    victim = ordered[len(ordered) // 2]

    forget_statement(conn, paths, victim)
    after_delete = _snapshot(conn)
    conn.close()

    remaining = sorted(paths.archive.rglob("*.pdf"))
    assert len(remaining) == len(real_statements) - 1, "the archived original went with it"

    fresh = DataPaths.resolve(git_free_tmp / "from-what-is-left")
    other = _ingest_all(fresh, remaining)
    rebuilt = _snapshot(other)
    other.close()

    for table in STATEMENT_DERIVED:
        assert after_delete[table] == rebuilt[table], f"{table} differs from the rebuild"
    for table in ("txn", "posting", "raw_record", "source_file", "balance_assertion"):
        assert [row[0] for row in after_delete[table]] == [row[0] for row in rebuilt[table]], (
            f"{table} ids drifted"
        )

    # Not part of the claim, and equal here anyway: twelve statements recreate
    # the account and every category. The test below is where the two come
    # apart, and it is the reason this is asserted separately rather than by
    # widening the loop above.
    for table in REFERENCE_TABLES:
        assert after_delete[table] == rebuilt[table], (
            f"{table} is reference data; the survivors happen to recreate it identically"
        )


def test_forgetting_every_statement_leaves_the_reference_rows_standing(
    git_free_tmp: Path, real_statements: list[Path]
) -> None:
    """The excluded half of the invariant, pinned rather than left unexamined.

    An empty archive rebuilds into an empty database — no account, no
    categories. Deleting every statement instead leaves those rows behind, and
    that is the documented behaviour rather than an oversight: they are
    idempotent reference data, and ``delete_statement`` removing them would be
    it deciding something about tables no statement owns.

    Deleted oldest first, so the opening entry is re-derived thirteen times.
    ``verify`` is asserted green after every single one: a ledger short its
    earliest months is still a ledger whose every remaining printed balance
    replays.
    """
    paths = DataPaths.resolve(git_free_tmp / "emptied")
    conn = _ingest_all(paths, real_statements)
    try:
        ordered = [
            str(row["source_file_id"])
            for row in conn.execute("SELECT source_file_id FROM v_statement ORDER BY period_end")
        ]
        for source_file_id in ordered:
            result = forget_statement(conn, paths, source_file_id)
            assert [check.check_id for check in result.failing_after] == [], (
                f"removing {source_file_id[:8]} left a check failing"
            )

        emptied = _snapshot(conn)
        for table in STATEMENT_DERIVED:
            assert emptied[table] == [], f"{table} still holds rows"
        for table in REFERENCE_TABLES:
            assert emptied[table] != [], f"{table} is reference data and must survive"

        counts = row_counts(conn)
        assert counts["account"] == 4, "three seeded counter-accounts plus the bank account"
        assert counts["category"] > 0
        assert list(paths.archive.rglob("*.pdf")) == []
        assert list(paths.extracted.glob("*.ndjson")) == []
        assert [r.check_id for r in pipeline.verify_ledger(conn, paths) if r.status != "pass"] == []
    finally:
        conn.close()
