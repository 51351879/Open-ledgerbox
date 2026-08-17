# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turn decisions into learned rules, and learned rules into claimed lines.

This is the loop that makes coverage compound: a category decided once -- by a
person, or by an Agent run that was allowed to apply -- claims every later line
with the same descriptor template, instead of the same merchant being nobody's
business again next month.

Three boundaries hold throughout. A human rule outranks an agent rule and an
agent decision can never overwrite one. An answer applied by a rule is written
as ``source='learned'`` naming its rule -- provenance stays honest, and when a
rule is re-taught its derived answers follow while direct human answers stay.
And an all-noise template teaches nothing, because a rule keyed on "the
descriptor said nothing" would claim money it knows nothing about.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from .descriptor_template import TEMPLATE_VERSION, descriptor_template


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _descriptor_of(conn: sqlite3.Connection, txn_id: str) -> str | None:
    row = conn.execute(
        "SELECT raw_descriptor FROM txn_identity WHERE txn_id = ? ORDER BY rowid LIMIT 1",
        (txn_id,),
    ).fetchone()
    return None if row is None else str(row["raw_descriptor"])


def learn_from_decision(
    conn: sqlite3.Connection,
    *,
    txn_id: str,
    category_id: str,
    source: str,
    agent_run_id: str | None = None,
    now: str | None = None,
) -> str | None:
    """Upsert the rule one decision teaches; return its id, or None when nothing is learnable.

    Must run inside the caller's transaction, alongside the decision itself.
    """
    descriptor = _descriptor_of(conn, txn_id)
    if descriptor is None:
        return None
    template = descriptor_template(descriptor)
    if not template:
        return None
    existing = conn.execute(
        "SELECT id, source FROM learned_rule "
        "WHERE match_kind = 'template' AND template = ? AND template_version = ?",
        (template, TEMPLATE_VERSION),
    ).fetchone()
    timestamp = now or _utc_now()
    if existing is None:
        rule_id = f"lr-{uuid.uuid4().hex}"
        conn.execute(
            "INSERT INTO learned_rule (id, template, template_version, category_id, "
            "source, agent_run_id, learned_from_txn_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rule_id,
                template,
                TEMPLATE_VERSION,
                category_id,
                source,
                agent_run_id,
                txn_id,
                timestamp,
            ),
        )
        return rule_id
    if existing["source"] == "human" and source == "agent":
        # A person taught this template; an agent never overwrites a person.
        return None
    rule_id = str(existing["id"])
    conn.execute(
        "UPDATE learned_rule SET category_id = ?, source = ?, agent_run_id = ?, "
        "learned_from_txn_id = ?, created_at = ? WHERE id = ?",
        (category_id, source, agent_run_id, txn_id, timestamp, rule_id),
    )
    # Derived answers cite the rule, so they follow it. Direct answers do not.
    conn.execute(
        "UPDATE category_override SET category_id = ?, created_at = ? "
        "WHERE learned_rule_id = ? AND source = 'learned'",
        (category_id, timestamp, rule_id),
    )
    return rule_id


MIN_PREFIX_LENGTH = 6


def add_prefix_rule(
    conn: sqlite3.Connection,
    *,
    prefix: str,
    category_id: str,
    now: str | None = None,
) -> str:
    """Decree one human standing rule: descriptors starting this way get this category.

    This exists for facts only the ledger's owner can know -- "every outgoing
    Zelle of mine moves my own money". It is never taught automatically and an
    agent can never create one. Must run inside the caller's transaction.
    """
    text = prefix.strip()
    if len(text) < MIN_PREFIX_LENGTH or not any(c.isalpha() for c in text):
        raise ValueError(
            f"a standing prefix needs at least {MIN_PREFIX_LENGTH} characters "
            "including a letter; a shorter one would claim lines it knows nothing about"
        )
    timestamp = now or _utc_now()
    existing = conn.execute(
        "SELECT id FROM learned_rule WHERE match_kind = 'prefix' AND template = ? "
        "AND template_version = ?",
        (text, TEMPLATE_VERSION),
    ).fetchone()
    if existing is not None:
        rule_id = str(existing["id"])
        conn.execute(
            "UPDATE learned_rule SET category_id = ?, created_at = ? WHERE id = ?",
            (category_id, timestamp, rule_id),
        )
        # A re-decreed prefix moves its derived answers, exactly like re-teaching.
        conn.execute(
            "UPDATE category_override SET category_id = ?, created_at = ? "
            "WHERE learned_rule_id = ? AND source = 'learned'",
            (category_id, timestamp, rule_id),
        )
        return rule_id
    rule_id = f"lr-{uuid.uuid4().hex}"
    conn.execute(
        "INSERT INTO learned_rule (id, match_kind, template, template_version, "
        "category_id, source, agent_run_id, learned_from_txn_id, created_at) "
        "VALUES (?, 'prefix', ?, ?, ?, 'human', NULL, NULL, ?)",
        (rule_id, text, TEMPLATE_VERSION, category_id, timestamp),
    )
    return rule_id


def remove_prefix_rule(conn: sqlite3.Connection, *, prefix: str) -> tuple[int, int]:
    """Withdraw one standing decree and every answer it derived.

    Returns ``(rules_removed, overrides_cleared)``. Direct answers -- human,
    agent, or template-learned -- are untouched: the decree was the only
    evidence behind its own derivations and nothing else. Must run inside the
    caller's transaction.
    """
    row = conn.execute(
        "SELECT id FROM learned_rule WHERE match_kind = 'prefix' AND template = ? "
        "AND template_version = ?",
        (prefix.strip(), TEMPLATE_VERSION),
    ).fetchone()
    if row is None:
        return (0, 0)
    rule_id = str(row["id"])
    cleared = conn.execute(
        "DELETE FROM category_override WHERE source = 'learned' AND learned_rule_id = ?",
        (rule_id,),
    ).rowcount
    removed = conn.execute(
        "DELETE FROM learned_rule WHERE id = ?", (rule_id,)
    ).rowcount
    return (removed, cleared)


def list_prefix_rules(conn: sqlite3.Connection) -> list[tuple[str, str, int]]:
    """Every standing decree with how many lines it currently answers."""
    return [
        (str(row["template"]), str(row["category_id"]), int(row["derived"]))
        for row in conn.execute(
            "SELECT r.template AS template, r.category_id AS category_id, "
            "(SELECT COUNT(*) FROM category_override o WHERE o.learned_rule_id = r.id) "
            "AS derived "
            "FROM learned_rule r WHERE r.match_kind = 'prefix' "
            "ORDER BY r.template",
        )
    ]


def apply_learned_rules(conn: sqlite3.Connection, *, now: str | None = None) -> int:
    """Claim every still-undecided matching line; return how many were claimed.

    Only lines with no override and no rule-derived category are eligible, so
    this can never overwrite any existing decision, whoever made it. An exact
    template rule outranks a prefix decree -- the template names the specific
    payee, and specific evidence beats the broad brush. Must run inside the
    caller's transaction.
    """
    rules = {
        str(row["template"]): (str(row["id"]), str(row["category_id"]))
        for row in conn.execute(
            "SELECT template, id, category_id FROM learned_rule "
            "WHERE match_kind = 'template' AND template_version = ?",
            (TEMPLATE_VERSION,),
        )
    }
    # Longest prefix first, so the most specific decree wins among decrees.
    prefixes = sorted(
        (
            (str(row["template"]).upper(), str(row["id"]), str(row["category_id"]))
            for row in conn.execute(
                "SELECT template, id, category_id FROM learned_rule "
                "WHERE match_kind = 'prefix' AND template_version = ?",
                (TEMPLATE_VERSION,),
            )
        ),
        key=lambda item: len(item[0]),
        reverse=True,
    )
    if not rules and not prefixes:
        return 0
    undecided = conn.execute(
        "SELECT t.id AS txn_id, ti.raw_descriptor AS raw_descriptor "
        "FROM txn t JOIN txn_identity ti ON ti.txn_id = t.id "
        "WHERE t.superseded_by IS NULL AND t.is_transfer = 0 "
        "AND NOT EXISTS (SELECT 1 FROM category_override co WHERE co.txn_id = t.id) "
        "AND NOT EXISTS (SELECT 1 FROM posting p "
        "                WHERE p.txn_id = t.id AND p.category_id IS NOT NULL)"
    ).fetchall()
    timestamp = now or _utc_now()
    applied = 0
    for row in undecided:
        descriptor = str(row["raw_descriptor"])
        match = rules.get(descriptor_template(descriptor))
        if match is None:
            upper = descriptor.upper()
            match = next(
                (
                    (rule_id, category_id)
                    for prefix, rule_id, category_id in prefixes
                    if upper.startswith(prefix)
                ),
                None,
            )
        if match is None:
            continue
        rule_id, category_id = match
        conn.execute(
            "INSERT INTO category_override "
            "(txn_id, category_id, created_at, source, learned_rule_id) "
            "VALUES (?, ?, ?, 'learned', ?)",
            (str(row["txn_id"]), category_id, timestamp, rule_id),
        )
        applied += 1
    return applied


def unlearn_agent_run(conn: sqlite3.Connection, *, run_id: str) -> tuple[int, int]:
    """Remove what one run taught and what those rules applied; keep everything human.

    Returns ``(rules_removed, overrides_cleared)``. Rules a person has since
    re-taught no longer name the run and are not touched; direct overrides are
    the withdrawal path's business, not this function's. Must run inside the
    caller's transaction.
    """
    rule_ids = [
        str(row["id"])
        for row in conn.execute(
            "SELECT id FROM learned_rule WHERE agent_run_id = ?", (run_id,)
        )
    ]
    if not rule_ids:
        return (0, 0)
    marks = ",".join("?" for _ in rule_ids)
    cleared = conn.execute(
        f"DELETE FROM category_override "
        f"WHERE source = 'learned' AND learned_rule_id IN ({marks})",
        rule_ids,
    ).rowcount
    removed = conn.execute(
        f"DELETE FROM learned_rule WHERE id IN ({marks})", rule_ids
    ).rowcount
    return (removed, cleared)
