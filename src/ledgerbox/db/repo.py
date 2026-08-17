# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every write the ledger accepts, as explicit SQL.

There is no ORM here for the same reason there is none in
:mod:`ledgerbox.db.connection`: the schema is the product. An ORM would hide
both the integer-minor-units discipline and the exact shape of an idempotent
insert, and those two things are the whole of this module.

**Transaction boundary.** Nothing in here opens a transaction. Every writer
below is a *fragment* of one unit of work, and the caller wraps the whole
statement in :func:`~ledgerbox.db.connection.transaction`::

    with transaction(conn):
        source_id = insert_source_file(conn, ...)
        insert_raw_records(conn, source_file_id=source_id, ...)
        ensure_account(conn, ...)
        counts = insert_entries(conn, source_file_id=source_id, entries=...)
        upsert_balance_assertions(conn, source_file_id=source_id, rows=...)
        replace_review_items(conn, source_file_id=source_id, items=...)

That is what makes "one PDF goes in whole or not at all" true. It also makes
the check-then-insert pattern below safe: ``BEGIN IMMEDIATE`` holds the write
lock for the duration, so no second writer can slip a row between the ``SELECT``
that finds nothing and the ``INSERT`` that follows.

**Ordering is a rule, not a convention.** ``raw_record`` references
``source_file``, ``txn_identity`` references ``raw_record``, and ``posting``
references ``account`` — with ``foreign_keys = ON`` the database enforces the
order above rather than trusting the caller to remember it.

**How idempotency is achieved.** Two different mechanisms, chosen per table
rather than uniformly, because they fail differently:

* ``ON CONFLICT(...) DO NOTHING`` where a re-insert of identical content is the
  only thing a conflict can mean (``account``, ``raw_record``, and the
  already-triaged ``review_item``). Note this is *not* ``INSERT OR IGNORE``:
  ``OR IGNORE`` swallows CHECK and NOT NULL violations too, so a malformed
  ``account.kind`` would vanish instead of raising.
* check-then-insert for transactions, where a conflict has two possible
  meanings and only one of them is benign. ``INSERT OR IGNORE`` on ``txn`` /
  ``posting`` / ``txn_identity`` would give the right row counts and the wrong
  database: if only the identity row collided, the transaction and its postings
  would still be written, leaving money in the ledger with no provenance — the
  exact silent-and-self-consistent failure this project exists to catch.
  So the identity is looked up first; a hit skips the entry whole, and any
  *other* collision is left to raise.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal, TypedDict

from ..learning import learn_from_decision
from ..ledger.identity import opening_txn_id


class BalanceAssertionConflict(RuntimeError):
    """Two sources disagree about one account's balance on one day.

    Not an idempotency problem — an evidence problem. Overwriting the old value
    would destroy the only trace that the disagreement ever existed.
    """

    def __init__(
        self,
        *,
        account_id: str,
        as_of: str,
        commodity_id: str,
        existing_minor: int | None,
        incoming_minor: int | None,
    ) -> None:
        super().__init__(
            f"balance assertion conflict for {account_id} on {as_of} ({commodity_id}): "
            f"stored {existing_minor}, incoming {incoming_minor} (minor units)"
        )
        self.account_id = account_id
        self.as_of = as_of
        self.commodity_id = commodity_id
        self.existing_minor = existing_minor
        self.incoming_minor = incoming_minor


@dataclass(frozen=True, slots=True)
class WriteCounts:
    """What one ingest actually wrote.

    Every field defaults to zero so a caller can assemble the total from the
    several writers below with :func:`dataclasses.replace`. ``skipped_duplicates``
    is the load-bearing one: on the second ingest of the same statement it
    equals the number of entries offered, and every other count is zero.
    """

    txns: int = 0
    postings: int = 0
    identities: int = 0
    raw_records: int = 0
    balance_assertions: int = 0
    review_items: int = 0
    skipped_duplicates: int = 0


def _utc_now() -> str:
    """The only non-deterministic value this module writes.

    ``created_at`` columns are provenance, not identity: every id, key and
    amount is a pure function of content, so rebuilding from ``archive/``
    reproduces the ledger byte for byte apart from these.
    """
    return datetime.now(UTC).isoformat(timespec="seconds")


def _minor(value: object, *, what: str) -> int:
    """Reject anything that is not already an ``int`` count of minor units.

    STRICT tables are necessary but not sufficient here: SQLite converts a REAL
    into an INTEGER column whenever the conversion is lossless, so ``4.0``
    would land as ``4`` and a float would have entered the ledger without a
    word. ``.10 + .20`` is not lossless, and a rounding error that only shows
    up on some rows is worse than one that shows up on all of them. The type
    boundary has to be enforced on this side of the driver.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{what} must be an int of minor units, got {type(value).__name__}: {value!r}"
        )
    return value


def _raw_record_id(source_file_id: str, record_index: int) -> str:
    """Mirrors :func:`ledgerbox.ledger.identity.raw_record_id`.

    Duplicated rather than imported: the identity module is a pure-function
    layer that knows nothing about SQLite, and importing it here would make the
    dependency point the wrong way. The format is pinned by the test suite on
    both sides.
    """
    return f"{source_file_id}:{record_index:05d}"


# ---------------------------------------------------------------------------
# source_file — content-addressed, so re-upload is a no-op by construction
# ---------------------------------------------------------------------------


def find_source_file(conn: sqlite3.Connection, sha256: str) -> sqlite3.Row | None:
    # `fetchone()` is typed as returning Any; naming the type here is what makes
    # every caller's `row["period_end"]` checkable instead of silently untyped.
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM source_file WHERE sha256 = ?", (sha256,)
    ).fetchone()
    return row


def insert_source_file(
    conn: sqlite3.Connection,
    *,
    sha256: str,
    rel_path: str,
    media_type: str,
    byte_len: int,
    institution: str | None,
    period_start: str | None,
    period_end: str | None,
    ingested_at: str,
) -> str:
    """Record one archived file. Returns ``source_file.id`` (which *is* the sha256).

    Idempotent on content: a file already present keeps its original row —
    including its original ``rel_path`` and ``ingested_at`` — and the caller
    gets the existing id back. The bytes are identical by definition of the
    hash, so a second row could only differ in where and when we filed them,
    and the first answer to that is the true one.

    Must run inside the caller's :func:`transaction`.
    """
    existing = find_source_file(conn, sha256)
    if existing is not None:
        return str(existing["id"])

    conn.execute(
        """
        INSERT INTO source_file
          (id, sha256, rel_path, media_type, byte_len,
           institution, period_start, period_end, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            sha256,
            sha256,
            rel_path,
            media_type,
            byte_len,
            institution,
            period_start,
            period_end,
            ingested_at,
        ),
    )
    return sha256


# ---------------------------------------------------------------------------
# account
# ---------------------------------------------------------------------------


def ensure_account(
    conn: sqlite3.Connection,
    *,
    account_id: str,
    name: str,
    kind: str,
    subtype: str | None,
    currency: str,
    institution: str | None,
    mask: str | None,
) -> None:
    """Create the account if it is new; leave it exactly as it is if it is not.

    Never an UPDATE. ``name`` and the rest are the user's to change — renaming
    "Chase Checking" to "Rent Account" is a normal thing to do, and having the
    next statement quietly rename it back would be a bug the user cannot even
    see. The account *id* is derived from institution and mask, so it is stable
    without any of this being mutable.

    ``ON CONFLICT(id) DO NOTHING`` rather than ``INSERT OR IGNORE``: the latter
    also swallows CHECK violations, and a ``kind`` outside the allowed set must
    raise rather than silently do nothing. ``is_own_account`` is left at its
    schema default of 1 — everything created here is one of the user's own
    accounts, and :func:`ledger_totals` depends on that being true.

    Must run inside the caller's :func:`transaction`.
    """
    conn.execute(
        """
        INSERT INTO account (id, name, kind, subtype, currency, institution, mask)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (account_id, name, kind, subtype, currency, institution, mask),
    )


# ---------------------------------------------------------------------------
# category — reference data mirrored from the shipped rules file
# ---------------------------------------------------------------------------


class CategoryKindConflict(RuntimeError):
    """A category already in the ledger has changed sides in the rules file.

    Not an idempotency problem, the same way
    :class:`BalanceAssertionConflict` is not one. Postings already carry this
    id; flipping ``dining`` from expense to income would move historical money
    from one side of every total to the other, and doing that under
    ``ON CONFLICT DO NOTHING`` would leave the database and the rules file
    quietly disagreeing about which side it was.
    """

    def __init__(self, *, category_id: str, stored_kind: str, incoming_kind: str) -> None:
        super().__init__(
            f"category {category_id!r} is {stored_kind!r} in the ledger but {incoming_kind!r} "
            f"in the rules file. Give the new meaning a new id rather than re-pointing this one; "
            f"postings already reference it."
        )
        self.category_id = category_id
        self.stored_kind = stored_kind
        self.incoming_kind = incoming_kind


def ensure_categories(
    conn: sqlite3.Connection, *, rows: Sequence[tuple[str, str | None, str]]
) -> int:
    """Create any category the rules file declares and the ledger has not seen.

    ``rows`` is ``(id, parent_id, kind)`` — exactly what
    :meth:`ledgerbox.analytics.categorize.RuleSet.rows` produces, because the
    rules file is the single definition of what the categories are. This table
    is a mirror of it, existing only so ``posting.category_id`` can have a
    foreign key.

    Returns the number of rows created. Never an UPDATE: an id that is already
    present keeps its ``parent_id`` (a user's arrangement is theirs), and a
    changed ``kind`` raises :class:`CategoryKindConflict` rather than being
    absorbed.

    Must run inside the caller's :func:`transaction`.
    """
    created = 0
    for category_id, parent_id, kind in rows:
        stored = conn.execute(
            "SELECT kind FROM category WHERE id = ?", (category_id,)
        ).fetchone()
        if stored is not None:
            if stored["kind"] != kind:
                raise CategoryKindConflict(
                    category_id=category_id, stored_kind=stored["kind"], incoming_kind=kind
                )
            continue
        conn.execute(
            "INSERT INTO category (id, parent_id, kind) VALUES (?, ?, ?)",
            (category_id, parent_id, kind),
        )
        created += 1
    return created


def set_posting_categories(
    conn: sqlite3.Connection, *, assignments: Mapping[str, str | None]
) -> int:
    """Write ``posting.category_id`` for the given postings. Returns rows changed.

    The one write path for categories, used by both the ingest pipeline and
    ``ledgerbox recategorize``. A second implementation of "how a category gets
    stored" is how the two would come to disagree about which leg carries it.

    ``category_id IS NOT ?`` rather than a plain ``=``: it is NULL-safe in both
    directions, so re-running with unchanged rules reports zero changes instead
    of reporting every row as touched. That count is what tells an operator
    whether editing a rule did anything.

    A posting id that matches nothing raises. An UPDATE that quietly affects no
    rows is the shape of a re-categorisation that reports success and changes
    nothing.

    Must run inside the caller's :func:`transaction`.
    """
    changed = 0
    for posting_id, category_id in assignments.items():
        cursor = conn.execute(
            "UPDATE posting SET category_id = ? WHERE id = ? AND category_id IS NOT ?",
            (category_id, posting_id, category_id),
        )
        if cursor.rowcount:
            changed += cursor.rowcount
            continue
        # Zero rows means either "already correct" or "no such posting", and
        # only the second is a bug. Paid for exclusively on the no-op path.
        if conn.execute("SELECT 1 FROM posting WHERE id = ?", (posting_id,)).fetchone() is None:
            raise LookupError(f"no posting {posting_id!r} to categorise")
    return changed


def categorized_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every booked statement line with the text and sign a classifier needs.

    Reads ``v_transaction``, which is the single-entry rendering: one row per
    statement line, on the bank leg, with the bank's verbatim descriptor. That
    is the same field and the same leg the ingest path classifies, so
    re-categorising cannot drift from categorising.

    ``rule_is_transfer`` and ``rule_category_id`` are joined in from ``txn`` and
    ``posting`` on purpose, and they are the only two *reads* in the codebase
    that reach past ``v_txn_transfer`` / ``v_txn_category`` for the raw columns.
    The caller is about to ask "would the rules answer differently now", and
    that question is about the rules' own previous answer; comparing against the
    effective value would count a person's override as a row the rules want to
    change.

    ``rule_category_id`` was added with migration 0006, which made
    ``v_transaction.category_id`` effective. Before it, this function returned
    the raw ``posting.category_id`` under the name ``category_id`` and the
    caller compared against that; leaving it alone would have reproduced, on the
    category column, the exact defect the paragraph below describes for the
    transfer flag — with the fix for the other half sitting two lines away in
    the same function.

    Being exact about the consequence, because a previous version of this
    paragraph was not: it is the **reported** count that goes wrong, not the
    stored data. What ``cli.cmd_reapply_rules`` writes is a pure function of the
    descriptor — it never reads either column — so a person's decision is safe
    either way and ``txn.is_transfer`` ends up identical. What breaks is that
    ``--dry-run`` promises "N flags would change" and the run that follows
    changes a different number, on exactly the ledgers where somebody has
    corrected something. A preview that disagrees with the thing it previews is
    worse than no preview.
    """
    rows = conn.execute(
        """
        SELECT v.posting_id, v.txn_id, v.amount_minor, v.raw_descriptor,
               p.category_id AS rule_category_id,
               t.is_transfer AS rule_is_transfer
        FROM v_transaction v
        JOIN txn t ON t.id = v.txn_id
        JOIN posting p ON p.id = v.posting_id
        ORDER BY v.date, v.posting_id
        """
    ).fetchall()
    return list(rows)


# ---------------------------------------------------------------------------
# category_override — what a person decided, and the one thing archive/ cannot
# rebuild
#
# The predecessor had no override mechanism at all: a misclassified line stayed
# misclassified forever. This table is the fix, and P2 M2 gives it a second job
# without giving it a second meaning.
#
# One table, one sentence: **this transaction's category is X.** A category
# whose ``kind`` is ``'transfer'`` therefore says "this is a transfer", and any
# income or expense category says "it is not a transfer — it is X". No sentinel
# value, no `is_transfer_override` column, no second table. Saying what a
# transaction *is* strictly dominates saying what it is not, and "what is this,
# then?" is a question a person can actually answer. No row at all means the
# rules' answer stands; nothing in the ingest path ever writes here, so a row
# in this table is always a human disagreeing with a rule.
#
# Nothing below writes ``txn.is_transfer`` or ``posting.category_id``. Deriving
# the effective answer from these rows is the reader's job and it has exactly
# one implementation (``v_txn_transfer``, migration 0005). A writer that also
# "helpfully" flipped ``txn.is_transfer`` would be the second definition of
# what counts as a transfer that STATUS §5.29 exists to forbid — and, being a
# copy, it would be the one that goes stale.
# ---------------------------------------------------------------------------

_CATEGORY_OVERRIDE_SELECT = """
SELECT
  co.txn_id,
  co.category_id,
  co.created_at,
  co.source,
  co.agent_run_id,
  c.kind AS category_kind
FROM category_override co
LEFT JOIN category c ON c.id = co.category_id
"""


def set_category_override(
    conn: sqlite3.Connection,
    *,
    txn_id: str,
    category_id: str,
    created_at: str | None = None,
    source: Literal["human", "agent"] = "human",
    agent_run_id: str | None = None,
) -> bool:
    """Record a human or Agent category answer and its honest provenance.

    False means the stored decision already named this category. That boolean
    is not decoration: it is the only evidence an operator — or an API handler
    about to answer 200 — has that the click landed, exactly the reason
    :func:`set_posting_categories` returns a count of rows changed rather than
    None (STATUS §5.44). ``created_at`` is left alone on that path, because
    nothing was decided.

    An unknown *txn_id* raises :class:`LookupError` rather than writing
    nothing. The foreign key would refuse it too, but ``IntegrityError`` does
    not say *which* of the two references failed, and the two are different
    bugs: an unknown transaction is a stale id in the caller, an unknown
    category is a rules file :func:`ensure_categories` never mirrored. Naming
    the first here leaves the second to the database and lets a caller tell
    them apart.

    This is the only conflict in this module resolved with ``DO UPDATE``, which
    the module docstring's two mechanisms oblige it to argue for. Two different
    values here are not two pieces of evidence the way two balance assertions
    are — they are one person changing their mind, and the later decision is by
    definition the one in force. ``created_at`` moves with it: it dates the
    decision that is current, not one the user has since abandoned.

    Must run inside the caller's :func:`transaction`.
    """
    if source == "human" and agent_run_id is not None:
        raise ValueError("a human category override cannot name an Agent run")
    if source == "agent" and agent_run_id is None:
        raise ValueError("an Agent category override must name its proposal run")

    existing = conn.execute(
        "SELECT category_id, source, agent_run_id FROM category_override WHERE txn_id = ?",
        (txn_id,),
    ).fetchone()
    if (
        existing is not None
        and existing["category_id"] == category_id
        and existing["source"] == source
        and existing["agent_run_id"] == agent_run_id
    ):
        return False

    # Only reached on a writing path, and only there. An override row already
    # present implies the transaction exists — the foreign key would not have
    # let the row in otherwise.
    if conn.execute("SELECT 1 FROM txn WHERE id = ?", (txn_id,)).fetchone() is None:
        raise LookupError(f"no txn {txn_id!r} to override")

    conn.execute(
        """
        INSERT INTO category_override
          (txn_id, category_id, created_at, source, agent_run_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(txn_id) DO UPDATE SET
          category_id     = excluded.category_id,
          created_at      = excluded.created_at,
          source          = excluded.source,
          agent_run_id    = excluded.agent_run_id,
          learned_rule_id = NULL
        """,
        (txn_id, category_id, created_at or _utc_now(), source, agent_run_id),
    )
    # The same decision that answers this transaction teaches its template, so
    # the next identical merchant is claimed instead of asked about again.
    learn_from_decision(
        conn,
        txn_id=txn_id,
        category_id=category_id,
        source=source,
        agent_run_id=agent_run_id,
        now=created_at,
    )
    return True


def clear_category_override(conn: sqlite3.Connection, *, txn_id: str) -> bool:
    """Withdraw the user's decision so the rules answer again. Returns whether a row went.

    False means there was nothing to withdraw, and the asymmetry with
    :func:`set_category_override` — which raises on an unknown *txn_id* — is
    deliberate. Setting an override on a transaction that does not exist loses
    a decision the user made; clearing one leaves the intended state, "this
    transaction has no override", true either way. The boolean still reports
    honestly which of the two happened, so a caller that wants to distinguish
    "cleared" from "there was nothing there" can.

    This restores the rules' answer rather than any earlier override: the
    previous value is gone, not stacked. An override is a correction to a
    derivation, and there is nothing underneath it but the derivation.

    Must run inside the caller's :func:`transaction`.
    """
    cursor = conn.execute("DELETE FROM category_override WHERE txn_id = ?", (txn_id,))
    return cursor.rowcount > 0


@dataclass(frozen=True, slots=True)
class BulkOverrideResult:
    """What one bulk decision did, counted by kind rather than summed.

    ``replaced`` is the field this exists for. The other counts describe work;
    that one describes a **loss**. Naming a category over a line somebody had
    already named by hand destroys a decision ``archive/`` cannot rebuild —
    §5.49's point, and the reason ``forget`` lists its two irreversible kinds on
    their own line rather than inside a total. Folding it into ``changed`` would
    hide the only part of this operation that cannot be undone by repeating it
    with a different answer.

    ``transfer_added`` and ``transfer_removed`` are the consequence that reaches
    beyond these rows: a line becoming a transfer leaves the In and Out figures,
    and one ceasing to be a transfer rejoins them. Counted rather than summed,
    because the amounts belong to the query that owns those figures and prose
    arithmetic beside a real total is how two numbers for one thing get onto a
    page (``routes/transactions._update_summary`` makes the same choice for one
    row).
    """

    changed: int
    unchanged: int
    replaced: int
    transfer_added: int
    transfer_removed: int


def set_category_overrides(
    conn: sqlite3.Connection,
    *,
    txn_ids: Sequence[str],
    category_id: str | None,
    source: Literal["human", "agent"] = "human",
    agent_run_id: str | None = None,
) -> BulkOverrideResult:
    """Record one decision about many transactions, in the caller's transaction.

    **This adds no definition of anything.** It calls
    :func:`set_category_override` and :func:`clear_category_override` once per
    id and counts what they report — the same two functions the single-row
    endpoint calls, so "what a person deciding a category does" has one
    implementation and marking eighty lines cannot come to mean something
    slightly different from marking one. That matters most for the word this
    feature exists for: naming the ``transfer`` category is how a person says
    "this is a transfer", and ``docs/STATUS.md`` §5.29 is the standing record of
    what a second definition of that costs.

    It exists at all because the rules claim **none** of the author's 415 real
    lines (§5.52), and 86.9% of the unclaimed spending is money moving between
    the author's own accounts (§5.79) — 79 rows that could only be marked one
    click at a time.

    Every id must already exist: an unknown one raises ``LookupError`` from
    :func:`set_category_override`. The route checks first and refuses the whole
    request, because a caller holding a stale id is holding a stale *list*, and
    writing the part of it that still resolves would be answering a question
    nobody asked.

    The effective transfer flag is read before and after through
    :func:`get_transaction`, which reads ``v_transaction`` — so the transition
    counts come from the same composed answer the table renders and not from a
    rule applied twice.
    """
    changed = unchanged = replaced = added = removed = 0

    for txn_id in txn_ids:
        before = get_transaction(conn, txn_id)
        if before is None:
            raise LookupError(f"no transaction {txn_id} in this ledger")
        previous = get_category_override(conn, txn_id)
        # A decision destroyed, rather than one recorded: this line already
        # carried a category somebody chose, and it was a different one.
        if previous is not None and previous["category_id"] != category_id:
            replaced += 1

        if category_id is None:
            moved = clear_category_override(conn, txn_id=txn_id)
        else:
            moved = set_category_override(
                conn,
                txn_id=txn_id,
                category_id=category_id,
                source=source,
                agent_run_id=agent_run_id,
            )

        if moved:
            changed += 1
        else:
            unchanged += 1

        after = get_transaction(conn, txn_id)
        was, now = bool(before["is_transfer"]), bool(after["is_transfer"]) if after else False
        if now and not was:
            added += 1
        elif was and not now:
            removed += 1

    return BulkOverrideResult(
        changed=changed,
        unchanged=unchanged,
        replaced=replaced,
        transfer_added=added,
        transfer_removed=removed,
    )


def get_category_override(conn: sqlite3.Connection, txn_id: str) -> sqlite3.Row | None:
    """The user's decision about one transaction, or None if they never made one.

    None is the whole of "no override" — the rules still speak for this
    transaction — and nothing here guesses at a default, because a default
    would be indistinguishable from a decision.

    ``category_kind`` rides along because the one question every reader of this
    table asks is "is this a transfer", and answering it from ``category_id``
    alone means every caller re-deriving the same join. The join is OUTER for
    the reason given in :func:`list_category_overrides`.
    """
    row: sqlite3.Row | None = conn.execute(
        _CATEGORY_OVERRIDE_SELECT + "WHERE co.txn_id = ?", (txn_id,)
    ).fetchone()
    return row


def list_category_overrides(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every manual decision in the ledger, ordered by ``txn_id``.

    **This is the one table ``archive/`` cannot reproduce.** Statements rebuild
    transactions, postings, balances, and the rule-derived categories; nobody
    ever wrote down that the user called *this* line a transfer except this
    table. ``tests/test_rebuild.py`` compares it like every other table, and it
    matches today only because it is empty on both sides. Being able to read
    the whole of it out in one call is therefore worth something by itself: it
    is the only backup-able form of the only data here that is not derived.
    A UI will list these too, but that is the smaller reason.

    Ordered by ``txn_id``, not by ``created_at``. A transaction id is a content
    hash, so this order is a pure function of what is stored; ``created_at`` is
    second-precision provenance that ties, and ties would then be broken
    arbitrarily by the storage engine. It is also the column
    ``test_rebuild.py`` excludes as volatile — an order that depends on when
    somebody clicked is an order two readers can disagree about.

    The join to ``category`` is OUTER. The foreign key makes the parent row
    present today, so INNER would return the same rows — which is exactly why
    writing INNER would be wrong: it would record, in the one query that reads
    unrecoverable user data, a promise about a table this function does not
    own. A missing category must show up as a NULL ``category_kind``, not as a
    decision that silently disappeared.
    """
    rows: list[sqlite3.Row] = conn.execute(
        _CATEGORY_OVERRIDE_SELECT + "ORDER BY co.txn_id"
    ).fetchall()
    return rows


# ---------------------------------------------------------------------------
# Agent category proposals — local audit data, never an effective answer
#
# These functions deliberately know no Agent protocol.  They persist and read
# the two 0009 tables; `ledgerbox.proposals` owns validation and the state
# machine.  Nothing in this section writes category_override.  The one bridge
# from proposal to effective category remains set_category_overrides above and
# the service calls it inside the same BEGIN IMMEDIATE as the outcome update.
# ---------------------------------------------------------------------------


def proposal_revision_transactions(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Stable transaction facts included in the proposal ledger revision.

    Effective category state is intentionally absent.  A review of group one
    writes an override and must not make group two from the same run globally
    stale.  Each pending row's current decision source is validated separately
    at submit and review time; ingest, forget, or changed transaction evidence
    still changes this structural revision.
    """
    rows: list[sqlite3.Row] = conn.execute(
        """
        SELECT txn_id, date, amount_minor, currency, raw_descriptor
        FROM v_transaction
        ORDER BY txn_id
        """
    ).fetchall()
    return rows


def insert_agent_proposal_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ledger_revision: str,
    schema_version: int,
    client: str,
    client_version: str | None,
    model_reported: str | None,
    application_mode: Literal["review_first", "automatic"] | None = None,
    created_at: str | None = None,
) -> None:
    """Insert one content-addressed run in the caller's transaction."""
    conn.execute(
        """
        INSERT INTO agent_proposal_run
          (id, ledger_revision, schema_version, client, client_version,
           model_reported, application_mode, created_at, state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (
            run_id,
            ledger_revision,
            schema_version,
            client,
            client_version,
            model_reported,
            application_mode,
            created_at or _utc_now(),
        ),
    )


def insert_agent_category_proposals(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    rows: Sequence[tuple[str, str, str]],
) -> None:
    """Insert ``(txn_id, group_id, category_id)`` rows for one run."""
    conn.executemany(
        """
        INSERT INTO agent_category_proposal
          (run_id, txn_id, group_id, suggested_category_id)
        VALUES (?, ?, ?, ?)
        """,
        [(run_id, txn_id, group_id, category_id) for txn_id, group_id, category_id in rows],
    )


def get_agent_proposal_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM agent_proposal_run WHERE id = ?", (run_id,)
    ).fetchone()
    return row


def list_agent_proposal_runs(
    conn: sqlite3.Connection, *, limit: int
) -> list[sqlite3.Row]:
    """Newest proposal runs with outcome counts, bounded by the caller.

    The review page needs to discover content-addressed runs without guessing
    ids.  It does not need proposal rows for every historical run, so this
    query returns only audit metadata and counts; the explicit run read remains
    the one place that loads the rows and their current ledger facts.

    ``rowid`` is the final ordering key because ``created_at`` has second
    precision and two local submissions can share it.  Proposal ids are hashes,
    so ordering by the id would turn a timestamp tie into arbitrary content
    order rather than insertion order.
    """
    rows: list[sqlite3.Row] = conn.execute(
        """
        SELECT
          r.*,
          COUNT(p.txn_id) AS proposal_count,
          SUM(CASE WHEN p.outcome = 'pending' THEN 1 ELSE 0 END) AS pending,
          SUM(CASE WHEN p.outcome = 'accepted' THEN 1 ELSE 0 END) AS accepted,
          SUM(CASE WHEN p.outcome = 'edited' THEN 1 ELSE 0 END) AS edited,
          SUM(CASE WHEN p.outcome = 'rejected' THEN 1 ELSE 0 END) AS rejected,
          SUM(CASE WHEN p.outcome = 'withdrawn' THEN 1 ELSE 0 END) AS withdrawn
        FROM agent_proposal_run AS r
        LEFT JOIN agent_category_proposal AS p ON p.run_id = r.id
        GROUP BY r.id
        ORDER BY r.created_at DESC, r.rowid DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows


def list_agent_category_proposals(
    conn: sqlite3.Connection, run_id: str
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = conn.execute(
        """
        SELECT * FROM agent_category_proposal
        WHERE run_id = ?
        ORDER BY group_id, txn_id
        """,
        (run_id,),
    ).fetchall()
    return rows


def get_agent_category_proposals(
    conn: sqlite3.Connection, run_id: str, txn_ids: Sequence[str]
) -> list[sqlite3.Row]:
    """The explicitly named proposal rows, preserving deterministic order."""
    found: list[sqlite3.Row] = []
    for chunk in _chunks(txn_ids):
        marks = ",".join("?" * len(chunk))
        found.extend(
            conn.execute(
                f"SELECT * FROM agent_category_proposal "
                f"WHERE run_id = ? AND txn_id IN ({marks}) ORDER BY txn_id",
                (run_id, *chunk),
            ).fetchall()
        )
    return sorted(found, key=lambda row: str(row["txn_id"]))


def review_agent_category_proposal(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    txn_id: str,
    outcome: str,
    applied_category_id: str | None,
    reviewed_at: str,
) -> None:
    """Move one pending row once; a stale/repeated review is an error."""
    cursor = conn.execute(
        """
        UPDATE agent_category_proposal
        SET outcome = ?, applied_category_id = ?, reviewed_at = ?
        WHERE run_id = ? AND txn_id = ? AND outcome = 'pending'
        """,
        (outcome, applied_category_id, reviewed_at, run_id, txn_id),
    )
    if cursor.rowcount != 1:
        raise LookupError(f"proposal {run_id!r}/{txn_id!r} is not pending")


def withdraw_agent_category_proposal(
    conn: sqlite3.Connection, *, run_id: str, txn_id: str, reviewed_at: str
) -> bool:
    """Mark an applied row withdrawn, retaining the category that was applied."""
    cursor = conn.execute(
        """
        UPDATE agent_category_proposal
        SET outcome = 'withdrawn', reviewed_at = ?
        WHERE run_id = ? AND txn_id = ? AND outcome IN ('accepted','edited')
        """,
        (reviewed_at, run_id, txn_id),
    )
    return cursor.rowcount == 1


def set_agent_proposal_run_state(
    conn: sqlite3.Connection, *, run_id: str, state: str
) -> None:
    cursor = conn.execute(
        "UPDATE agent_proposal_run SET state = ? WHERE id = ?", (state, run_id)
    )
    if cursor.rowcount != 1:
        raise LookupError(f"no proposal run {run_id!r}")


def count_pending_agent_proposals(conn: sqlite3.Connection, run_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM agent_category_proposal "
            "WHERE run_id = ? AND outcome = 'pending'",
            (run_id,),
        ).fetchone()[0]
    )


# ---------------------------------------------------------------------------
# Agent remaining-coverage triage — exhaustive audit, not a category answer
# ---------------------------------------------------------------------------


def pending_agent_proposal_txn_ids(
    conn: sqlite3.Connection, txn_ids: Sequence[str]
) -> tuple[str, ...]:
    """Pending category proposals overlapping an explicit transaction set."""
    found: set[str] = set()
    for chunk in _chunks(txn_ids):
        marks = ",".join("?" * len(chunk))
        found.update(
            str(row["txn_id"])
            for row in conn.execute(
                f"SELECT txn_id FROM agent_category_proposal "
                f"WHERE outcome = 'pending' AND txn_id IN ({marks})",
                chunk,
            ).fetchall()
        )
    return tuple(sorted(found))


def insert_agent_triage_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    ledger_revision: str,
    scope_revision: str,
    schema_version: int,
    since: str | None,
    until: str | None,
    client: str,
    client_version: str | None,
    model_reported: str | None,
    created_at: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO agent_triage_run
          (id, ledger_revision, scope_revision, schema_version, since, until,
           client, client_version, model_reported, created_at, state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
        """,
        (
            run_id,
            ledger_revision,
            scope_revision,
            schema_version,
            since,
            until,
            client,
            client_version,
            model_reported,
            created_at or _utc_now(),
        ),
    )


def insert_agent_triage_items(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    rows: Sequence[tuple[str, str, str, str]],
) -> None:
    """Insert ``(txn_id, group_id, route, reason_code)`` audit rows."""
    conn.executemany(
        """
        INSERT INTO agent_triage_item
          (run_id, txn_id, group_id, route, reason_code)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (run_id, txn_id, group_id, route, reason_code)
            for txn_id, group_id, route, reason_code in rows
        ],
    )


def get_agent_triage_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM agent_triage_run WHERE id = ?", (run_id,)
    ).fetchone()
    return row


def list_agent_triage_runs(conn: sqlite3.Connection, *, limit: int) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = conn.execute(
        """
        SELECT
          r.*,
          COUNT(i.txn_id) AS item_count,
          SUM(CASE WHEN i.outcome = 'pending' THEN 1 ELSE 0 END) AS pending,
          SUM(CASE WHEN i.outcome = 'confirmed_transfer' THEN 1 ELSE 0 END)
            AS confirmed_transfer,
          SUM(CASE WHEN i.outcome = 'confirmed_taxonomy_gap' THEN 1 ELSE 0 END)
            AS confirmed_taxonomy_gap,
          SUM(CASE WHEN i.outcome = 'left_uncertain' THEN 1 ELSE 0 END)
            AS left_uncertain,
          SUM(CASE WHEN i.outcome = 'classified_existing' THEN 1 ELSE 0 END)
            AS classified_existing,
          SUM(CASE WHEN i.outcome = 'stale' THEN 1 ELSE 0 END) AS stale,
          SUM(CASE WHEN i.outcome = 'withdrawn' THEN 1 ELSE 0 END) AS withdrawn
        FROM agent_triage_run AS r
        LEFT JOIN agent_triage_item AS i ON i.run_id = r.id
        GROUP BY r.id
        ORDER BY r.created_at DESC, r.rowid DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return rows


def list_agent_triage_items(
    conn: sqlite3.Connection, run_id: str
) -> list[sqlite3.Row]:
    rows: list[sqlite3.Row] = conn.execute(
        """
        SELECT * FROM agent_triage_item
        WHERE run_id = ?
        ORDER BY route, reason_code, group_id, txn_id
        """,
        (run_id,),
    ).fetchall()
    return rows


def get_agent_triage_items(
    conn: sqlite3.Connection, run_id: str, txn_ids: Sequence[str]
) -> list[sqlite3.Row]:
    found: list[sqlite3.Row] = []
    for chunk in _chunks(txn_ids):
        marks = ",".join("?" * len(chunk))
        found.extend(
            conn.execute(
                f"SELECT * FROM agent_triage_item "
                f"WHERE run_id = ? AND txn_id IN ({marks}) ORDER BY txn_id",
                (run_id, *chunk),
            ).fetchall()
        )
    return sorted(found, key=lambda row: str(row["txn_id"]))


def review_agent_triage_item(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    txn_id: str,
    outcome: str,
    applied_category_id: str | None,
    reviewed_at: str,
) -> None:
    cursor = conn.execute(
        """
        UPDATE agent_triage_item
        SET outcome = ?, applied_category_id = ?, reviewed_at = ?
        WHERE run_id = ? AND txn_id = ? AND outcome = 'pending'
        """,
        (outcome, applied_category_id, reviewed_at, run_id, txn_id),
    )
    if cursor.rowcount != 1:
        raise LookupError(f"triage item {run_id!r}/{txn_id!r} is not pending")


def withdraw_agent_triage_item(
    conn: sqlite3.Connection, *, run_id: str, txn_id: str, reviewed_at: str
) -> bool:
    cursor = conn.execute(
        """
        UPDATE agent_triage_item
        SET outcome = 'withdrawn', reviewed_at = ?
        WHERE run_id = ? AND txn_id = ?
          AND outcome IN ('confirmed_transfer','classified_existing')
        """,
        (reviewed_at, run_id, txn_id),
    )
    return cursor.rowcount == 1


def set_agent_triage_run_state(
    conn: sqlite3.Connection, *, run_id: str, state: str
) -> None:
    cursor = conn.execute(
        "UPDATE agent_triage_run SET state = ? WHERE id = ?", (state, run_id)
    )
    if cursor.rowcount != 1:
        raise LookupError(f"no triage run {run_id!r}")


def count_pending_agent_triage_items(conn: sqlite3.Connection, run_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM agent_triage_item "
            "WHERE run_id = ? AND outcome = 'pending'",
            (run_id,),
        ).fetchone()[0]
    )


# ---------------------------------------------------------------------------
# raw_record — provenance, never identity
# ---------------------------------------------------------------------------


def insert_raw_records(
    conn: sqlite3.Connection,
    *,
    source_file_id: str,
    payloads: Sequence[tuple[int, str, str]],
    parser_id: str,
    parser_version: str,
) -> int:
    """Store the verbatim parse of each record. Returns rows actually inserted.

    ``payloads`` is ``(record_index, kind, payload_json)``. The row id is
    ``{source_file_id}:{record_index:05d}``, so it is a pure function of
    content location and re-running the same file cannot produce a second copy.

    On conflict the stored row wins. ``source_file_id`` is the sha256 of the
    file, so the same id cannot possibly describe different bytes; a differing
    payload could only come from a changed parser, and re-keying the archive
    under a new parser version is a deliberate migration, not something an
    ingest should do behind the operator's back.

    Must run inside the caller's :func:`transaction`.
    """
    before = conn.total_changes
    conn.executemany(
        """
        INSERT INTO raw_record
          (id, source_file_id, record_index, kind, payload, parser_id, parser_version)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        """,
        [
            (
                _raw_record_id(source_file_id, record_index),
                source_file_id,
                record_index,
                kind,
                payload,
                parser_id,
                parser_version,
            )
            for record_index, kind, payload in payloads
        ],
    )
    return conn.total_changes - before


# ---------------------------------------------------------------------------
# txn + posting + txn_identity — the idempotent unit
# ---------------------------------------------------------------------------

_IDENTITY_EXISTS_SQL = """
SELECT 1 FROM txn_identity
WHERE account_id = ? AND source_system = ? AND natural_key = ? AND natural_key_version = ?
"""


def _identity_exists(conn: sqlite3.Connection, identity: Any) -> bool:
    """Has this exact transaction already been booked?

    The question is asked of ``UNIQUE(account_id, source_system, natural_key,
    natural_key_version)`` only — deliberately *not* of the partial unique index
    on ``(account_id, source_system, source_id)``. A repeat ingest of the same
    file reproduces both, so this arm always answers first in the benign case.
    A FITID that comes back attached to a *different* natural key is not a
    duplicate ingest: it is a bank reusing an id, and it must raise
    ``IntegrityError`` and take the whole statement down with it rather than be
    silently dropped.
    """
    row = conn.execute(
        _IDENTITY_EXISTS_SQL,
        (
            identity.account_id,
            identity.source_system,
            identity.natural_key,
            identity.natural_key_version,
        ),
    ).fetchone()
    return row is not None


def insert_entries(
    conn: sqlite3.Connection,
    *,
    source_file_id: str,
    entries: Sequence[Any],
) -> WriteCounts:
    """Book each entry as one ``txn`` + its ``posting`` legs + one ``txn_identity``.

    An entry whose identity key is already present is skipped **whole** — no
    txn, no postings — and counted in ``skipped_duplicates``. That is what makes
    ingesting the same PDF three times leave the row counts untouched.

    Anything else that collides raises. In particular, two entries in the *same*
    batch sharing an identity key will hit the UNIQUE constraint: identical rows
    within one statement are supposed to be separated by ``occurrence_index``,
    so a collision here means the upstream numbering failed, and that is not a
    condition to absorb quietly.

    Not checked here: that the legs sum to zero. That is reconciliation check 0,
    which runs before anything is written, and ``v_unbalanced_txn`` keeps it
    visible afterwards. Two implementations of one rule would eventually
    disagree.

    Requires the file's ``raw_record`` rows to exist already — the foreign key
    on ``txn_identity.raw_record_id`` enforces it. Must run inside the caller's
    :func:`transaction`.
    """
    txns = postings = identities = skipped = 0

    for entry in entries:
        identity = entry.identity
        if _identity_exists(conn, identity):
            skipped += 1
            continue

        conn.execute(
            """
            INSERT INTO txn (id, date, payee, narration, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (entry.txn_id, entry.date, entry.payee, entry.narration, _utc_now()),
        )
        txns += 1

        for posting in entry.postings:
            conn.execute(
                """
                INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    posting.id,
                    entry.txn_id,
                    posting.seq,
                    posting.account_id,
                    _minor(posting.amount_minor, what=f"posting {posting.id} amount_minor"),
                    posting.currency,
                ),
            )
            postings += 1

        # The identity carries its own record_index; entry.record_index mirrors
        # it for callers that only hold the entry.
        record_index = getattr(identity, "record_index", None)
        if record_index is None:
            record_index = entry.record_index

        conn.execute(
            """
            INSERT INTO txn_identity
              (txn_id, account_id, source_system, source_id, natural_key,
               natural_key_version, occurrence_index, raw_descriptor, raw_record_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry.txn_id,
                identity.account_id,
                identity.source_system,
                identity.source_id,
                identity.natural_key,
                identity.natural_key_version,
                identity.occurrence_index,
                identity.raw_descriptor,
                _raw_record_id(source_file_id, record_index),
            ),
        )
        identities += 1

    return WriteCounts(
        txns=txns, postings=postings, identities=identities, skipped_duplicates=skipped
    )


# ---------------------------------------------------------------------------
# balance_assertion — insert, or verify; never overwrite
# ---------------------------------------------------------------------------


def _closes_on(conn: sqlite3.Connection, source_file_id: str, as_of: str) -> bool:
    """Does this statement's period end on *as_of*?

    The tie-break for a balance two statements both print. See the call site.
    """
    row = conn.execute(
        "SELECT period_end FROM source_file WHERE id = ?", (source_file_id,)
    ).fetchone()
    return row is not None and row["period_end"] == as_of


def upsert_balance_assertions(
    conn: sqlite3.Connection,
    *,
    source_file_id: str,
    rows: Sequence[Any],
) -> int:
    """Record each declared balance. Returns rows actually inserted.

    "Upsert" is a slight lie and the lie is on purpose: an existing assertion
    for the same ``(account_id, as_of, commodity_id)`` is *verified*, not
    updated. Equal value, nothing happens. Different value, the ingest stops
    with :class:`BalanceAssertionConflict`.

    A balance on a given day is a fact the bank printed. Two statements
    printing two different numbers for it means one of them is wrong, or the
    account was restated — either way a human has to look. Taking the newer
    value would erase the only evidence that there was ever a question.

    Only ``amount_minor`` is compared: P0 asserts cash balances, and
    ``quantity_scaled`` is written as NULL until there are holdings to assert.

    Must run inside the caller's :func:`transaction`.
    """
    inserted = 0
    for row in rows:
        amount_minor = _minor(row.amount_minor, what=f"balance assertion {row.id} amount_minor")
        existing = conn.execute(
            """
            SELECT amount_minor FROM balance_assertion
            WHERE account_id = ? AND as_of = ? AND commodity_id = ?
            """,
            (row.account_id, row.as_of, row.commodity_id),
        ).fetchone()

        if existing is not None:
            if existing["amount_minor"] != amount_minor:
                raise BalanceAssertionConflict(
                    account_id=row.account_id,
                    as_of=row.as_of,
                    commodity_id=row.commodity_id,
                    existing_minor=existing["amount_minor"],
                    incoming_minor=amount_minor,
                )
            if _closes_on(conn, source_file_id, row.as_of):
                # Provenance for a shared seam must be a function of content,
                # not of ingest order. A rebuild reads archive/ in sha256 order,
                # which is nothing like the order the files first arrived in, so
                # "whoever got there first owns it" makes the same statements
                # produce a different ledger on the way back - and the rebuild
                # invariant is the reason every id in this system is a hash.
                #
                # Exactly one statement ends on any given day, and it is the one
                # whose transactions were replayed to reach this balance. The
                # next statement only restates it as its opening figure.
                conn.execute(
                    "UPDATE balance_assertion SET source_file_id = ? "
                    "WHERE account_id = ? AND as_of = ? AND commodity_id = ?",
                    (source_file_id, row.account_id, row.as_of, row.commodity_id),
                )
            continue

        conn.execute(
            """
            INSERT INTO balance_assertion
              (id, account_id, as_of, commodity_id, amount_minor, quantity_scaled, source_file_id)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (row.id, row.account_id, row.as_of, row.commodity_id, amount_minor, source_file_id),
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# review_item — regenerated per file, but never over a human's decision
# ---------------------------------------------------------------------------


def replace_review_items(
    conn: sqlite3.Connection,
    *,
    source_file_id: str,
    items: Sequence[Any],
) -> int:
    """Rebuild this file's open review queue. Returns rows actually inserted.

    Two halves, and both matter:

    * the file's ``open`` items are deleted first, so re-ingesting a statement
      that still fails four checks leaves four rows, not eight. Review-item ids
      are deterministic, so this mostly amounts to refreshing them in place;
      the DELETE is what retires an item for a check that now passes.
    * ``resolved`` and ``dismissed`` items are left alone, and a regenerated
      item that collides with one is dropped (``ON CONFLICT(id) DO NOTHING``).
      A queue that resurrects something the user has already dismissed is a
      queue the user stops reading.

    Must run inside the caller's :func:`transaction`.
    """
    conn.execute(
        "DELETE FROM review_item WHERE source_file_id = ? AND status = 'open'",
        (source_file_id,),
    )

    created_at = _utc_now()
    inserted = 0
    for item in items:
        # Catching a mis-wired batch here rather than after it is written:
        # deleting file A's queue and inserting items belonging to file B would
        # leave A looking clean.
        if item.source_file_id != source_file_id:
            raise ValueError(
                f"review item {item.id} belongs to {item.source_file_id}, "
                f"not to the file being ingested ({source_file_id})"
            )
        before = conn.total_changes
        conn.execute(
            """
            INSERT INTO review_item
              (id, source_file_id, status, severity, check_id, detail, created_at)
            VALUES (?, ?, 'open', ?, ?, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (item.id, source_file_id, item.severity, item.check_id, item.detail, created_at),
        )
        inserted += conn.total_changes - before
    return inserted


# ---------------------------------------------------------------------------
# read side
# ---------------------------------------------------------------------------

#: What was counted and what was left out, from one scan.
#:
#: ``v_txn_transfer`` rather than ``txn.is_transfer``: the view is the single
#: definition, folding the rule-derived flag together with a person's override
#: (migration 0005). Reading the raw column here would honour a rule and ignore
#: the human who overruled it, and ``v_cashflow_monthly`` -- which does read the
#: view -- would disagree with this function. That disagreement is not
#: hypothetical: it was measured in the window between the migration landing and
#: this query being changed, and the ``cashflow_agreement`` check caught it.
#:
#: The excluded amounts come out of the same scan as the included ones, and that
#: is the point rather than an optimisation. Two queries would be two chances to
#: disagree about which rows the flag removed, and the whole subject of this
#: module right now is concepts that grew a second definition.
#: Reads ``v_cashflow_line`` (migration 0008), which is the row set every money
#: figure on the page is a sum of. The joins and the `superseded_by` predicate
#: that used to be written here live there now, so this query and the two
#: projections beside it -- ``category_spend`` and ``monthly_cashflow`` -- cannot
#: come to disagree about which postings count. They are sums of the same rows.
_TOTALS_SQL = """
SELECT
  COALESCE(-SUM(CASE WHEN l.is_transfer = 0 AND l.account_kind = 'income'
                     THEN l.amount_minor ELSE 0 END), 0) AS inflow_minor,
  COALESCE(-SUM(CASE WHEN l.is_transfer = 0 AND l.account_kind = 'expense'
                     THEN l.amount_minor ELSE 0 END), 0) AS outflow_minor,
  COUNT(DISTINCT CASE WHEN l.is_transfer = 0 THEN l.txn_id END)
                                                          AS txn_count,
  COALESCE(-SUM(CASE WHEN l.is_transfer = 1 AND l.account_kind = 'income'
                     THEN l.amount_minor ELSE 0 END), 0) AS transfer_excluded_in_minor,
  COALESCE(-SUM(CASE WHEN l.is_transfer = 1 AND l.account_kind = 'expense'
                     THEN l.amount_minor ELSE 0 END), 0) AS transfer_excluded_out_minor
FROM v_cashflow_line l
"""

#: **No ``COALESCE``, and a count beside the sum.** An empty sum is not a
#: balance of zero. A window that closes before this ledger begins selects no
#: own-account leg at all, and the ledger has nothing to say about what was in
#: the account that day -- while a ledger whose legs really do cancel to nothing
#: has a balance, and it is $0.00. Folding both into one figure prints "$0.00"
#: over a date this ledger has never heard of, which is a claim about somebody's
#: money rather than the absence of one; ``routes/analytics.py`` argues exactly
#: that for ``/api/health``'s null totals and this query was contradicting it.
#: The count is what separates the two, since ``amount_minor`` is NOT NULL and
#: the sum is therefore NULL only when nothing was selected.
_BALANCE_SQL = """
SELECT SUM(p.amount_minor) AS balance_minor, COUNT(*) AS leg_count
FROM posting p
JOIN account a ON a.id = p.account_id
JOIN txn     t ON t.id = p.txn_id
WHERE a.is_own_account = 1 AND t.superseded_by IS NULL
"""

_TRANSFER_SQL = """
SELECT COUNT(*) AS transfer_count
FROM txn t
JOIN v_txn_transfer vt ON vt.txn_id = t.id
WHERE vt.is_transfer = 1 AND t.superseded_by IS NULL
"""


def _narrowed(
    sql: str, span: DateSpan | None, *, column: str, connective: str
) -> tuple[str, list[str]]:
    """Append *span*'s bounds to *sql*, which must end where its filter ends.

    Every statement this is used on stops at the end of its own filter -- no
    ``GROUP BY`` and no ``ORDER BY`` follows -- which is what makes appending
    safe, and is stated here rather than left for somebody to notice after
    adding one. *connective* is ``WHERE`` for a query that has no filter yet and
    ``AND`` for one that does.

    The column is always a transaction date; which alias spells it depends on
    whether the query reads ``v_cashflow_line`` or joins ``txn`` itself. See
    :class:`DateSpan` for why it is that column and not the statement month.
    """
    parts, params = (span or UNBOUNDED).clauses(column)
    if not parts:
        return sql, []
    return f"{sql} {connective} " + " AND ".join(parts), params


class LedgerTotals(TypedDict):
    """The shape :func:`ledger_totals` returns.

    Spelled out rather than left as ``dict[str, int]`` for one field's sake:
    ``balance_minor`` is the only one that can be ``None``, and a mapping typed
    loosely enough to carry that would force every reader of the other seven to
    handle a ``None`` that cannot arrive. The point of naming the shape is that
    the type checker asks about the balance at each of its six call sites and
    about nothing else.
    """

    inflow_minor: int
    outflow_minor: int
    net_minor: int
    txn_count: int
    #: ``None`` when the window selects no own-account posting at all — before
    #: the ledger begins, or after everything has been forgotten. Distinct from
    #: ``0``, which is a balance that was measured and came to nothing.
    balance_minor: int | None
    transfer_count: int
    transfer_excluded_in_minor: int
    transfer_excluded_out_minor: int


def ledger_totals(conn: sqlite3.Connection, span: DateSpan | None = None) -> LedgerTotals:
    """What was earned, what was spent, and what is left.

    *span* narrows every figure to a window of **transaction dates**, except
    ``balance_minor``, which takes only the closing bound because a balance is a
    position rather than a movement. ``None`` means the whole ledger, which is
    what every caller predating the date control passes and what
    ``verify``/``doctor`` must always pass -- a check that reported on a slice
    would be a check that could be made green by choosing a window.

    Income and expense are measured on the **income and expense legs**, not on
    the bank leg. Summing the bank leg answers "how did the balance move",
    which is a different question and is contaminated by everything that moves
    a balance without being income or expense:

    * a transfer between two of your own accounts moves the bank leg twice and
      earns nothing — the predecessor counted those, and 82.6% of its "income"
      and 77.5% of its "expenses" were transfers;
    * the opening balance moves the bank leg once and earns nothing.

    How much of that is structural and how much is the ``WHERE`` clause above
    is worth being exact about, because an earlier version of this docstring
    was not:

    * A transfer **between two accounts you own** touches no income or expense
      account at either end, so it cannot reach these sums however it is
      flagged. That part really is structural.
    * A **one-sided** transfer — a card payment, a Zelle to yourself — has no
      second own-account in the ledger, so its counter leg is
      ``expenses:uncategorized`` like any other outflow. Nothing structural
      excludes it. The ``vt.is_transfer = 0`` arm of the ``CASE`` above is what
      excludes it, and it is a condition someone did have to remember to write.
    * The opening balance books against equity, which is structural again.

    ``transfer_excluded_in_minor`` and ``transfer_excluded_out_minor`` are the
    second bullet made visible: exactly what the flag took out of the two
    figures above, on the same legs and in the same sign convention, so
    ``inflow_minor + transfer_excluded_in_minor`` is what the ledger would have
    reported with no transfer detection at all. Reporting only how *many*
    transactions were excluded leaves a reader with a count they cannot compare
    against anything, while one wrong flag quietly shrinks their spending —
    which is the failure mode this project exists for, pointed at itself.
    Neither field is a verdict: an exclusion may be entirely correct.

    Which raises the question of why this function and ``v_cashflow_monthly``
    give the same answer at all, since they sum **different postings of
    different row sets**: this one sums income and expense legs over every
    non-transfer ``txn``, while the view sums the own-account leg over the
    transactions that have a ``txn_identity`` row.

    Three attempts at stating a sufficient condition for their agreement were
    each refuted by construction during verification, so this paragraph now
    states the guarantee instead of a condition list:

        **One function produces every row that ``inflow_minor`` /
        ``outflow_minor`` and ``v_cashflow_monthly`` count.**
        :func:`ledgerbox.ledger.posting.build_entries` emits, for each
        statement line, a bank leg plus exactly one counter leg chosen by sign
        from ``income:uncategorized`` or ``expenses:uncategorized``;
        ``insert_entries`` writes those two postings and an identity row in one
        transaction, and it is the only writer of ``txn_identity`` **anywhere in
        src/** (``tests/test_db.py`` writes the table directly, on purpose, to
        build shapes this sentence says are unreachable).

    Two scope limits on that sentence, both found by reading it adversarially:

    * It is about the **cashflow pair**, not about everything this function
      returns. ``balance_minor`` comes from a different query and deliberately
      *does* count the opening entry's asset leg, which
      :func:`sync_opening_entry` writes and ``build_entries`` never sees.
    * "Counts", not "reads". The ``FROM``/``JOIN``/``WHERE`` above scans the
      opening entry's two postings like any others; what keeps them out of
      these sums is the ``CASE``, since neither ``asset`` nor ``equity``
      matches ``income`` or ``expense``. (No row count is quoted here on
      purpose — the size of that scan is a property of whatever has been
      ingested, and it changes the moment anything sets ``is_transfer``.)

    Two refuted attempts, kept as illustrations of how it breaks rather than as
    a complete list of the ways:

    * a transaction between two accounts you own, with an identity row, has two
      own-account legs and no income/expense leg — the view counts it and this
      function does not;
    * a transaction with an income/expense leg and **no** identity row — this
      function counts it and the view does not.

    Both are unreachable while ``build_entries`` is the only producer, and
    neither is prevented by anything else. There is no claim here that those are
    the only two ways: three previous versions of this paragraph each claimed
    completeness and each was wrong.

    Which is why the agreement is no longer left to this paragraph.
    ``pipeline.verify_ledger``'s block-level ``cashflow_agreement`` check
    compares the two queries field by field, and ``doctor`` folds the same
    comparison into its exit code. Both illustrations above are its negative
    test cases, which is the only reason this sentence is allowed to stop
    trying to be exhaustive.

    What that check does **not** buy, said here because the sentence above is
    the third one in this docstring to have over-promised: it asserts the two
    queries *match*, never that either is *right*. A second writer that copies
    the shape ``build_entries`` produces — bank leg, income/expense counter
    leg, identity row — and gets the amounts wrong, or books the same line
    twice, moves both sums by the same amount and the check stays green.
    Verification constructed exactly that. It catches a **shape one query
    cannot see**, and nothing else; ``docs/STATUS.md`` §7 lists the blind spots
    it leaves.

    Separately, and often confused with the above: the opening entry is missing
    from *this* function's sums because it books against **equity**, which no
    branch of the ``CASE`` matches. Its absence from the *view* is the different
    fact that it has no identity row. Two mechanisms, one per query.

    ``balance_minor`` is the actual balance of the user's own accounts, and it
    is a plain sum only because an opening entry exists — see
    :func:`sync_opening_entry`. It is ``None`` when the window selects no
    own-account posting at all, which is not the same fact as a balance of zero
    and must not be printed as one: a window closing before this ledger begins
    gets a figure the ledger has no evidence for. ``outflow_minor`` is negative,
    matching ``v_cashflow_monthly``, so ``inflow + outflow == net``.
    """
    flows, flow_params = _narrowed(_TOTALS_SQL, span, column="l.date", connective="WHERE")
    transfers, transfer_params = _narrowed(_TRANSFER_SQL, span, column="t.date", connective="AND")
    # A balance is a level, not a flow: it is whatever the account holds at the
    # end of the window, so only the closing bound applies. Narrowing it at both
    # ends would report "the money that arrived during these weeks" under a
    # label that says Balance, which is the one thing on this page that means a
    # position rather than a movement.
    balance, balance_params = _narrowed(
        _BALANCE_SQL,
        DateSpan(until=(span or UNBOUNDED).until),
        column="t.date",
        connective="AND",
    )

    row = conn.execute(flows, flow_params).fetchone()
    inflow = int(row["inflow_minor"])
    outflow = int(row["outflow_minor"])
    held = conn.execute(balance, balance_params).fetchone()
    return {
        "inflow_minor": inflow,
        "outflow_minor": outflow,
        "net_minor": inflow + outflow,
        "txn_count": int(row["txn_count"]),
        # The count and not the sum decides. A sum of legs that cancel is a
        # measured zero; no legs at all is nothing measured.
        "balance_minor": int(held["balance_minor"]) if int(held["leg_count"]) else None,
        "transfer_count": int(
            conn.execute(transfers, transfer_params).fetchone()["transfer_count"]
        ),
        "transfer_excluded_in_minor": int(row["transfer_excluded_in_minor"]),
        "transfer_excluded_out_minor": int(row["transfer_excluded_out_minor"]),
    }


def set_transfer_flags(conn: sqlite3.Connection, *, assignments: Mapping[str, bool]) -> int:
    """Write ``txn.is_transfer`` for the given transactions. Returns rows changed.

    The **rules'** answer, and only theirs. A person's answer lives in
    ``category_override`` and is folded in by ``v_txn_transfer``, so re-running
    the rules over a ledger cannot overwrite a decision somebody made — which is
    the whole reason the two are stored apart rather than one column being
    edited by both.

    Shaped after :func:`set_posting_categories`, down to reporting only real
    changes and raising on an unknown id, for the same two reasons: the count is
    how an operator learns whether editing a rule did anything, and an UPDATE
    that quietly matches nothing is a re-tagging that reports success and
    changes nothing.

    Must run inside the caller's :func:`transaction`.
    """
    changed = 0
    for txn_id, flagged in assignments.items():
        value = 1 if flagged else 0
        cursor = conn.execute(
            "UPDATE txn SET is_transfer = ? WHERE id = ? AND is_transfer IS NOT ?",
            (value, txn_id, value),
        )
        if cursor.rowcount:
            changed += cursor.rowcount
            continue
        if conn.execute("SELECT 1 FROM txn WHERE id = ?", (txn_id,)).fetchone() is None:
            raise LookupError(f"no transaction {txn_id!r} to flag")
    return changed


OPENING_EQUITY_ACCOUNT = "equity:opening-balances"
OPENING_NARRATION = "Opening balance"


def sync_opening_entry(
    conn: sqlite3.Connection, *, account_id: str, currency: str, created_at: str | None = None
) -> str | None:
    """Give the account the balance it already had before the first statement.

    Without this, ``SUM(posting.amount_minor)`` over the bank account is the
    *net change* across every statement, not the balance — the ledger holds
    -$212.40 where the bank says $288.71. Anyone who sums the postings to get a
    balance is then quietly wrong, and the plain-text beancount export cannot
    validate at all, because beancount replays from zero and every printed
    balance assertion would fail.

    The opening event is the earliest balance the statements assert, booked
    against ``equity:opening-balances`` — the account migration 0003 seeds and
    which nothing else uses. Deriving it from the earliest assertion rather
    than taking it from whichever statement happens to be ingested first is
    what keeps it order-independent: a rebuild reads ``archive/`` in sha256
    order, and the answer must not depend on that. If a statement older than
    any seen so far arrives later, the entry moves, because the account's
    opening event moved.

    Must run inside the caller's :func:`transaction`.
    """
    earliest = conn.execute(
        "SELECT as_of, amount_minor FROM balance_assertion "
        "WHERE account_id = ? AND amount_minor IS NOT NULL "
        "ORDER BY as_of ASC LIMIT 1",
        (account_id,),
    ).fetchone()

    wanted = (
        None
        if earliest is None
        else opening_txn_id(
            account_id, str(earliest["as_of"]), int(earliest["amount_minor"])
        )
    )

    # Structural, not textual: in this schema an equity posting only ever comes
    # from an opening entry, so this finds them without trusting a narration
    # string that a future feature might reuse.
    #
    # This runs *before* the early return for "no assertions left", and it used
    # not to. Nothing could reach that state while statements only ever arrived,
    # so the function returned None and left whatever was already there. Removing
    # a statement reaches it immediately: forgetting the last one deleted every
    # assertion and left the opening entry standing, an equity leg asserting a
    # balance no document in the ledger claims any more. `balance_assertions`
    # would pass (there is nothing left to check), `double_entry` would pass (the
    # orphan sums to zero), and `balance_minor` would report money that is not
    # there -- while re-ingesting the remaining archive into an empty database
    # produced no such row. That difference is the whole of what deletion has to
    # not do.
    stale = [
        str(row["txn_id"])
        for row in conn.execute(
            "SELECT DISTINCT p.txn_id FROM posting p "
            "WHERE p.account_id = ? AND p.txn_id IN "
            "(SELECT txn_id FROM posting WHERE account_id = ?)",
            (OPENING_EQUITY_ACCOUNT, account_id),
        )
        if str(row["txn_id"]) != wanted
    ]
    for txn_id in stale:
        conn.execute("DELETE FROM posting WHERE txn_id = ?", (txn_id,))
        conn.execute("DELETE FROM txn WHERE id = ?", (txn_id,))

    if earliest is None:
        return None

    as_of = str(earliest["as_of"])
    amount_minor = int(earliest["amount_minor"])
    # Recomputed rather than narrowed with an assert: it is a pure function of
    # three values already in hand, and the second call costs one hash.
    wanted = opening_txn_id(account_id, as_of, amount_minor)

    if conn.execute("SELECT 1 FROM txn WHERE id = ?", (wanted,)).fetchone() is not None:
        return wanted

    conn.execute(
        "INSERT INTO txn (id, date, payee, narration, flag, is_transfer, created_at) "
        "VALUES (?, ?, NULL, ?, '*', 0, ?)",
        (wanted, as_of, OPENING_NARRATION, created_at or _utc_now()),
    )
    for seq, (target, amount) in enumerate(
        ((account_id, amount_minor), (OPENING_EQUITY_ACCOUNT, -amount_minor))
    ):
        conn.execute(
            "INSERT INTO posting (id, txn_id, seq, account_id, amount_minor, currency) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"{wanted}:{seq}", wanted, seq, target, amount, currency),
        )
    return wanted


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """``{table: rows}`` for every table, including ``schema_migration``.

    The idempotency assertion in one call: ingest the same statement three
    times and this dict must not move. Table names are interpolated because
    SQL has no parameter for an identifier — they come from the database's own
    catalogue, not from anything a user or a statement can influence, and they
    are quoted anyway.
    """
    names = [
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]) for name in names
    }


# ---------------------------------------------------------------------------
# --- review queue (P1) ---
#
# What the HTTP API reads, plus the single write that resolving a queued item
# is allowed to make. The reads run on the request's read-only handle; the one
# writer keeps the module's rule and runs inside the caller's transaction.
#
# The queue is the only place where "this statement was refused" is visible
# after the ingest that refused it has scrolled off the terminal, so every
# query below is written so that it cannot lose a row: filters are explicit,
# joins are outer, and a missing period produces a NULL month rather than a
# missing item.
# ---------------------------------------------------------------------------

#: One shape for a queue row, shared by the list and the single-item read.
#: Resolving an item answers with the same body the list renders, and two
#: queries that select different columns for the same model is a discrepancy
#: that only surfaces after a user has already clicked something.
#:
#: The join to ``source_file`` is OUTER. The foreign key makes the parent row
#: present today, so INNER would return the same rows — which is exactly why it
#: would be the wrong thing to write down: the one query that must never drop a
#: row is the one listing what nobody has looked at yet. A statement refused for
#: an unrecognised layout has no ``period_end``, ``substr(NULL, …)`` is NULL,
#: and the item arrives with ``statement_month`` unset rather than not at all.
_REVIEW_ITEM_SELECT = """
SELECT
  ri.id,
  ri.source_file_id,
  ri.status,
  ri.severity,
  ri.check_id,
  ri.detail,
  ri.created_at,
  ri.resolved_at,
  substr(sf.period_end, 1, 7) AS statement_month
FROM review_item ri
LEFT JOIN source_file sf ON sf.id = ri.source_file_id
"""

#: A NULL filter means "every value", so one query serves the queue and its
#: history without string-building a WHERE clause per call.
_REVIEW_ITEM_FILTER = """
WHERE (:status IS NULL OR ri.status = :status)
  AND (:severity IS NULL OR ri.severity = :severity)
"""

#: Blocking items first, then oldest first. Severity is the only thing that
#: reorders the queue: a ``block`` means a statement is not in the ledger, and
#: a warning about one that is cannot be allowed to sit above it.
_REVIEW_ITEM_ORDER = """
ORDER BY CASE ri.severity WHEN 'block' THEN 0 ELSE 1 END, ri.created_at, ri.check_id
"""


def list_review_items(
    conn: sqlite3.Connection,
    *,
    status: str | None = "open",
    severity: str | None = None,
) -> list[sqlite3.Row]:
    """Everything a human still has to look at, blocking items first.

    ``status=None`` lists every item whatever its state, which is how the
    queue's history is read; the default is the queue itself. ``severity``
    narrows it further and is unset by default, because a caller who wants only
    warnings has to say so — defaulting to ``block`` would hide the warnings and
    defaulting to ``warn`` would hide the refusals.

    Each row carries ``detail`` exactly as stored: the JSON string
    ``{"message": …, "detail": {…}}`` that :mod:`ledgerbox.reconcile.report`
    wrote. Splitting it is the API layer's job, and doing it here would mean
    parsing the same text twice in two places that could disagree about what a
    malformed payload means.
    """
    rows: list[sqlite3.Row] = conn.execute(
        _REVIEW_ITEM_SELECT + _REVIEW_ITEM_FILTER + _REVIEW_ITEM_ORDER,
        {"status": status, "severity": severity},
    ).fetchall()
    return rows


def get_review_item(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    """One queue row, or None if that id was never issued.

    Selects the same columns as :func:`list_review_items` so that the answer to
    "resolve this" and the answer to "list these" describe an item identically.
    None is the whole of "unknown id" — the caller turns it into a 404, and
    nothing here guesses at a near match.
    """
    row: sqlite3.Row | None = conn.execute(
        _REVIEW_ITEM_SELECT + "WHERE ri.id = ?", (item_id,)
    ).fetchone()
    return row


def set_review_status(
    conn: sqlite3.Connection,
    *,
    item_id: str,
    status: str,
    resolved_at: str | None = None,
) -> bool:
    """Record that a person made a decision about one item. Returns False if it does not exist.

    **This books nothing.** It writes ``review_item.status`` and
    ``review_item.resolved_at``, and it touches no other table — not ``txn``,
    not ``posting``, not ``txn_identity``, not ``balance_assertion``. Marking a
    blocking item resolved says a human looked at a statement that is not in the
    ledger; it does not put it there, and ``verify`` keeps reporting the file as
    unbooked afterwards. The only way a refused statement enters the ledger is
    to fix the parser and re-ingest the same archived bytes, which is possible
    precisely because the bytes were kept.

    ``status`` is not validated here. The column's CHECK constraint is the
    single definition of the allowed set, and re-stating it in Python would give
    two definitions that can drift; a bad value raises ``IntegrityError`` and
    takes the transaction down rather than writing a row no reader understands.

    Must run inside the caller's :func:`transaction`.
    """
    cursor = conn.execute(
        "UPDATE review_item SET status = ?, resolved_at = ? WHERE id = ?",
        (status, resolved_at, item_id),
    )
    return cursor.rowcount > 0


def open_review_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """``{"block": n, "warn": n}`` for the open queue. Both keys, always.

    A severity with no open items reports zero rather than being absent, so no
    caller has to remember which of the two ways of writing "nothing is wrong"
    this function chose.
    """
    counts = {"block": 0, "warn": 0}
    for row in conn.execute(
        "SELECT severity, COUNT(*) AS n FROM review_item WHERE status = 'open' GROUP BY severity"
    ):
        counts[str(row["severity"])] = int(row["n"])
    return counts


#: Booked transactions per archived file, by provenance rather than by date:
#: an identity row is what says "this transaction came from that file", and it
#: is the same evidence :func:`count_unbooked_statements` asks for. Joining from
#: ``raw_record`` inward is deliberate — an identity with no ``raw_record_id``
#: has no source file to be counted against, and ``v_identity_without_source``
#: is where that condition is meant to be seen.
_STATEMENT_TXN_COUNTS = """
SELECT rr.source_file_id AS source_file_id, COUNT(DISTINCT ti.txn_id) AS txn_count
FROM raw_record rr
JOIN txn_identity ti ON ti.raw_record_id = rr.id
GROUP BY rr.source_file_id
"""

_STATEMENT_REVIEW_COUNTS = """
SELECT
  source_file_id,
  SUM(CASE WHEN severity = 'block' THEN 1 ELSE 0 END) AS open_block,
  SUM(CASE WHEN severity = 'warn'  THEN 1 ELSE 0 END) AS open_warn
FROM review_item
WHERE status = 'open'
GROUP BY source_file_id
"""

_STATEMENTS_SQL = f"""
SELECT
  s.source_file_id,
  s.institution,
  s.period_start,
  s.period_end,
  s.statement_month,
  s.byte_len,
  s.ingested_at,
  COALESCE(b.txn_count, 0) AS txn_count,
  COALESCE(r.open_block, 0) AS open_block,
  COALESCE(r.open_warn,  0) AS open_warn
FROM v_statement s
LEFT JOIN ({_STATEMENT_TXN_COUNTS}) b ON b.source_file_id = s.source_file_id
LEFT JOIN ({_STATEMENT_REVIEW_COUNTS}) r ON r.source_file_id = s.source_file_id
ORDER BY s.period_end DESC, s.ingested_at DESC, s.source_file_id
"""


def list_statements(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every archived statement, newest period first, with what it actually produced.

    ``txn_count`` is the load-bearing column and the reason this is not just
    ``SELECT * FROM v_statement``. A file that was archived and refused has a
    row in ``source_file`` exactly like one that was booked — content addressing
    means the bytes are kept either way — and from ``v_statement`` alone the two
    are indistinguishable. **``txn_count = 0`` is the statement that never made
    it into the ledger.** Both count joins are outer for that reason: an INNER
    join would drop precisely those rows and leave a list in which every
    statement looks fine.

    ``open_block`` / ``open_warn`` are that file's *open* queue depth, so the
    list can say why a statement is at zero without a second request per row.

    Superseded transactions are not excluded from ``txn_count``. The question
    this column answers is "did these bytes produce booked rows", which is
    provenance and does not stop being true when a corrected statement later
    supersedes them; filtering them would report a statement that has already
    been superseded as one that was never read.
    """
    rows: list[sqlite3.Row] = conn.execute(_STATEMENTS_SQL).fetchall()
    return rows


_UNBOOKED_STATEMENTS_SQL = """
SELECT
  sf.id                        AS source_file_id,
  sf.period_end,
  substr(sf.period_end, 1, 7)  AS statement_month
FROM source_file sf
WHERE NOT EXISTS (
  SELECT 1 FROM raw_record rr
  JOIN txn_identity ti ON ti.raw_record_id = rr.id
  WHERE rr.source_file_id = sf.id
)
ORDER BY sf.period_end, sf.id
"""


def count_unbooked_statements(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Archived statements with nothing booked behind them. Empty is healthy.

    Feeds a block-level ``verify`` check, which is why it returns the rows and
    not the number: "3 statements were never booked" is a fact nobody can act
    on, and the months are already in hand when the count is.

    The evidence is structural. A statement is booked when some ``txn_identity``
    row points, through ``raw_record``, back at its file; nothing else counts,
    because that chain is the only thing that ties money in the ledger to the
    document it came from. This deliberately does not consult ``review_item``:
    an unbooked statement whose queue entry has been resolved or dismissed is
    still unbooked, and a check that a human can silence by clicking Dismiss is
    not a check.
    """
    rows: list[sqlite3.Row] = conn.execute(_UNBOOKED_STATEMENTS_SQL).fetchall()
    return rows


# ---------------------------------------------------------------------------
# --- reading transactions (P2 M4) ---
#
# The page could not show a single transaction until this existed. Everything
# here reads `v_transaction`, which is the single-entry rendering: one row per
# statement line, on the bank leg, carrying the effective category (migration
# 0006) and the effective transfer flag (0005).
#
# **Filtering, sorting and paging happen here, in SQL.** EXECUTION_PLAN §6 says
# so, and the reason is not performance on 415 rows -- it is that the browser
# holding the whole ledger in order to slice it is how the predecessor came to
# have two month definitions, two category answers and a table that disagreed
# with its own chart. One query, one answer.
#
# **The summary and the rows are the same query's WHERE clause.** They are two
# statements rather than one windowed statement, because `COUNT(*) OVER ()`
# returns nothing at all on a page past the end -- the totals would read zero
# beside a pager saying 415, and a figure that contradicts the widget next to it
# is worse than a second scan. The caller runs both inside one read transaction
# so they see one snapshot; `api/routes/transactions.py` is where that happens.
#
# What this must never grow: a third definition of income and spending. The
# figures below are measured on the **bank leg** -- how these lines moved this
# account's balance -- and `ledger_totals` is measured on the income and expense
# legs with transfers and the opening entry excluded. They answer different
# questions and they will not match. The field names say `bank_` for that reason
# and STATUS §5.45 is what happens when two cashflow figures are allowed to look
# like the same figure.
# ---------------------------------------------------------------------------


_ISO_DATE = re.compile(r"\A\d{4}-\d{2}-\d{2}\Z")


@dataclass(frozen=True, slots=True)
class DateSpan:
    """A closed range of **transaction dates**. ``None`` on either end is open.

    One column answers this question everywhere in the ledger: ``txn.date``.
    That is a decision with a reason and not the obvious default, so it is
    written down here rather than inferred from the SQL.

    **Why ``txn.date`` and not ``statement_month``.** Every aggregation that
    reports money already has this column in hand: since migration 0008 the
    money queries read ``v_cashflow_line``, which carries ``txn.date`` as a
    column of its own, and ``v_transaction`` joins ``txn`` directly. So a bound
    on it is one more ``AND`` and no new join. Nothing can therefore drop a row
    or duplicate one, and the equalities the charts rest on (the slices summing
    to the Out, the months summing to the four figures) survive a filter by
    construction rather than by argument. Reaching ``statement_month`` instead
    would mean joining ``txn_identity -> raw_record -> source_file`` inside
    ``_TOTALS_SQL``, which silently drops a transaction with no identity row --
    the first of the two shapes ``cashflow_agreement`` exists to catch.

    It is also the only one of the two that can express the range a person
    actually asks for. "The last week" is not a number of statement months.

    **These are two different questions and the product keeps both.** This span
    asks *when did it happen*; the transaction table's month control asks *which
    statement is it printed on*. They disagree for any line near a period
    boundary, because a Chase period does not start on the 1st. The predecessor
    had exactly these two definitions and did not label them -- its chart used
    the transaction month, its table used the statement month, and 83 of its 415
    rows fell in different buckets with nothing on screen saying so. Both are
    kept here and both are labelled; what is not allowed is a screen where one
    silently stands in for the other.

    Bounds are inclusive at both ends, which is what a person means by "from the
    1st to the 31st".
    """

    since: str | None = None
    until: str | None = None

    def __post_init__(self) -> None:
        for name, value in (("since", self.since), ("until", self.until)):
            if value is None:
                continue
            # Both checks are load-bearing and neither is enough alone. The
            # pattern refuses the shapes `date.fromisoformat` has accepted since
            # 3.11 but this column never holds -- ISO week dates and the rest
            # of the extended grammar. `fromisoformat` refuses what the
            # pattern cannot see: that `2025-13-01` is not a day. Left to the
            # pattern alone it would reach SQL, compare as a string against
            # dates that are all smaller, and select nothing at all -- a filter
            # answering "no rows" to a question that was never asked.
            if not _ISO_DATE.match(value):
                raise ValueError(f"{name} must be an ISO date (YYYY-MM-DD), not {value!r}")
            try:
                date.fromisoformat(value)
            except ValueError as bad:
                raise ValueError(f"{name} is not a real date: {value!r}") from bad
        if self.since is not None and self.until is not None and self.since > self.until:
            # ISO dates compare correctly as strings, which is the property the
            # whole schema stores dates as text for.
            raise ValueError(f"since {self.since!r} is after until {self.until!r}")

    @property
    def bounded(self) -> bool:
        """Whether this narrows anything at all."""
        return self.since is not None or self.until is not None

    def clauses(self, column: str) -> tuple[list[str], list[str]]:
        """``(sql fragments, bound values)`` for *column*, both possibly empty.

        The column name is interpolated and the dates are bound. Every caller
        passes a literal from this module; no value a caller typed reaches the
        string.
        """
        parts: list[str] = []
        params: list[str] = []
        if self.since is not None:
            parts.append(f"{column} >= ?")
            params.append(self.since)
        if self.until is not None:
            parts.append(f"{column} <= ?")
            params.append(self.until)
        return parts, params


#: Used where a caller passed nothing, so the SQL below has one shape.
UNBOUNDED = DateSpan()


#: The ``category`` filter value meaning "nothing claimed this line".
#:
#: A sentinel rather than a category id, because there is no ``uncategorized``
#: row to select: an unmatched descriptor is stored as SQL NULL on purpose
#: (STATUS §5.38), and on the 13 real statements that is 285 of 415 lines.
#:
#: **The parentheses are the whole point.** This was spelled ``none``, to match
#: ``v_txn_category.decided_by``'s third value, until verification observed that
#: ``analytics.categorize``'s id pattern — ``\A[a-z][a-z0-9-]*\Z`` — accepts
#: ``none`` as a category id. A hand-edited rules file declaring one would leave
#: this filter silently answering a different question than the one selected,
#: and a filter that quietly changes its meaning is the exact failure this
#: project exists to make impossible rather than to document. Parentheses cannot
#: appear in an id, so the collision is now unrepresentable instead of merely
#: absent from the shipped file — and ``cli.cmd_reapply_rules`` already prints
#: the null bucket as ``(uncategorized)``, so the shape is this codebase's own.
#:
#: STATUS §5.41 is the same lesson from the other side: when a guard's own
#: sentinel looks like the thing another rule can produce, change the sentinel
#: rather than adding an exemption.
NO_CATEGORY = "(none)"

#: One page. 50 is what EXECUTION_PLAN §1.3 measured a rendered table at.
DEFAULT_PAGE_SIZE = 50

#: The most a single request may ask for. Not a performance limit — it is the
#: line past which "a page" has quietly become "the whole ledger in the browser",
#: which is the thing this module exists to prevent.
MAX_PAGE_SIZE = 500

#: Sortable columns, by the name the wire uses. A whitelist and not a parameter
#: because ``ORDER BY`` cannot be bound: the value has to be interpolated, so the
#: only safe form is one that can never carry anything a caller typed.
SORT_KEYS: dict[str, str] = {
    "date": "v.date",
    "amount": "v.amount_minor",
    "description": "v.raw_descriptor",
    "category": "v.category_id",
    "month": "v.statement_month",
}

DEFAULT_SORT_KEY = "date"

#: Appended to every ordering. ``record_index`` is the statement's own row order,
#: which is what a person expects within one day; ``posting_id`` is a content
#: hash and unique, so the total order is fixed by the query rather than by the
#: engine.
#:
#: Said precisely, because the loose version is a claim about behaviour that does
#: not reproduce: SQL leaves the order of tied rows **unspecified**, so a paged
#: query ordered on ``date`` alone is free to show one row twice and never show
#: another. Verification looked for that happening and did not find it — SQLite
#: 3.50.4 returned tied rows in the same order across pages under every plan
#: tried. **What is missing is the guarantee, not the behaviour**, and a paging
#: scheme resting on an engine's incidental stability is one nobody will think to
#: re-check when the plan changes.
_TIEBREAK = "v.record_index, v.posting_id"


@dataclass(frozen=True, slots=True)
class TransactionQuery:
    """One request for a page of transactions: what to match, how to order it.

    Validated in ``__post_init__`` rather than at the SQL boundary, so an
    unsortable column or a page size of ten thousand is a :class:`ValueError`
    from the caller's own line rather than something that reaches a query
    string. ``sort`` and ``direction`` are the two fields that get interpolated
    into SQL; both are checked against a fixed set here.

    ``limit`` and ``offset`` apply to :func:`list_transactions` only.
    :func:`summarize_transactions` deliberately ignores them — it describes
    everything the filter matched, not the slice on screen.
    """

    text: str | None = None
    #: ``statement_month`` — *which statement is this printed on*. Deliberately
    #: not the same question as :attr:`span`, which asks *when did it happen*;
    #: see :class:`DateSpan`. Both are offered and both are labelled.
    month: str | None = None
    #: A category id, or :data:`NO_CATEGORY`. ``None`` means "do not filter".
    category: str | None = None
    transfer: bool | None = None
    #: ``"in"`` (amount > 0), ``"out"`` (amount < 0), or ``None``.
    direction: str | None = None
    #: The page-wide date range, on transaction dates. Unbounded by default so
    #: every caller predating the control keeps its behaviour exactly.
    span: DateSpan = UNBOUNDED
    sort: str = DEFAULT_SORT_KEY
    descending: bool = True
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0

    def __post_init__(self) -> None:
        if self.sort not in SORT_KEYS:
            raise ValueError(f"cannot sort by {self.sort!r}; known: {sorted(SORT_KEYS)}")
        if self.direction not in (None, "in", "out"):
            raise ValueError(f"direction must be 'in', 'out' or None, not {self.direction!r}")
        if not 1 <= self.limit <= MAX_PAGE_SIZE:
            raise ValueError(f"limit must be 1..{MAX_PAGE_SIZE}, not {self.limit}")
        if self.offset < 0:
            raise ValueError(f"offset must not be negative, not {self.offset}")


def _like_argument(text: str) -> str:
    """Wrap *text* for a ``LIKE ... ESCAPE '\\'`` containment match.

    The three characters SQLite's ``LIKE`` treats specially are escaped, so a
    descriptor search for ``100%`` finds the literal string rather than every
    row. The backslash goes first: escaping it after the others would escape the
    escapes.
    """
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _transaction_where(query: TransactionQuery) -> tuple[str, list[Any]]:
    """The one WHERE clause both readers below use. Every value is bound.

    Returned rather than inlined so that the rows and the figures describing
    them cannot come from two different filters — which is the specific way a
    table and its own total come to disagree.
    """
    clauses: list[str] = []
    params: list[Any] = []

    if query.text:
        # raw_descriptor only. It is the bank's verbatim line and the string the
        # page shows; `narration` is a copy of it for every row a statement
        # produced, and `payee` is NULL in P0 because cutting one out of the
        # descriptor would be a heuristic rendered as a fact. Searching three
        # columns to search one string would only make the match harder to
        # explain. Case-insensitive for ASCII, which is SQLite's LIKE.
        clauses.append("v.raw_descriptor LIKE ? ESCAPE '\\'")
        params.append(_like_argument(query.text))

    if query.month:
        clauses.append("v.statement_month = ?")
        params.append(query.month)

    if query.category is not None:
        if query.category == NO_CATEGORY:
            clauses.append("v.category_id IS NULL")
        else:
            clauses.append("v.category_id = ?")
            params.append(query.category)

    if query.transfer is not None:
        clauses.append("v.is_transfer = ?")
        params.append(1 if query.transfer else 0)

    if query.direction == "in":
        clauses.append("v.amount_minor > 0")
    elif query.direction == "out":
        clauses.append("v.amount_minor < 0")

    # The page-wide range, on the transaction date — the same column and the
    # same bounds the two charts use, so the table and the charts above it are
    # always showing the same window of the ledger. `month` above is a different
    # question and can be combined with this one.
    span_parts, span_params = query.span.clauses("v.date")
    clauses.extend(span_parts)
    params.extend(span_params)

    return ("WHERE " + " AND ".join(clauses)) if clauses else "", params


_TRANSACTION_SELECT = """
SELECT
  v.txn_id,
  v.posting_id,
  v.date,
  v.statement_month,
  v.amount_minor,
  v.currency,
  v.raw_descriptor,
  v.occurrence_index,
  v.category_id,
  v.category_decided_by,
  v.is_transfer,
  v.transfer_decided_by,
  v.source_file_id,
  v.record_index
FROM v_transaction v
"""

_TRANSACTION_SUMMARY = """
SELECT
  COUNT(*) AS matched,
  COALESCE(SUM(CASE WHEN v.amount_minor > 0 THEN v.amount_minor ELSE 0 END), 0)
    AS bank_in_minor,
  COALESCE(SUM(CASE WHEN v.amount_minor < 0 THEN v.amount_minor ELSE 0 END), 0)
    AS bank_out_minor,
  COALESCE(SUM(v.amount_minor), 0) AS bank_net_minor
FROM v_transaction v
"""


def list_transactions(conn: sqlite3.Connection, query: TransactionQuery) -> list[sqlite3.Row]:
    """One page of statement lines, filtered and ordered by the database.

    ``category_id`` and ``is_transfer`` are the **effective** values — a
    person's override folded over what the rules derived — because
    ``v_transaction`` is built on ``v_txn_category`` and ``v_txn_transfer``. The
    two ``*_decided_by`` columns say which source answered, which is the only
    way to tell "no rule claimed this" from "somebody chose this", and those two
    must never render alike.

    The ordering always ends in a unique column — see :data:`_TIEBREAK`, which
    also records that the failure it prevents is one SQL permits rather than one
    this engine was caught doing.
    """
    where, params = _transaction_where(query)
    column = SORT_KEYS[query.sort]
    heading = "DESC" if query.descending else "ASC"
    # Interpolated: `column` came from SORT_KEYS and `heading` from a boolean.
    # Nothing a caller typed reaches this string — TransactionQuery refuses an
    # unknown sort key before it can get here.
    sql = f"{_TRANSACTION_SELECT}{where} ORDER BY {column} {heading}, {_TIEBREAK} LIMIT ? OFFSET ?"
    rows: list[sqlite3.Row] = conn.execute(sql, (*params, query.limit, query.offset)).fetchall()
    return rows


def summarize_transactions(conn: sqlite3.Connection, query: TransactionQuery) -> dict[str, int]:
    """How many lines the filter matched, and what they did to this balance.

    **These are not income and spending.** They are the bank leg: the sum of the
    matched lines as they moved this account, transfers and all. ``ledger_totals``
    measures the income and expense legs, excludes anything flagged as a transfer
    and excludes the opening entry. The ``bank_`` prefix is there so nobody has
    to remember that, and STATUS §5.45 records what a second figure that merely
    *looks* like the first costs: a block-level check now exists solely to keep
    two cashflow queries honest.

    **What the two do to each other is a fact about row sets, not about
    filtering.** They are equal while this query selects the lines
    ``ledger_totals`` counts — which is the out-of-the-box state, since nothing
    on the author's own corpus is flagged a transfer. This paragraph said "the
    two will differ and are meant to"; the first acceptance round measured them
    identical, and the second refuted the replacement as well, because
    ``transfer=false`` with nothing flagged, or a search for a single space,
    selects every row and leaves them equal. Whether a filter was applied has
    never been the question.

    ``limit`` and ``offset`` are ignored on purpose. A total that described only
    the rows currently on screen would change when somebody turned the page,
    while sitting under a heading that says how many matched.

    Count and sums come from one statement so they cannot disagree about which
    rows they described. Whether they agree with :func:`list_transactions` is a
    property of the caller reading both inside one transaction — see this
    section's header.
    """
    where, params = _transaction_where(query)
    row = conn.execute(f"{_TRANSACTION_SUMMARY}{where}", params).fetchone()
    return {
        "matched": int(row["matched"]),
        "bank_in_minor": int(row["bank_in_minor"]),
        "bank_out_minor": int(row["bank_out_minor"]),
        "bank_net_minor": int(row["bank_net_minor"]),
    }


def get_transaction(conn: sqlite3.Connection, txn_id: str) -> sqlite3.Row | None:
    """One statement line by transaction id, or None if the ledger has no such row.

    Reads ``v_transaction``, so "no such row" also covers a ``txn`` that exists
    but is not a statement line: the opening entry has no identity row and is
    therefore not a transaction anything here can show or a person can
    recategorise. Answering 404 for it is correct rather than incidental.

    Ordered by the same tiebreak :func:`list_transactions` ends with, and
    limited to one. A transaction with two identity rows renders twice in the
    list today only in theory — nothing pairs both sides of a transfer — and
    this returns the same one of the two that the list puts first, rather than
    whichever the engine happens to reach.
    """
    row: sqlite3.Row | None = conn.execute(
        f"{_TRANSACTION_SELECT}WHERE v.txn_id = ? ORDER BY {_TIEBREAK} LIMIT 1", (txn_id,)
    ).fetchone()
    return row


def category_exists(conn: sqlite3.Connection, category_id: str) -> bool:
    """Whether the ledger mirrors this category.

    Asked before writing an override so that an unknown id is a refusal naming
    the category, rather than an ``IntegrityError`` from a foreign key that does
    not say which of its two references failed —
    :func:`set_category_override` makes the same distinction for the other one.
    """
    found = conn.execute("SELECT 1 FROM category WHERE id = ?", (category_id,)).fetchone()
    return found is not None


def get_category(conn: sqlite3.Connection, category_id: str) -> sqlite3.Row | None:
    """One stored taxonomy row, or ``None`` for an unknown category id."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, parent_id, kind FROM category WHERE id = ?", (category_id,)
    ).fetchone()
    return row


def list_categories(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every category the ledger knows, for a person to choose from.

    The ``category`` table is the rules file's mirror (STATUS §5.37), so this is
    the shipped taxonomy rather than a second list maintained for the UI — the
    frontend hardcoding eighteen ids would be the two-definitions shape §5.29
    exists to name.

    Nothing here is filtered by the sign of any transaction. ``classify()``
    refuses to claim a deposit with an expense rule because a *derivation* has
    no business guessing, but an override is a person overruling the derivation:
    a refunded restaurant charge really does arrive as a deposit and really is
    dining. Categories take part in no aggregate — only a category whose kind is
    ``transfer`` changes a number, and it does so through ``v_txn_transfer`` —
    so the cost of an odd choice is a display, and the cost of forbidding it is
    a tool overruling the person it exists to serve.
    """
    rows: list[sqlite3.Row] = conn.execute(
        "SELECT c.id, c.kind, c.parent_id FROM category c ORDER BY c.kind, c.id"
    ).fetchall()
    return rows


# ---------------------------------------------------------------------------
# --- what the two charts read (P2 M5) ---
#
# Both readers below return their own total alongside their own rows, summed
# from the same fetch. That is the shape `ledger_totals` uses for
# `transfer_excluded_*` (§5.50) and the shape `_transaction_where` enforces for
# the table and its summary (§5.70), for one reason: a chart's slices and the
# figure printed beside them must not be two chances to answer the same
# question. A caller that re-summed the rows itself, or asked the database a
# second time, is a caller that can draw a pie whose wedges do not add up to the
# number under it.
#
# What they measure is *not* the same quantity, and neither is what the
# transaction table measures. What reaches the page, after M6 moved the bars:
#
#   ledger_totals        income and expense legs, transfers and opening entry
#                        excluded -- the four at the top
#   monthly_cashflow     the same legs and the same exclusions, grouped by the
#                        month of the transaction date -- the bars. So the bars
#                        are the four figures decomposed, and sum back to them
#                        under any window by construction
#   category_spend       the expense legs of the same rows, grouped by category
#                        -- the donut. A breakdown of the Out, for the same
#                        reason and by the same construction
#   summarize_transactions   the bank leg of whatever the filter matched,
#                        transfers included -- the table's three figures (§5.69)
#
# `v_cashflow_monthly` is deliberately **not** in that list any more. M6 changed
# the bars from the own-account leg bucketed by statement month to the income and
# expense legs bucketed by transaction date, and the old view stopped reaching
# the page at that moment; it stayed because `verify` compares it against
# `ledger_totals` by a path that shares no SQL with either, which is worth more
# as a check than as a chart source. This list said otherwise for the whole of
# M6, and named the view as what the bars read.
#
# STATUS §5.45 is what it cost, once, to let two cashflow figures merely look
# alike.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CategorySlice:
    """One category's share of what was spent. ``category_id`` may be ``None``.

    ``None`` means no rule claimed those lines and nobody has overruled that.
    It is a slice like any other and callers must render it as one — with area,
    and never under a name like "other". A bucket that collects the leftovers
    is indistinguishable in a chart from one that was matched on purpose, and
    that indistinguishability is what made the predecessor's breakdown look
    complete while it was wrong (§5.38).
    """

    category_id: str | None
    #: Negative, in the same sign convention as ``outflow_minor``.
    spend_minor: int
    txn_count: int


@dataclass(frozen=True, slots=True)
class CategoryBreakdown:
    """Every slice, and what they add up to.

    ``total_minor`` is the sum of :attr:`slices` and is not asked of the
    database separately, so no caller can print a total its own wedges
    contradict.

    It is also equal to ``ledger_totals()['outflow_minor']`` — the Out already
    printed at the top of the page. That is what migration 0007 was shaped
    around and it is what makes this a *breakdown* rather than a fourth
    measurement; ``verify``'s ``cashflow_agreement`` check asserts it on the
    operator's own ledger, for this query as well as for ``v_category_spend``.
    That "as well as" is the whole of a defect: only the view was checked until
    an acceptance round edited **this** text and found the chart summing to a
    twelfth of the figure above it with all nine checks passing.
    ``tests/test_pipeline.py`` carries a negative case for each.
    """

    slices: tuple[CategorySlice, ...]
    total_minor: int
    txn_count: int


#: Largest spend first. ``spend_minor`` is negative, so ascending is descending
#: by magnitude. Ordering ends in ``category_id`` because SQL leaves tied rows
#: unspecified and a chart's legend that reshuffles between two loads of the
#: same data is one nobody can read twice (§5.71 — the guarantee is what is
#: missing there, not the observed behaviour).
_CATEGORY_SPEND_SQL = """
SELECT
  l.category_id                 AS category_id,
  -SUM(l.amount_minor)          AS spend_minor,
  COUNT(DISTINCT l.txn_id)      AS txn_count
FROM v_cashflow_line l
WHERE l.is_transfer = 0 AND l.account_kind = 'expense'
"""

_CATEGORY_SPEND_TAIL = """
GROUP BY l.category_id
ORDER BY spend_minor, category_id
"""


def category_spend(
    conn: sqlite3.Connection, span: DateSpan | None = None
) -> CategoryBreakdown:
    """What each category cost, largest first, with the unclaimed lines included.

    Reads ``v_cashflow_line`` (migration 0008), the row set every money figure
    on the page is a sum of. ``v_category_spend`` is the same projection, held
    in SQL and read unscoped by ``verify``, which is what makes it a check on
    this function rather than a duplicate of it.

    Nothing is filtered, dropped or renamed here: a slice whose
    ``category_id`` is ``None`` arrives as ``None``, because substituting a
    placeholder at this layer would make "no rule claimed this" and "somebody
    chose this" arrive looking identical — the same substitution
    :func:`~ledgerbox.api.routes.transactions._transaction_out` refuses to make
    for a single row.

    *span* narrows to a window of transaction dates. The equality with
    ``ledger_totals``' ``outflow_minor`` holds **for any window**, because both
    are sums of ``v_cashflow_line`` under the same bound -- not because two
    queries were written to agree.
    """
    sql, params = _narrowed(_CATEGORY_SPEND_SQL, span, column="l.date", connective="AND")
    rows = conn.execute(sql + _CATEGORY_SPEND_TAIL, params).fetchall()
    slices = tuple(
        CategorySlice(
            category_id=row["category_id"],
            spend_minor=int(row["spend_minor"]),
            txn_count=int(row["txn_count"]),
        )
        for row in rows
    )
    return CategoryBreakdown(
        slices=slices,
        total_minor=sum(part.spend_minor for part in slices),
        txn_count=sum(part.txn_count for part in slices),
    )


@dataclass(frozen=True, slots=True)
class CashflowMonth:
    """One month of the bars. ``outflow_minor`` is negative.

    ``month`` is the month of the **transaction date**, and the field is named
    ``month`` rather than ``statement_month`` because it is not one. P2 M6
    changed this deliberately, and the field name had to change with it: a
    reader who kept the old name would be reading a different question's answer
    under the old question's label.

    The two differ for any line near a period boundary, because a Chase period
    does not begin on the 1st. Which is right depends on the question:

    * *when did this happen* — the transaction date. That is the axis a person
      reads, and it is the only one that can answer "the last week";
    * *which statement is this printed on* — ``statement_month``, still what
      ``v_statement``, the statement list and the transaction table's month
      control mean, and still derived from the period's **end** day, because
      taking the start day is what made three months vanish from the
      predecessor's output.

    Both survive in the product; neither stands in silently for the other. The
    predecessor had both and labelled neither, and 83 of its 415 rows fell in
    different buckets depending on which chart was asking.

    Never ``None``: ``txn.date`` is NOT NULL, so every line has a month here.
    The previous version of this field could be null, because it reached the
    month through the statement and a line can lack one.
    """

    month: str
    inflow_minor: int
    outflow_minor: int
    net_minor: int
    txn_count: int


@dataclass(frozen=True, slots=True)
class MonthlyCashflow:
    """The months in order, and their sums.

    The four aggregate fields are summed from the same rows, so the bars and
    the figure under them cannot describe different sets of months.
    """

    months: tuple[CashflowMonth, ...]
    inflow_minor: int
    outflow_minor: int
    net_minor: int
    txn_count: int


#: The same ``CASE`` arithmetic :data:`_TOTALS_SQL` does, over the same rows,
#: grouped by month instead of collapsed. That is the whole design: the bars are
#: the four figures decomposed by month exactly as the pie is the Out decomposed
#: by category, so both sum back to what is printed above them under **any**
#: date bound, by construction rather than by agreement.
_CASHFLOW_MONTHS_SQL = """
SELECT
  substr(l.date, 1, 7)          AS month,
  COALESCE(-SUM(CASE WHEN l.account_kind = 'income'
                     THEN l.amount_minor ELSE 0 END), 0) AS inflow_minor,
  COALESCE(-SUM(CASE WHEN l.account_kind = 'expense'
                     THEN l.amount_minor ELSE 0 END), 0) AS outflow_minor,
  COUNT(DISTINCT l.txn_id)                               AS txn_count
FROM v_cashflow_line l
WHERE l.is_transfer = 0
"""

#: Oldest first, which is the direction a time axis reads.
_CASHFLOW_MONTHS_TAIL = """
GROUP BY month
ORDER BY month
"""


def monthly_cashflow(
    conn: sqlite3.Connection, span: DateSpan | None = None
) -> MonthlyCashflow:
    """In and out per **transaction** month, oldest first.

    Not ``v_cashflow_monthly``, which buckets by statement month and sums the
    bank leg. This reads ``v_cashflow_line`` and sums the income and expense
    legs, which is what :func:`ledger_totals` measures — so the bars add up to
    the four figures above them for any window, rather than merely agreeing with
    them when nothing is filtered.

    That is a change of meaning made on purpose in P2 M6, and it is what let the
    date range apply to the whole page at once. ``v_cashflow_monthly`` keeps its
    job: ``verify``'s ``cashflow_agreement`` still compares it against
    ``ledger_totals`` by an independent path, which is worth more as a check
    than as a chart source.

    See :class:`CashflowMonth` for why the field is ``month`` and not
    ``statement_month``, and why both concepts still exist.
    """
    sql, params = _narrowed(_CASHFLOW_MONTHS_SQL, span, column="l.date", connective="AND")
    rows = conn.execute(sql + _CASHFLOW_MONTHS_TAIL, params).fetchall()
    months = tuple(
        CashflowMonth(
            month=str(row["month"]),
            inflow_minor=int(row["inflow_minor"]),
            outflow_minor=int(row["outflow_minor"]),
            net_minor=int(row["inflow_minor"]) + int(row["outflow_minor"]),
            txn_count=int(row["txn_count"]),
        )
        for row in rows
    )
    return MonthlyCashflow(
        months=months,
        inflow_minor=sum(month.inflow_minor for month in months),
        outflow_minor=sum(month.outflow_minor for month in months),
        net_minor=sum(month.net_minor for month in months),
        txn_count=sum(month.txn_count for month in months),
    )


# ---------------------------------------------------------------------------
# --- removing a statement (P2 M3) ---
#
# The inverse of the ingest at the top of this module, and it is held to one
# standard:
#
#     what is left must equal what re-ingesting the *remaining* archive into an
#     empty database would produce -- over the eight statement-derived tables.
#
# The qualification is not a detail. `account`, `category` and `commodity` are
# reference rows created at ingest and idempotent (§5.37), so forgetting the last
# statement leaves them standing while a rebuild from an emptied archive creates
# neither; the unqualified sentence is false. `tests/test_rebuild.py` names both
# sets (`STATEMENT_DERIVED`, `REFERENCE_TABLES`) so the exclusion is a property
# under test rather than a paragraph.
#
# That is the rebuild invariant (`docs/ARCHITECTURE.md`, `tests/test_rebuild.py`)
# applied to a smaller archive, and it is the reason the three awkward questions
# about deletion have answers instead of opinions:
#
#   * a middle month leaves the later printed balances irreproducible -- correct,
#     because a rebuild from the remaining archive is irreproducible in exactly
#     the same places. The ledger really does have a hole. What must not happen
#     is the operator finding out afterwards, which is why the plan is measured
#     before anything is written rather than described;
#   * an assertion on a day two statements share must survive if the other one is
#     still here, because ingesting that other statement alone still produces it;
#   * the opening entry moves, because it is derived from the earliest surviving
#     assertion and nothing else.
#
# Three things do *not* come back, and they are why a deletion has to be confirmed
# rather than merely offered. All are decisions or review history, and `archive/`
# holds documents rather than decisions:
#
#   * `category_override` -- the category somebody set by hand (§5.49);
#   * `agent_category_proposal` -- what an external Agent suggested and what the
#     person did with it; neither the PDF nor a deterministic rebuild knows it;
#   * a `review_item` somebody resolved or dismissed. Re-ingesting the same bytes
#     regenerates the queue entry as `open`, never as the answer they gave it,
#     because `replace_review_items` deliberately refuses to overwrite a decided
#     item -- the protection is what makes the decision unreproducible once the
#     row is gone.
#
# This comment said `category_override` was *the only* one for a milestone. An
# acceptance run dismissed an item through the product's own API, deleted the
# statement, re-ingested the identical bytes and watched the dismissal come back
# as `open`. §9 rule 11: the sentence was true of the table its author had in
# mind.
# ---------------------------------------------------------------------------

#: How much of a sha-256 has to be typed. Long enough that hitting a statement
#: you did not mean requires getting eight hex characters of it right; short
#: enough to copy off a screen. An ambiguous prefix is refused, never resolved.
STATEMENT_ID_MIN_PREFIX = 8

_HEX_PREFIX = re.compile(r"[0-9a-f]+\Z")


class StatementNotFound(LookupError):
    """No archived statement matches what was asked for."""


class AmbiguousStatement(LookupError):
    """A prefix matches more than one statement. Carries the candidates."""

    def __init__(self, needle: str, candidates: Sequence[str]) -> None:
        self.needle = needle
        self.candidates = tuple(candidates)
        listed = "\n  ".join(self.candidates)
        super().__init__(
            f"{needle!r} matches {len(self.candidates)} statements:\n  {listed}\n"
            f"Give more of the id."
        )


def _chunks(values: Sequence[str], size: int = 500) -> Iterator[Sequence[str]]:
    """Split a list of ids so an ``IN (...)`` never outgrows the parameter limit."""
    for start in range(0, len(values), size):
        yield values[start : start + size]


def find_statement(conn: sqlite3.Connection, needle: str) -> sqlite3.Row:
    """One archived statement, by full id or by an unambiguous sha-256 prefix.

    Raises :class:`StatementNotFound` or :class:`AmbiguousStatement` rather than
    returning None, because every caller of this is about to delete something:
    "no match" and "several matches" are different mistakes and the operator has
    to be told which one they made. Nothing here guesses at a near match.
    """
    value = needle.strip().lower()
    if len(value) < STATEMENT_ID_MIN_PREFIX or _HEX_PREFIX.match(value) is None:
        raise StatementNotFound(
            f"{needle!r} is not a statement id: give the full sha-256 or at least "
            f"{STATEMENT_ID_MIN_PREFIX} of its leading hex characters"
        )

    rows = conn.execute(
        "SELECT * FROM v_statement WHERE source_file_id LIKE ? || '%' "
        "ORDER BY source_file_id",
        (value,),
    ).fetchall()
    if not rows:
        raise StatementNotFound(f"no archived statement whose id starts with {value!r}")
    if len(rows) > 1:
        raise AmbiguousStatement(value, [str(row["source_file_id"]) for row in rows])
    row: sqlite3.Row = rows[0]
    return row


@dataclass(frozen=True, slots=True)
class DeletionFacts:
    """What is attached to one statement right now, counted before anything moves.

    Every number here is a row count from this database, not an estimate. They
    exist so that the sentence shown to a person before they confirm is made of
    measurements.
    """

    source_file_id: str
    statement_month: str | None
    period_start: str | None
    period_end: str | None
    txns: int
    postings: int
    identities: int
    raw_records: int
    review_items: int
    #: Of those, the ones a person has already resolved or dismissed. **Also not
    #: recoverable.** Re-ingesting the same bytes regenerates the queue entry as
    #: ``open``: ``replace_review_items`` protects a decided item from being
    #: resurrected precisely because a queue that undoes your decisions is a
    #: queue you stop reading, and that protection is exactly what makes the
    #: decision unreproducible once the row is gone.
    review_items_decided: int
    #: Assertions this file owns. Some of them another statement also prints.
    balance_assertions: int
    #: Of those, the ones a surviving statement also asserts — they stay, with
    #: their provenance moved to that statement. See :func:`delete_statement`.
    balance_assertions_shared: int
    #: **Not recoverable by any rebuild** (``docs/STATUS.md`` §5.49). Called "the
    #: only" such rows for one milestone; it was never the only one — see
    #: :attr:`review_items_decided`.
    category_overrides: int
    #: Proposal outcome rows attached to these transactions. Local audit data,
    #: not reproducible from statement bytes.
    agent_proposals: int
    #: Run metadata rows whose every proposal belongs to this statement and
    #: will therefore become empty and be removed too.
    agent_proposal_runs: int
    #: Remaining-coverage triage audit rows attached to these transactions.
    agent_triage_items: int
    #: Triage run rows whose every item belongs to this statement.
    agent_triage_runs: int
    #: Transactions *outside* this file that are marked as superseded by one
    #: inside it. Unreachable today (nothing writes ``superseded_by``), and
    #: reported rather than absorbed because the alternative is an
    #: ``IntegrityError`` from a foreign key with no explanation attached.
    superseded_by_this: tuple[str, ...]


_DELETED_TXN_IDS_SQL = """
SELECT DISTINCT ti.txn_id AS txn_id
FROM txn_identity ti
JOIN raw_record rr ON rr.id = ti.raw_record_id
WHERE rr.source_file_id = ?
ORDER BY ti.txn_id
"""

_OWNED_ASSERTIONS_SQL = """
SELECT id AS assertion_id, as_of
FROM balance_assertion
WHERE source_file_id = ?
ORDER BY as_of, id
"""

#: The surviving statement that would still be printing this balance on this
#: day, or no row when nobody else prints it.
#:
#: The heir is chosen by the rule that assigned ownership in the first place
#: (``docs/STATUS.md`` §5.7): the statement that *closes* on that day owns it,
#: and only failing that the one whose period opens the day after. Both arms
#: matter — deleting the statement that closed on a shared seam leaves the next
#: statement's opening figure as the surviving evidence, and it is evidence: the
#: two were compared when the second was ingested, and disagreement raised
#: ``BalanceAssertionConflict`` instead of being written.
#:
#: ``as_of`` is bound rather than correlated from an enclosing query. SQLite
#: resolves an outer column in a subquery's ``WHERE`` but **not** in its
#: ``ORDER BY``, and the ordering is where the §5.7 tie-break lives.
_ASSERTION_HEIR_SQL = """
SELECT sf.id AS heir FROM source_file sf
WHERE sf.id <> :file
  AND (sf.period_end = :as_of OR date(sf.period_start, '-1 day') = :as_of)
ORDER BY CASE WHEN sf.period_end = :as_of THEN 0 ELSE 1 END, sf.id
LIMIT 1
"""


def _assertion_heirs(
    conn: sqlite3.Connection, source_file_id: str
) -> list[tuple[str, str | None]]:
    """``[(assertion id, heir statement id or None), …]`` for one file's assertions.

    One implementation, used by both the count and the delete, so that what the
    operator is told will happen and what happens are the same decision.
    """
    heirs: list[tuple[str, str | None]] = []
    for row in conn.execute(_OWNED_ASSERTIONS_SQL, (source_file_id,)).fetchall():
        found = conn.execute(
            _ASSERTION_HEIR_SQL, {"file": source_file_id, "as_of": row["as_of"]}
        ).fetchone()
        heirs.append((str(row["assertion_id"]), None if found is None else str(found["heir"])))
    return heirs


def _proposal_history_impact(
    conn: sqlite3.Connection, txn_ids: Sequence[str]
) -> tuple[int, tuple[str, ...]]:
    """Proposal rows and runs that become empty when *txn_ids* disappear."""
    affected: dict[str, int] = {}
    proposals = 0
    for chunk in _chunks(txn_ids):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT run_id, COUNT(*) AS n FROM agent_category_proposal "
            f"WHERE txn_id IN ({marks}) GROUP BY run_id",
            chunk,
        ).fetchall():
            run_id = str(row["run_id"])
            count = int(row["n"])
            proposals += count
            affected[run_id] = affected.get(run_id, 0) + count

    emptied = tuple(
        sorted(
            run_id
            for run_id, removed in affected.items()
            if removed
            == int(
                conn.execute(
                    "SELECT COUNT(*) FROM agent_category_proposal WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
        )
    )
    return proposals, emptied


def _triage_history_impact(
    conn: sqlite3.Connection, txn_ids: Sequence[str]
) -> tuple[int, tuple[str, ...]]:
    """Triage items and runs that become empty when *txn_ids* disappear."""
    affected: dict[str, int] = {}
    items = 0
    for chunk in _chunks(txn_ids):
        marks = ",".join("?" * len(chunk))
        for row in conn.execute(
            f"SELECT run_id, COUNT(*) AS n FROM agent_triage_item "
            f"WHERE txn_id IN ({marks}) GROUP BY run_id",
            chunk,
        ).fetchall():
            run_id = str(row["run_id"])
            count = int(row["n"])
            items += count
            affected[run_id] = affected.get(run_id, 0) + count

    emptied = tuple(
        sorted(
            run_id
            for run_id, removed in affected.items()
            if removed
            == int(
                conn.execute(
                    "SELECT COUNT(*) FROM agent_triage_item WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
        )
    )
    return items, emptied

_OVERLAPPING_SQL = """
SELECT s.* FROM v_statement s
WHERE s.source_file_id <> :file
  AND s.period_start IS NOT NULL AND s.period_end IS NOT NULL
  AND s.period_start <= :period_end
  AND s.period_end   >= :period_start
ORDER BY s.period_end, s.source_file_id
"""


def overlapping_statements(conn: sqlite3.Connection, source_file_id: str) -> list[sqlite3.Row]:
    """Other statements whose period overlaps this one's. Empty is the normal case.

    Why this is asked at all: :func:`insert_entries` is check-then-insert on the
    natural key, so when two statements report the same transaction only the one
    ingested first gets the ``txn_identity`` row. Deleting *that* one removes a
    transaction the other statement also evidences, and nothing notices —
    ``unbooked_statements`` still sees the survivor's other identity rows and
    reports it as booked.

    A statement with no period (refused before one could be read) can neither
    overlap nor be overlapped: it has no transactions for anyone to share.
    """
    row = conn.execute(
        "SELECT period_start, period_end FROM source_file WHERE id = ?", (source_file_id,)
    ).fetchone()
    if row is None or row["period_start"] is None or row["period_end"] is None:
        return []
    rows: list[sqlite3.Row] = conn.execute(
        _OVERLAPPING_SQL,
        {
            "file": source_file_id,
            "period_start": row["period_start"],
            "period_end": row["period_end"],
        },
    ).fetchall()
    return rows


def statement_deletion_facts(conn: sqlite3.Connection, source_file_id: str) -> DeletionFacts:
    """Count everything one statement is holding onto. Reads only."""
    statement = conn.execute(
        "SELECT * FROM v_statement WHERE source_file_id = ?", (source_file_id,)
    ).fetchone()
    if statement is None:
        raise StatementNotFound(f"no archived statement with id {source_file_id!r}")

    txn_ids = [
        str(row["txn_id"]) for row in conn.execute(_DELETED_TXN_IDS_SQL, (source_file_id,))
    ]

    postings = identities = overrides = 0
    superseded: list[str] = []
    for chunk in _chunks(txn_ids):
        marks = ",".join("?" * len(chunk))
        postings += int(
            conn.execute(
                f"SELECT COUNT(*) FROM posting WHERE txn_id IN ({marks})", chunk
            ).fetchone()[0]
        )
        identities += int(
            conn.execute(
                f"SELECT COUNT(*) FROM txn_identity WHERE txn_id IN ({marks})", chunk
            ).fetchone()[0]
        )
        overrides += int(
            conn.execute(
                f"SELECT COUNT(*) FROM category_override WHERE txn_id IN ({marks})",
                chunk,
            ).fetchone()[0]
        )
        superseded.extend(
            str(row["id"])
            for row in conn.execute(
                f"SELECT id FROM txn WHERE superseded_by IN ({marks})", chunk
            )
        )

    heirs = _assertion_heirs(conn, source_file_id)
    proposal_rows, proposal_runs = _proposal_history_impact(conn, txn_ids)
    triage_items, triage_runs = _triage_history_impact(conn, txn_ids)

    return DeletionFacts(
        source_file_id=source_file_id,
        statement_month=statement["statement_month"],
        period_start=statement["period_start"],
        period_end=statement["period_end"],
        txns=len(txn_ids),
        postings=postings,
        identities=identities,
        raw_records=int(
            conn.execute(
                "SELECT COUNT(*) FROM raw_record WHERE source_file_id = ?", (source_file_id,)
            ).fetchone()[0]
        ),
        review_items=int(
            conn.execute(
                "SELECT COUNT(*) FROM review_item WHERE source_file_id = ?", (source_file_id,)
            ).fetchone()[0]
        ),
        review_items_decided=int(
            conn.execute(
                "SELECT COUNT(*) FROM review_item WHERE source_file_id = ? AND status <> 'open'",
                (source_file_id,),
            ).fetchone()[0]
        ),
        balance_assertions=len(heirs),
        balance_assertions_shared=sum(1 for _, heir in heirs if heir is not None),
        category_overrides=overrides,
        agent_proposals=proposal_rows,
        agent_proposal_runs=len(proposal_runs),
        agent_triage_items=triage_items,
        agent_triage_runs=len(triage_runs),
        superseded_by_this=tuple(sorted(set(superseded) - set(txn_ids))),
    )


@dataclass(frozen=True, slots=True)
class DeletionCounts:
    """What one call to :func:`delete_statement` actually changed."""

    txns: int
    postings: int
    identities: int
    raw_records: int
    review_items: int
    #: Of those, ones a person had already resolved or dismissed. Counted before
    #: the delete, and reported separately for the same reason as
    #: ``category_overrides``: a rebuild brings the queue entry back as ``open``,
    #: never as the answer somebody gave it.
    review_items_decided: int
    category_overrides: int
    agent_proposals: int
    agent_proposal_runs: int
    agent_triage_items: int
    agent_triage_runs: int
    balance_assertions_removed: int
    #: Kept, with provenance moved to a statement that still prints the balance.
    balance_assertions_reassigned: int
    #: One opening transaction id per own account that still has an assertion to
    #: derive one from, after the recomputation. Empty when none of them does —
    #: which is what forgetting the last statement leaves behind, and the state
    #: :func:`sync_opening_entry` used to return from before clearing the entry.
    opening_txn_ids: tuple[str, ...] = ()


def delete_statement(conn: sqlite3.Connection, source_file_id: str) -> DeletionCounts:
    """Remove one statement and everything the ledger derived from it.

    Touches the database only. The archived PDF and the extraction cache are
    removed by :func:`ledgerbox.ingest.forget.forget_statement` **after** this
    transaction commits, and the order is deliberate: a crash between the two
    then leaves bytes on disk with no row, which ``archived_not_recorded``
    reports and which re-ingesting that very file repairs. Doing it the other way
    round leaves a row whose bytes are gone — ``recorded_not_archived``, whose
    documented repair is to re-ingest the original, which would be the file just
    deleted.

    Deletion order follows the foreign keys, and the balance assertions are the
    only interesting part. An assertion this file owns is *not* unconditionally
    removed: on a day two statements share, the survivor still prints that
    balance, and ingesting the survivor alone would produce the row. So the row
    stays and its provenance moves — see :data:`_ASSERTION_HEIR_SQL`.

    Afterwards every own account's opening entry is re-derived, because it comes
    from the earliest surviving assertion and that may now be a different day, or
    no day at all.

    Must run inside the caller's :func:`~ledgerbox.db.connection.transaction`.
    """
    txn_ids = [
        str(row["txn_id"]) for row in conn.execute(_DELETED_TXN_IDS_SQL, (source_file_id,))
    ]

    proposal_rows, proposal_runs = _proposal_history_impact(conn, txn_ids)
    triage_items, triage_runs = _triage_history_impact(conn, txn_ids)
    overrides = postings = identities = txns = 0
    for chunk in _chunks(txn_ids):
        marks = ",".join("?" * len(chunk))
        # Ordered by the foreign keys: audit rows, category_override and posting
        # reference txn; txn_identity references txn and raw_record, so the
        # parents go last.
        conn.execute(
            f"DELETE FROM agent_triage_item WHERE txn_id IN ({marks})", chunk
        )
        conn.execute(
            f"DELETE FROM agent_category_proposal WHERE txn_id IN ({marks})", chunk
        )
        # A rule learned from a doomed transaction carries its descriptor
        # template, so erasing the transaction erases the rule -- and every
        # answer the rule derived, wherever it lives, reverts to undecided
        # rather than surviving as an orphan of deleted evidence.
        doomed_rules = [
            str(row["id"])
            for row in conn.execute(
                f"SELECT id FROM learned_rule WHERE learned_from_txn_id IN ({marks})",
                chunk,
            )
        ]
        if doomed_rules:
            rule_marks = ",".join("?" * len(doomed_rules))
            overrides += conn.execute(
                f"DELETE FROM category_override WHERE learned_rule_id IN ({rule_marks})",
                doomed_rules,
            ).rowcount
            conn.execute(
                f"DELETE FROM learned_rule WHERE id IN ({rule_marks})", doomed_rules
            )
        overrides += conn.execute(
            f"DELETE FROM category_override WHERE txn_id IN ({marks})", chunk
        ).rowcount
        postings += conn.execute(
            f"DELETE FROM posting WHERE txn_id IN ({marks})", chunk
        ).rowcount
        identities += conn.execute(
            f"DELETE FROM txn_identity WHERE txn_id IN ({marks})", chunk
        ).rowcount
        txns += conn.execute(
            f"DELETE FROM txn WHERE id IN ({marks})", chunk
        ).rowcount

    removed_runs = 0
    for chunk in _chunks(proposal_runs):
        marks = ",".join("?" * len(chunk))
        removed_runs += conn.execute(
            f"DELETE FROM agent_proposal_run WHERE id IN ({marks}) "
            "AND NOT EXISTS (SELECT 1 FROM agent_category_proposal p "
            "WHERE p.run_id = agent_proposal_run.id)",
            chunk,
        ).rowcount

    removed_triage_runs = 0
    for chunk in _chunks(triage_runs):
        marks = ",".join("?" * len(chunk))
        removed_triage_runs += conn.execute(
            f"DELETE FROM agent_triage_run WHERE id IN ({marks}) "
            "AND NOT EXISTS (SELECT 1 FROM agent_triage_item i "
            "WHERE i.run_id = agent_triage_run.id)",
            chunk,
        ).rowcount

    removed = reassigned = 0
    for assertion_id, heir in _assertion_heirs(conn, source_file_id):
        if heir is None:
            conn.execute("DELETE FROM balance_assertion WHERE id = ?", (assertion_id,))
            removed += 1
        else:
            conn.execute(
                "UPDATE balance_assertion SET source_file_id = ? WHERE id = ?",
                (heir, assertion_id),
            )
            reassigned += 1

    raw_records = conn.execute(
        "DELETE FROM raw_record WHERE source_file_id = ?", (source_file_id,)
    ).rowcount
    # Counted before the DELETE, because afterwards there is nothing to count and
    # this is one of the two numbers a person is entitled to see afterwards.
    decided = int(
        conn.execute(
            "SELECT COUNT(*) FROM review_item WHERE source_file_id = ? AND status <> 'open'",
            (source_file_id,),
        ).fetchone()[0]
    )
    review_items = conn.execute(
        "DELETE FROM review_item WHERE source_file_id = ?", (source_file_id,)
    ).rowcount
    conn.execute("DELETE FROM source_file WHERE id = ?", (source_file_id,))

    # Every own account, not only the ones this statement touched. The opening
    # entry is a function of the earliest assertion for an account, and this is
    # the one moment that set can shrink; asking for all of them costs one query
    # on a table that has a handful of rows and removes the need to be right
    # about which accounts were involved.
    opening: list[str] = []
    for account in conn.execute(
        "SELECT id, currency FROM account WHERE is_own_account = 1 ORDER BY id"
    ).fetchall():
        txn_id = sync_opening_entry(
            conn, account_id=str(account["id"]), currency=str(account["currency"])
        )
        if txn_id is not None:
            opening.append(txn_id)

    return DeletionCounts(
        txns=txns,
        postings=postings,
        identities=identities,
        raw_records=raw_records,
        review_items=review_items,
        review_items_decided=decided,
        category_overrides=overrides,
        agent_proposals=proposal_rows,
        agent_proposal_runs=removed_runs,
        agent_triage_items=triage_items,
        agent_triage_runs=removed_triage_runs,
        balance_assertions_removed=removed,
        balance_assertions_reassigned=reassigned,
        opening_txn_ids=tuple(opening),
    )
