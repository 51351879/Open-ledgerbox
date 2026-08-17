# SPDX-License-Identifier: AGPL-3.0-or-later
"""``python -m ledgerbox`` — the same entry point as the ``ledgerbox`` script.

Bare, it starts the local server and opens a browser at it. With arguments it
is the full command line, so ``python -m ledgerbox ingest ~/statements`` keeps
working and needs none of the optional web dependencies.
"""

from .cli import main

raise SystemExit(main())
