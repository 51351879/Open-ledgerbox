# SPDX-License-Identifier: AGPL-3.0-or-later
"""Agent-neutral read contract and strict proposal JSON boundary.

This module does not invoke a model, open a socket, read an archived PDF, or
write an effective category.  It exposes the smallest ledger view a local
Codex or Claude Code workflow needs, then delegates proposal semantics to
``ledgerbox.proposals``.  The CLI is a serialization adapter over these values;
future MCP code must not grow a second query or proposal state machine.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal, cast

from .agent_center import AgentPolicy, read_policy
from .config import DataPaths
from .db import repo
from .db.migrate import schema_version
from .ingest.pipeline import verify_ledger
from .proposals import (
    APPLICATION_MODES,
    CLIENTS,
    PROPOSAL_SCHEMA_V1,
    PROPOSAL_SCHEMA_VERSION,
    Producer,
    ProposalGroup,
    ProposalSubmission,
    SubmitResult,
    ValidationResult,
    group_id_for,
    ledger_revision,
)
from .reconcile.checks import PASS
from .triage import (
    TRIAGE_REASON_CODES,
    TRIAGE_ROUTES,
    TRIAGE_SCHEMA_VERSION,
    TriageDraft,
    TriageGroup,
    TriageScope,
    TriageSubmission,
    TriageSubmitResult,
    TriageValidationResult,
)
from .triage import (
    group_id_for as triage_group_id_for,
)

AGENT_SCHEMA_VERSION = 1
MAX_PROPOSAL_JSON_CHARS = 1_000_000
_HASH_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


class AgentInputError(ValueError):
    """A malformed Agent command input safe to report without a traceback."""


class AgentLedgerNotReady(RuntimeError):
    """Candidates are unavailable because at least one ledger check did not pass."""

    def __init__(self, failed_checks: tuple[str, ...]) -> None:
        self.failed_checks = failed_checks
        super().__init__("ledger verification did not pass; run ledgerbox agent status for details")


@dataclass(frozen=True, slots=True)
class AgentCheck:
    check_id: str
    severity: str
    status: str
    message: str


@dataclass(frozen=True, slots=True)
class AgentStatus:
    ledger_schema_version: int
    ledger_revision: str
    ready_for_proposals: bool
    uncategorized_count: int
    checks: tuple[AgentCheck, ...]
    local_policy: AgentPolicy


@dataclass(frozen=True, slots=True)
class AgentCategory:
    id: str
    kind: str
    parent_id: str | None

    @property
    def label(self) -> str:
        """The stored id is the only stable label in today's taxonomy."""
        return self.id


@dataclass(frozen=True, slots=True)
class AgentCategoryCatalog:
    ledger_revision: str
    categories: tuple[AgentCategory, ...]


@dataclass(frozen=True, slots=True)
class AgentCandidate:
    txn_id: str
    date: str
    direction: Literal["in", "out", "zero"]
    amount_minor: int
    currency: str
    raw_descriptor: str


@dataclass(frozen=True, slots=True)
class AgentCandidateBatch:
    ledger_revision: str
    since: str | None
    until: str | None
    matched: int
    candidates: tuple[AgentCandidate, ...]

    @property
    def has_more(self) -> bool:
        return self.matched > len(self.candidates)


def agent_error_to_wire(
    code: str,
    message: str,
    *,
    failed_checks: tuple[str, ...] = (),
) -> dict[str, Any]:
    """One error shape shared by the JSON CLI and the MCP adapter."""
    error: dict[str, Any] = {"code": code, "message": message}
    if failed_checks:
        error["failed_checks"] = list(failed_checks)
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "kind": "ledgerbox.agent.error",
        "error": error,
    }


def agent_status_to_wire(status: AgentStatus) -> dict[str, Any]:
    """Serialize proposal readiness without giving transports a second schema."""
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "kind": "ledgerbox.agent.status",
        "ledger_schema_version": status.ledger_schema_version,
        "proposal_schema_version": PROPOSAL_SCHEMA_VERSION,
        "triage_schema_version": TRIAGE_SCHEMA_VERSION,
        "ledger_revision": status.ledger_revision,
        "ready_for_proposals": status.ready_for_proposals,
        "uncategorized_count": status.uncategorized_count,
        "local_agent_policy": {
            "enabled": status.local_policy.enabled,
            "selected_client": status.local_policy.selected_client,
            "application_mode": status.local_policy.application_mode,
            "auto_classify_new_imports": status.local_policy.auto_classify_new_imports,
        },
        "checks": [
            {
                "check_id": check.check_id,
                "severity": check.severity,
                "status": check.status,
                "message": check.message,
            }
            for check in status.checks
        ],
    }


def agent_categories_to_wire(catalog: AgentCategoryCatalog) -> dict[str, Any]:
    """Serialize the stored taxonomy for every Agent transport."""
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "kind": "ledgerbox.agent.categories",
        "ledger_revision": catalog.ledger_revision,
        "categories": [
            {
                "id": category.id,
                "kind": category.kind,
                "label": category.label,
                "parent_id": category.parent_id,
            }
            for category in catalog.categories
        ],
    }


def agent_candidates_to_wire(batch: AgentCandidateBatch) -> dict[str, Any]:
    """Serialize only the minimum verified facts allowed across the boundary."""
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "kind": "ledgerbox.agent.candidates",
        "ledger_revision": batch.ledger_revision,
        "range": {"since": batch.since, "until": batch.until},
        "matched": batch.matched,
        "returned": len(batch.candidates),
        "has_more": batch.has_more,
        "candidates": [
            {
                "txn_id": candidate.txn_id,
                "date": candidate.date,
                "direction": candidate.direction,
                "amount_minor": candidate.amount_minor,
                "currency": candidate.currency,
                # This is untrusted bank data, not an instruction to an Agent.
                "raw_descriptor": candidate.raw_descriptor,
            }
            for candidate in batch.candidates
        ],
    }


def proposal_to_wire(submission: ProposalSubmission) -> dict[str, Any]:
    """Serialize the exact, canonical object accepted by the submit boundary."""
    value: dict[str, Any] = {
        "schema_version": submission.schema_version,
        "ledger_revision": submission.ledger_revision,
        "producer": {
            "client": submission.producer.client,
            "client_version": submission.producer.client_version,
            "model_reported": submission.producer.model_reported,
        },
        "groups": [
            {
                "group_id": group.group_id,
                "category_id": group.category_id,
                "txn_ids": sorted(group.txn_ids),
            }
            for group in sorted(submission.groups, key=lambda item: item.group_id)
        ],
    }
    if submission.schema_version == PROPOSAL_SCHEMA_VERSION:
        value["application_mode"] = submission.application_mode
    return value


def proposal_validation_to_wire(
    result: ValidationResult, submission: ProposalSubmission
) -> dict[str, Any]:
    """Serialize a dry-run plus the exact normalized object to submit."""
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "kind": "ledgerbox.agent.proposal-validation",
        "valid": True,
        "run_id": result.run_id,
        "proposal_count": result.proposal_count,
        "proposal": proposal_to_wire(submission),
    }


def proposal_submission_to_wire(result: SubmitResult) -> dict[str, Any]:
    """Serialize an idempotent pending-proposal submission result."""
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "kind": "ledgerbox.agent.proposal-submission",
        "run_id": result.run_id,
        "created": result.created,
        "proposal_count": result.proposal_count,
    }


def triage_to_wire(submission: TriageSubmission) -> dict[str, Any]:
    """Serialize the exact normalized triage object accepted by submit."""
    return {
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
        "groups": [
            {
                "group_id": group.group_id,
                "route": group.route,
                "reason_code": group.reason_code,
                "txn_ids": sorted(group.txn_ids),
            }
            for group in sorted(submission.groups, key=lambda item: item.group_id)
        ],
    }


def triage_validation_to_wire(result: TriageValidationResult) -> dict[str, Any]:
    """Return the only exact triage object that may cross the submit boundary."""
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "kind": "ledgerbox.agent.triage-validation",
        "valid": True,
        "run_id": result.run_id,
        "item_count": result.item_count,
        "triage": triage_to_wire(result.submission),
    }


def triage_submission_to_wire(result: TriageSubmitResult) -> dict[str, Any]:
    """Aggregate-only result; it repeats no transaction or bank data."""
    return {
        "schema_version": AGENT_SCHEMA_VERSION,
        "kind": "ledgerbox.agent.triage-submission",
        "run_id": result.run_id,
        "created": result.created,
        "item_count": result.item_count,
    }


def _uncategorized_query(
    *, since: str | None = None, until: str | None = None, limit: int = 1
) -> repo.TransactionQuery:
    return repo.TransactionQuery(
        category=repo.NO_CATEGORY,
        span=repo.DateSpan(since=since, until=until),
        sort="date",
        descending=False,
        limit=limit,
    )


def read_agent_status(conn: sqlite3.Connection, paths: DataPaths) -> AgentStatus:
    """Return the actual nine verifier results plus proposal readiness facts."""
    results = verify_ledger(conn, paths)
    uncategorized = repo.summarize_transactions(conn, _uncategorized_query())["matched"]
    checks = tuple(
        AgentCheck(
            check_id=result.check_id,
            severity=result.severity,
            status=result.status,
            message=result.message,
        )
        for result in results
    )
    return AgentStatus(
        ledger_schema_version=schema_version(conn),
        ledger_revision=ledger_revision(conn),
        ready_for_proposals=all(result.status == PASS for result in results),
        uncategorized_count=int(uncategorized),
        checks=checks,
        local_policy=read_policy(conn),
    )


def read_agent_categories(conn: sqlite3.Connection) -> AgentCategoryCatalog:
    """Read the database taxonomy mirror; do not invent a second category list."""
    categories = tuple(
        AgentCategory(
            id=str(row["id"]),
            kind=str(row["kind"]),
            parent_id=None if row["parent_id"] is None else str(row["parent_id"]),
        )
        for row in repo.list_categories(conn)
    )
    return AgentCategoryCatalog(ledger_revision=ledger_revision(conn), categories=categories)


def read_agent_candidates(
    conn: sqlite3.Connection,
    paths: DataPaths,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = repo.MAX_PAGE_SIZE,
) -> AgentCandidateBatch:
    """Return verified, unanswered statement lines and only their allowed fields."""
    results = verify_ledger(conn, paths)
    failed = tuple(result.check_id for result in results if result.status != PASS)
    if failed:
        raise AgentLedgerNotReady(failed)

    query = _uncategorized_query(since=since, until=until, limit=limit)
    summary = repo.summarize_transactions(conn, query)
    rows = repo.list_transactions(conn, query)
    candidates = tuple(
        AgentCandidate(
            txn_id=str(row["txn_id"]),
            date=str(row["date"]),
            direction=(
                "in"
                if int(row["amount_minor"]) > 0
                else "out"
                if int(row["amount_minor"]) < 0
                else "zero"
            ),
            amount_minor=int(row["amount_minor"]),
            currency=str(row["currency"]),
            raw_descriptor=str(row["raw_descriptor"]),
        )
        for row in rows
    )
    return AgentCandidateBatch(
        ledger_revision=ledger_revision(conn),
        since=since,
        until=until,
        matched=int(summary["matched"]),
        candidates=candidates,
    )


def _duplicate_safe_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AgentInputError(f"JSON object repeats key {key!r}")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise AgentInputError(f"JSON constant {value!r} is not allowed")


def _object(
    value: object,
    *,
    path: str,
    required: set[str],
    optional: set[str] | None = None,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise AgentInputError(f"{path} must be a JSON object")
    result = value
    allowed = required | (optional or set())
    missing = sorted(required - set(result))
    extra = sorted(set(result) - allowed)
    if missing:
        raise AgentInputError(f"{path} is missing field(s): {', '.join(missing)}")
    if extra:
        raise AgentInputError(f"{path} has unknown field(s): {', '.join(extra)}")
    return result


def _string(value: object, *, path: str, max_length: int | None = None) -> str:
    if type(value) is not str or not value:
        raise AgentInputError(f"{path} must be a non-empty JSON string")
    if max_length is not None and len(value) > max_length:
        raise AgentInputError(f"{path} is longer than {max_length} characters")
    return value


def _optional_string(value: object, *, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path=path, max_length=200)


def _proposal_from_wire(value: object, *, allow_draft_groups: bool) -> ProposalSubmission:
    """Convert one JSON value to the shared service type.

    The validation boundary may fill a missing content-derived ``group_id`` so
    an Agent never needs a shell/hash helper.  Submit remains strict and accepts
    only the exact normalized object returned by validation.
    """
    if type(value) is not dict:
        raise AgentInputError("proposal must be a JSON object")
    version = value.get("schema_version")
    base_fields = {"schema_version", "ledger_revision", "producer", "groups"}
    if type(version) is not int or version not in {
        PROPOSAL_SCHEMA_V1,
        PROPOSAL_SCHEMA_VERSION,
    }:
        raise AgentInputError(
            f"proposal.schema_version must be {PROPOSAL_SCHEMA_V1} or {PROPOSAL_SCHEMA_VERSION}"
        )
    root = _object(
        value,
        path="proposal",
        required=(
            base_fields
            if version == PROPOSAL_SCHEMA_V1
            else base_fields | {"application_mode"}
        ),
    )
    application_mode: Literal["review_first", "automatic"] | None = None
    if version == PROPOSAL_SCHEMA_VERSION:
        raw_mode = root["application_mode"]
        if type(raw_mode) is not str or raw_mode not in APPLICATION_MODES:
            raise AgentInputError(
                "proposal.application_mode must be review_first or automatic"
            )
        application_mode = cast(Literal["review_first", "automatic"], raw_mode)

    revision = _string(root["ledger_revision"], path="proposal.ledger_revision")
    if _HASH_ID.fullmatch(revision) is None:
        raise AgentInputError("proposal.ledger_revision must be a sha256 content id")

    producer_value = _object(
        root["producer"],
        path="proposal.producer",
        required={"client"},
        optional={"client_version", "model_reported"},
    )
    client = _string(producer_value["client"], path="proposal.producer.client")
    if client not in CLIENTS:
        raise AgentInputError("proposal.producer.client must be codex, claude-code, or other")
    producer = Producer(
        client=client,
        client_version=_optional_string(
            producer_value.get("client_version"), path="proposal.producer.client_version"
        ),
        model_reported=_optional_string(
            producer_value.get("model_reported"), path="proposal.producer.model_reported"
        ),
    )

    groups_value = root["groups"]
    minimum_groups = 1 if version == PROPOSAL_SCHEMA_V1 else 0
    if (
        type(groups_value) is not list
        or not minimum_groups <= len(groups_value) <= repo.MAX_PAGE_SIZE
    ):
        raise AgentInputError(
            f"proposal.groups must contain {minimum_groups}..{repo.MAX_PAGE_SIZE} groups"
        )
    groups: list[ProposalGroup] = []
    for index, raw_group in enumerate(groups_value):
        path = f"proposal.groups[{index}]"
        group = _object(
            raw_group,
            path=path,
            required=(
                {"category_id", "txn_ids"}
                if allow_draft_groups
                else {"group_id", "category_id", "txn_ids"}
            ),
            optional={"group_id"} if allow_draft_groups else None,
        )
        category_id = _string(group["category_id"], path=f"{path}.category_id", max_length=200)
        txn_values = group["txn_ids"]
        if type(txn_values) is not list or not 1 <= len(txn_values) <= repo.MAX_PAGE_SIZE:
            raise AgentInputError(f"{path}.txn_ids must contain 1..{repo.MAX_PAGE_SIZE} ids")
        txn_ids = tuple(
            _string(txn_id, path=f"{path}.txn_ids[{txn_index}]")
            for txn_index, txn_id in enumerate(txn_values)
        )
        if "group_id" in group:
            group_id = _string(group["group_id"], path=f"{path}.group_id")
            if _HASH_ID.fullmatch(group_id) is None:
                raise AgentInputError(f"{path}.group_id must be a sha256 content id")
        elif allow_draft_groups:
            group_id = group_id_for(category_id, txn_ids)
        else:  # pragma: no cover - strict _object validation requires this field.
            raise AssertionError("strict proposal group reached parser without group_id")
        groups.append(
            ProposalGroup(
                group_id=group_id,
                category_id=category_id,
                txn_ids=txn_ids,
            )
        )

    return ProposalSubmission(
        schema_version=version,
        ledger_revision=revision,
        producer=producer,
        groups=tuple(groups),
        application_mode=application_mode,
    )


def proposal_submission_from_wire(value: object) -> ProposalSubmission:
    """Convert one exact submit object to the shared proposal service type."""
    return _proposal_from_wire(value, allow_draft_groups=False)


def proposal_draft_from_wire(value: object) -> ProposalSubmission:
    """Normalize a validation draft while preserving strict known fields."""
    return _proposal_from_wire(value, allow_draft_groups=True)


def _triage_from_wire(
    value: object, *, normalized: bool
) -> TriageDraft | TriageSubmission:
    """Parse a triage draft or the exact normalized validation result."""
    root = _object(
        value,
        path="triage",
        required=(
            {
                "schema_version",
                "ledger_revision",
                "scope_revision",
                "scope",
                "producer",
                "groups",
            }
            if normalized
            else {"schema_version", "ledger_revision", "scope", "producer", "groups"}
        ),
    )
    version = root["schema_version"]
    if type(version) is not int or version != TRIAGE_SCHEMA_VERSION:
        raise AgentInputError(f"triage.schema_version must be {TRIAGE_SCHEMA_VERSION}")
    revision = _string(root["ledger_revision"], path="triage.ledger_revision")
    if _HASH_ID.fullmatch(revision) is None:
        raise AgentInputError("triage.ledger_revision must be a sha256 content id")

    scope_value = _object(
        root["scope"],
        path="triage.scope",
        required={"since", "until"},
    )
    since = scope_value["since"]
    until = scope_value["until"]
    if since is not None:
        since = _string(since, path="triage.scope.since", max_length=10)
    if until is not None:
        until = _string(until, path="triage.scope.until", max_length=10)
    try:
        repo.DateSpan(since=since, until=until)
    except ValueError as error:
        raise AgentInputError(str(error)) from error
    scope = TriageScope(since=since, until=until)

    producer_value = _object(
        root["producer"],
        path="triage.producer",
        required={"client"},
        optional={"client_version", "model_reported"},
    )
    client = _string(producer_value["client"], path="triage.producer.client")
    if client not in CLIENTS:
        raise AgentInputError("triage.producer.client must be codex, claude-code, or other")
    producer = Producer(
        client=client,
        client_version=_optional_string(
            producer_value.get("client_version"), path="triage.producer.client_version"
        ),
        model_reported=_optional_string(
            producer_value.get("model_reported"), path="triage.producer.model_reported"
        ),
    )

    groups_value = root["groups"]
    if type(groups_value) is not list or not 1 <= len(groups_value) <= repo.MAX_PAGE_SIZE:
        raise AgentInputError(f"triage.groups must contain 1..{repo.MAX_PAGE_SIZE} groups")
    groups: list[TriageGroup] = []
    for index, raw_group in enumerate(groups_value):
        path = f"triage.groups[{index}]"
        required = {"route", "reason_code", "txn_ids"}
        if normalized:
            required.add("group_id")
        group = _object(raw_group, path=path, required=required)
        route = _string(group["route"], path=f"{path}.route")
        if route not in TRIAGE_ROUTES:
            raise AgentInputError(f"{path}.route is not a known triage route")
        reason_code = _string(group["reason_code"], path=f"{path}.reason_code")
        if reason_code not in TRIAGE_REASON_CODES[route]:
            raise AgentInputError(f"{path}.reason_code does not belong to route {route}")
        txn_values = group["txn_ids"]
        if type(txn_values) is not list or not 1 <= len(txn_values) <= repo.MAX_PAGE_SIZE:
            raise AgentInputError(f"{path}.txn_ids must contain 1..{repo.MAX_PAGE_SIZE} ids")
        txn_ids = tuple(
            _string(txn_id, path=f"{path}.txn_ids[{txn_index}]")
            for txn_index, txn_id in enumerate(txn_values)
        )
        if len(txn_ids) != len(set(txn_ids)):
            raise AgentInputError(f"{path}.txn_ids must be unique")
        if normalized:
            group_id = _string(group["group_id"], path=f"{path}.group_id")
            if _HASH_ID.fullmatch(group_id) is None:
                raise AgentInputError(f"{path}.group_id must be a sha256 content id")
        else:
            group_id = triage_group_id_for(route, reason_code, txn_ids)
        groups.append(
            TriageGroup(
                group_id=group_id,
                route=route,
                reason_code=reason_code,
                txn_ids=txn_ids,
            )
        )

    if normalized:
        scope_revision = _string(root["scope_revision"], path="triage.scope_revision")
        if _HASH_ID.fullmatch(scope_revision) is None:
            raise AgentInputError("triage.scope_revision must be a sha256 content id")
        return TriageSubmission(
            schema_version=version,
            ledger_revision=revision,
            scope_revision=scope_revision,
            scope=scope,
            producer=producer,
            groups=tuple(groups),
        )
    return TriageDraft(
        schema_version=version,
        ledger_revision=revision,
        scope=scope,
        producer=producer,
        groups=tuple(groups),
    )


def triage_draft_from_wire(value: object) -> TriageDraft:
    parsed = _triage_from_wire(value, normalized=False)
    assert isinstance(parsed, TriageDraft)
    return parsed


def triage_submission_from_wire(value: object) -> TriageSubmission:
    parsed = _triage_from_wire(value, normalized=True)
    assert isinstance(parsed, TriageSubmission)
    return parsed


def _parse_agent_json(text: str, *, label: str) -> object:
    if not text.strip():
        raise AgentInputError(f"{label} JSON on stdin is empty")
    if len(text) > MAX_PROPOSAL_JSON_CHARS:
        raise AgentInputError(
            f"{label} JSON is larger than {MAX_PROPOSAL_JSON_CHARS} characters"
        )
    try:
        return json.loads(
            text,
            object_pairs_hook=_duplicate_safe_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise AgentInputError(
            f"{label} JSON is invalid at line {error.lineno}, column {error.colno}"
        ) from error


def _parse_proposal_json(text: str, *, allow_draft_groups: bool) -> ProposalSubmission:
    """Parse bounded JSON while rejecting duplicate keys and non-JSON numbers."""
    value = _parse_agent_json(text, label="proposal")
    return _proposal_from_wire(value, allow_draft_groups=allow_draft_groups)


def parse_proposal_json(text: str) -> ProposalSubmission:
    """Parse one exact submit object."""
    return _parse_proposal_json(text, allow_draft_groups=False)


def parse_proposal_draft_json(text: str) -> ProposalSubmission:
    """Parse and normalize one validate-only draft."""
    return _parse_proposal_json(text, allow_draft_groups=True)


def parse_triage_draft_json(text: str) -> TriageDraft:
    """Parse a strict draft that omits content-derived ids."""
    return triage_draft_from_wire(_parse_agent_json(text, label="triage"))


def parse_triage_json(text: str) -> TriageSubmission:
    """Parse only the exact normalized triage object returned by validation."""
    return triage_submission_from_wire(_parse_agent_json(text, label="triage"))
