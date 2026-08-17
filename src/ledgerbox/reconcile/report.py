# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turning failures into things a human can act on.

Two audiences:

* the terminal, which wants a short verdict and then the specifics;
* ``review_item``, which wants a stable id, a severity, and a structured
  ``detail`` payload that still means something a year from now.

Both render the *same* :class:`~ledgerbox.reconcile.checks.CheckResult` objects.
A report that says something different from the database would be its own bug.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..ledger.identity import review_item_id
from .checks import BLOCK, FAIL, PASS, SKIP, CheckResult, ReconciliationReport

#: ASCII on purpose. ✓/✗ raise UnicodeEncodeError on a cp1252 or cp936
#: console, and a reconciliation report that crashes while printing a failure
#: is worse than one that is ugly. ``configure_stdio()`` fixes the stream, but
#: this text must survive being piped, redirected and logged by anything.
STATUS_MARK = {PASS: "ok  ", FAIL: "FAIL", SKIP: "skip"}


@dataclass(frozen=True, slots=True)
class ReviewItem:
    """One row of the review queue, ready for insertion."""

    id: str
    source_file_id: str
    severity: str
    check_id: str
    detail: str  # JSON: {"message": ..., "detail": {...}}

    @classmethod
    def from_result(cls, source_file_id: str, result: CheckResult) -> ReviewItem:
        payload = json.dumps(
            {"message": result.message, "detail": result.detail},
            ensure_ascii=False,
            sort_keys=True,
        )
        return cls(
            # Deterministic: re-ingesting the same broken statement must update
            # one review item, not breed a new one on every attempt.
            id=review_item_id(source_file_id, result.check_id, result.severity),
            source_file_id=source_file_id,
            severity=result.severity,
            check_id=result.check_id,
            detail=payload,
        )


def review_items(
    source_file_id: str, report: ReconciliationReport, *, include_warnings: bool = True
) -> list[ReviewItem]:
    """Everything a human must look at, blocking first.

    Includes block-level checks that were *skipped*: an assertion that could
    not run has not passed, and the queue is the only place that difference
    gets seen.
    """
    results = [r for r in report.results if r.failed] + list(report.unverified)
    if not include_warnings:
        results = [r for r in results if r.severity == BLOCK]
    results.sort(key=lambda r: (r.severity != BLOCK, r.check_id))
    return [ReviewItem.from_result(source_file_id, r) for r in results]


def verdict(report: ReconciliationReport) -> str:
    """One word for the top of the report — and never a flattering one.

    A block-level check that was skipped reads as UNVERIFIED, not ``ok``.
    "Every check I managed to run passed" and "the ledger is right" are not the
    same sentence, and only one of them is evidence.
    """
    if report.blocking_failures:
        return "BLOCKED"
    if report.unverified:
        return f"UNVERIFIED ({len(report.unverified)} block-level check(s) could not run)"
    return "ok"


def render_report(report: ReconciliationReport, *, verbose: bool = False) -> str:
    """One statement, for the terminal."""
    lines = [f"{report.statement_month}  {verdict(report)}"]
    for result in report.results:
        if not verbose and result.status == PASS:
            continue
        mark = STATUS_MARK.get(result.status, "?")
        lines.append(f"  {mark} [{result.severity:5}] {result.check_id}: {result.message}")
    if not verbose and not report.failures and not report.skipped:
        lines.append(f"  all {len(report.results)} checks passed")
    return "\n".join(lines)


def render_summary(reports: Sequence[ReconciliationReport]) -> str:
    """Every statement in one block, with the counts that matter."""
    blocked = [r for r in reports if r.blocked]
    warned = [r for r in reports if r.failures and not r.blocked]
    unverified = [r for r in reports if r.unverified]
    skipped = sum(len(r.skipped) for r in reports)

    lines = [render_report(report) for report in reports]
    lines.append("")
    lines.append(
        f"{len(reports)} statement(s): {len(reports) - len(blocked)} passed all block-level "
        f"checks, {len(blocked)} blocked ({len(unverified)} of them for unverifiable rather "
        f"than failed checks), {len(warned)} with warnings only, {skipped} check(s) skipped"
    )
    if skipped:
        # Never let a skip masquerade as a pass in the one line people read.
        names = sorted({r.check_id for report in reports for r in report.skipped})
        lines.append(f"  skipped: {', '.join(names)}")
    return "\n".join(lines)


def failures_as_json(reports: Iterable[ReconciliationReport]) -> str:
    return json.dumps(
        [
            {"statement_month": report.statement_month, **result.to_dict()}
            for report in reports
            for result in report.failures
        ],
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
