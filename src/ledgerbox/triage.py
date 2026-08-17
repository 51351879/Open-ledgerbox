# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exhaustive, proposal-only triage for the ledger's remaining coverage.

A user-owned local Agent may sort every currently unanswered transaction in a
bounded date scope into one of three routes.  Submission stores audit rows only.
Only a later human review may write an existing category, in the same database
transaction that records the review outcome.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from .config import DataPaths
from .content_ids import content_hash
from .db import repo
from .db.connection import transaction
from .ingest.pipeline import verify_ledger
from .proposals import CLIENTS, Producer, ledger_revision
from .reconcile.checks import PASS

TRIAGE_SCHEMA_VERSION = 1
TRIAGE_ROUTES = frozenset({"possible_transfer", "taxonomy_gap", "uncertain"})
TRIAGE_REASON_CODES: dict[str, frozenset[str]] = {
    "possible_transfer": frozenset(
        {
            "payment_rail_ownership_unknown",
            "account_movement_language",
            "debt_or_card_settlement",
            "investment_platform_flow",
        }
    ),
    "taxonomy_gap": frozenset(
        {
            "repeated_cluster_without_category",
            "coherent_activity_missing",
            "current_category_too_broad",
        }
    ),
    "uncertain": frozenset(
        {
            "descriptor_ambiguous",
            "counterparty_role_unknown",
            "mixed_signal",
            "insufficient_context",
            "one_off_unresolved",
        }
    ),
}


class TriageError(RuntimeError):
    """Base class for triage failures safe to show to a local caller."""


class TriageConflict(TriageError):
    """The current ledger no longer accepts this exact triage operation."""


class TriageScopeIncomplete(TriageConflict):
    """The draft does not exhaustively partition its current bounded scope."""


class TriageNotFound(TriageError):
    """No triage run with the explicit content id exists."""


class TriageLedgerNotReady(TriageError):
    """Triage is unavailable until every ledger verification check passes."""

    def __init__(self, failed_checks: tuple[str, ...]) -> None:
        self.failed_checks = failed_checks
        super().__init__("ledger verification did not pass; run ledgerbox agent status for details")


@dataclass(frozen=True, slots=True)
class TriageScope:
    since: str | None = None
    until: str | None = None


@dataclass(frozen=True, slots=True)
class TriageGroup:
    group_id: str
    route: str
    reason_code: str
    txn_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TriageDraft:
    schema_version: int
    ledger_revision: str
    scope: TriageScope
    producer: Producer
    groups: tuple[TriageGroup, ...]


@dataclass(frozen=True, slots=True)
class TriageSubmission:
    schema_version: int
    ledger_revision: str
    scope_revision: str
    scope: TriageScope
    producer: Producer
    groups: tuple[TriageGroup, ...]


@dataclass(frozen=True, slots=True)
class TriageItem:
    txn_id: str
    group_id: str
    route: str
    reason_code: str
    outcome: str
    applied_category_id: str | None
    reviewed_at: str | None


@dataclass(frozen=True, slots=True)
class TriageRun:
    run_id: str
    ledger_revision: str
    scope_revision: str
    schema_version: int
    scope: TriageScope
    producer: Producer
    created_at: str
    state: str
    items: tuple[TriageItem, ...]


@dataclass(frozen=True, slots=True)
class TriageRunSummary:
    run_id: str
    created_at: str
    state: str
    scope: TriageScope
    producer: Producer
    item_count: int
    pending: int
    confirmed_transfer: int
    confirmed_taxonomy_gap: int
    left_uncertain: int
    classified_existing: int
    stale: int
    withdrawn: int


@dataclass(frozen=True, slots=True)
class TriageValidationResult:
    run_id: str
    item_count: int
    submission: TriageSubmission


@dataclass(frozen=True, slots=True)
class TriageSubmitResult:
    run_id: str
    created: bool
    item_count: int


@dataclass(frozen=True, slots=True)
class TriageReviewResult:
    run_id: str
    confirmed_transfer: int = 0
    confirmed_taxonomy_gap: int = 0
    left_uncertain: int = 0
    classified_existing: int = 0
    state: str = "open"


@dataclass(frozen=True, slots=True)
class TriageWithdrawResult:
    run_id: str
    withdrawn: int
    already_absent: int
    changed_later: int


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def group_id_for(route: str, reason_code: str, txn_ids: tuple[str, ...]) -> str:
    return content_hash(
        {
            "route": route,
            "reason_code": reason_code,
            "txn_ids": sorted(txn_ids),
        }
    )


def scope_revision_for(
    *,
    ledger_revision_value: str,
    scope: TriageScope,
    txn_ids: tuple[str, ...],
) -> str:
    return content_hash(
        {
            "revision_schema": 1,
            "ledger_revision": ledger_revision_value,
            "since": scope.since,
            "until": scope.until,
            "txn_ids": sorted(txn_ids),
        }
    )


def _normalised_groups(groups: tuple[TriageGroup, ...]) -> list[dict[str, object]]:
    return sorted(
        [
            {
                "group_id": group.group_id,
                "route": group.route,
                "reason_code": group.reason_code,
                "txn_ids": sorted(group.txn_ids),
            }
            for group in groups
        ],
        key=lambda group: str(group["group_id"]),
    )


def run_id_for(submission: TriageSubmission) -> str:
    return content_hash(
        {
            "schema_version": submission.schema_version,
            "ledger_revision": submission.ledger_revision,
            "scope_revision": submission.scope_revision,
            "scope": {
                "since": submission.scope.since,
                "until": submission.scope.until,
            },
            "producer": {
                "client": submission.producer.client,
                "client_version": submission.producer.client_version,
                "model_reported": submission.producer.model_reported,
            },
            "groups": _normalised_groups(submission.groups),
        }
    )


def _shape_rows(
    draft: TriageDraft | TriageSubmission,
) -> list[tuple[str, str, str, str]]:
    if draft.schema_version != TRIAGE_SCHEMA_VERSION:
        raise TriageConflict(f"triage schema_version must be {TRIAGE_SCHEMA_VERSION}")
    if draft.producer.client not in CLIENTS:
        raise TriageConflict("producer client must be codex, claude-code, or other")
    for field_name, value in (
        ("client_version", draft.producer.client_version),
        ("model_reported", draft.producer.model_reported),
    ):
        if value is not None and len(value) > 200:
            raise TriageConflict(f"producer {field_name} is longer than 200 characters")
    try:
        repo.DateSpan(since=draft.scope.since, until=draft.scope.until)
    except ValueError as error:
        raise TriageConflict(str(error)) from error
    if not draft.groups:
        raise TriageScopeIncomplete("a triage run must contain at least one group")

    seen: set[str] = set()
    rows: list[tuple[str, str, str, str]] = []
    for group in draft.groups:
        if group.route not in TRIAGE_ROUTES:
            raise TriageConflict(f"unknown triage route {group.route!r}")
        if group.reason_code not in TRIAGE_REASON_CODES[group.route]:
            raise TriageConflict(
                f"reason_code {group.reason_code!r} does not belong to route {group.route!r}"
            )
        if not group.txn_ids:
            raise TriageScopeIncomplete(f"group {group.group_id!r} has no txn_ids")
        if len(group.txn_ids) != len(set(group.txn_ids)):
            raise TriageScopeIncomplete(f"group {group.group_id!r} repeats a txn_id")
        expected = group_id_for(group.route, group.reason_code, group.txn_ids)
        if group.group_id != expected:
            raise TriageConflict(
                "group_id does not match the route, reason_code and explicit txn_ids: "
                f"want {expected}"
            )
        for txn_id in group.txn_ids:
            if txn_id in seen:
                raise TriageScopeIncomplete("a transaction appears in more than one triage group")
            seen.add(txn_id)
            rows.append((txn_id, group.group_id, group.route, group.reason_code))

    if len(rows) > repo.MAX_PAGE_SIZE:
        raise TriageScopeIncomplete(
            f"a triage run may name at most {repo.MAX_PAGE_SIZE} transactions"
        )
    return sorted(rows)


def _ready(paths: DataPaths, conn: sqlite3.Connection) -> None:
    results = verify_ledger(conn, paths)
    failed = tuple(result.check_id for result in results if result.status != PASS)
    if failed:
        raise TriageLedgerNotReady(failed)


def _eligible_ids(
    conn: sqlite3.Connection, *, scope: TriageScope
) -> tuple[str, ...]:
    query = repo.TransactionQuery(
        category=repo.NO_CATEGORY,
        span=repo.DateSpan(since=scope.since, until=scope.until),
        sort="date",
        descending=False,
        limit=repo.MAX_PAGE_SIZE,
    )
    matched = int(repo.summarize_transactions(conn, query)["matched"])
    if matched == 0:
        raise TriageScopeIncomplete("the current triage scope has no eligible transactions")
    if matched > repo.MAX_PAGE_SIZE:
        raise TriageScopeIncomplete(
            f"the current scope has {matched} eligible transactions; "
            "narrow it until has_more is false"
        )
    rows = repo.list_transactions(conn, query)
    txn_ids = tuple(sorted(str(row["txn_id"]) for row in rows))
    pending = repo.pending_agent_proposal_txn_ids(conn, txn_ids)
    if pending:
        raise TriageConflict(
            f"{len(pending)} transaction(s) in this scope still have pending category proposals"
        )
    return txn_ids


def _current_normalised(
    conn: sqlite3.Connection,
    paths: DataPaths,
    draft: TriageDraft,
) -> tuple[list[tuple[str, str, str, str]], TriageSubmission]:
    rows = _shape_rows(draft)
    _ready(paths, conn)
    current_revision = ledger_revision(conn)
    if draft.ledger_revision != current_revision:
        raise TriageConflict(
            f"ledger revision changed: read {current_revision} and triage again"
        )
    eligible = _eligible_ids(conn, scope=draft.scope)
    named = tuple(sorted(txn_id for txn_id, _, _, _ in rows))
    if named != eligible:
        missing = len(set(eligible) - set(named))
        extra = len(set(named) - set(eligible))
        raise TriageScopeIncomplete(
            "triage must name every eligible transaction exactly once: "
            f"{missing} missing, {extra} extra"
        )
    scope_revision = scope_revision_for(
        ledger_revision_value=current_revision,
        scope=draft.scope,
        txn_ids=eligible,
    )
    submission = TriageSubmission(
        schema_version=draft.schema_version,
        ledger_revision=draft.ledger_revision,
        scope_revision=scope_revision,
        scope=draft.scope,
        producer=draft.producer,
        groups=draft.groups,
    )
    return rows, submission


def validate_triage(
    conn: sqlite3.Connection, paths: DataPaths, draft: TriageDraft
) -> TriageValidationResult:
    rows, submission = _current_normalised(conn, paths, draft)
    return TriageValidationResult(
        run_id=run_id_for(submission),
        item_count=len(rows),
        submission=submission,
    )


def submit_triage(
    conn: sqlite3.Connection,
    paths: DataPaths,
    submission: TriageSubmission,
) -> TriageSubmitResult:
    rows = _shape_rows(submission)
    run_id = run_id_for(submission)
    with transaction(conn):
        if repo.get_agent_triage_run(conn, run_id) is not None:
            return TriageSubmitResult(run_id=run_id, created=False, item_count=len(rows))

        draft = TriageDraft(
            schema_version=submission.schema_version,
            ledger_revision=submission.ledger_revision,
            scope=submission.scope,
            producer=submission.producer,
            groups=submission.groups,
        )
        current_rows, current = _current_normalised(conn, paths, draft)
        if current.scope_revision != submission.scope_revision:
            raise TriageConflict("triage scope changed; validate the complete current scope again")
        if run_id_for(current) != run_id:
            raise TriageConflict("triage normalized content changed; validate it again")

        repo.insert_agent_triage_run(
            conn,
            run_id=run_id,
            ledger_revision=submission.ledger_revision,
            scope_revision=submission.scope_revision,
            schema_version=submission.schema_version,
            since=submission.scope.since,
            until=submission.scope.until,
            client=submission.producer.client,
            client_version=submission.producer.client_version,
            model_reported=submission.producer.model_reported,
        )
        repo.insert_agent_triage_items(conn, run_id=run_id, rows=current_rows)
    return TriageSubmitResult(run_id=run_id, created=True, item_count=len(rows))


def get_run(conn: sqlite3.Connection, run_id: str) -> TriageRun | None:
    row = repo.get_agent_triage_run(conn, run_id)
    if row is None:
        return None
    items = tuple(
        TriageItem(
            txn_id=str(item["txn_id"]),
            group_id=str(item["group_id"]),
            route=str(item["route"]),
            reason_code=str(item["reason_code"]),
            outcome=str(item["outcome"]),
            applied_category_id=(
                None
                if item["applied_category_id"] is None
                else str(item["applied_category_id"])
            ),
            reviewed_at=None if item["reviewed_at"] is None else str(item["reviewed_at"]),
        )
        for item in repo.list_agent_triage_items(conn, run_id)
    )
    return TriageRun(
        run_id=str(row["id"]),
        ledger_revision=str(row["ledger_revision"]),
        scope_revision=str(row["scope_revision"]),
        schema_version=int(row["schema_version"]),
        scope=TriageScope(
            since=None if row["since"] is None else str(row["since"]),
            until=None if row["until"] is None else str(row["until"]),
        ),
        producer=Producer(
            client=str(row["client"]),
            client_version=None if row["client_version"] is None else str(row["client_version"]),
            model_reported=None if row["model_reported"] is None else str(row["model_reported"]),
        ),
        created_at=str(row["created_at"]),
        state=str(row["state"]),
        items=items,
    )


def list_runs(conn: sqlite3.Connection, *, limit: int = 50) -> tuple[TriageRunSummary, ...]:
    if not 1 <= limit <= 100:
        raise ValueError("triage run limit must be from 1 to 100")
    return tuple(
        TriageRunSummary(
            run_id=str(row["id"]),
            created_at=str(row["created_at"]),
            state=str(row["state"]),
            scope=TriageScope(
                since=None if row["since"] is None else str(row["since"]),
                until=None if row["until"] is None else str(row["until"]),
            ),
            producer=Producer(
                client=str(row["client"]),
                client_version=(
                    None if row["client_version"] is None else str(row["client_version"])
                ),
                model_reported=(
                    None if row["model_reported"] is None else str(row["model_reported"])
                ),
            ),
            item_count=int(row["item_count"]),
            pending=int(row["pending"]),
            confirmed_transfer=int(row["confirmed_transfer"]),
            confirmed_taxonomy_gap=int(row["confirmed_taxonomy_gap"]),
            left_uncertain=int(row["left_uncertain"]),
            classified_existing=int(row["classified_existing"]),
            stale=int(row["stale"]),
            withdrawn=int(row["withdrawn"]),
        )
        for row in repo.list_agent_triage_runs(conn, limit=limit)
    )


def _named_pending(
    conn: sqlite3.Connection, run_id: str, txn_ids: tuple[str, ...]
) -> list[sqlite3.Row]:
    if not txn_ids or len(txn_ids) > repo.MAX_PAGE_SIZE:
        raise TriageConflict(f"review must name 1..{repo.MAX_PAGE_SIZE} explicit txn_ids")
    if len(txn_ids) != len(set(txn_ids)):
        raise TriageConflict("review txn_ids must be unique")
    rows = repo.get_agent_triage_items(conn, run_id, txn_ids)
    if len(rows) != len(txn_ids):
        raise TriageConflict("one or more triage txn_ids are not in this run")
    if any(row["outcome"] != "pending" for row in rows):
        raise TriageConflict("one or more triage items are no longer pending")
    return rows


def review_triage(
    conn: sqlite3.Connection,
    run_id: str,
    txn_ids: tuple[str, ...],
    *,
    action: Literal["classify", "confirm_gap", "leave_uncertain"],
    category_id: str | None = None,
) -> TriageReviewResult:
    if action not in {"classify", "confirm_gap", "leave_uncertain"}:
        raise TriageConflict("triage review action is invalid")
    if action == "classify" and category_id is None:
        raise TriageConflict("classify requires category_id")
    if action != "classify" and category_id is not None:
        raise TriageConflict(f"{action} cannot carry category_id")

    counts: defaultdict[str, int] = defaultdict(int)
    with transaction(conn):
        run = repo.get_agent_triage_run(conn, run_id)
        if run is None:
            raise TriageNotFound(f"no triage run {run_id!r}")
        if run["state"] != "open":
            raise TriageConflict(f"triage run is {run['state']}, not open")
        if str(run["ledger_revision"]) != ledger_revision(conn):
            raise TriageConflict("ledger revision changed; this triage run is stale")
        rows = _named_pending(conn, run_id, txn_ids)
        category = None if category_id is None else repo.get_category(conn, category_id)
        if category_id is not None and category is None:
            raise TriageConflict(f"no category {category_id!r}")

        for row in rows:
            current = repo.get_transaction(conn, str(row["txn_id"]))
            if current is None or current["category_decided_by"] != "none":
                raise TriageConflict("one or more triage items are no longer unanswered")
        if action == "confirm_gap" and any(row["route"] != "taxonomy_gap" for row in rows):
            raise TriageConflict("confirm_gap only applies to taxonomy_gap items")
        if action == "leave_uncertain" and any(row["route"] != "uncertain" for row in rows):
            raise TriageConflict("leave_uncertain only applies to uncertain items")

        now = _now()
        if action == "classify":
            assert category_id is not None and category is not None
            ids = [str(row["txn_id"]) for row in rows]
            repo.set_category_overrides(conn, txn_ids=ids, category_id=category_id)
            outcome = (
                "confirmed_transfer" if str(category["kind"]) == "transfer"
                else "classified_existing"
            )
            for row in rows:
                repo.review_agent_triage_item(
                    conn,
                    run_id=run_id,
                    txn_id=str(row["txn_id"]),
                    outcome=outcome,
                    applied_category_id=category_id,
                    reviewed_at=now,
                )
                counts[outcome] += 1
        else:
            outcome = "confirmed_taxonomy_gap" if action == "confirm_gap" else "left_uncertain"
            for row in rows:
                repo.review_agent_triage_item(
                    conn,
                    run_id=run_id,
                    txn_id=str(row["txn_id"]),
                    outcome=outcome,
                    applied_category_id=None,
                    reviewed_at=now,
                )
                counts[outcome] += 1

        state = "open" if repo.count_pending_agent_triage_items(conn, run_id) else "completed"
        repo.set_agent_triage_run_state(conn, run_id=run_id, state=state)

    return TriageReviewResult(
        run_id=run_id,
        confirmed_transfer=counts["confirmed_transfer"],
        confirmed_taxonomy_gap=counts["confirmed_taxonomy_gap"],
        left_uncertain=counts["left_uncertain"],
        classified_existing=counts["classified_existing"],
        state=state,
    )


def dismiss_run(conn: sqlite3.Connection, run_id: str) -> TriageReviewResult:
    """Dismiss every pending item by explicitly leaving it unclassified."""
    left = 0
    with transaction(conn):
        run = repo.get_agent_triage_run(conn, run_id)
        if run is None:
            raise TriageNotFound(f"no triage run {run_id!r}")
        if run["state"] != "open":
            raise TriageConflict(f"triage run is {run['state']}, not open")
        now = _now()
        for row in repo.list_agent_triage_items(conn, run_id):
            if row["outcome"] == "pending":
                repo.review_agent_triage_item(
                    conn,
                    run_id=run_id,
                    txn_id=str(row["txn_id"]),
                    outcome="left_uncertain",
                    applied_category_id=None,
                    reviewed_at=now,
                )
                left += 1
        repo.set_agent_triage_run_state(conn, run_id=run_id, state="dismissed")
    return TriageReviewResult(
        run_id=run_id,
        left_uncertain=left,
        state="dismissed",
    )


def withdraw_run(
    conn: sqlite3.Connection,
    run_id: str,
    txn_ids: tuple[str, ...] | None = None,
) -> TriageWithdrawResult:
    """Compare-and-clear all or explicitly selected applied categories; retain audit rows."""
    withdrawn = absent = changed_later = 0
    with transaction(conn):
        run = repo.get_agent_triage_run(conn, run_id)
        if run is None:
            raise TriageNotFound(f"no triage run {run_id!r}")
        rows = repo.list_agent_triage_items(conn, run_id)
        if txn_ids is not None:
            if not txn_ids or len(txn_ids) > repo.MAX_PAGE_SIZE:
                raise TriageConflict(
                    f"selected withdrawal must name 1..{repo.MAX_PAGE_SIZE} explicit txn_ids"
                )
            if len(txn_ids) != len(set(txn_ids)):
                raise TriageConflict("selected withdrawal txn_ids must be unique")
            by_id = {str(row["txn_id"]): row for row in rows}
            if any(txn_id not in by_id for txn_id in txn_ids):
                raise TriageConflict("one or more withdrawal txn_ids are not in this run")
            rows = [by_id[txn_id] for txn_id in txn_ids]
            if any(
                row["outcome"] not in {"confirmed_transfer", "classified_existing"}
                for row in rows
            ):
                raise TriageConflict(
                    "selected withdrawal only applies to rows with an applied category"
                )
        now = _now()
        for row in rows:
            if row["outcome"] not in {"confirmed_transfer", "classified_existing"}:
                continue
            txn_id = str(row["txn_id"])
            applied = str(row["applied_category_id"])
            current = repo.get_category_override(conn, txn_id)
            if current is None:
                absent += 1
            elif str(current["category_id"]) == applied:
                repo.clear_category_override(conn, txn_id=txn_id)
                withdrawn += 1
            else:
                changed_later += 1
            repo.withdraw_agent_triage_item(
                conn, run_id=run_id, txn_id=txn_id, reviewed_at=now
            )
        if run["state"] != "dismissed":
            state = "open" if repo.count_pending_agent_triage_items(conn, run_id) else "completed"
            repo.set_agent_triage_run_state(conn, run_id=run_id, state=state)
    return TriageWithdrawResult(
        run_id=run_id,
        withdrawn=withdrawn,
        already_absent=absent,
        changed_later=changed_later,
    )
