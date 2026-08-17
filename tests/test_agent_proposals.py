# SPDX-License-Identifier: AGPL-3.0-or-later
"""A1: proposal-only Agent classification, before any Agent or MCP exists."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from test_transactions import Line, book

from ledgerbox.agent_center import start_session, update_policy
from ledgerbox.agent_jobs import (
    claim_next_job,
    enqueue_import_job,
    enqueue_manual_job,
    finish_job,
    get_job,
)
from ledgerbox.db import repo
from ledgerbox.db.connection import transaction
from ledgerbox.db.migrate import open_ledger
from ledgerbox.proposals import (
    Producer,
    ProposalConflict,
    ProposalGroup,
    ProposalSubmission,
    dismiss_run,
    get_run,
    group_id_for,
    ledger_revision,
    list_runs,
    review_proposals,
    submit_proposal,
    validate_proposal,
    withdraw_run,
)


@pytest.fixture
def proposal_db(git_free_tmp: Path):
    conn = open_ledger(git_free_tmp / "proposal-ledger.db")
    yield conn
    conn.close()


@pytest.fixture
def proposal_ledger(
    proposal_db: sqlite3.Connection,
) -> tuple[sqlite3.Connection, tuple[str, str, str, str, str]]:
    """Three eligible rows, one rule answer and one existing human answer."""
    conn = proposal_db
    ids = book(
        conn,
        [
            Line(-1_000, "synthetic unclaimed one"),
            Line(-2_000, "synthetic unclaimed two"),
            Line(-3_000, "synthetic unclaimed three"),
            Line(-4_000, "synthetic rule answer", rule_category="groceries"),
            Line(-5_000, "synthetic human answer", override="dining"),
        ],
    )
    return conn, tuple(ids)  # type: ignore[return-value]


def _submission(conn: sqlite3.Connection, txn_ids: tuple[str, ...]) -> ProposalSubmission:
    category_id = "dining"
    group = ProposalGroup(
        group_id=group_id_for(category_id, txn_ids),
        category_id=category_id,
        txn_ids=txn_ids,
    )
    return ProposalSubmission(
        schema_version=1,
        ledger_revision=ledger_revision(conn),
        producer=Producer(client="codex", client_version="synthetic", model_reported=None),
        groups=(group,),
    )


def _v2_submission(
    conn: sqlite3.Connection,
    groups: tuple[ProposalGroup, ...],
    *,
    application_mode: str | None = "automatic",
) -> ProposalSubmission:
    return ProposalSubmission(
        schema_version=2,
        application_mode=application_mode,  # type: ignore[arg-type]
        ledger_revision=ledger_revision(conn),
        producer=Producer(client="codex", client_version="synthetic", model_reported=None),
        groups=groups,
    )


def _group(category_id: str, txn_ids: tuple[str, ...]) -> ProposalGroup:
    return ProposalGroup(
        group_id=group_id_for(category_id, txn_ids),
        category_id=category_id,
        txn_ids=txn_ids,
    )


def test_a_v2_run_may_honestly_propose_nothing(
    proposal_ledger: tuple[sqlite3.Connection, tuple[str, ...]],
) -> None:
    """A real Codex run examined 98 candidates, abstained on every one, and had
    no way to say so: empty proposals were rejected, so the only honest outcome
    was recorded as a client failure. "Omission is a valid proposal result" has
    to be true on the wire, not only in the Skill text.
    """
    conn, _ = proposal_ledger
    submission = _v2_submission(conn, ())

    validated = validate_proposal(conn, submission)
    assert validated.proposal_count == 0

    result = submit_proposal(conn, submission)

    assert result.created is True and result.proposal_count == 0
    run = get_run(conn, result.run_id)
    assert run is not None and run.state == "completed"
    assert run.proposals == ()
    assert conn.execute(
        "SELECT COUNT(*) FROM category_override WHERE source = 'agent'"
    ).fetchone()[0] == 0
    # Content identity still deduplicates: saying "nothing" twice is one run.
    assert submit_proposal(conn, submission).created is False


def test_an_empty_review_first_run_leaves_nothing_pending(
    proposal_ledger: tuple[sqlite3.Connection, tuple[str, ...]],
) -> None:
    conn, _ = proposal_ledger
    submission = _v2_submission(conn, (), application_mode="review_first")

    result = submit_proposal(conn, submission)

    run = get_run(conn, result.run_id)
    assert run is not None and run.state == "completed", (
        "an open run with zero proposals would sit in the review area forever"
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM agent_category_proposal WHERE outcome = 'pending'"
    ).fetchone()[0] == 0


def test_declining_the_same_pool_twice_is_the_same_statement_not_a_conflict(
    proposal_ledger: tuple[sqlite3.Connection, tuple[str, ...]],
) -> None:
    """An empty run leaves the revision where it was, so a later round's
    identical declaration deduplicates onto it. On the real ledger that arrived
    through a second job, whose attribution attempt turned honesty into
    proposal_conflict -- three rounds in a row, forever at that revision.
    """
    conn, _ = proposal_ledger
    first_job, first_session = _job_session(conn)
    submission = _v2_submission(conn, ())
    created = submit_proposal(conn, submission, job_id=first_job, session_id=first_session)
    assert created.created is True

    second_job, second_session = _next_round(conn, first_job)
    repeated = submit_proposal(
        conn, submission, job_id=second_job, session_id=second_session
    )

    assert repeated.created is False and repeated.proposal_count == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM category_override WHERE source = 'agent'"
    ).fetchone()[0] == 0
    # The second job holds no claim on the run; nothing was applied on its watch.
    second = get_job(conn, second_job)
    assert second is not None and second.proposal_run_id is None


def test_a_repeat_of_an_applied_run_still_cannot_be_claimed_by_another_job(
    proposal_ledger: tuple[sqlite3.Connection, tuple[str, ...]],
) -> None:
    """The exemption is for empty declarations only: applied work keeps strict
    single-job attribution."""
    conn, ids = proposal_ledger
    first_job, first_session = _job_session(conn)
    submission = _v2_submission(conn, (_group("dining", (ids[0],)),))
    assert submit_proposal(
        conn, submission, job_id=first_job, session_id=first_session
    ).created is True

    second_job, second_session = _next_round(conn, first_job)
    with pytest.raises(ProposalConflict):
        submit_proposal(conn, submission, job_id=second_job, session_id=second_session)


def test_schema_v1_still_requires_at_least_one_group(
    proposal_ledger: tuple[sqlite3.Connection, tuple[str, ...]],
) -> None:
    """V1 semantics are frozen; the empty proposal is a v2-only capability."""
    conn, _ = proposal_ledger
    submission = ProposalSubmission(
        schema_version=1,
        ledger_revision=ledger_revision(conn),
        producer=Producer(client="codex", client_version=None, model_reported=None),
        groups=(),
    )

    with pytest.raises(ProposalConflict):
        validate_proposal(conn, submission)
    with pytest.raises(ProposalConflict):
        submit_proposal(conn, submission)


def _assert_no_automatic_writes(conn: sqlite3.Connection) -> None:
    assert conn.execute(
        "SELECT COUNT(*) FROM agent_proposal_run WHERE schema_version = 2"
    ).fetchone()[0] == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM category_override WHERE source = 'agent'"
    ).fetchone()[0] == 0


def _next_round(conn: sqlite3.Connection, previous_job: str) -> tuple[str, str]:
    """Finish the previous round and claim a fresh manual round with its session."""
    finish_job(
        conn,
        job_id=previous_job,
        candidate_count=1,
        submitted_count=0,
        applied_count=0,
        omitted_count=1,
    )
    queued = enqueue_manual_job(conn)
    assert queued is not None
    claimed = claim_next_job(conn)
    assert claimed is not None
    session_id = start_session(
        conn,
        client="codex",
        session_id="proposal-round-two-session",
        job_id=claimed.id,
        now="2026-08-10T12:05:00+00:00",
    )
    return claimed.id, session_id


def _job_session(
    conn: sqlite3.Connection,
    *,
    application_mode: str = "automatic",
) -> tuple[str, str]:
    source_id = str(conn.execute("SELECT id FROM source_file LIMIT 1").fetchone()[0])
    update_policy(
        conn,
        selected_client="codex",
        application_mode=application_mode,  # type: ignore[arg-type]
        enabled=True,
        auto_classify_new_imports=True,
        acknowledge_provider_data_policy=True,
        now="2026-08-10T12:00:00+00:00",
    )
    queued = enqueue_import_job(conn, source_file_id=source_id)
    assert queued is not None
    claimed = claim_next_job(conn)
    assert claimed is not None
    session_id = start_session(
        conn,
        client="codex",
        session_id="proposal-job-session",
        job_id=claimed.id,
        now="2026-08-10T12:00:10+00:00",
    )
    return claimed.id, session_id


def test_0009_tables_are_strict_and_constrained(
    proposal_db: sqlite3.Connection,
) -> None:
    db = proposal_db
    tables = {
        row["name"]: row["strict"]
        for row in db.execute("PRAGMA table_list")
        if row["name"] in {"agent_proposal_run", "agent_category_proposal"}
    }
    assert tables == {"agent_proposal_run": 1, "agent_category_proposal": 1}

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO agent_proposal_run "
            "(id, ledger_revision, schema_version, client, created_at, state) "
            "VALUES ('bad', 'bad', 1, 'codex', 'now', 'invented')"
        )


def test_submit_is_content_addressed_and_never_changes_effective_categories(
    proposal_ledger,
) -> None:
    conn, ids = proposal_ledger
    submission = _submission(conn, ids[:2])

    validated = validate_proposal(conn, submission)
    assert validated.proposal_count == 2
    assert conn.execute("SELECT COUNT(*) FROM agent_proposal_run").fetchone()[0] == 0

    first = submit_proposal(conn, submission)
    second = submit_proposal(conn, submission)

    assert first.created is True
    assert second.created is False
    assert second.run_id == first.run_id
    assert validated.run_id == first.run_id
    run = get_run(conn, first.run_id)
    assert run is not None
    assert run.state == "open"
    assert [(row.txn_id, row.outcome) for row in run.proposals] == [
        (txn_id, "pending") for txn_id in sorted(ids[:2])
    ]
    assert [row["txn_id"] for row in repo.list_category_overrides(conn)] == [ids[4]], (
        "submitting proposals leaves the one pre-existing human answer unchanged"
    )


def test_v1_can_never_request_automatic_application(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    submission = replace(_submission(conn, ids[:1]), application_mode="automatic")

    with pytest.raises(ProposalConflict, match="version 1.*review-only"):
        submit_proposal(conn, submission)

    assert conn.execute("SELECT COUNT(*) FROM agent_proposal_run").fetchone()[0] == 0
    assert repo.get_category_override(conn, ids[0]) is None


@pytest.mark.parametrize("mode", [None, "review-frist", 1])
def test_v2_requires_an_exact_application_mode(proposal_ledger, mode: object) -> None:
    conn, ids = proposal_ledger
    submission = _v2_submission(
        conn,
        (_group("dining", ids[:1]),),
        application_mode=mode,  # type: ignore[arg-type]
    )

    with pytest.raises(ProposalConflict, match="application_mode"):
        submit_proposal(conn, submission)

    _assert_no_automatic_writes(conn)


def test_v2_review_first_only_creates_pending_audit(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    submission = _v2_submission(
        conn,
        (_group("dining", ids[:2]),),
        application_mode="review_first",
    )

    result = submit_proposal(conn, submission)

    run = get_run(conn, result.run_id)
    assert run is not None
    assert (run.schema_version, run.application_mode, run.state) == (
        2,
        "review_first",
        "open",
    )
    assert {row.outcome for row in run.proposals} == {"pending"}
    assert repo.get_category_override(conn, ids[0]) is None
    assert repo.get_category_override(conn, ids[1]) is None


def test_v2_automatic_applies_ordinary_and_transfer_in_one_boundary(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    submission = _v2_submission(
        conn,
        (
            _group("dining", ids[:1]),
            _group("transfer", ids[1:2]),
        ),
    )

    result = submit_proposal(conn, submission)

    run = get_run(conn, result.run_id)
    assert run is not None
    assert (run.application_mode, run.state) == ("automatic", "completed")
    assert {
        row.txn_id: (row.outcome, row.applied_category_id)
        for row in run.proposals
    } == {
        ids[0]: ("accepted", "dining"),
        ids[1]: ("accepted", "transfer"),
    }
    for txn_id, category_id in ((ids[0], "dining"), (ids[1], "transfer")):
        override = repo.get_category_override(conn, txn_id)
        assert override is not None
        assert (override["category_id"], override["source"], override["agent_run_id"]) == (
            category_id,
            "agent",
            result.run_id,
        )


def test_v2_submit_atomically_links_the_matching_job_and_run(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    job_id, session_id = _job_session(conn)
    submission = _v2_submission(conn, (_group("dining", ids[:1]),))

    result = submit_proposal(
        conn,
        submission,
        job_id=job_id,
        session_id=session_id,
    )

    job = get_job(conn, job_id)
    assert job is not None and job.proposal_run_id == result.run_id


def test_job_mode_mismatch_leaves_proposal_and_link_at_zero(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    job_id, session_id = _job_session(conn, application_mode="review_first")
    submission = _v2_submission(conn, (_group("dining", ids[:1]),))

    with pytest.raises(ProposalConflict, match="application mode"):
        submit_proposal(
            conn,
            submission,
            job_id=job_id,
            session_id=session_id,
        )

    _assert_no_automatic_writes(conn)
    job = get_job(conn, job_id)
    assert job is not None and job.proposal_run_id is None


@pytest.mark.parametrize("failure_stage", ["audit", "override", "outcome", "completion"])
def test_v2_automatic_injected_failures_leave_zero_partial_writes(
    proposal_ledger,
    failure_stage: str,
) -> None:
    conn, ids = proposal_ledger
    trigger = {
        "audit": (
            "BEFORE INSERT ON agent_category_proposal",
            "synthetic audit failure",
        ),
        "override": (
            "BEFORE INSERT ON category_override",
            "synthetic override failure",
        ),
        "outcome": (
            "BEFORE UPDATE OF outcome ON agent_category_proposal",
            "synthetic outcome failure",
        ),
        "completion": (
            "BEFORE UPDATE OF state ON agent_proposal_run",
            "synthetic completion failure",
        ),
    }[failure_stage]
    conn.execute(
        f"CREATE TRIGGER automatic_{failure_stage}_probe {trigger[0]} "
        f"BEGIN SELECT RAISE(ABORT, '{trigger[1]}'); END"
    )
    submission = _v2_submission(conn, (_group("dining", ids[:2]),))

    with pytest.raises(sqlite3.IntegrityError, match=trigger[1]):
        submit_proposal(conn, submission)

    _assert_no_automatic_writes(conn)
    assert repo.get_category_override(conn, ids[0]) is None
    assert repo.get_category_override(conn, ids[1]) is None


def test_v2_automatic_lock_failure_leaves_zero_partial_writes(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    db_path = str(conn.execute("PRAGMA database_list").fetchone()[2])
    contender = open_ledger(db_path, migrate_if_needed=False)
    contender.execute("PRAGMA busy_timeout = 1")
    submission = _v2_submission(conn, (_group("dining", ids[:1]),))
    try:
        with transaction(conn), pytest.raises(sqlite3.OperationalError, match="locked"):
            submit_proposal(contender, submission)
    finally:
        contender.close()

    _assert_no_automatic_writes(conn)
    assert repo.get_category_override(conn, ids[0]) is None


def test_v2_automatic_conflicts_leave_zero_partial_writes(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    cases = [
        replace(
            _v2_submission(conn, (_group("dining", ids[:1]),)),
            ledger_revision="sha256:" + "0" * 64,
        ),
        _v2_submission(conn, (_group("dining", ("missing-synthetic-txn",)),)),
        _v2_submission(conn, (_group("dining", (ids[0], ids[0])),)),
        _v2_submission(
            conn,
            (
                _group("dining", ids[:2]),
                _group("groceries", ids[1:2]),
            ),
        ),
        _v2_submission(conn, (_group("unknown-synthetic-category", ids[:1]),)),
    ]

    for submission in cases:
        with pytest.raises(ProposalConflict):
            submit_proposal(conn, submission)
        _assert_no_automatic_writes(conn)


def test_automatic_withdrawal_preserves_a_later_human_same_category(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    created = submit_proposal(
        conn,
        _v2_submission(conn, (_group("dining", ids[:3]),)),
    )
    with transaction(conn):
        repo.clear_category_override(conn, txn_id=ids[1])
        repo.set_category_override(conn, txn_id=ids[2], category_id="dining")

    result = withdraw_run(conn, created.run_id)

    assert (result.withdrawn, result.already_absent, result.changed_later) == (1, 1, 1)
    assert repo.get_category_override(conn, ids[0]) is None
    assert repo.get_category_override(conn, ids[1]) is None
    later_human = repo.get_category_override(conn, ids[2])
    assert later_human is not None
    assert (later_human["category_id"], later_human["source"], later_human["agent_run_id"]) == (
        "dining",
        "human",
        None,
    )


def test_automatic_withdrawal_preserves_a_later_agent_run_same_category(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    later = submit_proposal(
        conn,
        _v2_submission(
            conn,
            (_group("dining", ids[:1]),),
            application_mode="review_first",
        ),
    )
    automatic = submit_proposal(
        conn,
        _v2_submission(conn, (_group("dining", ids[:1]),)),
    )
    with transaction(conn):
        repo.set_category_override(
            conn,
            txn_id=ids[0],
            category_id="dining",
            source="agent",
            agent_run_id=later.run_id,
        )

    result = withdraw_run(conn, automatic.run_id)

    assert (result.withdrawn, result.already_absent, result.changed_later) == (0, 0, 1)
    current = repo.get_category_override(conn, ids[0])
    assert current is not None
    assert (current["source"], current["agent_run_id"]) == ("agent", later.run_id)


def test_override_writer_requires_and_preserves_honest_agent_provenance(
    proposal_ledger,
) -> None:
    conn, ids = proposal_ledger
    run = submit_proposal(conn, _submission(conn, ids[:1]))

    with pytest.raises(ValueError, match="must name its proposal run"):
        repo.set_category_override(
            conn, txn_id=ids[0], category_id="dining", source="agent"
        )

    with transaction(conn):
        changed = repo.set_category_override(
            conn,
            txn_id=ids[0],
            category_id="dining",
            source="agent",
            agent_run_id=run.run_id,
        )
    assert changed is True
    current = repo.get_category_override(conn, ids[0])
    assert current is not None
    assert (current["source"], current["agent_run_id"]) == ("agent", run.run_id)

    with transaction(conn):
        changed = repo.set_category_override(
            conn, txn_id=ids[0], category_id="dining"
        )
    assert changed is True, "a human claiming the same category changes its provenance"
    current = repo.get_category_override(conn, ids[0])
    assert current is not None
    assert (current["source"], current["agent_run_id"]) == ("human", None)


def test_list_runs_is_newest_first_bounded_and_reports_outcome_counts(
    proposal_ledger,
) -> None:
    conn, ids = proposal_ledger
    first = submit_proposal(conn, _submission(conn, ids[:2]))
    second = submit_proposal(conn, _submission(conn, ids[2:3]))
    review_proposals(conn, first.run_id, ids[:1], action="accept")

    summaries = list_runs(conn, limit=10)

    assert [summary.run_id for summary in summaries] == [second.run_id, first.run_id]
    assert summaries[0].pending == 1
    assert summaries[0].proposal_count == 1
    assert (
        summaries[1].pending,
        summaries[1].accepted,
        summaries[1].edited,
        summaries[1].rejected,
        summaries[1].withdrawn,
    ) == (1, 1, 0, 0, 0)
    assert [summary.run_id for summary in list_runs(conn, limit=1)] == [second.run_id]


@pytest.mark.parametrize("ineligible_index", [3, 4])
def test_submit_refuses_rule_and_human_answers_as_one_unchanged_batch(
    proposal_ledger,
    ineligible_index: int,
) -> None:
    conn, ids = proposal_ledger
    submission = _submission(conn, (ids[0], ids[ineligible_index]))

    with pytest.raises(ProposalConflict, match="not eligible"):
        submit_proposal(conn, submission)

    assert conn.execute("SELECT COUNT(*) FROM agent_proposal_run").fetchone()[0] == 0
    assert repo.list_category_overrides(conn)[0]["txn_id"] == ids[4]


def test_submit_refuses_stale_revision_bad_group_hash_and_cross_group_duplicates(
    proposal_ledger,
) -> None:
    conn, ids = proposal_ledger
    good = _submission(conn, ids[:2])

    with pytest.raises(ProposalConflict, match="revision"):
        submit_proposal(conn, replace(good, ledger_revision="sha256:" + "0" * 64))

    bad_hash = replace(good.groups[0], group_id="sha256:" + "0" * 64)
    with pytest.raises(ProposalConflict, match="group_id"):
        submit_proposal(conn, replace(good, groups=(bad_hash,)))

    second = ProposalGroup(
        group_id=group_id_for("groceries", (ids[1],)),
        category_id="groceries",
        txn_ids=(ids[1],),
    )
    with pytest.raises(ProposalConflict, match="more than one group"):
        submit_proposal(conn, replace(good, groups=(good.groups[0], second)))

    assert conn.execute("SELECT COUNT(*) FROM agent_proposal_run").fetchone()[0] == 0


def test_accept_and_outcome_are_one_transaction(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    created = submit_proposal(conn, _submission(conn, ids[:2]))
    conn.execute(
        "CREATE TRIGGER proposal_outcome_probe BEFORE UPDATE OF outcome "
        "ON agent_category_proposal BEGIN SELECT RAISE(ABORT, 'synthetic outcome failure'); END"
    )

    with pytest.raises(sqlite3.IntegrityError, match="synthetic outcome failure"):
        review_proposals(conn, created.run_id, ids[:1], action="accept")

    assert repo.get_category_override(conn, ids[0]) is None
    assert get_run(conn, created.run_id).proposals[0].outcome == "pending"  # type: ignore[union-attr]


def test_review_refuses_the_whole_batch_if_one_row_gained_a_human_answer(
    proposal_ledger,
) -> None:
    conn, ids = proposal_ledger
    created = submit_proposal(conn, _submission(conn, ids[:2]))
    with transaction(conn):
        repo.set_category_override(conn, txn_id=ids[1], category_id="groceries")

    with pytest.raises(ProposalConflict, match="no longer eligible"):
        review_proposals(conn, created.run_id, ids[:2], action="accept")

    assert repo.get_category_override(conn, ids[0]) is None
    assert repo.get_category_override(conn, ids[1])["category_id"] == "groceries"  # type: ignore[index]
    run = get_run(conn, created.run_id)
    assert run is not None and {row.outcome for row in run.proposals} == {"pending"}


def test_accept_edit_reject_and_dismiss_preserve_the_state_machine(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    created = submit_proposal(conn, _submission(conn, ids[:3]))

    accepted = review_proposals(conn, created.run_id, ids[:1], action="accept")
    edited = review_proposals(
        conn, created.run_id, ids[1:2], action="accept", category_id="groceries"
    )
    rejected = review_proposals(conn, created.run_id, ids[2:3], action="reject")

    assert (accepted.accepted, accepted.edited, accepted.rejected) == (1, 0, 0)
    assert (edited.accepted, edited.edited, edited.rejected) == (0, 1, 0)
    assert (rejected.accepted, rejected.edited, rejected.rejected) == (0, 0, 1)
    run = get_run(conn, created.run_id)
    assert run is not None and run.state == "completed"
    assert {row.txn_id: row.outcome for row in run.proposals} == {
        ids[0]: "accepted",
        ids[1]: "edited",
        ids[2]: "rejected",
    }

    other = submit_proposal(conn, _submission(conn, ids[2:3]))
    dismissed = dismiss_run(conn, other.run_id)
    assert dismissed.rejected == 1
    assert get_run(conn, other.run_id).state == "dismissed"  # type: ignore[union-attr]


def test_withdraw_uses_the_applied_value_as_a_compare_and_clear_guard(proposal_ledger) -> None:
    conn, ids = proposal_ledger
    created = submit_proposal(conn, _submission(conn, ids[:3]))
    review_proposals(conn, created.run_id, ids[:3], action="accept")
    with transaction(conn):
        repo.clear_category_override(conn, txn_id=ids[1])
        repo.set_category_override(conn, txn_id=ids[2], category_id="groceries")

    result = withdraw_run(conn, created.run_id)

    assert (result.withdrawn, result.already_absent, result.changed_later) == (1, 1, 1)
    assert repo.get_category_override(conn, ids[0]) is None
    assert repo.get_category_override(conn, ids[1]) is None
    assert repo.get_category_override(conn, ids[2])["category_id"] == "groceries"  # type: ignore[index]
    run = get_run(conn, created.run_id)
    assert run is not None
    assert {row.outcome for row in run.proposals} == {"withdrawn"}


def test_forget_counts_and_removes_proposal_history_before_its_transactions(
    proposal_ledger,
) -> None:
    conn, ids = proposal_ledger
    created = submit_proposal(conn, _submission(conn, ids[:2]))

    facts = repo.statement_deletion_facts(conn, "c" * 64)
    assert (facts.agent_proposals, facts.agent_proposal_runs) == (2, 1)

    with transaction(conn):
        counts = repo.delete_statement(conn, "c" * 64)

    assert (counts.agent_proposals, counts.agent_proposal_runs) == (2, 1)
    assert get_run(conn, created.run_id) is None
    assert conn.execute("SELECT COUNT(*) FROM txn").fetchone()[0] == 0
