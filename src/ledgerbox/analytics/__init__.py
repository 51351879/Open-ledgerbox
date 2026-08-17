# SPDX-License-Identifier: AGPL-3.0-or-later
"""What is computed *from* a booked ledger, never what decides whether to book.

Nothing in this package is a gate. Reconciliation decides what enters the
ledger; these modules describe what is already in it, and being wrong here
produces a misleading chart rather than a wrong balance. That is why
categorisation is allowed to be a heuristic at all — and why
``reconcile.checks.check_declared_buckets`` stays a *warn*.

The rules that drive it are data (``rules/categories.json``), the functions are
pure, and the answers are written to ``posting.category_id`` once at ingest
rather than recomputed on every page load. The predecessor recalculated 234
``includes()`` calls per render and still had no way for a user to correct a
single row.
"""

from .categorize import (
    CANARIES,
    MIN_WORD_LENGTH,
    RULES_PATH,
    TRANSFER_CATEGORY_ID,
    CategoryRule,
    RulesError,
    RuleSet,
    assign_categories,
    classify,
    default_rules,
    load_rules,
    matches_transfer,
    side_for,
)

__all__ = [
    "CANARIES",
    "MIN_WORD_LENGTH",
    "RULES_PATH",
    "TRANSFER_CATEGORY_ID",
    "CategoryRule",
    "RuleSet",
    "RulesError",
    "assign_categories",
    "classify",
    "default_rules",
    "load_rules",
    "matches_transfer",
    "side_for",
]
