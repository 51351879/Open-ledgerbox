# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-request wiring: application state, connections, and the write lock.

Three decisions live here, and every route depends on all three.

**One connection per request.** ``sqlite3`` connections are bound to the thread
that created them, and FastAPI runs synchronous endpoints in a worker pool — a
module-level connection would be a ``ProgrammingError`` waiting for the second
request. Opening one per request costs a few hundred microseconds against a
local file and removes the entire class of problem.

**Reads get a handle that cannot write.** :func:`ledger_ro` opens the database
``mode=ro`` *and* sets ``PRAGMA query_only``. A bug in a GET endpoint should be
a failed query, not a mutation. Migrations therefore run once at startup (see
:func:`ledgerbox.api.app.create_app`), never on a request: a read-only handle
could not apply them anyway, and a schema upgrade triggered by whichever
request happens to arrive first is not a thing anyone wants to debug.

**Writes are serialised in-process.** ``BEGIN IMMEDIATE`` plus
``busy_timeout`` already make concurrent writers correct, but "correct" there
means one of them waits five seconds and then fails. Two browser tabs dropping
statements at once is an ordinary accident, not an exotic one, so
:func:`ledger_rw` takes a lock and they queue instead. This does not extend
across processes — ``ledgerbox ingest`` running at the same time as the server
still relies on SQLite's own locking, which is why ``docs/STATUS.md`` §7 lists
multi-process ingest as untested.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast

from fastapi import Request

from ..config import DEFAULT_HOST, DEFAULT_PORT, DataPaths
from ..db.connection import connect, connect_read_only

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MAX_UPLOAD_BYTES",
    "STATE_ATTR",
    "AppState",
    "get_state",
    "ledger_ro",
    "ledger_rw",
]

# DEFAULT_HOST / DEFAULT_PORT are defined in ledgerbox.config and re-exported
# here, because the CLI has to name the default port in `--help` without
# importing FastAPI. Loopback, never 0.0.0.0 (EXECUTION_PLAN §6); the bind
# address is this application's only access control and tests assert it.

#: Upload ceiling (EXECUTION_PLAN §6). A real Chase statement is ~130 KB; this
#: is three orders of magnitude of headroom and still bounds what an accidental
#: drag of a video file can write to the spool.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

#: Where :class:`AppState` hangs off ``app.state``.
STATE_ATTR = "ledgerbox"

#: Held for the whole of a write request, including the parse. See module docs.
WRITE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class AppState:
    """Everything a route needs that is not in the request itself."""

    paths: DataPaths
    max_upload_bytes: int = MAX_UPLOAD_BYTES


def get_state(request: Request) -> AppState:
    """FastAPI dependency: ``state: AppState = Depends(get_state)``."""
    return cast(AppState, getattr(request.app.state, STATE_ATTR))


@contextmanager
def ledger_ro(state: AppState) -> Iterator[sqlite3.Connection]:
    """A handle that cannot write. For every GET."""
    conn = connect_read_only(state.paths.db)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def ledger_rw(state: AppState) -> Iterator[sqlite3.Connection]:
    """A writable handle, one writer at a time. For every POST that persists."""
    with WRITE_LOCK:
        conn = connect(state.paths.db)
        try:
            yield conn
        finally:
            conn.close()
