# SPDX-License-Identifier: AGPL-3.0-or-later
"""The application factory: one process, one data directory, one ledger.

Everything the server needs is settled here, at construction, and nothing is
settled per request:

* **migrations run once**, before any route can be reached. Requests never
  migrate — a GET holds a ``mode=ro`` handle that could not apply a migration
  anyway, and a schema upgrade performed by whichever request happened to
  arrive first is not something anyone should have to debug.
* **the data directory is an argument.** The CLI, a test and a second instance
  all say which ledger they mean instead of discovering it from the environment
  at some later moment.
* **the bind address is not settled here at all.** :func:`create_app` takes no
  host parameter. There is no authentication anywhere in this application, so
  the loopback bind *is* the access control (``docs/THREAT_MODEL.md``,
  :data:`ledgerbox.api.dependencies.DEFAULT_HOST`), and a factory that accepted
  a host would be a factory that could be handed an address on the LAN.

There is deliberately no CORS middleware. Same origin is the whole design: the
page is served by the process that owns the database, nothing else has any
business reading a response, and an ``Access-Control-Allow-Origin`` header is
precisely how that would stop being true.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .. import __version__
from ..config import DataPaths
from ..db.migrate import open_ledger
from .dependencies import MAX_UPLOAD_BYTES, STATE_ATTR, AppState
from .routes import (
    agent_center,
    agent_proposals,
    agent_triage,
    analytics,
    health,
    review,
    statements,
    transactions,
    upload,
)

__all__ = ["SECURITY_HEADERS", "WEB_ROOT", "SecurityHeadersMiddleware", "create_app"]

#: The static frontend, shipped inside the package so that an install has a
#: page without a build step ever having run.
WEB_ROOT = Path(__file__).parent.parent / "web"

#: Sent on every response, including every error response.
#:
#: ``default-src 'self'`` is what makes the frontend's "no CDN, no bundler, no
#: framework" rule enforceable rather than aspirational: an inline ``<script>``
#: or an off-origin ``src`` simply does not execute. Statement descriptions,
#: payee names and memos are third-party text that reaches the DOM, so the
#: assumption to design against is that some of it will one day be interpreted
#: as markup; ``base-uri`` and ``form-action`` close the two routes by which
#: injected markup exfiltrates without needing a script at all.
#:
#: The price is that FastAPI's ``/docs`` page, which pulls Swagger UI off a
#: CDN, renders blank. ``/openapi.json`` is untouched and is the document that
#: the frontend and the tests actually read.
SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'; object-src 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
}


class SecurityHeadersMiddleware:
    """Stamps :data:`SECURITY_HEADERS` onto every response that leaves.

    Raw ASGI rather than ``BaseHTTPMiddleware`` so that it also covers the
    responses this application never builds itself: the 413 raised out of the
    upload route, a 404 from the static mount, the validation error FastAPI
    produced before any of our code ran. A header present only on the happy
    path is a header that is missing exactly when the page is rendering
    something nobody expected.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                # Assigned, not defaulted: no route gets to weaken the policy
                # by setting a laxer value of its own further in.
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(paths: DataPaths, *, max_upload_bytes: int = MAX_UPLOAD_BYTES) -> FastAPI:
    """Build the application for one data directory.

    *max_upload_bytes* is a parameter only so that a test can lower it. Raising
    it does not make a larger statement parseable; it makes ``incoming/``
    bigger.
    """
    paths.ensure()

    # Spool files outlive only a process that died mid-upload, and they are
    # statement PDFs. Nothing watches incoming/ otherwise, so a crash would
    # leave one there silently for as long as the data directory exists.
    abandoned = paths.sweep_incoming()
    if abandoned:
        print(f"ledgerbox: removed {len(abandoned)} abandoned upload(s) from {paths.incoming}")

    # Same shape, other directory: an archive write interrupted between the
    # temp file and the os.replace leaves debris that nothing else removes.
    debris = paths.sweep_archive_temp()
    if debris:
        print(f"ledgerbox: removed {len(debris)} interrupted archive write(s) from {paths.archive}")

    # The one migration point. Opening and immediately closing looks odd until
    # you notice that no route could do this: reads hold a read-only handle,
    # and writes must not discover a half-migrated schema mid-request.
    conn = open_ledger(paths.db)
    conn.close()

    app = FastAPI(
        title="ledgerbox",
        version=__version__,
        summary="A local-first ledger that refuses to give you numbers it cannot prove.",
    )
    setattr(app.state, STATE_ATTR, AppState(paths=paths, max_upload_bytes=max_upload_bytes))

    app.add_middleware(SecurityHeadersMiddleware)

    # Starlette wraps the whole stack in ServerErrorMiddleware, *outside* every
    # middleware added above, so an unhandled exception produces a 500 that has
    # never passed through SecurityHeadersMiddleware — measured, not assumed.
    # Registering a handler for 500 is the documented way in: Starlette hands
    # this response out itself and then re-raises, so the traceback still
    # reaches the log.
    #
    # The body is a fixed string. A 500 is the response most likely to carry a
    # path or a stack frame, and this application's 500s would be describing a
    # ledger.
    @app.exception_handler(500)
    async def server_error(_request: Request, _exc: Exception) -> PlainTextResponse:
        return PlainTextResponse(
            "Internal Server Error - see the terminal running ledgerbox for the traceback.",
            status_code=500,
            headers=SECURITY_HEADERS,
        )

    app.include_router(upload.router)
    app.include_router(review.router)
    app.include_router(health.router)
    app.include_router(statements.router)
    app.include_router(transactions.router)
    app.include_router(analytics.router)
    app.include_router(agent_center.router)
    app.include_router(agent_proposals.router)
    app.include_router(agent_triage.router)

    # check_dir=False: a checkout whose frontend has not been written yet, or
    # an install that only ever uses the CLI, must not turn a missing web/ into
    # a server that refuses to start. The half with the data in it still works.
    app.mount("/static", StaticFiles(directory=WEB_ROOT, check_dir=False), name="static")

    @app.get("/", response_class=FileResponse, include_in_schema=False)
    async def index() -> FileResponse:
        page = WEB_ROOT / "index.html"
        if not page.is_file():
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"No frontend was installed at {WEB_ROOT}. The API is still available.",
            )
        return FileResponse(page, media_type="text/html")

    return app
