# SPDX-License-Identifier: AGPL-3.0-or-later
"""The local HTTP service. Loopback only, no authentication, no egress.

Importing this package requires the ``web`` optional dependencies
(``pip install ledgerbox[web]``). Nothing outside :mod:`ledgerbox.api` may
import it at module level — the CLI has to keep working on an install that
never asked for a web server, so :func:`ledgerbox.cli.cmd_serve` imports it
inside the function body and turns an ``ImportError`` into a sentence.

The security boundary is "the local user of this machine". That is stated
plainly in ``docs/THREAT_MODEL.md`` rather than implied, and everything here is
built for it: bound to ``127.0.0.1``, no auth, no telemetry, no CDN.
"""

from __future__ import annotations

__all__ = ["create_app"]


def __getattr__(name: str) -> object:
    # Lazy so that `from ledgerbox.api import create_app` works without making
    # `import ledgerbox.api` itself drag in FastAPI at package-import time.
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
