# SPDX-License-Identifier: AGPL-3.0-or-later
"""M1: schema, migrations, connections.

The load-bearing test here is :func:`test_schema_matches_execution_plan`: it
executes the DDL *out of the design document* into a scratch database and
diffs it against what the migrations produce. The plan says "schema 严格按
§3.2"; this is that sentence, enforced.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from ledgerbox.config import DataDirRefused
from ledgerbox.db.connection import (
    connect,
    connect_read_only,
    dump_schema,
    integrity_check,
    transaction,
)
from ledgerbox.db.migrate import (
    MIGRATIONS_DIR,
    MigrationError,
    discover,
    migrate,
    open_ledger,
    pending,
    schema_version,
    split_statements,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN = REPO_ROOT / "docs" / "EXECUTION_PLAN.md"
SCHEMA_SNAPSHOT = REPO_ROOT / "src" / "ledgerbox" / "db" / "schema.sql"


def _stage_up_to(work: Path, label: str):  # type: ignore[no-untyped-def]
    """Stage every migration before ``label`` and hand back ``label`` itself.

    Naming the migration under test beats counting back from the tail: a later
    migration used to silently repoint each of these upgrade tests at a
    different pair, so they all failed at once for a reason none of them was
    about.
    """
    migrations = discover()
    index = [migration.label for migration in migrations].index(label)
    for migration in migrations[:index]:
        (work / migration.path.name).write_bytes(migration.path.read_bytes())
    return migrations[index]

MONEY_COLUMNS = [
    ("posting", "amount_minor"),
    ("posting", "cost_per_unit_minor"),
    ("posting", "price_per_unit_minor"),
    ("posting", "quantity_scaled"),
    ("lot", "cost_per_unit_minor"),
    ("balance_assertion", "amount_minor"),
    ("balance_assertion", "quantity_scaled"),
    ("price", "price_minor"),
    ("corporate_action", "cash_per_unit_minor"),
]


@pytest.fixture
def db(git_free_tmp: Path) -> sqlite3.Connection:
    conn = open_ledger(git_free_tmp / "ledger.db")
    yield conn
    conn.close()


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]


# --------------------------------------------------------------------------
# migration mechanics
# --------------------------------------------------------------------------


def test_migrations_are_contiguous_and_named() -> None:
    versions = [m.version for m in discover()]
    assert versions == list(range(1, len(versions) + 1))
    assert discover()[0].label == "0001_init"


def test_fresh_database_applies_everything(git_free_tmp: Path) -> None:
    conn = connect(git_free_tmp / "ledger.db")
    applied = migrate(conn)
    assert [m.label for m in applied] == [m.label for m in discover()]
    assert schema_version(conn) == len(applied)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(applied)
    conn.close()


def test_migrating_twice_is_a_no_op(db: sqlite3.Connection) -> None:
    before = _tables(db)
    assert migrate(db) == []
    assert pending(db) == []
    assert _tables(db) == before


def test_0010_database_upgrades_to_0011_with_existing_overrides_owned_by_human(
    git_free_tmp: Path,
) -> None:
    work = git_free_tmp / "migrations"
    work.mkdir()
    migrations = discover()
    assert migrations[10].label == "0011_agent_override_provenance"
    for migration in migrations[:10]:
        (work / migration.path.name).write_bytes(migration.path.read_bytes())

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)
    assert schema_version(conn) == 10
    conn.execute(
        "INSERT INTO category (id, parent_id, kind) VALUES ('synthetic-existing', NULL, 'expense')"
    )
    conn.execute(
        "INSERT INTO txn (id, date, flag, is_transfer, created_at) "
        "VALUES ('existing-txn', '2026-08-10', '*', 0, '2026-08-10T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO category_override (txn_id, category_id, created_at) "
        "VALUES ('existing-txn', 'synthetic-existing', '2026-08-10T00:00:00+00:00')"
    )

    latest = migrations[10]
    (work / latest.path.name).write_bytes(latest.path.read_bytes())
    applied = migrate(conn, work)

    assert [migration.label for migration in applied] == ["0011_agent_override_provenance"]
    assert schema_version(conn) == 11
    assert conn.execute(
        "SELECT kind FROM category WHERE id = 'synthetic-existing'"
    ).fetchone()[0] == "expense"
    override = conn.execute(
        "SELECT source, agent_run_id FROM category_override WHERE txn_id = 'existing-txn'"
    ).fetchone()
    assert tuple(override) == ("human", None)
    assert {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name LIKE 'agent_%'"
        )
    } == {
        "agent_proposal_run",
        "agent_category_proposal",
        "agent_triage_run",
        "agent_triage_item",
    }
    conn.close()


def test_0011_database_upgrades_to_0012_without_losing_proposal_audit_or_provenance(
    git_free_tmp: Path,
) -> None:
    work = git_free_tmp / "migrations"
    work.mkdir()
    latest = _stage_up_to(work, "0012_agent_proposal_v2")

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)
    assert schema_version(conn) == 11
    run_id = "sha256:" + "1" * 64
    revision = "sha256:" + "2" * 64
    group_id = "sha256:" + "3" * 64
    conn.execute(
        "INSERT INTO category (id, parent_id, kind) "
        "VALUES ('synthetic-existing', NULL, 'expense')"
    )
    conn.execute(
        "INSERT INTO txn (id, date, flag, is_transfer, created_at) "
        "VALUES ('existing-txn', '2026-08-10', '*', 0, '2026-08-10T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO agent_proposal_run "
        "(id, ledger_revision, schema_version, client, created_at, state) "
        "VALUES (?, ?, 1, 'codex', '2026-08-10T00:00:00+00:00', 'completed')",
        (run_id, revision),
    )
    conn.execute(
        "INSERT INTO agent_category_proposal "
        "(run_id, txn_id, group_id, suggested_category_id, outcome, "
        "applied_category_id, reviewed_at) "
        "VALUES (?, 'existing-txn', ?, 'synthetic-existing', 'accepted', "
        "'synthetic-existing', '2026-08-10T00:00:00+00:00')",
        (run_id, group_id),
    )
    conn.execute(
        "INSERT INTO category_override "
        "(txn_id, category_id, created_at, source, agent_run_id) "
        "VALUES ('existing-txn', 'synthetic-existing', "
        "'2026-08-10T00:00:00+00:00', 'agent', ?)",
        (run_id,),
    )

    (work / latest.path.name).write_bytes(latest.path.read_bytes())
    applied = migrate(conn, work)

    assert [migration.label for migration in applied] == ["0012_agent_proposal_v2"]
    assert schema_version(conn) == 12
    assert tuple(
        conn.execute(
            "SELECT schema_version, application_mode, state "
            "FROM agent_proposal_run WHERE id = ?",
            (run_id,),
        ).fetchone()
    ) == (1, None, "completed")
    assert tuple(
        conn.execute(
            "SELECT outcome, applied_category_id FROM agent_category_proposal "
            "WHERE run_id = ? AND txn_id = 'existing-txn'",
            (run_id,),
        ).fetchone()
    ) == ("accepted", "synthetic-existing")
    assert tuple(
        conn.execute(
            "SELECT source, agent_run_id FROM category_override "
            "WHERE txn_id = 'existing-txn'"
        ).fetchone()
    ) == ("agent", run_id)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    conn.execute(
        "INSERT INTO agent_proposal_run "
        "(id, ledger_revision, schema_version, application_mode, client, created_at, state) "
        "VALUES (?, ?, 2, 'automatic', 'codex', '2026-08-10T00:00:01+00:00', 'open')",
        ("sha256:" + "4" * 64, revision),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO agent_proposal_run "
            "(id, ledger_revision, schema_version, application_mode, client, created_at) "
            "VALUES (?, ?, 1, 'automatic', 'codex', '2026-08-10T00:00:02+00:00')",
            ("sha256:" + "5" * 64, revision),
        )
    conn.close()


def test_0012_database_upgrades_to_0013_with_disconnected_local_policy(
    git_free_tmp: Path,
) -> None:
    work = git_free_tmp / "migrations"
    work.mkdir()
    latest = _stage_up_to(work, "0013_agent_center")

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)
    assert schema_version(conn) == 12
    run_id = "sha256:" + "6" * 64
    revision = "sha256:" + "7" * 64
    conn.execute(
        "INSERT INTO agent_proposal_run "
        "(id, ledger_revision, schema_version, application_mode, client, created_at, state) "
        "VALUES (?, ?, 2, 'automatic', 'codex', '2026-08-10T00:00:00+00:00', 'completed')",
        (run_id, revision),
    )

    (work / latest.path.name).write_bytes(latest.path.read_bytes())
    applied = migrate(conn, work)

    assert [migration.label for migration in applied] == ["0013_agent_center"]
    assert schema_version(conn) == 13
    assert tuple(
        conn.execute(
            "SELECT schema_version, application_mode, state "
            "FROM agent_proposal_run WHERE id = ?",
            (run_id,),
        ).fetchone()
    ) == (2, "automatic", "completed")
    assert tuple(
        conn.execute(
            "SELECT selected_client, application_mode, enabled, auto_classify_new_imports "
            "FROM agent_local_policy WHERE id = 1"
        ).fetchone()
    ) == (None, "automatic", 0, 1)
    assert conn.execute("SELECT COUNT(*) FROM agent_local_session").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_0013_database_upgrades_to_0014_with_empty_persistent_job_queue(
    git_free_tmp: Path,
) -> None:
    work = git_free_tmp / "migrations"
    work.mkdir()
    latest = _stage_up_to(work, "0014_agent_classification_jobs")

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)
    assert schema_version(conn) == 13
    conn.execute(
        "INSERT INTO source_file "
        "(id, sha256, rel_path, media_type, byte_len, ingested_at) "
        "VALUES ('existing', 'existing', '2026/08/existing.pdf', "
        "'application/pdf', 1, '2026-08-10T00:00:00+00:00')"
    )

    (work / latest.path.name).write_bytes(latest.path.read_bytes())
    applied = migrate(conn, work)

    assert [migration.label for migration in applied] == [
        "0014_agent_classification_jobs"
    ]
    assert schema_version(conn) == 14
    assert conn.execute("SELECT COUNT(*) FROM source_file").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM agent_classification_job").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM agent_local_session").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_0014_database_upgrades_to_0015_with_unattributed_existing_jobs(
    git_free_tmp: Path,
) -> None:
    work = git_free_tmp / "migrations"
    work.mkdir()
    latest = _stage_up_to(work, "0015_agent_job_attribution")

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)
    assert schema_version(conn) == 14
    conn.execute(
        "INSERT INTO source_file "
        "(id, sha256, rel_path, media_type, byte_len, ingested_at) "
        "VALUES ('existing', 'existing', '2026/08/existing.pdf', "
        "'application/pdf', 1, '2026-08-10T00:00:00+00:00')"
    )
    job_id = "job-" + "8" * 32
    conn.execute(
        "INSERT INTO agent_classification_job "
        "(id, trigger_source_file_id, client, application_mode, queued_at) "
        "VALUES (?, 'existing', 'codex', 'automatic', '2026-08-10T00:00:00+00:00')",
        (job_id,),
    )

    (work / latest.path.name).write_bytes(latest.path.read_bytes())
    applied = migrate(conn, work)

    assert [migration.label for migration in applied] == ["0015_agent_job_attribution"]
    assert schema_version(conn) == 15
    assert tuple(
        conn.execute(
            "SELECT state, session_id, proposal_run_id "
            "FROM agent_classification_job WHERE id = ?",
            (job_id,),
        ).fetchone()
    ) == ("queued", None, None)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_0018_database_upgrades_to_0019_keeping_taught_rules_and_derived_answers(
    git_free_tmp: Path,
) -> None:
    """The rebuild must carry every rule and its derivations across unchanged."""
    work = git_free_tmp / "migrations"
    work.mkdir()
    latest = _stage_up_to(work, "0019_standing_prefix_rules")

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)
    assert schema_version(conn) == 18
    conn.execute(
        "INSERT INTO category (id, parent_id, kind) VALUES ('synthetic-cat', NULL, 'expense')"
    )
    conn.execute(
        "INSERT INTO txn (id, date, flag, is_transfer, created_at) "
        "VALUES ('existing-txn', '2026-08-10', '*', 0, '2026-08-10T00:00:00+00:00')"
    )
    rule_id = "lr-" + "7" * 32
    conn.execute(
        "INSERT INTO learned_rule (id, template, template_version, category_id, "
        "source, agent_run_id, learned_from_txn_id, created_at) "
        "VALUES (?, 'SYNTHETIC TEMPLATE #', 1, 'synthetic-cat', 'human', NULL, "
        "'existing-txn', '2026-08-10T00:00:00+00:00')",
        (rule_id,),
    )
    conn.execute(
        "INSERT INTO txn (id, date, flag, is_transfer, created_at) "
        "VALUES ('claimed-txn', '2026-08-11', '*', 0, '2026-08-11T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO category_override (txn_id, category_id, created_at, source, "
        "learned_rule_id) VALUES ('claimed-txn', 'synthetic-cat', "
        "'2026-08-11T00:00:00+00:00', 'learned', ?)",
        (rule_id,),
    )

    (work / latest.path.name).write_bytes(latest.path.read_bytes())
    applied = migrate(conn, work)

    assert [migration.label for migration in applied] == ["0019_standing_prefix_rules"]
    assert schema_version(conn) == 19
    assert tuple(
        conn.execute(
            "SELECT match_kind, template, source, learned_from_txn_id "
            "FROM learned_rule WHERE id = ?",
            (rule_id,),
        ).fetchone()
    ) == ("template", "SYNTHETIC TEMPLATE #", "human", "existing-txn")
    assert tuple(
        conn.execute(
            "SELECT source, learned_rule_id FROM category_override WHERE txn_id = 'claimed-txn'"
        ).fetchone()
    ) == ("learned", rule_id)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_0015_database_upgrades_to_0016_leaving_older_jobs_without_client_evidence(
    git_free_tmp: Path,
) -> None:
    """Runs finished before this migration cannot be given an outcome after the fact."""
    work = git_free_tmp / "migrations"
    work.mkdir()
    latest = _stage_up_to(work, "0016_agent_job_client_evidence")

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)
    assert schema_version(conn) == 15
    conn.execute(
        "INSERT INTO source_file "
        "(id, sha256, rel_path, media_type, byte_len, ingested_at) "
        "VALUES ('existing', 'existing', '2026/08/existing.pdf', "
        "'application/pdf', 1, '2026-08-10T00:00:00+00:00')"
    )
    job_id = "job-" + "4" * 32
    conn.execute(
        "INSERT INTO agent_classification_job "
        "(id, trigger_source_file_id, client, application_mode, state, candidate_count, "
        "submitted_count, applied_count, omitted_count, queued_at, started_at, finished_at) "
        "VALUES (?, 'existing', 'codex', 'automatic', 'partial', 120, 2, 2, 118, "
        "'2026-08-10T00:00:00+00:00', '2026-08-10T00:00:01+00:00', '2026-08-10T00:20:00+00:00')",
        (job_id,),
    )

    (work / latest.path.name).write_bytes(latest.path.read_bytes())
    applied = migrate(conn, work)

    assert [migration.label for migration in applied] == ["0016_agent_job_client_evidence"]
    assert schema_version(conn) == 16
    assert tuple(
        conn.execute(
            "SELECT state, omitted_count, client_outcome, client_exit_code, client_log_excerpt "
            "FROM agent_classification_job WHERE id = ?",
            (job_id,),
        ).fetchone()
    ) == ("partial", 118, None, None, None)
    refused = pytest.raises(sqlite3.IntegrityError)
    with refused:
        conn.execute(
            "UPDATE agent_classification_job SET client_outcome = 'vanished' WHERE id = ?",
            (job_id,),
        )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_0016_database_upgrades_to_0017_keeping_existing_jobs_as_import_round_one(
    git_free_tmp: Path,
) -> None:
    work = git_free_tmp / "migrations"
    work.mkdir()
    latest = _stage_up_to(work, "0017_agent_job_rounds")

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)
    assert schema_version(conn) == 16
    conn.execute(
        "INSERT INTO source_file "
        "(id, sha256, rel_path, media_type, byte_len, ingested_at) "
        "VALUES ('existing', 'existing', '2026/08/existing.pdf', "
        "'application/pdf', 1, '2026-08-10T00:00:00+00:00')"
    )
    job_id = "job-" + "3" * 32
    conn.execute(
        "INSERT INTO agent_classification_job "
        "(id, trigger_source_file_id, client, application_mode, queued_at) "
        "VALUES (?, 'existing', 'codex', 'automatic', '2026-08-10T00:00:00+00:00')",
        (job_id,),
    )

    (work / latest.path.name).write_bytes(latest.path.read_bytes())
    applied = migrate(conn, work)

    assert [migration.label for migration in applied] == ["0017_agent_job_rounds"]
    assert schema_version(conn) == 17
    assert tuple(
        conn.execute(
            "SELECT trigger_source_file_id, trigger_kind, round_index, state "
            "FROM agent_classification_job WHERE id = ?",
            (job_id,),
        ).fetchone()
    ) == ("existing", "import", 1, "queued")

    # Only an import names a statement, and an import must name one.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO agent_classification_job "
            "(id, trigger_source_file_id, trigger_kind, client, application_mode, queued_at) "
            "VALUES (?, 'existing', 'manual', 'codex', 'automatic', "
            "'2026-08-10T00:00:00+00:00')",
            ("job-" + "2" * 32,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO agent_classification_job "
            "(id, trigger_source_file_id, trigger_kind, client, application_mode, queued_at) "
            "VALUES (?, NULL, 'import', 'codex', 'automatic', '2026-08-10T00:00:00+00:00')",
            ("job-" + "1" * 32,),
        )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_0017_database_upgrades_to_0018_keeping_override_provenance_untouched(
    git_free_tmp: Path,
) -> None:
    work = git_free_tmp / "migrations"
    work.mkdir()
    latest = _stage_up_to(work, "0018_learned_rules")

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)
    assert schema_version(conn) == 17
    conn.execute(
        "INSERT INTO category (id, parent_id, kind) "
        "VALUES ('synthetic-existing', NULL, 'expense')"
    )
    conn.execute(
        "INSERT INTO txn (id, date, flag, is_transfer, created_at) "
        "VALUES ('existing-txn', '2026-08-10', '*', 0, '2026-08-10T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO category_override (txn_id, category_id, created_at, source) "
        "VALUES ('existing-txn', 'synthetic-existing', '2026-08-10T00:00:00+00:00', 'human')"
    )

    (work / latest.path.name).write_bytes(latest.path.read_bytes())
    applied = migrate(conn, work)

    assert [migration.label for migration in applied] == ["0018_learned_rules"]
    assert schema_version(conn) == 18
    assert tuple(
        conn.execute(
            "SELECT source, agent_run_id, learned_rule_id, decided_by FROM category_override "
            "JOIN v_txn_category USING (txn_id) WHERE txn_id = 'existing-txn'"
        ).fetchone()
    ) == ("human", None, None, "override")
    assert conn.execute("SELECT COUNT(*) FROM learned_rule").fetchone()[0] == 0

    # A learned override must name its rule, and a machine-applied answer can
    # never pass for a person's: the view has to say 'learned'.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO category_override (txn_id, category_id, created_at, source) "
            "VALUES ('existing-txn', 'synthetic-existing', "
            "'2026-08-10T00:00:01+00:00', 'learned')"
        )
    conn.execute(
        "INSERT INTO learned_rule "
        "(id, template, template_version, category_id, source, learned_from_txn_id, created_at) "
        "VALUES (?, 'SQ *SYNTHETIC', 1, 'synthetic-existing', 'human', 'existing-txn', "
        "'2026-08-10T00:00:01+00:00')",
        ("lr-" + "a" * 32,),
    )
    conn.execute(
        "INSERT INTO txn (id, date, flag, is_transfer, created_at) "
        "VALUES ('later-txn', '2026-08-11', '*', 0, '2026-08-11T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO category_override "
        "(txn_id, category_id, created_at, source, learned_rule_id) "
        "VALUES ('later-txn', 'synthetic-existing', '2026-08-11T00:00:01+00:00', "
        "'learned', ?)",
        ("lr-" + "a" * 32,),
    )
    assert conn.execute(
        "SELECT decided_by FROM v_txn_category WHERE txn_id = 'later-txn'"
    ).fetchone()[0] == "learned"
    assert conn.execute(
        "SELECT decided_by FROM v_txn_transfer WHERE txn_id = 'later-txn'"
    ).fetchone()[0] == "learned"
    # One template learns one answer per template version.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO learned_rule "
            "(id, template, template_version, category_id, source, learned_from_txn_id, "
            "created_at) VALUES (?, 'SQ *SYNTHETIC', 1, 'synthetic-existing', 'human', "
            "'existing-txn', '2026-08-10T00:00:02+00:00')",
            ("lr-" + "b" * 32,),
        )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_editing_an_applied_migration_is_refused(git_free_tmp: Path) -> None:
    work = git_free_tmp / "migrations"
    work.mkdir()
    for src in sorted(MIGRATIONS_DIR.glob("*.sql")):
        (work / src.name).write_bytes(src.read_bytes())

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)

    target = work / "0002_indexes.sql"
    target.write_text(target.read_text(encoding="utf-8") + "\n-- tampered\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="modified after it ran"):
        pending(conn, work)
    conn.close()


def test_database_newer_than_code_is_refused(git_free_tmp: Path) -> None:
    work = git_free_tmp / "migrations"
    work.mkdir()
    names = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for src in names:
        (work / src.name).write_bytes(src.read_bytes())

    conn = connect(git_free_tmp / "ledger.db")
    migrate(conn, work)
    (work / names[-1].name).unlink()

    with pytest.raises(MigrationError, match="newer than this code"):
        pending(conn, work)
    conn.close()


def test_a_failing_migration_rolls_back_whole_file(git_free_tmp: Path) -> None:
    work = git_free_tmp / "migrations"
    work.mkdir()
    (work / "0001_init.sql").write_text(
        "CREATE TABLE good (id TEXT PRIMARY KEY) STRICT;\n"
        "CREATE TABLE bad (id TEXT PRIMARY KEY) STRICT;\n"
        "SELECT this_function_does_not_exist();\n",
        encoding="utf-8",
    )
    conn = connect(git_free_tmp / "ledger.db")
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, work)
    assert _tables(conn) == ["schema_migration"], "partial DDL must not survive"
    assert schema_version(conn) == 0
    conn.close()


def test_split_statements_ignores_semicolons_in_literals() -> None:
    parts = split_statements(
        "CREATE TABLE t (a TEXT DEFAULT 'x;y') STRICT;\n"
        "-- a trailing comment\n"
        "INSERT INTO t (a) VALUES ('p;q');\n"
        "-- and a final one, unterminated\n"
    )
    assert len(parts) == 2
    assert parts[0].endswith(";")
    assert "'x;y'" in parts[0]


def test_split_statements_rejects_truncated_sql() -> None:
    with pytest.raises(MigrationError, match="incomplete"):
        split_statements("CREATE TABLE t (a TEXT\n")


# --------------------------------------------------------------------------
# what the schema must guarantee
# --------------------------------------------------------------------------


def test_every_table_is_strict(db: sqlite3.Connection) -> None:
    for name in _tables(db):
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]
        assert "STRICT" in sql.upper(), f"{name} is not STRICT"


def test_no_floating_point_column_anywhere(db: sqlite3.Connection) -> None:
    offenders = [
        f"{table}.{column['name']}:{column['type']}"
        for table in _tables(db)
        for column in db.execute(f"PRAGMA table_info({table})")
        if column["type"].upper() in {"REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL"}
    ]
    assert offenders == []


def test_money_columns_are_integers(db: sqlite3.Connection) -> None:
    for table, column in MONEY_COLUMNS:
        info = {row["name"]: row["type"] for row in db.execute(f"PRAGMA table_info({table})")}
        assert info[column] == "INTEGER", f"{table}.{column} is {info[column]}"


def test_strict_typing_rejects_text_in_an_integer_column(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO source_file "
            "(id, sha256, rel_path, media_type, byte_len, ingested_at) "
            "VALUES ('x', 'x', 'a.pdf', 'application/pdf', 'not-a-number', '2026-01-01')"
        )


def test_foreign_keys_are_enforced(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        db.execute(
            "INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency) "
            "VALUES ('p1', 'no-such-txn', 0, 'income:uncategorized', 1, 'USD')"
        )


def test_wal_and_integrity(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert integrity_check(db) == []


def test_seed_rows(db: sqlite3.Connection) -> None:
    assert db.execute("SELECT scale FROM commodity WHERE id='USD'").fetchone()[0] == 2
    seeded = {row[0] for row in db.execute("SELECT id FROM account")}
    assert seeded == {
        "income:uncategorized",
        "expenses:uncategorized",
        "equity:opening-balances",
    }
    assert all(
        row[0] == 0 for row in db.execute("SELECT is_own_account FROM account")
    ), "counter-accounts are not your accounts; transfer detection depends on it"


def test_views_exist_and_are_empty_on_a_fresh_ledger(db: sqlite3.Connection) -> None:
    views = {
        row[0]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='view'")
    }
    assert views == {
        "v_statement",
        "v_transaction",
        "v_txn_category",
        "v_txn_transfer",
        "v_cashflow_monthly",
        "v_cashflow_line",
        "v_category_spend",
        "v_identity_without_source",
        "v_unbalanced_txn",
    }
    for view in sorted(views):
        assert db.execute(f"SELECT COUNT(*) FROM {view}").fetchone()[0] == 0


def test_statement_month_view_uses_period_end(db: sqlite3.Connection) -> None:
    """A period ending 2025-06-03 is 2025-06, not 2025-05. This is the bug."""
    with transaction(db):
        db.execute(
            "INSERT INTO source_file "
            "(id, sha256, rel_path, media_type, byte_len, period_start, period_end, ingested_at) "
            "VALUES ('f1','f1','2025/06/f1.pdf','application/pdf',1,"
            "'2025-05-04','2025-06-03','2026-01-01T00:00:00+00:00')"
        )
    assert db.execute("SELECT statement_month FROM v_statement").fetchone()[0] == "2025-06"


# --------------------------------------------------------------------------
# fidelity to the design document
# --------------------------------------------------------------------------


def _plan_ddl() -> str:
    text = PLAN.read_text(encoding="utf-8")
    blocks = re.findall(r"```sql\n(.*?)```", text, flags=re.DOTALL)
    for block in blocks:
        if "CREATE TABLE source_file" in block:
            return block
    raise AssertionError("§3.2 DDL block not found in docs/EXECUTION_PLAN.md")


def test_schema_matches_execution_plan(db: sqlite3.Connection) -> None:
    """Apply the plan's own DDL to a scratch DB and diff, table by table."""
    reference = sqlite3.connect(":memory:")
    reference.row_factory = sqlite3.Row
    reference.executescript(_plan_ddl())

    planned = {
        row[0]
        for row in reference.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    ours = set(_tables(db))
    assert planned - ours == set(), f"tables missing from the migrations: {planned - ours}"
    # The execution plan predates migration-owned bookkeeping, the A7.4
    # persistent classification queue, and the A7.7 learning loop. All are
    # deliberate post-plan additions.
    post_plan_tables = {"schema_migration", "agent_classification_job", "learned_rule"}
    assert ours - planned == post_plan_tables, (
        f"unexpected tables added beyond §3.2: {ours - planned - post_plan_tables}"
    )

    for table in sorted(planned):
        want = [tuple(r) for r in reference.execute(f"PRAGMA table_info({table})")]
        got = [tuple(r) for r in db.execute(f"PRAGMA table_info({table})")]
        assert got == want, f"column drift in {table}"

        want_fks = sorted(
            tuple(r)[2:] for r in reference.execute(f"PRAGMA foreign_key_list({table})")
        )
        got_fks = sorted(tuple(r)[2:] for r in db.execute(f"PRAGMA foreign_key_list({table})"))
        assert got_fks == want_fks, f"foreign-key drift in {table}"

    reference.close()


def test_plan_indexes_keep_their_uniqueness_and_partiality(db: sqlite3.Connection) -> None:
    """§3.2 declares one index and both of its properties carry weight.

    ``txn_identity_src`` being UNIQUE is the only thing stopping a duplicate
    FITID from being booked twice; its ``WHERE source_id IS NOT NULL`` is what
    stops many NULL ids from colliding with each other. Comparing tables alone
    would let either be dropped silently.
    """
    reference = sqlite3.connect(":memory:")
    reference.row_factory = sqlite3.Row
    reference.executescript(_plan_ddl())

    planned = {
        row["name"]: row["tbl_name"]
        for row in reference.execute(
            "SELECT name, tbl_name FROM sqlite_master "
            "WHERE type='index' AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert planned, "§3.2 declares at least one index; extraction found none"

    for name, table in planned.items():
        want = next(
            r for r in reference.execute(f"PRAGMA index_list({table})") if r["name"] == name
        )
        got_rows = [r for r in db.execute(f"PRAGMA index_list({table})") if r["name"] == name]
        assert got_rows, f"index {name} is missing from the migrations"
        got = got_rows[0]
        assert got["unique"] == want["unique"], f"{name} lost UNIQUE"
        assert got["partial"] == want["partial"], f"{name} lost its WHERE clause"
        assert [tuple(r) for r in db.execute(f"PRAGMA index_info({name})")] == [
            tuple(r) for r in reference.execute(f"PRAGMA index_info({name})")
        ], f"{name} indexes different columns"
        assert _normalised_ddl(db, name) == _normalised_ddl(reference, name), f"DDL drift in {name}"

    reference.close()


def _normalised_ddl(conn: sqlite3.Connection, name: str) -> str:
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]
    sql = re.sub(r"--[^\n]*", " ", sql)
    return re.sub(r"\s+", " ", sql).strip().rstrip(";")


def test_plan_check_constraints_are_present(db: sqlite3.Connection) -> None:
    """table_info cannot see CHECKs, so compare the normalised DDL text."""
    reference = sqlite3.connect(":memory:")
    reference.executescript(_plan_ddl())

    for table in sorted(
        row[0]
        for row in reference.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ):
        assert _normalised_ddl(db, table) == _normalised_ddl(reference, table), (
            f"DDL drift in {table}"
        )
    reference.close()


def test_schema_snapshot_is_current(db: sqlite3.Connection) -> None:
    """db/schema.sql is generated; a stale copy is a lie in the diff.

    Newlines are normalised so a CRLF checkout fails the *lint*, not this.
    """
    snapshot = SCHEMA_SNAPSHOT.read_text(encoding="utf-8").replace("\r\n", "\n")
    body = snapshot.split("\n\n", 1)[1] if "\n\n" in snapshot else snapshot
    assert body.strip() == dump_schema(db).replace("\r\n", "\n").strip(), (
        "schema.sql is stale — run `python tools/dump_schema.py`"
    )


# --------------------------------------------------------------------------
# read-only handle
# --------------------------------------------------------------------------


def test_read_only_handle_cannot_write(git_free_tmp: Path) -> None:
    path = git_free_tmp / "ledger.db"
    open_ledger(path).close()

    ro = connect_read_only(path)
    assert ro.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("INSERT INTO commodity (id, kind, scale) VALUES ('EUR','currency',2)")
    assert ro.execute("SELECT COUNT(*) FROM commodity").fetchone()[0] == 1
    ro.close()


def test_read_only_handle_survives_cjk_paths(git_free_tmp: Path) -> None:
    path = git_free_tmp / "中文 目录" / "ledger.db"
    open_ledger(path).close()
    ro = connect_read_only(path)
    assert ro.execute("SELECT COUNT(*) FROM account").fetchone()[0] == 3
    ro.close()


def test_read_only_context_manager_closes_the_windows_file_handle(
    git_free_tmp: Path,
) -> None:
    path = git_free_tmp / "ledger.db"
    open_ledger(path).close()

    with connect_read_only(path) as ro:
        assert ro.execute("SELECT COUNT(*) FROM commodity").fetchone()[0] == 1

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        ro.execute("SELECT 1")


# --------------------------------------------------------------------------
# the guard applies to the ledger file itself
# --------------------------------------------------------------------------


def test_connect_refuses_to_create_a_ledger_inside_a_repo(git_free_tmp: Path) -> None:
    """ledger.db is the most sensitive artefact here; it gets the same guard."""
    repo = git_free_tmp / "repo"
    (repo / ".git").mkdir(parents=True)
    with pytest.raises(DataDirRefused):
        connect(repo / "data" / "ledger.db")
    with pytest.raises(DataDirRefused):
        open_ledger(repo / "data" / "ledger.db")
    assert not (repo / "data").exists(), "refused before creating anything"


def test_connect_guard_can_be_waived_explicitly(git_free_tmp: Path) -> None:
    """Throwaway probe databases opt out at the call site, visibly."""
    repo = git_free_tmp / "repo"
    (repo / ".git").mkdir(parents=True)
    conn = connect(repo / "probe.db", guard=False)
    conn.close()
    assert (repo / "probe.db").exists()


def test_read_only_open_is_not_guarded(git_free_tmp: Path) -> None:
    """Reading a ledger that already sits in a repo writes nothing."""
    repo = git_free_tmp / "repo"
    (repo / ".git").mkdir(parents=True)
    open_ledger(repo / "ledger.db", guard=False).close()
    ro = connect_read_only(repo / "ledger.db")
    assert ro.execute("SELECT COUNT(*) FROM account").fetchone()[0] == 3
    ro.close()


# --------------------------------------------------------------------------
# views must not hide rows
# --------------------------------------------------------------------------


def _book_one(conn: sqlite3.Connection, *, txn: str, amount: int, raw_record: bool) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO txn (id, date, created_at) VALUES (?, '2025-01-02', '2026-01-01')",
            (txn,),
        )
        conn.execute(
            "INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency) "
            "VALUES (?, ?, 0, 'income:uncategorized', ?, 'USD')",
            (f"{txn}:0", txn, amount),
        )
        raw_id = None
        if raw_record:
            conn.execute(
                "INSERT OR IGNORE INTO source_file "
                "(id, sha256, rel_path, media_type, byte_len, period_start, period_end, "
                " ingested_at) VALUES ('f1','f1','2025/01/f1.pdf','application/pdf',1,"
                "'2024-12-04','2025-01-03','2026-01-01')"
            )
            raw_id = f"f1:{txn}"
            conn.execute(
                "INSERT INTO raw_record "
                "(id, source_file_id, record_index, kind, payload, parser_id, parser_version) "
                "VALUES (?, 'f1', ?, 'stmttrn', '{}', 'test', '1')",
                (raw_id, len(txn)),
            )
        conn.execute(
            "INSERT INTO txn_identity (txn_id, account_id, source_system, natural_key, "
            "natural_key_version, occurrence_index, raw_descriptor, raw_record_id) "
            "VALUES (?, 'income:uncategorized', 'pdf', ?, 1, 0, 'X', ?)",
            (txn, f"nk-{txn}", raw_id),
        )


def test_v_transaction_does_not_silently_drop_rows_without_provenance(
    db: sqlite3.Connection,
) -> None:
    """txn_identity.raw_record_id is nullable; an INNER JOIN would under-report.

    Under-reporting that looks self-consistent is the exact failure this
    project exists to catch, so it is a test rather than a comment.
    """
    _book_one(db, txn="with-source", amount=1000, raw_record=True)
    _book_one(db, txn="no-source", amount=1000, raw_record=False)

    identities = db.execute("SELECT COUNT(*) FROM txn_identity").fetchone()[0]
    in_view = db.execute("SELECT COUNT(*) FROM v_transaction").fetchone()[0]
    assert in_view == identities == 2

    total = db.execute("SELECT SUM(amount_minor) FROM v_transaction").fetchone()[0]
    assert total == db.execute("SELECT SUM(amount_minor) FROM posting").fetchone()[0] == 2000


def test_missing_provenance_is_visible_rather_than_invisible(db: sqlite3.Connection) -> None:
    _book_one(db, txn="no-source", amount=1000, raw_record=False)
    orphans = db.execute("SELECT txn_id FROM v_identity_without_source").fetchall()
    assert [row[0] for row in orphans] == ["no-source"]
    assert db.execute(
        "SELECT statement_month FROM v_transaction WHERE txn_id='no-source'"
    ).fetchone()[0] is None


# --------------------------------------------------------------------------
# P2 M2.2: one definition of "is this a transfer"
# --------------------------------------------------------------------------


def _seed_transfer_categories(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT OR IGNORE INTO category (id, parent_id, kind) VALUES "
            "('probe-transfer', NULL, 'transfer'), ('probe-dining', NULL, 'expense')"
        )


def _override(conn: sqlite3.Connection, txn_id: str, category_id: str) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO category_override (txn_id, category_id, created_at) "
            "VALUES (?, ?, '2026-08-04T00:00:00+00:00') "
            "ON CONFLICT(txn_id) DO UPDATE SET category_id = excluded.category_id",
            (txn_id, category_id),
        )


def _effective(conn: sqlite3.Connection) -> dict[str, tuple[int, str]]:
    return {
        row["txn_id"]: (row["is_transfer"], row["decided_by"])
        for row in conn.execute("SELECT * FROM v_txn_transfer")
    }


def test_the_transfer_predicate_prefers_a_person_over_a_rule(db: sqlite3.Connection) -> None:
    """Both directions, because a rule that cannot be overruled is not a heuristic.

    Marking a transfer removes money from the headline figures, so a false
    positive shrinks reported spending silently. The way out of that has to be
    reachable, and it has to work in both directions: mark one the rules missed,
    un-mark one they got wrong.
    """
    _seed_transfer_categories(db)
    # Names of different lengths: `_book_one` derives record_index from len(txn),
    # and raw_record is UNIQUE on (source_file_id, record_index).
    _book_one(db, txn="ruled", amount=-5000, raw_record=True)
    _book_one(db, txn="unruled", amount=-5000, raw_record=True)
    with transaction(db):
        db.execute("UPDATE txn SET is_transfer = 1 WHERE id = 'ruled'")

    assert _effective(db) == {"ruled": (1, "rule"), "unruled": (0, "rule")}

    _override(db, "unruled", "probe-transfer")
    assert _effective(db)["unruled"] == (1, "override")

    _override(db, "ruled", "probe-dining")
    assert _effective(db)["ruled"] == (0, "override"), "a person can overrule a rule"

    with transaction(db):
        db.execute("DELETE FROM category_override")
    assert _effective(db) == {"ruled": (1, "rule"), "unruled": (0, "rule")}


def test_agent_override_source_reaches_category_and_transfer_views(
    db: sqlite3.Connection,
) -> None:
    _seed_transfer_categories(db)
    _book_one(db, txn="agent-transfer", amount=-5000, raw_record=True)
    run_id = "sha256:" + "a" * 64
    revision = "sha256:" + "b" * 64
    with transaction(db):
        db.execute(
            "INSERT INTO agent_proposal_run "
            "(id, ledger_revision, schema_version, client, created_at, state) "
            "VALUES (?, ?, 1, 'claude-code', '2026-08-10T00:00:00+00:00', 'completed')",
            (run_id, revision),
        )
        db.execute(
            "INSERT INTO category_override "
            "(txn_id, category_id, created_at, source, agent_run_id) "
            "VALUES ('agent-transfer', 'probe-transfer', '2026-08-10T00:00:00+00:00', "
            "'agent', ?)",
            (run_id,),
        )

    row = db.execute(
        "SELECT category_decided_by, transfer_decided_by "
        "FROM v_transaction WHERE txn_id = 'agent-transfer'"
    ).fetchone()
    assert tuple(row) == ("agent", "agent")


def test_the_predicate_covers_every_transaction_exactly_once(db: sqlite3.Connection) -> None:
    """``v_transaction`` joins it INNER, so a missing row would drop a transaction.

    0004's own comment explains at length why the joins to ``raw_record`` and
    ``source_file`` are LEFT: an INNER join there would drop rows *silently* and
    the cashflow view would under-report with no error anywhere. 0005 adds an
    INNER join, which is only safe because this view selects ``FROM txn`` with
    no filter -- asserted here rather than trusted.
    """
    _book_one(db, txn="a", amount=-1000, raw_record=True)
    _book_one(db, txn="bb", amount=-1000, raw_record=False)

    txns = db.execute("SELECT COUNT(*) FROM txn").fetchone()[0]
    predicate_rows = db.execute("SELECT COUNT(*) FROM v_txn_transfer").fetchone()[0]
    in_view = db.execute("SELECT COUNT(*) FROM v_transaction").fetchone()[0]
    assert predicate_rows == txns == 2
    assert in_view == 2, "the new INNER join must not drop a transaction"


def test_an_override_to_a_transfer_category_removes_it_from_cashflow(
    db: sqlite3.Connection,
) -> None:
    """The point of the whole exercise: one definition, and the view obeys it."""
    _seed_transfer_categories(db)
    _book_one(db, txn="keep", amount=1000, raw_record=True)
    _book_one(db, txn="moved", amount=1000, raw_record=True)

    before = db.execute("SELECT SUM(inflow_minor) FROM v_cashflow_monthly").fetchone()[0]
    assert before == 2000

    _override(db, "moved", "probe-transfer")
    after = db.execute("SELECT SUM(inflow_minor) FROM v_cashflow_monthly").fetchone()[0]
    assert after == 1000

    _override(db, "moved", "probe-dining")
    assert db.execute("SELECT SUM(inflow_minor) FROM v_cashflow_monthly").fetchone()[0] == 2000


def test_the_raw_column_is_not_reachable_through_the_rendering_view(
    db: sqlite3.Connection,
) -> None:
    """``v_transaction.is_transfer`` is the effective answer, not ``txn.is_transfer``.

    Substituted rather than added alongside. Exposing both would leave a second,
    wronger answer within reach of every future reader, and one of them would
    take it -- which is the shape §5.29 records the archive paying for twice.
    """
    _seed_transfer_categories(db)
    _book_one(db, txn="ruled", amount=-5000, raw_record=True)
    with transaction(db):
        db.execute("UPDATE txn SET is_transfer = 1 WHERE id = 'ruled'")
    _override(db, "ruled", "probe-dining")

    row = db.execute("SELECT is_transfer, transfer_decided_by FROM v_transaction").fetchone()
    assert row["is_transfer"] == 0, "the view must report the effective value"
    assert row["transfer_decided_by"] == "override"
    raw = db.execute("SELECT is_transfer FROM txn WHERE id='ruled'").fetchone()[0]
    assert raw == 1, "and the rule's own answer is still recorded underneath"


def test_transaction_rolls_back_on_error(db: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError), transaction(db):
        db.execute("INSERT INTO commodity (id, kind, scale) VALUES ('EUR','currency',2)")
        raise RuntimeError("boom")
    assert db.execute("SELECT COUNT(*) FROM commodity WHERE id='EUR'").fetchone()[0] == 0
