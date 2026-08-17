# SPDX-License-Identifier: AGPL-3.0-or-later
"""The learning loop: a decision made once claims the same merchant next time.

Counterexamples pin the promises: a human's rule outranks an agent's, an
all-noise descriptor teaches nothing, applying rules never overwrites any
existing decision, and withdrawing an agent run takes the rules it taught and
their downstream effects while leaving later human answers alone.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from ledgerbox.config import DataPaths
from ledgerbox.db import repo
from ledgerbox.db.connection import transaction
from ledgerbox.db.migrate import open_ledger
from ledgerbox.learning import (
    add_prefix_rule,
    apply_learned_rules,
    remove_prefix_rule,
    unlearn_agent_run,
)


@pytest.fixture
def ledger(git_free_tmp: Path) -> Iterator[sqlite3.Connection]:
    paths = DataPaths.resolve(git_free_tmp / "learning-data")
    conn = open_ledger(paths.db)
    conn.execute("INSERT OR IGNORE INTO commodity (id, kind) VALUES ('USD', 'currency')")
    conn.execute(
        "INSERT INTO account (id, parent_id, name, kind, subtype, currency) "
        "VALUES ('acct-test', NULL, 'Assets:Test:Checking', 'asset', 'checking', 'USD')"
    )
    conn.execute(
        "INSERT INTO category (id, parent_id, kind) VALUES ('synthetic-coffee', NULL, 'expense')"
    )
    conn.execute(
        "INSERT INTO category (id, parent_id, kind) VALUES ('synthetic-books', NULL, 'expense')"
    )
    try:
        yield conn
    finally:
        conn.close()


def _line(conn: sqlite3.Connection, txn_id: str, descriptor: str) -> None:
    conn.execute(
        "INSERT INTO txn (id, date, flag, is_transfer, created_at) "
        "VALUES (?, '2026-08-10', '*', 0, '2026-08-10T00:00:00+00:00')",
        (txn_id,),
    )
    conn.execute(
        "INSERT INTO txn_identity (txn_id, account_id, source_system, natural_key, "
        "natural_key_version, occurrence_index, raw_descriptor) "
        "VALUES (?, 'acct-test', 'pdf', ?, 1, 0, ?)",
        (txn_id, f"key-{txn_id}", descriptor),
    )


def _decide(
    conn: sqlite3.Connection,
    txn_id: str,
    category_id: str,
    *,
    source: str = "human",
    agent_run_id: str | None = None,
) -> None:
    with transaction(conn):
        repo.set_category_override(
            conn,
            txn_id=txn_id,
            category_id=category_id,
            source=source,  # type: ignore[arg-type]
            agent_run_id=agent_run_id,
        )


def _agent_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        "INSERT INTO agent_proposal_run "
        "(id, ledger_revision, schema_version, application_mode, client, created_at, state) "
        "VALUES (?, ?, 2, 'automatic', 'codex', '2026-08-10T00:00:00+00:00', 'completed')",
        (run_id, "sha256:" + "5" * 64),
    )


def _rule_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    return [
        (r["template"], r["category_id"], r["source"])
        for r in conn.execute("SELECT * FROM learned_rule ORDER BY template")
    ]


def test_a_human_decision_becomes_a_rule_and_claims_the_next_identical_template(
    ledger: sqlite3.Connection,
) -> None:
    _line(ledger, "txn-monday", "Card Purchase 03/12 Sq *Blue Bottle 4471")
    _line(ledger, "txn-friday", "Card Purchase 03/16 Sq *Blue Bottle 5512")
    _decide(ledger, "txn-monday", "synthetic-coffee")

    assert _rule_rows(ledger) == [
        ("CARD PURCHASE #/# SQ *BLUE BOTTLE #", "synthetic-coffee", "human")
    ]

    with transaction(ledger):
        applied = apply_learned_rules(ledger)

    assert applied == 1
    row = ledger.execute(
        "SELECT source, learned_rule_id, decided_by FROM category_override "
        "JOIN v_txn_category USING (txn_id) WHERE txn_id = 'txn-friday'"
    ).fetchone()
    assert row["source"] == "learned"
    assert row["learned_rule_id"] is not None
    assert row["decided_by"] == "learned", "a machine-applied answer never wears a person's name"


def test_applying_rules_never_overwrites_any_existing_decision(
    ledger: sqlite3.Connection,
) -> None:
    _line(ledger, "txn-a", "Sq *Blue Bottle 4471")
    _line(ledger, "txn-b", "Sq *Blue Bottle 5512")
    _decide(ledger, "txn-b", "synthetic-books")
    _decide(ledger, "txn-a", "synthetic-coffee")

    with transaction(ledger):
        assert apply_learned_rules(ledger) == 0

    assert ledger.execute(
        "SELECT category_id FROM category_override WHERE txn_id = 'txn-b'"
    ).fetchone()[0] == "synthetic-books"


def test_an_agent_rule_never_overwrites_a_human_rule(ledger: sqlite3.Connection) -> None:
    _agent_run(ledger, "sha256:" + "6" * 64)
    _line(ledger, "txn-h", "Sq *Blue Bottle 11")
    _line(ledger, "txn-agent", "Sq *Blue Bottle 22")
    _decide(ledger, "txn-h", "synthetic-coffee")
    _decide(
        ledger, "txn-agent", "synthetic-books", source="agent", agent_run_id="sha256:" + "6" * 64
    )

    assert _rule_rows(ledger) == [("SQ *BLUE BOTTLE #", "synthetic-coffee", "human")]


def test_a_newer_human_decision_updates_the_rule_and_its_derived_answers(
    ledger: sqlite3.Connection,
) -> None:
    _line(ledger, "txn-first", "Sq *Blue Bottle 11")
    _line(ledger, "txn-copy", "Sq *Blue Bottle 22")
    _decide(ledger, "txn-first", "synthetic-coffee")
    with transaction(ledger):
        apply_learned_rules(ledger)
    _line(ledger, "txn-later", "Sq *Blue Bottle 33")
    _decide(ledger, "txn-later", "synthetic-books")

    assert _rule_rows(ledger) == [("SQ *BLUE BOTTLE #", "synthetic-books", "human")]
    assert ledger.execute(
        "SELECT category_id FROM category_override WHERE txn_id = 'txn-copy'"
    ).fetchone()[0] == "synthetic-books", "derived answers follow the rule they cite"
    assert ledger.execute(
        "SELECT category_id FROM category_override WHERE txn_id = 'txn-first'"
    ).fetchone()[0] == "synthetic-coffee", "the person's own earlier answer is not derived"


def test_an_all_noise_descriptor_teaches_nothing(ledger: sqlite3.Connection) -> None:
    _line(ledger, "txn-noise", "0312 4471 998")
    _decide(ledger, "txn-noise", "synthetic-coffee")

    assert _rule_rows(ledger) == []


def test_withdrawing_an_agent_run_takes_its_rules_and_keeps_later_human_answers(
    ledger: sqlite3.Connection,
) -> None:
    run_id = "sha256:" + "7" * 64
    _agent_run(ledger, run_id)
    _line(ledger, "txn-taught", "Sq *Blue Bottle 11")
    _line(ledger, "txn-derived", "Sq *Blue Bottle 22")
    _line(ledger, "txn-human", "TST* Corner Books 44")
    _decide(ledger, "txn-taught", "synthetic-coffee", source="agent", agent_run_id=run_id)
    with transaction(ledger):
        apply_learned_rules(ledger)
    _decide(ledger, "txn-human", "synthetic-books")

    with transaction(ledger):
        rules_removed, overrides_cleared = unlearn_agent_run(ledger, run_id=run_id)

    assert (rules_removed, overrides_cleared) == (1, 1)
    assert _rule_rows(ledger) == [("TST* CORNER BOOKS #", "synthetic-books", "human")], (
        "withdrawal takes what the run taught and nothing a person taught"
    )
    assert ledger.execute(
        "SELECT COUNT(*) FROM category_override WHERE txn_id = 'txn-derived'"
    ).fetchone()[0] == 0
    assert ledger.execute(
        "SELECT category_id FROM category_override WHERE txn_id = 'txn-human'"
    ).fetchone()[0] == "synthetic-books"


def _category(conn: sqlite3.Connection, category_id: str, kind: str = "transfer") -> None:
    conn.execute(
        "INSERT INTO category (id, parent_id, kind) VALUES (?, NULL, ?)",
        (category_id, kind),
    )


def test_a_prefix_decree_claims_every_payee_but_only_forward_matches(
    ledger: sqlite3.Connection,
) -> None:
    """The owner may know every outgoing Zelle is their own money moving.

    The decree crosses payees -- that is its whole point and why only a person
    may make one -- yet an incoming line and an unrelated line stay untouched.
    """
    _category(ledger, "synthetic-transfer")
    _line(ledger, "txn-a", "ZELLE PAYMENT TO PERSON ONE 1122334")
    _line(ledger, "txn-b", "ZELLE PAYMENT TO PERSON TWO 5566778")
    _line(ledger, "txn-in", "ZELLE PAYMENT FROM PERSON ONE 9900112")
    _line(ledger, "txn-x", "CARD PURCHASE COFFEE 4471")

    with transaction(ledger):
        add_prefix_rule(ledger, prefix="ZELLE PAYMENT TO ", category_id="synthetic-transfer")
        claimed = apply_learned_rules(ledger)

    assert claimed == 2
    answers = {
        r["txn_id"]: (r["category_id"], r["source"])
        for r in ledger.execute("SELECT txn_id, category_id, source FROM category_override")
    }
    assert answers == {
        "txn-a": ("synthetic-transfer", "learned"),
        "txn-b": ("synthetic-transfer", "learned"),
    }


def test_a_specific_template_rule_outranks_the_broad_decree(
    ledger: sqlite3.Connection,
) -> None:
    _category(ledger, "synthetic-transfer")
    _line(ledger, "txn-rent-1", "ZELLE PAYMENT TO LANDLORD 1111111")
    _decide(ledger, "txn-rent-1", "synthetic-books")
    _line(ledger, "txn-rent-2", "ZELLE PAYMENT TO LANDLORD 2222222")
    _line(ledger, "txn-other", "ZELLE PAYMENT TO PERSON ONE 3333333")

    with transaction(ledger):
        add_prefix_rule(ledger, prefix="ZELLE PAYMENT TO ", category_id="synthetic-transfer")
        apply_learned_rules(ledger)

    answers = {
        r["txn_id"]: r["category_id"]
        for r in ledger.execute("SELECT txn_id, category_id FROM category_override")
    }
    assert answers["txn-rent-2"] == "synthetic-books", (
        "the payee's own taught rule is more specific evidence than the decree"
    )
    assert answers["txn-other"] == "synthetic-transfer"


def test_a_short_or_letterless_prefix_is_refused(ledger: sqlite3.Connection) -> None:
    _category(ledger, "synthetic-transfer")
    for bad in ("ZELLE", "  ZE  ", "1234567"):
        with pytest.raises(ValueError), transaction(ledger):
            add_prefix_rule(ledger, prefix=bad, category_id="synthetic-transfer")
    assert ledger.execute("SELECT COUNT(*) FROM learned_rule").fetchone()[0] == 0


def test_an_agent_cannot_write_a_prefix_rule_at_all(ledger: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        ledger.execute(
            "INSERT INTO learned_rule (id, match_kind, template, template_version, "
            "category_id, source, agent_run_id, learned_from_txn_id, created_at) "
            "VALUES (?, 'prefix', 'ZELLE PAYMENT TO ', 1, 'synthetic-coffee', "
            "'agent', NULL, NULL, '2026-08-12T00:00:00+00:00')",
            ("lr-" + "a" * 32,),
        )


def test_removing_a_decree_reverts_only_what_it_derived(
    ledger: sqlite3.Connection,
) -> None:
    _category(ledger, "synthetic-transfer")
    _line(ledger, "txn-a", "ZELLE PAYMENT TO PERSON ONE 1122334")
    _line(ledger, "txn-direct", "ZELLE PAYMENT TO PERSON TWO 5566778")
    _decide(ledger, "txn-direct", "synthetic-transfer")

    with transaction(ledger):
        add_prefix_rule(ledger, prefix="ZELLE PAYMENT TO ", category_id="synthetic-transfer")
        apply_learned_rules(ledger)
    with transaction(ledger):
        removed, cleared = remove_prefix_rule(ledger, prefix="ZELLE PAYMENT TO ")

    assert (removed, cleared) == (1, 1)
    answers = {
        r["txn_id"]: r["source"]
        for r in ledger.execute("SELECT txn_id, source FROM category_override")
    }
    assert answers == {"txn-direct": "human"}, (
        "the decree's derivations revert; the person's direct answer stays"
    )


def test_redecreeing_a_prefix_moves_its_derived_answers(
    ledger: sqlite3.Connection,
) -> None:
    _category(ledger, "synthetic-transfer")
    _line(ledger, "txn-a", "ZELLE PAYMENT TO PERSON ONE 1122334")
    with transaction(ledger):
        add_prefix_rule(ledger, prefix="ZELLE PAYMENT TO ", category_id="synthetic-transfer")
        apply_learned_rules(ledger)
    with transaction(ledger):
        add_prefix_rule(ledger, prefix="ZELLE PAYMENT TO ", category_id="synthetic-books")

    row = ledger.execute(
        "SELECT category_id, source FROM category_override WHERE txn_id = 'txn-a'"
    ).fetchone()
    assert (row["category_id"], row["source"]) == ("synthetic-books", "learned")
    assert ledger.execute(
        "SELECT COUNT(*) FROM learned_rule WHERE match_kind = 'prefix'"
    ).fetchone()[0] == 1
