# SPDX-License-Identifier: AGPL-3.0-or-later
"""Forward-only migrations.

Rules:

* files are ``NNNN_name.sql``, numbered from 0001 with no gaps;
* each is applied exactly once, inside one transaction together with the row
  that records it — so a crash can never leave "applied but unrecorded";
* the checksum of every applied file is re-verified on every startup. Editing
  a migration that has already run is an error, not a convenience.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..fsutil import sha256_bytes
from .connection import connect, transaction

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migration (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  sha256     TEXT NOT NULL,
  applied_at TEXT NOT NULL
) STRICT
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    sha256: str

    @property
    def label(self) -> str:
        return f"{self.version:04d}_{self.name}"


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise MigrationError(f"bad migration filename: {path.name} (want NNNN_name.sql)")
        raw = path.read_bytes()
        found.append(
            Migration(
                version=int(match.group(1)),
                name=match.group(2),
                path=path,
                sql=raw.decode("utf-8"),
                sha256=sha256_bytes(raw),
            )
        )

    expected = list(range(1, len(found) + 1))
    if [m.version for m in found] != expected:
        raise MigrationError(
            f"migration versions must be contiguous from 1; got {[m.version for m in found]}"
        )
    return found


def split_statements(sql: str) -> list[str]:
    """Split a script into complete statements.

    Uses ``sqlite3.complete_statement`` — SQLite's own parser — instead of
    splitting on ``;``, which would break on semicolons inside string literals
    or trigger bodies.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""

    # Trailing comments are not an incomplete statement. Strip both forms
    # before deciding the file was truncated.
    tail = re.sub(r"/\*.*?\*/", " ", buffer, flags=re.DOTALL)
    leftover = "\n".join(
        line for line in tail.splitlines() if line.strip() and not line.strip().startswith("--")
    ).strip()
    if leftover:
        raise MigrationError(f"trailing incomplete statement: {leftover[:80]!r}")
    return statements


def applied(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    conn.execute(BOOTSTRAP_SQL)
    rows = conn.execute("SELECT * FROM schema_migration ORDER BY version").fetchall()
    return {int(row["version"]): row for row in rows}


def _verify_checksums(known: dict[int, sqlite3.Row], available: list[Migration]) -> None:
    by_version = {m.version: m for m in available}
    for version, row in known.items():
        migration = by_version.get(version)
        if migration is None:
            raise MigrationError(
                f"database has migration {version:04d}_{row['name']} applied, "
                f"but the file is missing — this database is newer than this code."
            )
        if migration.sha256 != row["sha256"]:
            raise MigrationError(
                f"{migration.label} was modified after it ran "
                f"(recorded {row['sha256'][:12]}…, file {migration.sha256[:12]}…). "
                f"Migrations are forward-only: add a new one instead of editing this."
            )


def pending(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    available = discover(directory)
    known = applied(conn)
    _verify_checksums(known, available)
    return [m for m in available if m.version not in known]


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Apply everything outstanding. Returns what was applied, in order."""
    done: list[Migration] = []
    for migration in pending(conn, directory):
        with transaction(conn):
            for statement in split_statements(migration.sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_migration (version, name, sha256, applied_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    migration.version,
                    migration.name,
                    migration.sha256,
                    datetime.now(UTC).isoformat(timespec="seconds"),
                ),
            )
        done.append(migration)

    if done:
        # Mirror the version into the file header too, so `sqlite3 file
        # 'PRAGMA user_version'` tells the truth without a query.
        conn.execute(f"PRAGMA user_version = {done[-1].version}")
    return done


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migration").fetchone()
    return 0 if row is None or row["v"] is None else int(row["v"])


def open_ledger(
    path: str | Path, *, migrate_if_needed: bool = True, guard: bool = True
) -> sqlite3.Connection:
    """The normal way to get a writable, up-to-date ledger connection."""
    conn = connect(path, guard=guard)
    try:
        if migrate_if_needed:
            migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn
