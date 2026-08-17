# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the ledger currently is: ``GET /api/health``.

``ledgerbox doctor`` in the browser the operator already has open — version,
schema, integrity, row counts, queue depth, totals.

``GET /api/statements``, the list behind it, was here until a statement grew
things you could *do* to it. It now lives with them in
:mod:`ledgerbox.api.routes.statements`: one resource, one module.

Runs on the read-only handle, and does not migrate: the schema is brought up to
date once, in :func:`ledgerbox.api.app.create_app`, and a handle opened
``mode=ro`` could not do it anyway.

``data_dir`` and ``database`` are absolute paths. That is the operator's own
machine shown in the operator's own browser, over loopback with no egress —
``doctor`` prints the same two lines.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from ... import __version__
from ...db import repo
from ...db.connection import integrity_check
from ...db.migrate import discover, schema_version
from ..dependencies import AppState, get_state, ledger_ro
from ..schemas import HealthOut, TotalsOut

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["health"])

StateDep = Annotated[AppState, Depends(get_state)]

_MONTHS_SQL = "SELECT COUNT(DISTINCT statement_month) FROM v_statement"


@router.get("/health")
def read_health(state: StateDep) -> HealthOut:
    """Version, schema, integrity and counts, in one request.

    ``integrity_ok`` is ``PRAGMA integrity_check`` plus ``PRAGMA
    foreign_key_check`` — real corruption is *reported* by those rather than
    raised, which is why nothing here catches database errors and turns them
    into a false negative. A file that is not a database at all is a
    misconfiguration, not corruption, and it deserves the traceback it gets.

    With no database file, ``integrity_ok`` is False and ``database_present``
    says why. Reporting a check as passed when it never ran is the same lie
    :func:`ledgerbox.reconcile.report.verdict` refuses to tell when it says
    UNVERIFIED instead of ok. ``create_app`` creates the file at startup, so in
    practice this branch means it was removed underneath a running server.

    ``totals`` is None on an empty ledger. Zeroes there would render as a real
    balance of $0.00 rather than as an absence of anything to total.
    """
    paths = state.paths
    latest = len(discover())

    if not paths.db.exists():
        return HealthOut(
            version=__version__,
            schema_version=0,
            schema_latest=latest,
            data_dir=str(paths.root),
            database=str(paths.db),
            database_present=False,
            integrity_ok=False,
        )

    with ledger_ro(state) as conn:
        rows = repo.row_counts(conn)
        queue = repo.open_review_counts(conn)
        months = int(conn.execute(_MONTHS_SQL).fetchone()[0])
        # ledger_totals returns exactly TotalsOut's fields; TotalsOut's own
        # docstring names it as the definition of what they mean.
        totals = TotalsOut(**repo.ledger_totals(conn)) if rows.get("txn", 0) > 0 else None
        return HealthOut(
            version=__version__,
            schema_version=schema_version(conn),
            schema_latest=latest,
            data_dir=str(paths.root),
            database=str(paths.db),
            database_present=True,
            integrity_ok=integrity_check(conn) == [],
            rows=rows,
            open_block=queue["block"],
            open_warn=queue["warn"],
            statement_months=months,
            totals=totals,
        )
