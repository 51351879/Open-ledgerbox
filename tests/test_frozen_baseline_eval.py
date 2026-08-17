# SPDX-License-Identifier: AGPL-3.0-or-later
"""C4 frozen-reference scoring stays aggregate-only and fail closed."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from ledgerbox.frozen_eval import (
    BaselineSnapshot,
    CandidateReference,
    FrozenEvalError,
    ProposalDecision,
    ReachBaseline,
    compare_preflight,
    score_proposals,
)
from tools.evaluate_frozen_baseline import _public_score, _spend_magnitude, main

ROOT = Path(__file__).parents[1]
C4_PROMPT = ROOT / ".agents" / "skills" / "ledgerbox" / "evals" / "c4-run-prompt.md"


def _snapshot(*, candidates: tuple[str, ...] = ("private-a", "private-b")) -> BaselineSnapshot:
    return BaselineSnapshot(
        schema_version=10,
        ledger_revision="opaque-ledger-revision",
        verifier_passed=9,
        verifier_total=9,
        taxonomy=(("dining", "expense", None), ("transfer", "transfer", None)),
        stable_row_counts=(
            ("source_file", 2),
            ("raw_record", 4),
            ("txn", 4),
            ("posting", 8),
        ),
        candidate_ids=frozenset(candidates),
        category_override_count=0,
        agent_audit_count=0,
    )


def _references() -> tuple[CandidateReference, ...]:
    return (
        CandidateReference("private-a", -4_001, "dining", "expense"),
        CandidateReference("private-b", -900_007, "transfer", "transfer"),
    )


def _reach() -> ReachBaseline:
    return ReachBaseline(
        rule_spend_lines=3,
        rule_spend_minor=12_000,
        truth_spend_lines=4,
        truth_spend_minor=16_001,
    )


def test_preflight_requires_identical_base_and_clone_candidates() -> None:
    base = _snapshot()
    claude = replace(base, candidate_ids=frozenset({"private-a"}))

    with pytest.raises(FrozenEvalError) as caught:
        compare_preflight(base=base, codex=base, claude=claude)

    assert caught.value.code == "candidate_set_mismatch"
    assert "private" not in str(caught.value)


def test_preflight_requires_identical_taxonomy_rows_and_clean_clones() -> None:
    base = _snapshot()
    wrong_taxonomy = replace(base, taxonomy=(("dining", "expense", None),))
    wrong_rows = replace(base, stable_row_counts=(("txn", 3),))
    dirty = replace(base, category_override_count=1)
    changed_facts = replace(base, ledger_revision="different-opaque-revision")

    with pytest.raises(FrozenEvalError, match="taxonomy"):
        compare_preflight(base=base, codex=wrong_taxonomy, claude=base)
    with pytest.raises(FrozenEvalError, match="row counts"):
        compare_preflight(base=base, codex=wrong_rows, claude=base)
    with pytest.raises(FrozenEvalError) as caught:
        compare_preflight(base=base, codex=dirty, claude=base)
    assert caught.value.code == "clone_not_clean"
    with pytest.raises(FrozenEvalError) as caught:
        compare_preflight(base=base, codex=changed_facts, claude=base)
    assert caught.value.code == "ledger_revision_mismatch"


def test_truth_must_label_every_candidate() -> None:
    with pytest.raises(FrozenEvalError) as caught:
        score_proposals(
            candidate_ids=frozenset({"private-a", "private-b"}),
            references=_references()[:1],
            proposals=(),
            reach=_reach(),
        )

    assert caught.value.code == "truth_label_missing"
    assert "private" not in str(caught.value)


def test_duplicate_proposal_fails_instead_of_inflating_coverage() -> None:
    duplicate = ProposalDecision("private-a", "dining", "expense")

    with pytest.raises(FrozenEvalError) as caught:
        score_proposals(
            candidate_ids=frozenset({"private-a", "private-b"}),
            references=_references(),
            proposals=(duplicate, duplicate),
            reach=_reach(),
        )

    assert caught.value.code == "duplicate_proposal"


def test_wrong_ordinary_category_is_wrong_and_not_correct_reach() -> None:
    report = score_proposals(
        candidate_ids=frozenset({"private-a", "private-b"}),
        references=_references(),
        proposals=(ProposalDecision("private-a", "fees", "expense"),),
        reach=_reach(),
    )

    assert report["wrong_category"]["ordinary"] == 1
    assert report["agreement"]["exact"] == 0
    assert report["correct_reach"]["line_numerator"] == 3
    assert report["correct_reach"]["amount_numerator_minor"] == 12_000


def test_exact_transfer_is_reported_but_never_auto_write_eligible() -> None:
    report = score_proposals(
        candidate_ids=frozenset({"private-a", "private-b"}),
        references=_references(),
        proposals=(ProposalDecision("private-b", "transfer", "transfer"),),
        reach=_reach(),
    )

    assert report["agreement"]["transfer_exact"] == 1
    assert report["auto_write_eligible"] == 0
    assert report["correct_reach"]["line_numerator"] == 3
    assert report["correct_reach"]["amount_numerator_minor"] == 12_000


def test_line_and_net_spend_amount_reach_keep_separate_denominators() -> None:
    report = score_proposals(
        candidate_ids=frozenset({"private-a", "private-b"}),
        references=_references(),
        proposals=(ProposalDecision("private-a", "dining", "expense"),),
        reach=_reach(),
    )

    assert report["correct_reach"] == {
        "line_numerator": 4,
        "line_denominator": 4,
        "amount_numerator_minor": 16_001,
        "amount_denominator_minor": 16_001,
    }


def test_omission_and_proposal_coverage_share_the_frozen_denominator() -> None:
    report = score_proposals(
        candidate_ids=frozenset({"private-a", "private-b"}),
        references=_references(),
        proposals=(ProposalDecision("private-a", "dining", "expense"),),
        reach=_reach(),
    )

    assert report["candidate_denominator"] == 2
    assert report["proposal_coverage"] == {"numerator": 1, "denominator": 2}
    assert report["omission"] == {"numerator": 1, "denominator": 2}


def test_report_never_contains_candidate_ids_or_per_row_amounts() -> None:
    report = score_proposals(
        candidate_ids=frozenset({"private-a", "private-b"}),
        references=_references(),
        proposals=(ProposalDecision("private-a", "dining", "expense"),),
        reach=_reach(),
    )
    rendered = repr(report)

    assert "private-a" not in rendered
    assert "private-b" not in rendered
    assert "900007" not in rendered
    assert "4001" not in rendered


def test_scope_external_proposal_fails_without_echoing_the_identifier() -> None:
    with pytest.raises(FrozenEvalError) as caught:
        score_proposals(
            candidate_ids=frozenset({"private-a", "private-b"}),
            references=_references(),
            proposals=(ProposalDecision("outside-secret", "dining", "expense"),),
            reach=_reach(),
        )

    assert caught.value.code == "proposal_scope_mismatch"
    assert "outside-secret" not in str(caught.value)


def test_public_cli_score_replaces_money_with_amount_basis_points() -> None:
    report = score_proposals(
        candidate_ids=frozenset({"private-a", "private-b"}),
        references=_references(),
        proposals=(ProposalDecision("private-a", "dining", "expense"),),
        reach=_reach(),
    )

    public = _public_score(report)

    assert public["correct_reach"]["amount_basis_points"] == 10_000
    assert "amount_numerator_minor" not in repr(public)
    assert "amount_denominator_minor" not in repr(public)


def test_ledger_negative_outflow_is_normalized_before_reach_scoring() -> None:
    assert _spend_magnitude(-12_345) == 12_345
    assert _spend_magnitude(0) == 0
    with pytest.raises(FrozenEvalError) as caught:
        _spend_magnitude(1)
    assert caught.value.code == "reach_invalid"


def test_cli_failure_does_not_echo_repository_external_paths(
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_path = "D:\\private-ledger-location"

    exit_code = main(
        [
            "preflight",
            "--truth",
            secret_path,
            "--base",
            secret_path,
            "--codex",
            secret_path,
            "--claude",
            secret_path,
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 3
    assert "ledger_missing" in output
    assert secret_path not in output


def test_c4_uses_one_client_neutral_frozen_operation_prompt() -> None:
    prompt = C4_PROMPT.read_text(encoding="utf-8")

    assert "exactly one all-dates pending" in prompt
    assert "only the five proposal tools" in prompt
    assert "submit the exact normalized proposal" in prompt
    assert "must not apply an effective category" in prompt
    assert "fixed aggregate summary" in prompt
    assert "confidence" in prompt
    assert "triage tools" in prompt
    assert "Codex" not in prompt
    assert "Claude" not in prompt
