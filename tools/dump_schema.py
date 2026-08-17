# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regenerate ``src/ledgerbox/db/schema.sql`` from the migrations.

``schema.sql`` is a *generated snapshot*, never hand-edited: two sources of
truth for a schema is how a database and its documentation drift apart.

    python tools/dump_schema.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from ledgerbox.config import configure_stdio  # noqa: E402
from ledgerbox.db.connection import connect, dump_schema  # noqa: E402
from ledgerbox.db.migrate import migrate  # noqa: E402
from ledgerbox.fsutil import atomic_write_text  # noqa: E402

HEADER = """\
-- GENERATED FILE — do not edit.
--
-- Snapshot of the schema produced by applying every migration in
-- src/ledgerbox/db/migrations/ in order. Regenerate with:
--
--     python tools/dump_schema.py
--
-- The authoritative definitions are the migrations; this file exists so the
-- current shape is reviewable in one place and diffable in a pull request.

"""


def render() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        # guard=False: this database is a throwaway probe with no user data in
        # it, and the system temp directory may itself sit inside a repository.
        # It is deleted before this function returns.
        conn = connect(Path(tmp) / "schema-probe.db", guard=False)
        try:
            migrate(conn)
            return HEADER + dump_schema(conn)
        finally:
            conn.close()


def main() -> int:
    target = REPO_ROOT / "src" / "ledgerbox" / "db" / "schema.sql"
    rendered = render()
    previous = target.read_text(encoding="utf-8") if target.exists() else None
    atomic_write_text(target, rendered)
    print(f"{'unchanged' if previous == rendered else 'wrote'} {target}")
    return 0


if __name__ == "__main__":
    # The same job as the hand-rolled `sys.stdout.reconfigure` that was here,
    # done by the function that already handles a redirected or closed stream --
    # and typed, which the bare call was not: `TextIO` has no `reconfigure`, so
    # this line was an error the moment `tools/` came under mypy.
    configure_stdio()
    raise SystemExit(main())
