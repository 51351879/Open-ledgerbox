# SPDX-License-Identifier: AGPL-3.0-or-later
"""Aggregate-only C4 frozen-reference comparison primitives.

The objects in this module deliberately carry opaque transaction identifiers only
inside the process.  Public reports and errors expose counts and stable error codes,
never identifiers, descriptors, dates, account details, or per-row amounts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class FrozenEvalError(RuntimeError):
    """A fail-closed C4 condition whose text is safe for aggregate logs."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class BaselineSnapshot:
    schema_version: int
    ledger_revision: str
    verifier_passed: int
    verifier_total: int
    taxonomy: tuple[tuple[str, str, str | None], ...]
    stable_row_counts: tuple[tuple[str, int], ...]
    candidate_ids: frozenset[str]
    category_override_count: int
    agent_audit_count: int


@dataclass(frozen=True, slots=True)
class CandidateReference:
    txn_id: str
    amount_minor: int
    truth_category_id: str
    truth_category_kind: str


@dataclass(frozen=True, slots=True)
class ProposalDecision:
    txn_id: str
    category_id: str
    category_kind: str


@dataclass(frozen=True, slots=True)
class ReachBaseline:
    """Rule-covered numerator and frozen-Truth spending denominator."""

    rule_spend_lines: int
    rule_spend_minor: int
    truth_spend_lines: int
    truth_spend_minor: int


def _check_verified(name: str, snapshot: BaselineSnapshot) -> None:
    if snapshot.verifier_total != 9 or snapshot.verifier_passed != 9:
        raise FrozenEvalError("verifier_failed", f"{name} verifier is not 9/9")
    if snapshot.schema_version != 10:
        raise FrozenEvalError("schema_mismatch", f"{name} schema is not 10")


def compare_preflight(
    *,
    base: BaselineSnapshot,
    codex: BaselineSnapshot,
    claude: BaselineSnapshot,
) -> dict[str, Any]:
    """Prove clone equality without serializing any member of the ID set."""

    for name, snapshot in (("Base", base), ("Codex clone", codex), ("Claude clone", claude)):
        _check_verified(name, snapshot)
        if snapshot.category_override_count or snapshot.agent_audit_count:
            raise FrozenEvalError(
                "clone_not_clean",
                f"{name} contains an effective override or proposal audit",
            )

    if not (base.taxonomy == codex.taxonomy == claude.taxonomy):
        raise FrozenEvalError("taxonomy_mismatch", "Base and clone taxonomy differ")
    if not (base.ledger_revision == codex.ledger_revision == claude.ledger_revision):
        raise FrozenEvalError("ledger_revision_mismatch", "Base and clone ledger facts differ")
    if not (base.stable_row_counts == codex.stable_row_counts == claude.stable_row_counts):
        raise FrozenEvalError("row_count_mismatch", "Base and clone stable row counts differ")
    if not (base.candidate_ids == codex.candidate_ids == claude.candidate_ids):
        raise FrozenEvalError("candidate_set_mismatch", "Base and clone candidate sets differ")

    return {
        "status": "preflight_passed",
        "schema_version": base.schema_version,
        "verifier": {"passed": 9, "total": 9},
        "taxonomy": {"equal": True, "count": len(base.taxonomy)},
        "ledger_revision_equal": True,
        "stable_row_counts_equal": True,
        "candidate_set": {"equal": True, "count": len(base.candidate_ids)},
        "clean_base_and_clones": True,
    }


def _validate_reach(reach: ReachBaseline) -> None:
    values = (
        reach.rule_spend_lines,
        reach.rule_spend_minor,
        reach.truth_spend_lines,
        reach.truth_spend_minor,
    )
    if any(type(value) is not int or value < 0 for value in values):
        raise FrozenEvalError("reach_invalid", "reach inputs must be non-negative integers")
    if reach.rule_spend_lines > reach.truth_spend_lines:
        raise FrozenEvalError("reach_invalid", "rule line reach exceeds the Truth denominator")
    if reach.rule_spend_minor > reach.truth_spend_minor:
        raise FrozenEvalError("reach_invalid", "rule amount reach exceeds the Truth denominator")


def score_proposals(
    *,
    candidate_ids: frozenset[str],
    references: tuple[CandidateReference, ...],
    proposals: tuple[ProposalDecision, ...],
    reach: ReachBaseline,
) -> dict[str, Any]:
    """Score one proposal run against frozen human labels, returning aggregates only."""

    _validate_reach(reach)
    reference_ids = [item.txn_id for item in references]
    if len(reference_ids) != len(set(reference_ids)):
        raise FrozenEvalError("truth_label_duplicate", "Truth contains duplicate candidate labels")
    if set(reference_ids) != candidate_ids:
        raise FrozenEvalError(
            "truth_label_missing",
            "Truth labels do not exactly cover the frozen candidate denominator",
        )

    proposal_ids = [item.txn_id for item in proposals]
    if len(proposal_ids) != len(set(proposal_ids)):
        raise FrozenEvalError("duplicate_proposal", "a proposal names a candidate more than once")
    if not set(proposal_ids).issubset(candidate_ids):
        raise FrozenEvalError(
            "proposal_scope_mismatch",
            "a proposal names a transaction outside the frozen candidate denominator",
        )

    by_id = {item.txn_id: item for item in references}
    exact = 0
    ordinary_proposed = 0
    ordinary_exact = 0
    ordinary_wrong = 0
    transfer_proposed = 0
    transfer_exact = 0
    transfer_wrong = 0
    correct_spend_lines = reach.rule_spend_lines
    correct_spend_minor = reach.rule_spend_minor

    for proposal in proposals:
        reference = by_id[proposal.txn_id]
        is_exact = proposal.category_id == reference.truth_category_id
        if is_exact:
            exact += 1

        if proposal.category_kind == "transfer":
            transfer_proposed += 1
            if is_exact:
                transfer_exact += 1
            else:
                transfer_wrong += 1
            continue

        ordinary_proposed += 1
        if not is_exact:
            ordinary_wrong += 1
            continue

        ordinary_exact += 1
        if reference.truth_category_kind == "expense":
            correct_spend_lines += 1
            correct_spend_minor += -reference.amount_minor

    if correct_spend_lines > reach.truth_spend_lines:
        raise FrozenEvalError("reach_invalid", "scored line reach exceeds the Truth denominator")
    if correct_spend_minor > reach.truth_spend_minor:
        raise FrozenEvalError("reach_invalid", "scored amount reach exceeds the Truth denominator")

    proposed = len(proposals)
    denominator = len(candidate_ids)
    omitted = denominator - proposed
    return {
        "candidate_denominator": denominator,
        "proposal_coverage": {"numerator": proposed, "denominator": denominator},
        "agreement": {
            "exact": exact,
            "proposed": proposed,
            "ordinary_exact": ordinary_exact,
            "ordinary_proposed": ordinary_proposed,
            "transfer_exact": transfer_exact,
            "transfer_proposed": transfer_proposed,
        },
        "omission": {"numerator": omitted, "denominator": denominator},
        "wrong_category": {
            "ordinary": ordinary_wrong,
            "transfer": transfer_wrong,
        },
        "correct_reach": {
            "line_numerator": correct_spend_lines,
            "line_denominator": reach.truth_spend_lines,
            "amount_numerator_minor": correct_spend_minor,
            "amount_denominator_minor": reach.truth_spend_minor,
        },
        # C4 can inform C5. It cannot authorize an automatic write, and an
        # exact transfer remains permanently review-only.
        "auto_write_eligible": 0,
    }
