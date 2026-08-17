# SPDX-License-Identifier: AGPL-3.0-or-later
"""A6.5 C2: exhaustive remaining-coverage triage and human review."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from test_transactions import Line, book

from ledgerbox.config import DataPaths
from ledgerbox.db import repo
from ledgerbox.db.connection import transaction
from ledgerbox.db.migrate import open_ledger
from ledgerbox.ingest import archive
from ledgerbox.proposals import (
    Producer,
    ProposalGroup,
    ProposalSubmission,
    ledger_revision,
    submit_proposal,
)
from ledgerbox.proposals import (
    group_id_for as proposal_group_id_for,
)
from ledgerbox.triage import (
    TriageConflict,
    TriageDraft,
    TriageGroup,
    TriageScope,
    TriageScopeIncomplete,
    dismiss_run,
    get_run,
    group_id_for,
    review_triage,
    submit_triage,
    validate_triage,
    withdraw_run,
)


@pytest.fixture
def triage_ledger(git_free_tmp: Path):
    paths = DataPaths.resolve(git_free_tmp / "triage data")
    conn = open_ledger(paths.db)
    source = paths.root / "synthetic-triage.pdf"
    prefix = b"%PDF-1.7\n"
    source.write_bytes(prefix + b"t" * (1024 - len(prefix)))
    archived = archive.archive_file(paths, source, ingested_on=date(2026, 8, 9))
    source.unlink()
    txn_ids = tuple(
        book(
            conn,
            [
                Line(-1_000, "synthetic possible transfer one", date="2025-05-06"),
                Line(-2_000, "synthetic possible transfer two", date="2025-05-07"),
                Line(-3_000, "synthetic taxonomy gap", date="2025-05-08"),
                Line(-4_000, "synthetic unresolved", date="2025-05-09"),
                Line(
                    -5_000,
                    "synthetic answered",
                    date="2025-05-10",
                    rule_category="groceries",
                ),
            ],
            sha256=archived.sha256,
        )
    )
    yield paths, conn, txn_ids, archived.sha256
    conn.close()


def _draft(conn: sqlite3.Connection, txn_ids: tuple[str, ...]) -> TriageDraft:
    definitions = (
        ("possible_transfer", "account_movement_language", txn_ids[:2]),
        ("taxonomy_gap", "coherent_activity_missing", txn_ids[2:3]),
        ("uncertain", "descriptor_ambiguous", txn_ids[3:4]),
    )
    return TriageDraft(
        schema_version=1,
        ledger_revision=ledger_revision(conn),
        scope=TriageScope(),
        producer=Producer(client="claude-code", client_version="test"),
        groups=tuple(
            TriageGroup(
                group_id=group_id_for(route, reason, ids),
                route=route,
                reason_code=reason,
                txn_ids=ids,
            )
            for route, reason, ids in definitions
        ),
    )


def test_0010_tables_are_strict_and_enforce_route_reason_and_outcome_pairs(
    triage_ledger,
) -> None:
    _, conn, txn_ids, _ = triage_ledger
    tables = {
        row["name"]: row["strict"]
        for row in conn.execute("PRAGMA table_list")
        if row["name"] in {"agent_triage_run", "agent_triage_item"}
    }
    assert tables == {"agent_triage_run": 1, "agent_triage_item": 1}

    validated = validate_triage(conn, triage_ledger[0], _draft(conn, txn_ids))
    submit_triage(conn, triage_ledger[0], validated.submission)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE agent_triage_item SET route='uncertain' "
            "WHERE run_id=? AND txn_id=?",
            (validated.run_id, txn_ids[0]),
        )


def test_validate_requires_one_exhaustive_partition_and_writes_nothing(
    triage_ledger,
) -> None:
    paths, conn, txn_ids, _ = triage_ledger
    good = _draft(conn, txn_ids)
    result = validate_triage(conn, paths, good)

    assert result.item_count == 4
    assert result.submission.scope_revision.startswith("sha256:")
    assert conn.execute("SELECT COUNT(*) FROM agent_triage_run").fetchone()[0] == 0

    missing = replace(good, groups=good.groups[:-1])
    with pytest.raises(TriageScopeIncomplete, match="missing"):
        validate_triage(conn, paths, missing)

    duplicate = replace(
        good.groups[2],
        txn_ids=(txn_ids[0],),
        group_id=group_id_for("uncertain", "descriptor_ambiguous", (txn_ids[0],)),
    )
    with pytest.raises(TriageScopeIncomplete, match="more than one"):
        validate_triage(conn, paths, replace(good, groups=(*good.groups[:2], duplicate)))

    wrong_reason = replace(good.groups[0], reason_code="descriptor_ambiguous")
    with pytest.raises(TriageConflict, match="does not belong"):
        validate_triage(conn, paths, replace(good, groups=(wrong_reason, *good.groups[1:])))


def test_submit_is_idempotent_audit_only_and_rechecks_scope_revision(triage_ledger) -> None:
    paths, conn, txn_ids, _ = triage_ledger
    validated = validate_triage(conn, paths, _draft(conn, txn_ids))
    before = conn.execute("SELECT COUNT(*) FROM category_override").fetchone()[0]

    first = submit_triage(conn, paths, validated.submission)
    second = submit_triage(conn, paths, validated.submission)

    assert (first.created, second.created) == (True, False)
    assert first.run_id == second.run_id == validated.run_id
    assert conn.execute("SELECT COUNT(*) FROM agent_triage_item").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM category_override").fetchone()[0] == before

    other = validate_triage(conn, paths, _draft(conn, txn_ids))
    assert other.run_id == validated.run_id


def test_submit_refuses_stale_scope_and_pending_category_proposal(triage_ledger) -> None:
    paths, conn, txn_ids, _ = triage_ledger
    validated = validate_triage(conn, paths, _draft(conn, txn_ids))
    with transaction(conn):
        repo.set_category_override(conn, txn_id=txn_ids[0], category_id="dining")
    with pytest.raises(TriageScopeIncomplete):
        submit_triage(conn, paths, validated.submission)
    assert conn.execute("SELECT COUNT(*) FROM agent_triage_run").fetchone()[0] == 0

    with transaction(conn):
        repo.clear_category_override(conn, txn_id=txn_ids[0])
    proposal_group = ProposalGroup(
        group_id=proposal_group_id_for("dining", txn_ids[:1]),
        category_id="dining",
        txn_ids=txn_ids[:1],
    )
    submit_proposal(
        conn,
        ProposalSubmission(
            schema_version=1,
            ledger_revision=ledger_revision(conn),
            producer=Producer(client="codex"),
            groups=(proposal_group,),
        ),
    )
    with pytest.raises(TriageConflict, match="pending category proposals"):
        validate_triage(conn, paths, _draft(conn, txn_ids))


def test_review_routes_only_human_classification_changes_categories(triage_ledger) -> None:
    paths, conn, txn_ids, _ = triage_ledger
    validated = validate_triage(conn, paths, _draft(conn, txn_ids))
    created = submit_triage(conn, paths, validated.submission)

    with pytest.raises(TriageConflict, match="leave_uncertain only applies to uncertain"):
        review_triage(conn, created.run_id, txn_ids[:1], action="leave_uncertain")
    assert all(item.outcome == "pending" for item in get_run(conn, created.run_id).items)  # type: ignore[union-attr]
    assert repo.get_category_override(conn, txn_ids[0]) is None

    transfer = review_triage(
        conn, created.run_id, txn_ids[:1], action="classify", category_id="transfer"
    )
    ordinary = review_triage(
        conn, created.run_id, txn_ids[1:2], action="classify", category_id="dining"
    )
    gap = review_triage(conn, created.run_id, txn_ids[2:3], action="confirm_gap")
    uncertain = review_triage(conn, created.run_id, txn_ids[3:4], action="leave_uncertain")

    assert transfer.confirmed_transfer == 1
    assert ordinary.classified_existing == 1
    assert gap.confirmed_taxonomy_gap == 1
    assert uncertain.left_uncertain == 1 and uncertain.state == "completed"
    assert repo.get_category_override(conn, txn_ids[0])["category_id"] == "transfer"  # type: ignore[index]
    assert repo.get_category_override(conn, txn_ids[1])["category_id"] == "dining"  # type: ignore[index]
    assert repo.get_category_override(conn, txn_ids[2]) is None
    assert repo.get_category_override(conn, txn_ids[3]) is None


def test_category_write_and_outcome_are_one_transaction(triage_ledger) -> None:
    paths, conn, txn_ids, _ = triage_ledger
    validated = validate_triage(conn, paths, _draft(conn, txn_ids))
    created = submit_triage(conn, paths, validated.submission)
    conn.execute(
        "CREATE TRIGGER triage_outcome_probe BEFORE UPDATE OF outcome "
        "ON agent_triage_item BEGIN SELECT RAISE(ABORT, 'synthetic triage failure'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic triage failure"):
        review_triage(
            conn, created.run_id, txn_ids[:1], action="classify", category_id="dining"
        )

    assert repo.get_category_override(conn, txn_ids[0]) is None
    assert get_run(conn, created.run_id).items[0].outcome == "pending"  # type: ignore[union-attr]


def test_dismiss_and_withdraw_preserve_later_manual_changes(triage_ledger) -> None:
    paths, conn, txn_ids, _ = triage_ledger
    validated = validate_triage(conn, paths, _draft(conn, txn_ids))
    created = submit_triage(conn, paths, validated.submission)
    review_triage(conn, created.run_id, txn_ids[:2], action="classify", category_id="dining")
    with transaction(conn):
        repo.set_category_override(conn, txn_id=txn_ids[1], category_id="groceries")

    dismissed = dismiss_run(conn, created.run_id)
    withdrawn = withdraw_run(conn, created.run_id)

    assert dismissed.left_uncertain == 2 and dismissed.state == "dismissed"
    assert (withdrawn.withdrawn, withdrawn.changed_later) == (1, 1)
    assert repo.get_category_override(conn, txn_ids[0]) is None
    assert repo.get_category_override(conn, txn_ids[1])["category_id"] == "groceries"  # type: ignore[index]


def test_selected_withdrawal_clears_only_named_applied_categories(triage_ledger) -> None:
    paths, conn, txn_ids, _ = triage_ledger
    validated = validate_triage(conn, paths, _draft(conn, txn_ids))
    created = submit_triage(conn, paths, validated.submission)
    review_triage(conn, created.run_id, txn_ids[:2], action="classify", category_id="dining")

    result = withdraw_run(conn, created.run_id, txn_ids[:1])

    assert result.withdrawn == 1
    assert repo.get_category_override(conn, txn_ids[0]) is None
    assert repo.get_category_override(conn, txn_ids[1])["category_id"] == "dining"  # type: ignore[index]
    outcomes = {item.txn_id: item.outcome for item in get_run(conn, created.run_id).items}  # type: ignore[union-attr]
    assert outcomes[txn_ids[0]] == "withdrawn"
    assert outcomes[txn_ids[1]] == "classified_existing"
    with pytest.raises(TriageConflict, match="applied category"):
        withdraw_run(conn, created.run_id, txn_ids[:1])


def test_forget_counts_and_removes_triage_history_before_transactions(triage_ledger) -> None:
    paths, conn, txn_ids, source_id = triage_ledger
    validated = validate_triage(conn, paths, _draft(conn, txn_ids))
    created = submit_triage(conn, paths, validated.submission)

    facts = repo.statement_deletion_facts(conn, source_id)
    assert (facts.agent_triage_items, facts.agent_triage_runs) == (4, 1)
    with transaction(conn):
        counts = repo.delete_statement(conn, source_id)

    assert (counts.agent_triage_items, counts.agent_triage_runs) == (4, 1)
    assert get_run(conn, created.run_id) is None
