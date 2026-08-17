# SPDX-License-Identifier: AGPL-3.0-or-later
"""The review queue over HTTP: ``GET /api/review``, ``POST /api/review/{id}/resolve``.

**Resolving books nothing.** There is no path from this module to ``txn``,
``posting``, ``txn_identity`` or ``balance_assertion``, and there is not meant
to be one. A queued item says a statement failed a check the ledger refuses to
accept on faith; recording that a human looked at it changes what the queue
shows and nothing else. The only way a refused statement enters the ledger is
to fix the parser and re-ingest the archived bytes.

That makes the endpoint's failure modes the interesting part of it, and both
are ``409`` rather than a quiet no-op:

* an item that is already ``resolved`` or ``dismissed`` — idempotency you can
  see, instead of a second write that leaves the caller believing it did
  something;
* dismissing a **block**-level item without ``acknowledge_unbooked``. Dismissal
  there means accepting a statement that is not in the ledger, and the message
  says so in words rather than leaving the user to infer it from a badge.

The whole request runs on one writable handle. Reading the item on a different
connection than the one that updates it would put a check-then-act race between
the 409 rules and the write; :func:`~ledgerbox.api.dependencies.ledger_rw` holds
the in-process write lock across both, so the item cannot change underneath the
rules that just approved the change.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from ...db import repo
from ...db.connection import transaction
from ..dependencies import AppState, get_state, ledger_ro, ledger_rw
from ..schemas import ResolveRequest, ReviewItemOut, ReviewListOut, ReviewStatus, Severity

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["review"])

StateDep = Annotated[AppState, Depends(get_state)]

#: The refusal that keeps "dismissed" from ever meaning "booked". Spelled out
#: because the user is about to accept a gap in their own ledger, and the flag
#: they have to send back is the point at which that becomes deliberate.
BLOCK_DISMISS_REFUSAL = (
    "This is a block-level item: the statement it belongs to was never booked, and "
    "nothing from it is in the ledger. Dismissing the item only records that you "
    "looked at it — it does not book the statement, and `ledgerbox verify` will keep "
    "reporting the statement as unbooked. To book it, fix the parser and re-ingest the "
    "archived file. To accept the gap anyway, confirm the dismissal."
)
# The last sentence deliberately does not name the field. This string is shown
# to a person in a browser who has a button in front of them, and telling them
# to "send acknowledge_unbooked: true" is asking them to read an API they are
# not using. The field is in the OpenAPI document, where a client author looks.


def _split_detail(raw: object) -> tuple[str, dict[str, Any]]:
    """Split the stored ``detail`` column into the message and the structured part.

    :class:`ledgerbox.reconcile.report.ReviewItem` writes
    ``{"message": …, "detail": {…}}``, and the two halves go to different places
    in the response so the page never parses anything.

    Anything else in that column degrades to "message is the raw text, detail is
    empty" rather than raising. This is the queue: it is read when something has
    already gone wrong, and one unparseable row must not be able to take out the
    list containing it — that would hide exactly the item worth seeing.
    """
    if not isinstance(raw, str):
        return "", {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, {}
    if not isinstance(payload, dict):
        return raw, {}
    message = payload.get("message")
    detail = payload.get("detail")
    return (
        message if isinstance(message, str) else raw,
        detail if isinstance(detail, dict) else {},
    )


def _item_out(row: sqlite3.Row) -> ReviewItemOut:
    """One queue row as the wire model.

    ``status`` and ``severity`` are handed to pydantic unconverted on purpose:
    they are constrained to a literal set there, so a value the schema's CHECK
    constraint should have made impossible fails loudly here instead of reaching
    the browser as an unstyled badge.
    """
    message, detail = _split_detail(row["detail"])
    return ReviewItemOut(
        id=row["id"],
        source_file_id=row["source_file_id"],
        status=row["status"],
        severity=row["severity"],
        check_id=row["check_id"],
        message=message,
        detail=detail,
        created_at=row["created_at"],
        resolved_at=row["resolved_at"],
        statement_month=row["statement_month"],
    )


@router.get("/review")
def read_review_queue(
    state: StateDep,
    status: ReviewStatus = "open",
    severity: Severity | None = None,
) -> ReviewListOut:
    """The queue, blocking items first.

    ``open_block`` and ``open_warn`` describe the whole open queue, not the
    filtered page: they are the depth the status strip reports, and a client
    that asked to see resolved history still needs to know what is outstanding.
    """
    with ledger_ro(state) as conn:
        rows = repo.list_review_items(conn, status=status, severity=severity)
        counts = repo.open_review_counts(conn)
    return ReviewListOut(
        items=[_item_out(row) for row in rows],
        open_block=counts["block"],
        open_warn=counts["warn"],
    )


@router.post("/review/{item_id}/resolve")
def resolve_review_item(
    item_id: str,
    body: ResolveRequest,
    state: StateDep,
) -> ReviewItemOut:
    """Record one human decision about one queued item, and book nothing.

    The rules run in this order, and the order is what makes the messages
    useful: an unknown id is a 404 before anything else is considered, an item
    that has already been decided is a 409 saying which way it went, and only
    then is a block-level dismissal checked for its acknowledgement.

    ``note`` is accepted and **not stored**. ``review_item`` has no column for
    it, and adding one is a migration rather than something this endpoint can
    do on the way past. It is documented here rather than dropped in silence so
    that the next person to want it finds the reason instead of a bug.
    """
    with ledger_rw(state) as conn:
        row = repo.get_review_item(conn, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no review item with id {item_id!r}")

        current = str(row["status"])
        if current != "open":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"review item {item_id} is already {current}"
                    f"{' at ' + str(row['resolved_at']) if row['resolved_at'] else ''}. "
                    f"A decision is recorded once; re-deciding it would overwrite the "
                    f"first answer without anything to show that it changed."
                ),
            )

        dismissing_a_block = body.action == "dismiss" and str(row["severity"]) == "block"
        if dismissing_a_block and not body.acknowledge_unbooked:
            raise HTTPException(status_code=409, detail=BLOCK_DISMISS_REFUSAL)

        new_status = "resolved" if body.action == "resolve" else "dismissed"
        with transaction(conn):
            repo.set_review_status(
                conn,
                item_id=item_id,
                status=new_status,
                resolved_at=datetime.now(UTC).isoformat(timespec="seconds"),
            )

        # Answer with what the database now holds rather than with what was just
        # sent to it. One extra read is the cheapest way for the page and the
        # ledger not to be able to disagree about a row the user just changed.
        stored = repo.get_review_item(conn, item_id)

    if stored is None:  # pragma: no cover - the write lock is held across both reads
        raise HTTPException(status_code=404, detail=f"review item {item_id!r} vanished mid-request")
    return _item_out(stored)
