# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only aggregate C4 preflight and frozen-reference scorer.

Real transaction identifiers and amounts remain process-local.  Stdout is one
strict aggregate JSON document suitable for a repository-external run record.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any

from ledgerbox.agent import read_agent_candidates, read_agent_status
from ledgerbox.config import DataPaths
from ledgerbox.db import repo
from ledgerbox.db.connection import connect_read_only
from ledgerbox.db.migrate import schema_version
from ledgerbox.frozen_eval import (
    BaselineSnapshot,
    CandidateReference,
    FrozenEvalError,
    ProposalDecision,
    ReachBaseline,
    compare_preflight,
    score_proposals,
)
from ledgerbox.proposals import get_run, list_runs

STABLE_TABLES = (
    "source_file",
    "raw_record",
    "account",
    "commodity",
    "txn",
    "posting",
    "txn_identity",
    "balance_assertion",
    "review_item",
    "category",
    "price",
    "lot",
    "corporate_action",
)


def _paths(raw: str) -> DataPaths:
    paths = DataPaths.resolve(raw, create=False)
    if not paths.db.is_file():
        raise FrozenEvalError("ledger_missing", "one required C4 ledger is missing")
    return paths


def _snapshot(conn: sqlite3.Connection, paths: DataPaths) -> BaselineSnapshot:
    status = read_agent_status(conn, paths)
    categories = tuple(
        sorted(
            (
                str(row["id"]),
                str(row["kind"]),
                None if row["parent_id"] is None else str(row["parent_id"]),
            )
            for row in repo.list_categories(conn)
        )
    )
    counts = repo.row_counts(conn)
    batch = read_agent_candidates(conn, paths, limit=repo.MAX_PAGE_SIZE)
    if batch.has_more:
        raise FrozenEvalError(
            "candidate_limit_exceeded",
            "the all-dates candidate denominator exceeds the supported frozen batch",
        )
    audit_count = sum(count for name, count in counts.items() if name.startswith("agent_"))
    return BaselineSnapshot(
        schema_version=schema_version(conn),
        ledger_revision=status.ledger_revision,
        verifier_passed=sum(check.status == "pass" for check in status.checks),
        verifier_total=len(status.checks),
        taxonomy=categories,
        stable_row_counts=tuple((name, counts[name]) for name in STABLE_TABLES),
        candidate_ids=frozenset(candidate.txn_id for candidate in batch.candidates),
        category_override_count=counts["category_override"],
        agent_audit_count=audit_count,
    )


def _reference_and_reach(
    *,
    truth: sqlite3.Connection,
    base: sqlite3.Connection,
    base_paths: DataPaths,
) -> tuple[tuple[CandidateReference, ...], ReachBaseline]:
    truth_kinds = {str(row["id"]): str(row["kind"]) for row in repo.list_categories(truth)}
    batch = read_agent_candidates(base, base_paths, limit=repo.MAX_PAGE_SIZE)
    references: list[CandidateReference] = []
    for candidate in batch.candidates:
        row = repo.get_transaction(truth, candidate.txn_id)
        if row is None or row["category_id"] is None:
            raise FrozenEvalError(
                "truth_label_missing",
                "Truth does not label every frozen Base candidate",
            )
        category_id = str(row["category_id"])
        category_kind = truth_kinds.get(category_id)
        if category_kind is None:
            raise FrozenEvalError(
                "truth_category_missing",
                "Truth uses a category outside its stored taxonomy",
            )
        references.append(
            CandidateReference(
                txn_id=candidate.txn_id,
                amount_minor=candidate.amount_minor,
                truth_category_id=category_id,
                truth_category_kind=category_kind,
            )
        )

    base_spend = repo.category_spend(base)
    truth_spend = repo.category_spend(truth)
    return tuple(references), ReachBaseline(
        rule_spend_lines=sum(part.txn_count for part in base_spend.slices if part.category_id),
        rule_spend_minor=_spend_magnitude(
            sum(part.spend_minor for part in base_spend.slices if part.category_id)
        ),
        truth_spend_lines=truth_spend.txn_count,
        truth_spend_minor=_spend_magnitude(truth_spend.total_minor),
    )


def _basis_points(numerator: int, denominator: int) -> int | None:
    if denominator == 0:
        return None
    return round(numerator * 10_000 / denominator)


def _spend_magnitude(signed_minor: int) -> int:
    """Normalize Ledgerbox's non-positive Out convention for ratio arithmetic."""

    if signed_minor > 0:
        raise FrozenEvalError(
            "reach_invalid", "a net-spend aggregate has the wrong ledger sign"
        )
    return -signed_minor


def _public_score(report: dict[str, Any]) -> dict[str, Any]:
    reach = report["correct_reach"]
    return {
        "candidate_denominator": report["candidate_denominator"],
        "proposal_coverage": report["proposal_coverage"],
        "agreement": report["agreement"],
        "omission": report["omission"],
        "wrong_category": report["wrong_category"],
        "correct_reach": {
            "line_numerator": reach["line_numerator"],
            "line_denominator": reach["line_denominator"],
            "line_basis_points": _basis_points(
                reach["line_numerator"], reach["line_denominator"]
            ),
            "amount_basis_points": _basis_points(
                reach["amount_numerator_minor"], reach["amount_denominator_minor"]
            ),
        },
        "auto_write_eligible": 0,
    }


def _preflight(args: argparse.Namespace) -> dict[str, Any]:
    truth_paths = _paths(args.truth)
    base_paths = _paths(args.base)
    codex_paths = _paths(args.codex)
    claude_paths = _paths(args.claude)
    with (
        connect_read_only(truth_paths.db) as truth,
        connect_read_only(base_paths.db) as base,
        connect_read_only(codex_paths.db) as codex,
        connect_read_only(claude_paths.db) as claude,
    ):
        truth_status = read_agent_status(truth, truth_paths)
        if schema_version(truth) != 10:
            raise FrozenEvalError("schema_mismatch", "Truth schema is not 10")
        if len(truth_status.checks) != 9 or any(
            check.status != "pass" for check in truth_status.checks
        ):
            raise FrozenEvalError("verifier_failed", "Truth verifier is not 9/9")
        if truth_status.uncategorized_count != 0:
            raise FrozenEvalError("truth_not_frozen", "Truth has an effective unclassified row")

        truth_snapshot = _snapshot(truth, truth_paths)
        base_snapshot = _snapshot(base, base_paths)
        if truth_snapshot.taxonomy != base_snapshot.taxonomy:
            raise FrozenEvalError(
                "truth_base_taxonomy_mismatch", "Truth and Base taxonomy differ"
            )
        if truth_snapshot.ledger_revision != base_snapshot.ledger_revision:
            raise FrozenEvalError(
                "truth_base_ledger_revision_mismatch", "Truth and Base ledger facts differ"
            )
        if truth_snapshot.stable_row_counts != base_snapshot.stable_row_counts:
            raise FrozenEvalError(
                "truth_base_row_count_mismatch", "Truth and Base stable row counts differ"
            )
        result = compare_preflight(
            base=base_snapshot,
            codex=_snapshot(codex, codex_paths),
            claude=_snapshot(claude, claude_paths),
        )
        references, _ = _reference_and_reach(
            truth=truth,
            base=base,
            base_paths=base_paths,
        )
        if len(references) != len(base_snapshot.candidate_ids):
            raise FrozenEvalError(
                "truth_label_missing",
                "Truth does not exactly cover the frozen candidate denominator",
            )

    result.update(
        {
            "truth": {
                "schema_version": 10,
                "verifier": {"passed": 9, "total": 9},
                "effective_unclassified": 0,
                "reference_complete": True,
            },
            "stable_table_count": len(STABLE_TABLES),
            "rules_source_equal": True,
            "truth_base_taxonomy_equal": True,
            "truth_base_ledger_revision_equal": True,
            "truth_base_stable_row_counts_equal": True,
        }
    )
    return result


def _score(args: argparse.Namespace) -> dict[str, Any]:
    truth_paths = _paths(args.truth)
    base_paths = _paths(args.base)
    clone_paths = _paths(args.clone)
    with (
        connect_read_only(truth_paths.db) as truth,
        connect_read_only(base_paths.db) as base,
        connect_read_only(clone_paths.db) as clone,
    ):
        base_snapshot = _snapshot(base, base_paths)
        clone_snapshot = _snapshot(clone, clone_paths)
        # A scored clone may contain exactly one proposal audit but must still
        # contain no effective category override.
        if clone_snapshot.category_override_count:
            raise FrozenEvalError("effective_write_detected", "the clone has an effective override")
        for name in (
            "schema_version",
            "ledger_revision",
            "taxonomy",
            "stable_row_counts",
            "candidate_ids",
        ):
            if getattr(base_snapshot, name) != getattr(clone_snapshot, name):
                raise FrozenEvalError("clone_drift", "the scored clone drifted from Base")

        runs = list_runs(clone, limit=2)
        if len(runs) != 1:
            raise FrozenEvalError(
                "run_count_mismatch", "the clone must contain exactly one proposal run"
            )
        run = get_run(clone, runs[0].run_id)
        if run is None or run.producer.client != args.client:
            raise FrozenEvalError(
                "producer_mismatch", "the proposal producer does not match the clone"
            )
        if any(item.outcome != "pending" for item in run.proposals):
            raise FrozenEvalError(
                "proposal_not_pending", "every scored proposal must remain pending"
            )
        if clone_snapshot.agent_audit_count != len(run.proposals) + 1:
            raise FrozenEvalError(
                "unexpected_agent_audit",
                "the clone contains Agent audit rows outside its one proposal run",
            )

        categories = {str(row["id"]): str(row["kind"]) for row in repo.list_categories(clone)}
        decisions: list[ProposalDecision] = []
        for proposal in run.proposals:
            kind = categories.get(proposal.suggested_category_id)
            if kind is None:
                raise FrozenEvalError(
                    "proposal_category_missing", "a proposal category is unavailable"
                )
            decisions.append(
                ProposalDecision(
                    txn_id=proposal.txn_id,
                    category_id=proposal.suggested_category_id,
                    category_kind=kind,
                )
            )

        references, reach = _reference_and_reach(
            truth=truth,
            base=base,
            base_paths=base_paths,
        )
        scored = score_proposals(
            candidate_ids=base_snapshot.candidate_ids,
            references=references,
            proposals=tuple(decisions),
            reach=reach,
        )
        public = _public_score(scored)
        public.update(
            {
                "status": "frozen_reference_scored",
                "client": args.client,
                "producer": {
                    "client": run.producer.client,
                    "client_version": run.producer.client_version,
                    "model_reported": run.producer.model_reported,
                },
                "proposal_state": "pending_human_review",
                "effective_category_changed": False,
            }
        )
        return public


def _compare(args: argparse.Namespace) -> dict[str, Any]:
    codex = _score(
        argparse.Namespace(
            truth=args.truth,
            base=args.base,
            clone=args.codex,
            client="codex",
        )
    )
    claude = _score(
        argparse.Namespace(
            truth=args.truth,
            base=args.base,
            clone=args.claude,
            client="claude-code",
        )
    )
    if codex["candidate_denominator"] != claude["candidate_denominator"]:
        raise FrozenEvalError(
            "candidate_denominator_mismatch",
            "the scored clients do not share one candidate denominator",
        )
    return {
        "status": "frozen_reference_comparison",
        "candidate_denominator": codex["candidate_denominator"],
        "codex": codex,
        "claude_code": claude,
        "effective_category_changed": False,
        "auto_write_eligible": 0,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--truth", required=True)
    preflight.add_argument("--base", required=True)
    preflight.add_argument("--codex", required=True)
    preflight.add_argument("--claude", required=True)
    score = subparsers.add_parser("score")
    score.add_argument("--truth", required=True)
    score.add_argument("--base", required=True)
    score.add_argument("--clone", required=True)
    score.add_argument("--client", required=True, choices=("codex", "claude-code"))
    compare = subparsers.add_parser("compare")
    compare.add_argument("--truth", required=True)
    compare.add_argument("--base", required=True)
    compare.add_argument("--codex", required=True)
    compare.add_argument("--claude", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "preflight":
            report = _preflight(args)
        elif args.command == "score":
            report = _score(args)
        else:
            report = _compare(args)
    except FrozenEvalError as exc:
        print(
            json.dumps(
                {"status": "failed", "error": {"code": exc.code, "message": str(exc)}},
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 3
    print(json.dumps(report, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
