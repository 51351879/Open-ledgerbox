# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contract tests for the synthetic Classification Skill evaluation harness."""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path
from typing import Any

import pytest

from tools.evaluate_classification_skill import (
    EvalCatalog,
    ResultSchemaError,
    evaluate_results,
    load_catalog,
    main,
)

ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = ROOT / ".agents" / "skills" / "ledgerbox" / "evals"
CASES = EVAL_ROOT / "synthetic-cases.jsonl"
EXPECTED = EVAL_ROOT / "expected-behaviour.json"
AGENT_PROMPT = EVAL_ROOT / "agent-prompt.md"
RESULT_SCHEMA = EVAL_ROOT / "result-schema.json"


@pytest.fixture
def catalog() -> EvalCatalog:
    return load_catalog(CASES, EXPECTED)


def _fixed_summary(
    client: str,
    candidate_count: int,
    proposed: int,
    groups: int,
    omitted: int,
) -> str:
    return (
        f"Producer: {client}\n"
        "Tools: ledgerbox_status, ledgerbox_categories, ledgerbox_candidates, "
        "ledgerbox_validate_proposal, ledgerbox_submit_proposal\n"
        "Run: created\n"
        f"Candidates: {candidate_count}; pending proposals: {proposed}; "
        f"groups: {groups}; omitted: {omitted}\n"
        "Pending human review in the local Ledgerbox proposal review area. "
        "No effective category changed."
    )


def _reference_results(catalog: EvalCatalog, *, origin: str = "official") -> dict[str, Any]:
    case_results: list[dict[str, Any]] = []
    for case_id in catalog.case_order:
        case = catalog.cases[case_id]
        expected = catalog.expected[case_id]
        proposed = sum(len(group.candidate_refs) for group in expected.groups)
        if expected.outcome == "submitted":
            summary = _fixed_summary(
                "codex",
                len(case.candidates),
                proposed,
                len(expected.groups),
                len(expected.omitted_refs),
            )
        elif expected.outcome == "omitted":
            summary = (
                f"Candidates: {len(case.candidates)}; pending proposals: 0; "
                f"groups: 0; omitted: {len(expected.omitted_refs)}. "
                "No proposal submitted. No effective category changed."
            )
        else:
            summary = "Ledger not ready. No proposal submitted. No effective category changed."
        case_results.append(
            {
                "case_id": case_id,
                "outcome": expected.outcome,
                "tools": list(expected.tools),
                "groups": [
                    {
                        "category_id": group.category_id,
                        "candidate_refs": list(group.candidate_refs),
                    }
                    for group in expected.groups
                ],
                "omitted_refs": list(expected.omitted_refs),
                "pending_human_review": expected.pending_human_review,
                "final_summary": summary,
            }
        )
    return {
        "schema_version": 1,
        "skill_origin": origin,
        "skill_version": catalog.skill_version if origin == "official" else None,
        "client": "codex",
        "cases": case_results,
    }


def _case_result(results: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(item for item in results["cases"] if item["case_id"] == case_id)


def _codes(report: dict[str, Any], case_id: str) -> set[str]:
    failure = next(item for item in report["failures"] if item["case_id"] == case_id)
    return set(failure["codes"])


def test_catalog_is_strict_versioned_and_entirely_synthetic(catalog: EvalCatalog) -> None:
    assert catalog.skill_version == "official-classification-v1"
    assert len(catalog.cases) >= 10
    assert set(catalog.cases) == set(catalog.expected)
    assert len(catalog.case_order) == len(set(catalog.case_order))

    for case in catalog.cases.values():
        for candidate in case.candidates:
            assert candidate.raw_descriptor.startswith("SYNTHETIC ")
            assert candidate.currency == "XTS"
            assert type(candidate.amount_minor) is int
            assert candidate.ref.startswith("syn-")


def test_catalog_covers_every_frozen_metric_and_boundary(catalog: EvalCatalog) -> None:
    dimensions = {dimension for case in catalog.cases.values() for dimension in case.dimensions}
    assert dimensions == {
        "contract_compliance",
        "synthetic_agreement",
        "omission",
        "transfer_review",
        "privacy",
    }
    assert {
        "status-not-ready",
        "ordinary-coherent-group",
        "ambiguous-payment-rail",
        "owned-account-transfer",
        "investment-platform-only",
        "principal-and-fee-split",
        "deposit-channel-only",
        "descriptor-prompt-injection",
        "taxonomy-gap-omission",
    } <= set(catalog.cases)


def test_agent_prompt_is_answer_blind_and_shared(catalog: EvalCatalog) -> None:
    prompt = AGENT_PROMPT.read_text(encoding="utf-8")

    assert "Do not read `expected-behaviour.json`" in prompt
    assert "Do not call a live Ledgerbox MCP server" in prompt
    assert "Output exactly one JSON object" in prompt
    assert "not the number of groups" in prompt
    expected_category_ids = {
        group.category_id
        for expected in catalog.expected.values()
        for group in expected.groups
    }
    for category_id in expected_category_ids:
        assert category_id not in prompt


def test_official_result_json_schema_is_strict_and_versioned() -> None:
    schema = json.loads(RESULT_SCHEMA.read_text(encoding="utf-8"))

    assert "$schema" not in schema
    assert schema["additionalProperties"] is False
    assert schema["properties"]["schema_version"]["type"] == "integer"
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["properties"]["skill_origin"]["type"] == "string"
    assert schema["properties"]["skill_origin"]["const"] == "official"
    assert (
        schema["properties"]["skill_version"]["const"]
        == "official-classification-v1"
    )
    case_schema = schema["properties"]["cases"]["items"]
    assert case_schema["additionalProperties"] is False
    assert case_schema["properties"]["groups"]["items"]["additionalProperties"] is False


def test_reference_trace_passes_but_is_not_called_real_accuracy(catalog: EvalCatalog) -> None:
    report = evaluate_results(catalog, _reference_results(catalog))

    assert report["status"] == "pass"
    assert report["claim"] == "synthetic regression result"
    assert report["skill_origin"] == "official"
    assert report["case_count"] == len(catalog.cases)
    assert report["passed"] == len(catalog.cases)
    assert report["failed"] == 0
    assert "accuracy" not in json.dumps(report).lower()


def test_status_not_ready_must_stop_after_status(catalog: EvalCatalog) -> None:
    results = _reference_results(catalog)
    result = _case_result(results, "status-not-ready")
    result["outcome"] = "submitted"
    result["tools"].append("ledgerbox_categories")

    report = evaluate_results(catalog, results)

    assert {"wrong_outcome", "tool_sequence"} <= _codes(report, "status-not-ready")


def test_unknown_category_is_a_failure(catalog: EvalCatalog) -> None:
    results = _reference_results(catalog)
    result = _case_result(results, "ordinary-coherent-group")
    result["groups"][0]["category_id"] = "not-returned"

    report = evaluate_results(catalog, results)

    assert "unknown_category" in _codes(report, "ordinary-coherent-group")


def test_duplicate_and_scope_external_candidate_refs_fail(catalog: EvalCatalog) -> None:
    duplicate = _reference_results(catalog)
    duplicate_result = _case_result(duplicate, "ordinary-coherent-group")
    duplicate_result["groups"][0]["candidate_refs"].append(
        duplicate_result["groups"][0]["candidate_refs"][0]
    )
    duplicate_report = evaluate_results(catalog, duplicate)
    assert "duplicate_candidate" in _codes(duplicate_report, "ordinary-coherent-group")

    external = _reference_results(catalog)
    external_result = _case_result(external, "ordinary-coherent-group")
    external_result["groups"][0]["candidate_refs"].append("syn-outside-scope")
    external_report = evaluate_results(catalog, external)
    assert "scope_violation" in _codes(external_report, "ordinary-coherent-group")


def test_ambiguous_payment_rail_cannot_be_promoted_to_transfer(catalog: EvalCatalog) -> None:
    results = _reference_results(catalog)
    result = _case_result(results, "ambiguous-payment-rail")
    transfer_id = next(
        category.id
        for category in catalog.cases["ambiguous-payment-rail"].categories
        if category.kind == "transfer"
    )
    ref = result["omitted_refs"].pop()
    result["outcome"] = "submitted"
    result["groups"] = [{"category_id": transfer_id, "candidate_refs": [ref]}]
    result["pending_human_review"] = True

    report = evaluate_results(catalog, results)

    assert {"wrong_outcome", "group_mismatch", "omission_mismatch"} <= _codes(
        report, "ambiguous-payment-rail"
    )


def test_transfer_submission_must_stay_pending(catalog: EvalCatalog) -> None:
    results = _reference_results(catalog)
    result = _case_result(results, "owned-account-transfer")
    result["pending_human_review"] = False

    report = evaluate_results(catalog, results)

    assert "transfer_not_pending" in _codes(report, "owned-account-transfer")


def test_prompt_injection_cannot_add_a_tool(catalog: EvalCatalog) -> None:
    results = _reference_results(catalog)
    result = _case_result(results, "descriptor-prompt-injection")
    result["tools"].append("ledgerbox_apply_categories")

    report = evaluate_results(catalog, results)

    assert {"forbidden_tool", "tool_sequence"} <= _codes(
        report, "descriptor-prompt-injection"
    )


def test_custom_skill_has_the_same_tool_boundary_and_no_official_claim(
    catalog: EvalCatalog,
) -> None:
    safe_custom = _reference_results(catalog, origin="custom")
    safe_report = evaluate_results(catalog, safe_custom)
    assert safe_report["status"] == "pass"
    assert safe_report["claim"] == "custom skill synthetic result; quality unverified"

    unsafe_custom = copy.deepcopy(safe_custom)
    result = _case_result(unsafe_custom, "ordinary-coherent-group")
    result["tools"].append("ledgerbox_apply_categories")
    unsafe_report = evaluate_results(catalog, unsafe_custom)
    assert "forbidden_tool" in _codes(unsafe_report, "ordinary-coherent-group")


@pytest.mark.parametrize("field", ["confidence", "reason", "category_breakdown"])
def test_unapproved_result_fields_are_schema_errors(
    catalog: EvalCatalog,
    field: str,
) -> None:
    results = _reference_results(catalog)
    _case_result(results, "ordinary-coherent-group")[field] = "invented"

    with pytest.raises(ResultSchemaError, match="unexpected field"):
        evaluate_results(catalog, results)


def test_final_summary_leaks_fail_without_echoing_private_values(catalog: EvalCatalog) -> None:
    results = _reference_results(catalog)
    case = catalog.cases["ordinary-coherent-group"]
    result = _case_result(results, "ordinary-coherent-group")
    leaked_values = [
        case.candidates[0].raw_descriptor,
        case.candidates[0].ref[:10],
        str(abs(case.candidates[0].amount_minor)),
        case.categories[0].id,
    ]
    result["final_summary"] += " " + " ".join(leaked_values)

    report = evaluate_results(catalog, results)
    rendered = json.dumps(report, sort_keys=True)

    assert "privacy_leak" in _codes(report, "ordinary-coherent-group")
    for value in leaked_values:
        assert value not in rendered


def test_submitted_summary_must_keep_the_fixed_shape(catalog: EvalCatalog) -> None:
    results = _reference_results(catalog)
    result = _case_result(results, "ordinary-coherent-group")
    result["final_summary"] = "Done."

    report = evaluate_results(catalog, results)

    assert "summary_shape" in _codes(report, "ordinary-coherent-group")


def test_case_set_must_be_exact(catalog: EvalCatalog) -> None:
    results = _reference_results(catalog)
    results["cases"].pop()

    report = evaluate_results(catalog, results)

    assert report["status"] == "fail"
    assert report["global_failures"] == ["case_set_mismatch"]


def test_cli_distinguishes_harness_ready_pass_and_eval_failure(
    catalog: EvalCatalog,
    git_free_tmp: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert main(["--cases", str(CASES), "--expected", str(EXPECTED)]) == 0
    ready = json.loads(capsys.readouterr().out)
    assert ready["status"] == "harness_ready"
    assert ready["case_count"] == len(catalog.cases)

    results_path = git_free_tmp / "synthetic-results.json"
    results_path.write_text(json.dumps(_reference_results(catalog)), encoding="utf-8")
    assert main(
        [
            "--cases",
            str(CASES),
            "--expected",
            str(EXPECTED),
            "--results",
            str(results_path),
        ]
    ) == 0
    passed = json.loads(capsys.readouterr().out)
    assert passed["status"] == "pass"

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_reference_results(catalog))))
    assert main(
        [
            "--cases",
            str(CASES),
            "--expected",
            str(EXPECTED),
            "--results",
            "-",
        ]
    ) == 0
    streamed = json.loads(capsys.readouterr().out)
    assert streamed["status"] == "pass"

    broken = _reference_results(catalog)
    _case_result(broken, "owned-account-transfer")["pending_human_review"] = False
    results_path.write_text(json.dumps(broken), encoding="utf-8")
    assert main(
        [
            "--cases",
            str(CASES),
            "--expected",
            str(EXPECTED),
            "--results",
            str(results_path),
        ]
    ) == 3
    failed = json.loads(capsys.readouterr().out)
    assert failed["status"] == "fail"
