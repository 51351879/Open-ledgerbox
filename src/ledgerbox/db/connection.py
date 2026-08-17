# SPDX-License-Identifier: AGPL-3.0-or-later
"""SQLite connections: pragmas, transactions, and a read-only handle.

There is no ORM by design — the schema *is* the product, and an ORM hides both
the integer-minor-units discipline and what a migration actually does.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Literal

from ..config import guard_data_dir

#: Minimum SQLite that understands STRICT tables.
MIN_SQLITE = (3, 37, 0)


class UnsupportedSQLite(RuntimeError):
    pass


class _ClosingReadOnlyConnection(sqlite3.Connection):
    """A read-only connection whose context manager also closes the handle.

    The stdlib context manager only commits or rolls back.  That shape leaked
    read handles on Windows because ``with connect_read_only(...)`` looked like
    lifetime management and was not.  The read-only factory is the one place we
    can make the natural spelling structurally safe without changing writable
    transaction semantics.
    """

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            super().__exit__(exc_type, exc_value, traceback)
            return False
        finally:
            self.close()


def check_sqlite_version() -> None:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise UnsupportedSQLite(
            f"SQLite {'.'.join(map(str, MIN_SQLITE))}+ required for STRICT tables; "
            f"this interpreter links {sqlite3.sqlite_version}."
        )


def _apply_pragmas(conn: sqlite3.Connection, *, read_only: bool) -> None:
    conn.execute("PRAGMA busy_timeout = 5000")
    if read_only:
        conn.execute("PRAGMA query_only = ON")
        return
    conn.execute("PRAGMA journal_mode = WAL")
    # FULL, not NORMAL: this is a ledger meant to outlive the hardware, and the
    # write volume (a few hundred rows a month) makes the cost irrelevant.
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")


def connect(
    path: str | Path, *, read_only: bool = False, guard: bool = True
) -> sqlite3.Connection:
    """Open *path*.

    ``isolation_level=None`` puts the driver in autocommit mode so transactions
    are explicit — see :func:`transaction`. Nothing here writes without a
    ``BEGIN IMMEDIATE``.

    The git-repository guard runs here too, not only in
    :class:`~ledgerbox.config.DataPaths`: ``ledger.db`` is the most sensitive
    artefact this project produces, and a control with a second unguarded door
    is not a control. Pass ``guard=False`` **only** for a throwaway database
    that holds no user data (``tools/dump_schema.py`` is the one caller), and
    say so at the call site.
    """
    check_sqlite_version()
    target = Path(path)
    conn: sqlite3.Connection

    if read_only:
        # Reading inside a repository is harmless; only writing puts data there.
        uri = f"{target.absolute().as_uri()}?mode=ro"
        conn = sqlite3.connect(
            uri,
            uri=True,
            isolation_level=None,
            factory=_ClosingReadOnlyConnection,
        )
    else:
        if guard:
            guard_data_dir(target.expanduser().resolve().parent)
        target.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(target, isolation_level=None)

    conn.row_factory = sqlite3.Row
    try:
        _apply_pragmas(conn, read_only=read_only)
    except BaseException:
        conn.close()
        raise
    return conn


def connect_read_only(path: str | Path) -> sqlite3.Connection:
    """A handle that cannot write, for analytics and for an MCP server.

    Both belt (``mode=ro`` in the URI) and braces (``PRAGMA query_only``).

    Unlike a normal ``sqlite3.Connection``, this handle closes on context-manager
    exit. ``with connect_read_only(p) as conn`` is therefore safe on Windows as
    well as explicit ``close()`` / ``contextlib.closing``. Writable connections
    keep the stdlib semantics because their ``with`` block means a transaction,
    not ownership of the handle.
    """
    return connect(path, read_only=True)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One all-or-nothing unit of work.

    ``IMMEDIATE`` takes the write lock up front rather than discovering the
    conflict at COMMIT, which is what you want when the alternative is a
    half-ingested statement.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextmanager
def read_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Several reads, one snapshot.

    Deferred rather than ``IMMEDIATE``, which is the whole difference from
    :func:`transaction`: this takes a read lock, so it works on a ``mode=ro``
    handle with ``PRAGMA query_only`` set — a handle that cannot take the write
    lock at all and raises *attempt to write a readonly database* if asked to.

    Worth having rather than leaving to each caller, because in WAL mode two
    SELECTs outside a transaction are two snapshots with a writer free to commit
    between them. For this application that reads as a page of transactions and
    a set of figures describing "the same" rows, computed either side of an
    ingest — a table disagreeing with its own total, which is the failure this
    project is organised around, arriving through the back door.
    """
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def pragma(conn: sqlite3.Connection, name: str) -> object:
    row = conn.execute(f"PRAGMA {name}").fetchone()
    return None if row is None else row[0]


def integrity_check(conn: sqlite3.Connection) -> list[str]:
    """Empty list means healthy."""
    rows = [r[0] for r in conn.execute("PRAGMA integrity_check").fetchall()]
    problems = [r for r in rows if r != "ok"]
    fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
    problems.extend(
        f"foreign key violation: table={r[0]} rowid={r[1]} parent={r[2]} fkid={r[3]}"
        for r in fk_rows
    )
    return problems


def dump_schema(conn: sqlite3.Connection) -> str:
    """Deterministic text rendering of the live schema.

    Used to keep ``db/schema.sql`` honest: it is a generated snapshot of what
    the migrations actually produce, not a second source of truth.
    """
    rows = conn.execute(
        """
        SELECT type, name, sql FROM sqlite_master
        WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'
        ORDER BY CASE type
                   WHEN 'table' THEN 0 WHEN 'index' THEN 1
                   WHEN 'view'  THEN 2 ELSE 3 END,
                 name
        """
    ).fetchall()
    return "".join(f"{row['sql'].strip()};\n\n" for row in rows)
