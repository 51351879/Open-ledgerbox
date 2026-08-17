# SPDX-License-Identifier: AGPL-3.0-or-later
"""Database layer: schema, migrations, connections.

Note what is *not* re-exported here: the ``migrate`` function. Binding it at
package level would shadow the ``ledgerbox.db.migrate`` module itself, so
``import ledgerbox.db.migrate`` would hand you a function. Import it from its
module: ``from ledgerbox.db.migrate import migrate``.
"""

from .connection import (
    connect,
    connect_read_only,
    dump_schema,
    integrity_check,
    transaction,
)
from .migrate import open_ledger, schema_version

__all__ = [
    "connect",
    "connect_read_only",
    "dump_schema",
    "integrity_check",
    "open_ledger",
    "schema_version",
    "transaction",
]
