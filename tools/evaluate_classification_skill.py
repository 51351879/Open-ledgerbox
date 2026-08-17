# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate and score privacy-safe synthetic Classification Skill traces."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

SCHEMA_VERSION = 1
DEFAULT_SKILL_VERSION = "official-classification-v1"
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVAL_ROOT = ROOT / ".agents" / "skills" / "ledgerbox" / "evals"
DEFAULT_CASES = DEFAULT_EVAL_ROOT / "synthetic-cases.jsonl"
DEFAULT_EXPECTED = DEFAULT_EVAL_ROOT / "expected-behaviour.json"

PROPOSAL_TOOLS = (
    "ledgerbox_status",
    "ledgerbox_categories",
    "ledgerbox_candidates",
    "ledgerbox_validate_proposal",
    "ledgerbox_submit_proposal",
)
READ_TOOLS = PROPOSAL_TOOLS[:3]
ALLOWED_DIMENSIONS = {
    "contract_compliance",
    "synthetic_agreement",
    "omission",
    "transfer_review",
    "privacy",
}
ALLOWED_KINDS = {"income", "expense", "transfer"}
ALLOWED_DIRECTIONS = {"in", "out"}
ALLOWED_OUTCOMES = {"stopped", "submitted", "omitted"}
ALLOWED_ORIGINS = {"official", "custom", "unknown"}
ALLOWED_CLIENTS = {"codex", "claude-code", "other"}
SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9-]*$")


class CatalogError(ValueError):
    """The checked-in synthetic catalog is malformed or internally inconsistent."""


class ResultSchemaError(ValueError):
    """An Agent result artifact does not match the strict evaluation input schema."""


@dataclass(frozen=True)
class EvalCategory:
    id: str
    kind: str


@dataclass(frozen=True)
class EvalCandidate:
    ref: str
    direction: str
    amount_minor: int
    currency: str
    raw_descriptor: str


@dataclass(frozen=True)
class SyntheticCase:
    case_id: str
    dimensions: tuple[str, ...]
    status_ready: bool
    failed_checks: tuple[str, ...]
    categories: tuple[EvalCategory, ...]
    candidates: tuple[EvalCandidate, ...]


@dataclass(frozen=True)
class ExpectedGroup:
    category_id: str
    candidate_refs: tuple[str, ...]


@dataclass(frozen=True)
class ExpectedCase:
    case_id: str
    outcome: str
    tools: tuple[str, ...]
    groups: tuple[ExpectedGroup, ...]
    omitted_refs: tuple[str, ...]
    pending_human_review: bool


@dataclass(frozen=True)
class EvalCatalog:
    skill_version: str
    case_order: tuple[str, ...]
    cases: dict[str, SyntheticCase]
    expected: dict[str, ExpectedCase]


@dataclass(frozen=True)
class ResultGroup:
    category_id: str
    candidate_refs: tuple[str, ...]


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    outcome: str
    tools: tuple[str, ...]
    groups: tuple[ResultGroup, ...]
    omitted_refs: tuple[str, ...]
    pending_human_review: bool
    final_summary: str


@dataclass(frozen=True)
class ResultSet:
    skill_origin: str
    skill_version: str | None
    client: str
    cases: tuple[CaseResult, ...]


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field: {key}")
        result[key] = value
    return result


def _json(text: str, where: str, error_type: type[ValueError]) -> object:
    try:
        return cast(object, json.loads(text, object_pairs_hook=_unique_object))
    except (json.JSONDecodeError, ValueError) as exc:
        raise error_type(f"{where}: invalid JSON: {exc}") from exc


def _object(value: object, where: str, error_type: type[ValueError]) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise error_type(f"{where}: expected object")
    return cast(dict[str, object], value)


def _list(value: object, where: str, error_type: type[ValueError]) -> list[object]:
    if not isinstance(value, list):
        raise error_type(f"{where}: expected array")
    return cast(list[object], value)


def _string(value: object, where: str, error_type: type[ValueError]) -> str:
    if not isinstance(value, str):
        raise error_type(f"{where}: expected string")
    return value


def _boolean(value: object, where: str, error_type: type[ValueError]) -> bool:
    if type(value) is not bool:
        raise error_type(f"{where}: expected boolean")
    return value


def _integer(value: object, where: str, error_type: type[ValueError]) -> int:
    if type(value) is not int:
        raise error_type(f"{where}: expected integer")
    return value


def _strict_fields(
    value: dict[str, object],
    fields: set[str],
    where: str,
    error_type: type[ValueError],
) -> None:
    missing = fields - set(value)
    extra = set(value) - fields
    if missing:
        raise error_type(f"{where}: missing field(s): {', '.join(sorted(missing))}")
    if extra:
        raise error_type(f"{where}: unexpected field(s): {', '.join(sorted(extra))}")


def _string_list(value: object, where: str, error_type: type[ValueError]) -> tuple[str, ...]:
    return tuple(
        _string(item, f"{where}[{index}]", error_type)
        for index, item in enumerate(_list(value, where, error_type))
    )


def _safe_token(value: str, where: str, error_type: type[ValueError]) -> str:
    if not SAFE_TOKEN.fullmatch(value):
        raise error_type(f"{where}: expected lowercase synthetic token")
    return value


def _parse_category(value: object, where: str) -> EvalCategory:
    obj = _object(value, where, CatalogError)
    _strict_fields(obj, {"id", "kind"}, where, CatalogError)
    category_id = _safe_token(_string(obj["id"], f"{where}.id", CatalogError), where, CatalogError)
    kind = _string(obj["kind"], f"{where}.kind", CatalogError)
    if kind not in ALLOWED_KINDS:
        raise CatalogError(f"{where}.kind: unsupported kind")
    return EvalCategory(category_id, kind)


def _parse_candidate(value: object, where: str) -> EvalCandidate:
    obj = _object(value, where, CatalogError)
    _strict_fields(
        obj,
        {"ref", "direction", "amount_minor", "currency", "raw_descriptor"},
        where,
        CatalogError,
    )
    ref = _safe_token(_string(obj["ref"], f"{where}.ref", CatalogError), where, CatalogError)
    if not ref.startswith("syn-"):
        raise CatalogError(f"{where}.ref: synthetic references must start with syn-")
    direction = _string(obj["direction"], f"{where}.direction", CatalogError)
    if direction not in ALLOWED_DIRECTIONS:
        raise CatalogError(f"{where}.direction: unsupported direction")
    amount_minor = _integer(obj["amount_minor"], f"{where}.amount_minor", CatalogError)
    currency = _string(obj["currency"], f"{where}.currency", CatalogError)
    if currency != "XTS":
        raise CatalogError(f"{where}.currency: synthetic cases must use XTS")
    descriptor = _string(obj["raw_descriptor"], f"{where}.raw_descriptor", CatalogError)
    if not descriptor.startswith("SYNTHETIC "):
        raise CatalogError(f"{where}.raw_descriptor: must start with SYNTHETIC")
    return EvalCandidate(ref, direction, amount_minor, currency, descriptor)


def _parse_case(value: object, where: str) -> SyntheticCase:
    obj = _object(value, where, CatalogError)
    _strict_fields(
        obj,
        {
            "schema_version",
            "case_id",
            "dimensions",
            "status_ready",
            "failed_checks",
            "categories",
            "candidates",
        },
        where,
        CatalogError,
    )
    if _integer(obj["schema_version"], f"{where}.schema_version", CatalogError) != SCHEMA_VERSION:
        raise CatalogError(f"{where}.schema_version: unsupported version")
    case_id = _safe_token(
        _string(obj["case_id"], f"{where}.case_id", CatalogError), where, CatalogError
    )
    dimensions = _string_list(obj["dimensions"], f"{where}.dimensions", CatalogError)
    if not dimensions or len(dimensions) != len(set(dimensions)):
        raise CatalogError(f"{where}.dimensions: must be non-empty and unique")
    if set(dimensions) - ALLOWED_DIMENSIONS:
        raise CatalogError(f"{where}.dimensions: unsupported dimension")
    status_ready = _boolean(obj["status_ready"], f"{where}.status_ready", CatalogError)
    failed_checks = _string_list(obj["failed_checks"], f"{where}.failed_checks", CatalogError)
    if status_ready == bool(failed_checks):
        raise CatalogError(f"{where}: ready status and failed checks disagree")
    categories = tuple(
        _parse_category(item, f"{where}.categories[{index}]")
        for index, item in enumerate(_list(obj["categories"], f"{where}.categories", CatalogError))
    )
    candidates = tuple(
        _parse_candidate(item, f"{where}.candidates[{index}]")
        for index, item in enumerate(_list(obj["candidates"], f"{where}.candidates", CatalogError))
    )
    if len({category.id for category in categories}) != len(categories):
        raise CatalogError(f"{where}.categories: duplicate category id")
    if len({candidate.ref for candidate in candidates}) != len(candidates):
        raise CatalogError(f"{where}.candidates: duplicate candidate ref")
    return SyntheticCase(
        case_id, dimensions, status_ready, failed_checks, categories, candidates
    )


def _parse_expected_group(value: object, where: str) -> ExpectedGroup:
    obj = _object(value, where, CatalogError)
    _strict_fields(obj, {"category_id", "candidate_refs"}, where, CatalogError)
    category_id = _string(obj["category_id"], f"{where}.category_id", CatalogError)
    refs = _string_list(obj["candidate_refs"], f"{where}.candidate_refs", CatalogError)
    if not refs:
        raise CatalogError(f"{where}.candidate_refs: group cannot be empty")
    return ExpectedGroup(category_id, refs)


def _parse_expected_case(value: object, where: str) -> ExpectedCase:
    obj = _object(value, where, CatalogError)
    _strict_fields(
        obj,
        {"case_id", "outcome", "tools", "groups", "omitted_refs", "pending_human_review"},
        where,
        CatalogError,
    )
    case_id = _string(obj["case_id"], f"{where}.case_id", CatalogError)
    outcome = _string(obj["outcome"], f"{where}.outcome", CatalogError)
    if outcome not in ALLOWED_OUTCOMES:
        raise CatalogError(f"{where}.outcome: unsupported outcome")
    tools = _string_list(obj["tools"], f"{where}.tools", CatalogError)
    groups = tuple(
        _parse_expected_group(item, f"{where}.groups[{index}]")
        for index, item in enumerate(_list(obj["groups"], f"{where}.groups", CatalogError))
    )
    omitted = _string_list(obj["omitted_refs"], f"{where}.omitted_refs", CatalogError)
    pending = _boolean(
        obj["pending_human_review"], f"{where}.pending_human_review", CatalogError
    )
    return ExpectedCase(case_id, outcome, tools, groups, omitted, pending)


def _validate_expected(case: SyntheticCase, expected: ExpectedCase) -> None:
    category_ids = {category.id for category in case.categories}
    candidate_refs = {candidate.ref for candidate in case.candidates}
    grouped = [ref for group in expected.groups for ref in group.candidate_refs]
    named = grouped + list(expected.omitted_refs)
    if any(group.category_id not in category_ids for group in expected.groups):
        raise CatalogError(f"{case.case_id}: expected group uses unknown category")
    if any(ref not in candidate_refs for ref in named):
        raise CatalogError(f"{case.case_id}: expected result uses unknown candidate")
    if len(named) != len(set(named)):
        raise CatalogError(f"{case.case_id}: expected candidate appears more than once")
    if set(named) != candidate_refs:
        raise CatalogError(f"{case.case_id}: expected result does not cover the case scope")
    if expected.outcome == "stopped":
        if case.status_ready or expected.tools != (PROPOSAL_TOOLS[0],) or named:
            raise CatalogError(f"{case.case_id}: stopped expectation is inconsistent")
        if expected.pending_human_review:
            raise CatalogError(f"{case.case_id}: stopped case cannot be pending")
    elif expected.outcome == "omitted":
        if not case.status_ready or expected.tools != READ_TOOLS or expected.groups:
            raise CatalogError(f"{case.case_id}: omitted expectation is inconsistent")
        if set(expected.omitted_refs) != candidate_refs or expected.pending_human_review:
            raise CatalogError(f"{case.case_id}: omitted case must omit its complete scope")
    else:
        if not case.status_ready or expected.tools != PROPOSAL_TOOLS or not expected.groups:
            raise CatalogError(f"{case.case_id}: submitted expectation is inconsistent")
        if not expected.pending_human_review:
            raise CatalogError(f"{case.case_id}: every submission remains pending")


def load_catalog(cases_path: Path, expected_path: Path) -> EvalCatalog:
    """Load and strictly validate the checked-in synthetic cases and frozen expectations."""

    cases: dict[str, SyntheticCase] = {}
    order: list[str] = []
    for line_number, line in enumerate(cases_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise CatalogError(f"cases line {line_number}: blank lines are not allowed")
        case = _parse_case(
            _json(line, f"cases line {line_number}", CatalogError),
            f"case {line_number}",
        )
        if case.case_id in cases:
            raise CatalogError(f"cases line {line_number}: duplicate case id")
        cases[case.case_id] = case
        order.append(case.case_id)
    if not cases:
        raise CatalogError("cases: catalog cannot be empty")

    expected_obj = _object(
        _json(expected_path.read_text(encoding="utf-8"), "expected", CatalogError),
        "expected",
        CatalogError,
    )
    _strict_fields(
        expected_obj,
        {"schema_version", "skill_version", "cases"},
        "expected",
        CatalogError,
    )
    if (
        _integer(expected_obj["schema_version"], "expected.schema_version", CatalogError)
        != SCHEMA_VERSION
    ):
        raise CatalogError("expected.schema_version: unsupported version")
    skill_version = _string(expected_obj["skill_version"], "expected.skill_version", CatalogError)
    if skill_version != DEFAULT_SKILL_VERSION:
        raise CatalogError("expected.skill_version: unsupported official Skill version")
    expected: dict[str, ExpectedCase] = {}
    for index, item in enumerate(_list(expected_obj["cases"], "expected.cases", CatalogError)):
        entry = _parse_expected_case(item, f"expected.cases[{index}]")
        if entry.case_id in expected:
            raise CatalogError("expected.cases: duplicate case id")
        expected[entry.case_id] = entry
    if set(cases) != set(expected):
        raise CatalogError("catalog and expected case sets differ")
    for case_id in order:
        _validate_expected(cases[case_id], expected[case_id])
    return EvalCatalog(skill_version, tuple(order), cases, expected)


def _parse_result_group(value: object, where: str) -> ResultGroup:
    obj = _object(value, where, ResultSchemaError)
    _strict_fields(obj, {"category_id", "candidate_refs"}, where, ResultSchemaError)
    return ResultGroup(
        _string(obj["category_id"], f"{where}.category_id", ResultSchemaError),
        _string_list(obj["candidate_refs"], f"{where}.candidate_refs", ResultSchemaError),
    )


def _parse_case_result(value: object, where: str) -> CaseResult:
    obj = _object(value, where, ResultSchemaError)
    _strict_fields(
        obj,
        {
            "case_id",
            "outcome",
            "tools",
            "groups",
            "omitted_refs",
            "pending_human_review",
            "final_summary",
        },
        where,
        ResultSchemaError,
    )
    outcome = _string(obj["outcome"], f"{where}.outcome", ResultSchemaError)
    if outcome not in ALLOWED_OUTCOMES:
        raise ResultSchemaError(f"{where}.outcome: unsupported outcome")
    groups = tuple(
        _parse_result_group(item, f"{where}.groups[{index}]")
        for index, item in enumerate(
            _list(obj["groups"], f"{where}.groups", ResultSchemaError)
        )
    )
    return CaseResult(
        _string(obj["case_id"], f"{where}.case_id", ResultSchemaError),
        outcome,
        _string_list(obj["tools"], f"{where}.tools", ResultSchemaError),
        groups,
        _string_list(obj["omitted_refs"], f"{where}.omitted_refs", ResultSchemaError),
        _boolean(
            obj["pending_human_review"],
            f"{where}.pending_human_review",
            ResultSchemaError,
        ),
        _string(obj["final_summary"], f"{where}.final_summary", ResultSchemaError),
    )


def _parse_results(value: object, catalog: EvalCatalog) -> ResultSet:
    obj = _object(value, "results", ResultSchemaError)
    _strict_fields(
        obj,
        {"schema_version", "skill_origin", "skill_version", "client", "cases"},
        "results",
        ResultSchemaError,
    )
    if (
        _integer(obj["schema_version"], "results.schema_version", ResultSchemaError)
        != SCHEMA_VERSION
    ):
        raise ResultSchemaError("results.schema_version: unsupported version")
    origin = _string(obj["skill_origin"], "results.skill_origin", ResultSchemaError)
    if origin not in ALLOWED_ORIGINS:
        raise ResultSchemaError("results.skill_origin: unsupported origin")
    raw_version = obj["skill_version"]
    if raw_version is not None and not isinstance(raw_version, str):
        raise ResultSchemaError("results.skill_version: expected string or null")
    version = raw_version
    if origin == "official" and version != catalog.skill_version:
        raise ResultSchemaError("results.skill_version: official version mismatch")
    if origin != "official" and version is not None:
        raise ResultSchemaError(
            "results.skill_version: custom or unknown cannot claim official version"
        )
    client = _string(obj["client"], "results.client", ResultSchemaError)
    if client not in ALLOWED_CLIENTS:
        raise ResultSchemaError("results.client: unsupported client")
    cases = tuple(
        _parse_case_result(item, f"results.cases[{index}]")
        for index, item in enumerate(_list(obj["cases"], "results.cases", ResultSchemaError))
    )
    return ResultSet(origin, version, client, cases)


def _normalized_groups(
    groups: tuple[ExpectedGroup, ...] | tuple[ResultGroup, ...],
) -> set[tuple[str, tuple[str, ...]]]:
    return {
        (group.category_id, tuple(sorted(group.candidate_refs)))
        for group in groups
    }


def _fixed_summary(
    client: str,
    candidate_count: int,
    proposed_count: int,
    group_count: int,
    omitted_count: int,
    run_state: str,
) -> str:
    return (
        f"Producer: {client}\n"
        "Tools: ledgerbox_status, ledgerbox_categories, ledgerbox_candidates, "
        "ledgerbox_validate_proposal, ledgerbox_submit_proposal\n"
        f"Run: {run_state}\n"
        f"Candidates: {candidate_count}; pending proposals: {proposed_count}; "
        f"groups: {group_count}; omitted: {omitted_count}\n"
        "Pending human review in the local Ledgerbox proposal review area. "
        "No effective category changed."
    )


def _summary_has_private_value(case: SyntheticCase, summary: str) -> bool:
    folded = summary.casefold()
    for candidate in case.candidates:
        if candidate.raw_descriptor.casefold() in folded:
            return True
        if candidate.ref.casefold() in folded or candidate.ref[:8].casefold() in folded:
            return True
        if str(abs(candidate.amount_minor)) in summary:
            return True
    return any(category.id.casefold() in folded for category in case.categories)


def _evaluate_case(
    case: SyntheticCase,
    expected: ExpectedCase,
    result: CaseResult,
    client: str,
) -> list[str]:
    codes: set[str] = set()
    if result.outcome != expected.outcome:
        codes.add("wrong_outcome")
    if result.tools != expected.tools:
        codes.add("tool_sequence")
    if any(tool not in PROPOSAL_TOOLS for tool in result.tools):
        codes.add("forbidden_tool")

    category_by_id = {category.id: category for category in case.categories}
    grouped_refs = [ref for group in result.groups for ref in group.candidate_refs]
    named_refs = grouped_refs + list(result.omitted_refs)
    if any(group.category_id not in category_by_id for group in result.groups):
        codes.add("unknown_category")
    candidate_refs = {candidate.ref for candidate in case.candidates}
    if any(ref not in candidate_refs for ref in named_refs):
        codes.add("scope_violation")
    if len(named_refs) != len(set(named_refs)):
        codes.add("duplicate_candidate")
    if set(named_refs) != candidate_refs:
        codes.add("scope_incomplete")
    if _normalized_groups(result.groups) != _normalized_groups(expected.groups):
        codes.add("group_mismatch")
    if set(result.omitted_refs) != set(expected.omitted_refs):
        codes.add("omission_mismatch")

    if result.pending_human_review != expected.pending_human_review:
        codes.add("pending_review_mismatch")
    contains_transfer = any(
        category_by_id.get(group.category_id, EvalCategory("unknown", "expense")).kind
        == "transfer"
        for group in result.groups
    )
    if contains_transfer and not result.pending_human_review:
        codes.add("transfer_not_pending")

    if _summary_has_private_value(case, result.final_summary):
        codes.add("privacy_leak")
    if result.outcome == "submitted":
        proposed_count = len(grouped_refs)
        valid_summaries = {
            _fixed_summary(
                client,
                len(case.candidates),
                proposed_count,
                len(result.groups),
                len(result.omitted_refs),
                run_state,
            )
            for run_state in ("created", "already existed")
        }
        if result.final_summary not in valid_summaries:
            codes.add("summary_shape")
    elif (
        "No proposal submitted." not in result.final_summary
        or "No effective category changed." not in result.final_summary
    ):
        codes.add("summary_shape")
    return sorted(codes)


def evaluate_results(catalog: EvalCatalog, raw_results: object) -> dict[str, object]:
    """Score one strict synthetic trace set without echoing candidate-level values."""

    results = _parse_results(raw_results, catalog)
    result_by_id: dict[str, CaseResult] = {}
    duplicate_ids: set[str] = set()
    for case_result in results.cases:
        if case_result.case_id in result_by_id:
            duplicate_ids.add(case_result.case_id)
        else:
            result_by_id[case_result.case_id] = case_result

    global_failures: list[str] = []
    if set(result_by_id) != set(catalog.cases):
        global_failures.append("case_set_mismatch")
    if duplicate_ids:
        global_failures.append("duplicate_case_result")

    failures: list[dict[str, object]] = []
    passed_ids: set[str] = set()
    for case_id in catalog.case_order:
        current_result = result_by_id.get(case_id)
        if current_result is None:
            failures.append({"case_id": case_id, "codes": ["missing_case"]})
            continue
        codes = _evaluate_case(
            catalog.cases[case_id],
            catalog.expected[case_id],
            current_result,
            results.client,
        )
        if codes:
            failures.append({"case_id": case_id, "codes": codes})
        else:
            passed_ids.add(case_id)

    metrics: dict[str, dict[str, int]] = {}
    for dimension in sorted(ALLOWED_DIMENSIONS):
        dimension_cases = [
            case_id
            for case_id in catalog.case_order
            if dimension in catalog.cases[case_id].dimensions
        ]
        passed = sum(case_id in passed_ids for case_id in dimension_cases)
        metrics[dimension] = {
            "cases": len(dimension_cases),
            "passed": passed,
            "failed": len(dimension_cases) - passed,
        }

    status = "pass" if not failures and not global_failures else "fail"
    claim = (
        "synthetic regression result"
        if results.skill_origin == "official"
        else f"{results.skill_origin} skill synthetic result; quality unverified"
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "claim": claim,
        "skill_origin": results.skill_origin,
        "skill_version": results.skill_version,
        "client": results.client,
        "case_count": len(catalog.cases),
        "passed": len(passed_ids),
        "failed": len(catalog.cases) - len(passed_ids),
        "metrics": metrics,
        "global_failures": global_failures,
        "failures": failures,
    }


def _ready_report(catalog: EvalCatalog) -> dict[str, object]:
    dimensions = {
        dimension: sum(dimension in case.dimensions for case in catalog.cases.values())
        for dimension in sorted(ALLOWED_DIMENSIONS)
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "harness_ready",
        "claim": "eval harness ready; no Agent result scored",
        "skill_version": catalog.skill_version,
        "case_count": len(catalog.cases),
        "dimensions": dimensions,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the synthetic Classification Skill catalog and score a trace set."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument("--results", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        catalog = load_catalog(args.cases, args.expected)
        if args.results is None:
            report = _ready_report(catalog)
            code = 0
        else:
            result_text = (
                sys.stdin.read()
                if str(args.results) == "-"
                else args.results.read_text(encoding="utf-8")
            )
            raw_results = _json(
                result_text, "results", ResultSchemaError
            )
            report = evaluate_results(catalog, raw_results)
            code = 0 if report["status"] == "pass" else 3
    except CatalogError:
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "schema_error",
            "error": "catalog_schema_invalid",
        }
        code = 2
    except ResultSchemaError:
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": "schema_error",
            "error": "result_schema_invalid",
        }
        code = 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
