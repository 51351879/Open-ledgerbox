# SPDX-License-Identifier: AGPL-3.0-or-later
"""P2 M4: the effective category, and the readers built on top of it.

Two things are under test here and they are not one thing:

* migration ``0006``'s ``v_txn_category`` -- one definition of "what category is
  this", folding a person's ``category_override`` over the rules'
  ``posting.category_id`` -- together with the substitution it makes in
  ``v_transaction.category_id``;
* the repository functions the page reads through: :func:`list_transactions`,
  :func:`summarize_transactions`, :func:`get_transaction`,
  :func:`category_exists`, :func:`list_categories`, and the deferred
  :func:`read_transaction` that lets a caller read two of them as one snapshot.

Every ledger below is built in code, through the repository's own writers.
Nothing here was copied out of a statement: the descriptors are invented, the
amounts are round and written with underscore separators, and no value in this
file came from looking at real data. ``tests/test_repo_hygiene.py`` records why
that rule has no exceptions.

The entry / posting / identity shapes are reproduced as local test doubles, for
the same reason ``tests/test_repo.py`` reproduces them: ``repo`` reads them by
duck typing and must not import the modules that produce them, so a test that
imported the producer would stop testing the contract and start assuming it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from ledgerbox.analytics.categorize import default_rules
from ledgerbox.db.connection import connect_read_only, read_transaction, transaction
from ledgerbox.db.migrate import open_ledger
from ledgerbox.db.repo import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT_KEY,
    MAX_PAGE_SIZE,
    NO_CATEGORY,
    SORT_KEYS,
    TransactionQuery,
    _like_argument,
    category_exists,
    clear_category_override,
    ensure_account,
    ensure_categories,
    get_transaction,
    insert_entries,
    insert_raw_records,
    insert_source_file,
    ledger_totals,
    list_categories,
    list_transactions,
    set_category_override,
    set_posting_categories,
    set_transfer_flags,
    summarize_transactions,
    sync_opening_entry,
    upsert_balance_assertions,
)

SHA = "c" * 64
OTHER_SHA = "d" * 64
BANK = "assets:chase:checking:1234"
INCOME = "income:uncategorized"
EXPENSES = "expenses:uncategorized"

#: The categories these tests use. Written out rather than taken from the rules
#: file: the ids are a frozen contract between the two, and only
#: :func:`test_list_categories_is_the_mirror_of_the_rules_file` is about what
#: the shipped file happens to contain.
CATEGORIES: tuple[tuple[str, None, str], ...] = (
    ("dining", None, "expense"),
    ("groceries", None, "expense"),
    ("salary", None, "income"),
    ("transfer", None, "transfer"),
)

WHEN = "2026-03-04T00:00:00+00:00"


@pytest.fixture
def db(git_free_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = open_ledger(git_free_tmp / "ledger.db")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# building a ledger -- the shapes repo reads by duck typing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Posting:
    id: str
    seq: int
    account_id: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class _Identity:
    account_id: str
    source_system: str
    source_id: str | None
    natural_key: str
    natural_key_version: int
    occurrence_index: int
    raw_descriptor: str
    record_index: int


@dataclass(frozen=True, slots=True)
class _Entry:
    txn_id: str
    date: str
    payee: str | None
    narration: str | None
    record_index: int
    postings: tuple[_Posting, ...]
    identity: _Identity


@dataclass(frozen=True, slots=True)
class _Assertion:
    id: str
    account_id: str
    as_of: str
    commodity_id: str
    amount_minor: int


@dataclass(frozen=True, slots=True)
class Line:
    """One statement line, plus every decision anybody has made about it.

    The three decision fields are stored in three different places on purpose,
    because that is where the ledger stores them: ``rule_category`` becomes
    ``posting.category_id`` on the bank leg (STATUS §5.36), ``rule_transfer``
    becomes ``txn.is_transfer``, and ``override`` becomes a ``category_override``
    row. Nothing in this file writes an effective value anywhere.
    """

    amount_minor: int
    descriptor: str
    date: str = "2025-05-06"
    occurrence_index: int = 0
    #: what the rules derived at ingest, or None for "no rule claimed this"
    rule_category: str | None = None
    #: what the rules derived at ingest
    rule_transfer: bool = False
    #: what a person decided afterwards
    override: str | None = None


def _entry(line: Line, record_index: int) -> _Entry:
    """One booked statement line: bank leg, counter leg, one identity row."""
    key = f"nk|{BANK}|{line.date}|{line.amount_minor}|{line.descriptor}|{line.occurrence_index}"
    counter = INCOME if line.amount_minor > 0 else EXPENSES
    return _Entry(
        txn_id=key,
        date=line.date,
        payee=None,
        narration=line.descriptor,
        record_index=record_index,
        postings=(
            _Posting(f"{key}:0", 0, BANK, line.amount_minor, "USD"),
            _Posting(f"{key}:1", 1, counter, -line.amount_minor, "USD"),
        ),
        identity=_Identity(
            account_id=BANK,
            source_system="pdf",
            source_id=None,
            natural_key=key,
            natural_key_version=1,
            occurrence_index=line.occurrence_index,
            raw_descriptor=line.descriptor,
            record_index=record_index,
        ),
    )


def book(
    conn: sqlite3.Connection,
    lines: Sequence[Line],
    *,
    sha256: str = SHA,
    period_start: str = "2025-05-04",
    period_end: str = "2025-06-03",
) -> list[str]:
    """Ingest *lines* as one statement. Returns the txn ids, in the order given.

    ``statement_month`` is ``period_end``'s first seven characters, which is why
    the two dates are parameters: the month filter is a question about this
    file's period, not about any transaction's date.
    """
    entries = [_entry(line, index) for index, line in enumerate(lines)]
    with transaction(conn):
        source_file_id = insert_source_file(
            conn,
            sha256=sha256,
            rel_path=f"2025/06/{sha256[:8]}.pdf",
            media_type="application/pdf",
            byte_len=1024,
            institution="chase",
            period_start=period_start,
            period_end=period_end,
            ingested_at="2026-03-01T00:00:00+00:00",
        )
        insert_raw_records(
            conn,
            source_file_id=source_file_id,
            payloads=[(entry.record_index, "stmttrn", "{}") for entry in entries],
            parser_id="test",
            parser_version="1",
        )
        ensure_account(
            conn,
            account_id=BANK,
            name="Chase Checking",
            kind="asset",
            subtype="checking",
            currency="USD",
            institution="chase",
            mask="1234",
        )
        ensure_categories(conn, rows=CATEGORIES)
        insert_entries(conn, source_file_id=source_file_id, entries=entries)

        assignments = {
            entry.postings[0].id: line.rule_category
            for entry, line in zip(entries, lines, strict=True)
            if line.rule_category is not None
        }
        if assignments:
            set_posting_categories(conn, assignments=assignments)

        flags = {
            entry.txn_id: True
            for entry, line in zip(entries, lines, strict=True)
            if line.rule_transfer
        }
        if flags:
            set_transfer_flags(conn, assignments=flags)

        for entry, line in zip(entries, lines, strict=True):
            if line.override is not None:
                set_category_override(
                    conn, txn_id=entry.txn_id, category_id=line.override, created_at=WHEN
                )

    return [entry.txn_id for entry in entries]


def _predicate(conn: sqlite3.Connection) -> dict[str, tuple[str | None, str]]:
    """``{txn_id: (effective category, which source answered)}`` from the view."""
    return {
        row["txn_id"]: (row["category_id"], row["decided_by"])
        for row in conn.execute("SELECT * FROM v_txn_category")
    }


def _rendered(conn: sqlite3.Connection, txn_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM v_transaction WHERE txn_id = ?", (txn_id,)).fetchone()
    assert row is not None, f"{txn_id} is not in v_transaction"
    return row


def _stored_rule_category(conn: sqlite3.Connection, txn_id: str) -> str | None:
    """``posting.category_id`` on the bank leg -- the raw column, not the view."""
    row = conn.execute(
        "SELECT category_id FROM posting WHERE txn_id = ? AND seq = 0", (txn_id,)
    ).fetchone()
    return None if row["category_id"] is None else str(row["category_id"])


def _ids(rows: Sequence[sqlite3.Row]) -> list[str]:
    return [str(row["txn_id"]) for row in rows]


# ---------------------------------------------------------------------------
# v_txn_category -- one definition of "what category is this"
# ---------------------------------------------------------------------------


def test_the_category_predicate_names_all_three_sources(db: sqlite3.Connection) -> None:
    """``none`` / ``rule`` / ``override``, and a person wins whatever the rule said.

    Three values where ``v_txn_transfer`` has two, because ``posting.category_id``
    is nullable: an unmatched descriptor is stored as SQL NULL on purpose and
    there is no catch-all row to fall into (STATUS §5.38). Reporting "no rule
    claimed this" as ``rule`` would be a line reading stronger than its evidence,
    on what is the larger part of a real ledger.

    The override is exercised over a rule that said something *different* and
    over a rule that said nothing, because those are two distinct arms of the
    ``COALESCE`` and only one of them is the obvious one.
    """
    unclaimed, ruled, corrected, chosen = book(
        db,
        [
            Line(-1_100, "CARD PURCHASE UNCLAIMED"),
            Line(-1_200, "CARD PURCHASE RULED", rule_category="dining"),
            Line(-1_300, "CARD PURCHASE CORRECTED", rule_category="dining", override="groceries"),
            Line(-1_400, "CARD PURCHASE CHOSEN", override="groceries"),
        ],
    )

    assert _predicate(db) == {
        unclaimed: (None, "none"),
        ruled: ("dining", "rule"),
        corrected: ("groceries", "override"),
        chosen: ("groceries", "override"),
    }


def test_clearing_an_override_hands_the_category_back_to_the_rules(
    db: sqlite3.Connection,
) -> None:
    """The other direction of the same fold: a person can also stop deciding.

    Without this, "the override wins" would be indistinguishable from "the
    override overwrote", and only one of those two is reversible.
    """
    corrected, chosen = book(
        db,
        [
            Line(-1_300, "CARD PURCHASE CORRECTED", rule_category="dining", override="groceries"),
            Line(-1_400, "CARD PURCHASE CHOSEN", override="groceries"),
        ],
    )
    assert _predicate(db)[corrected] == ("groceries", "override")

    with transaction(db):
        assert clear_category_override(db, txn_id=corrected) is True
        assert clear_category_override(db, txn_id=chosen) is True

    assert _predicate(db) == {
        corrected: ("dining", "rule"),
        chosen: (None, "none"),
    }


def test_the_rendering_view_reports_the_effective_category_not_the_stored_one(
    db: sqlite3.Connection,
) -> None:
    """A substitution, so both facts have to be observable independently.

    ``v_transaction.category_id`` is the effective value rather than a second
    column beside the raw one, for the reason 0005 gives for ``is_transfer``:
    exposing both would leave a wronger answer within reach of every future
    reader. Asserting only the view would not distinguish "the override was
    folded in" from "the override overwrote ``posting.category_id``", and those
    differ in whether re-ingesting ``archive/`` still reproduces the rules'
    answer.
    """
    (txn_id,) = book(
        db, [Line(-1_300, "CARD PURCHASE CORRECTED", rule_category="dining", override="groceries")]
    )

    row = _rendered(db, txn_id)
    assert row["category_id"] == "groceries"
    assert row["category_decided_by"] == "override"

    assert _stored_rule_category(db, txn_id) == "dining", "the rules' answer is still underneath"


def test_the_rendering_view_reports_a_rule_and_a_silence_as_themselves(
    db: sqlite3.Connection,
) -> None:
    """The negative case for the test above: with nobody overruling, nothing moves."""
    ruled, unclaimed = book(
        db,
        [
            Line(-1_200, "CARD PURCHASE RULED", rule_category="dining"),
            Line(-1_100, "CARD PURCHASE UNCLAIMED"),
        ],
    )

    assert (_rendered(db, ruled)["category_id"], _rendered(db, ruled)["category_decided_by"]) == (
        "dining",
        "rule",
    )
    assert _rendered(db, unclaimed)["category_id"] is None
    assert _rendered(db, unclaimed)["category_decided_by"] == "none"
    assert _stored_rule_category(db, ruled) == "dining"
    assert _stored_rule_category(db, unclaimed) is None


# ---------------------------------------------------------------------------
# the reason for the scalar subquery: no fan-out
# ---------------------------------------------------------------------------

#: ``v_transaction`` with the effective category reached the way the migration
#: refused to reach it -- by joining ``txn_identity`` and then ``posting``,
#: exactly as ``v_transaction`` reaches the bank leg -- and then joined back on
#: ``txn_id`` alone. Run as a plain query, so nothing here creates a view and
#: the migration is untouched.
#:
#: This exists so the fan-out test can be *watched* failing on the other shape
#: rather than asserted to. A test nobody has seen fail is not a test.
_FANNED_OUT_SQL = """
SELECT t.id AS txn_id, p.id AS posting_id, vc.category_id
FROM txn_identity ti
JOIN txn t ON t.id = ti.txn_id
JOIN (
       SELECT ti2.txn_id AS txn_id, p2.category_id AS category_id
       FROM txn_identity ti2
       JOIN posting p2 ON p2.txn_id = ti2.txn_id AND p2.account_id = ti2.account_id
     ) vc ON vc.txn_id = t.id
JOIN posting p ON p.txn_id = t.id AND p.account_id = ti.account_id
WHERE t.superseded_by IS NULL AND t.id = ?
"""


def _plant_second_identity(conn: sqlite3.Connection, txn_id: str) -> None:
    """Give *txn_id* a second ``txn_identity`` row on the same account.

    ``txn_identity`` is written directly here, as ``tests/test_db.py`` does and
    for the same reason: ``insert_entries`` writes exactly one identity row per
    entry and is the only writer in ``src/``, so this shape cannot be built
    through it. Nothing pairs the two sides of a transfer today, which is why
    the migration closed this before it became reachable rather than after
    (STATUS §5.45).
    """
    with transaction(conn):
        insert_raw_records(
            conn,
            source_file_id=SHA,
            payloads=[(9, "stmttrn", "{}")],
            parser_id="test",
            parser_version="1",
        )
        conn.execute(
            "INSERT INTO txn_identity (txn_id, account_id, source_system, source_id, "
            "natural_key, natural_key_version, occurrence_index, raw_descriptor, raw_record_id) "
            "VALUES (?, ?, 'pdf', NULL, ?, 1, 0, ?, ?)",
            (
                txn_id,
                BANK,
                f"{txn_id}|other-side",
                "THE OTHER SIDE OF THE SAME MOVE",
                f"{SHA}:00009",
            ),
        )


def test_two_identity_rows_render_twice_and_never_four_times(db: sqlite3.Connection) -> None:
    """One row per identity row, which is what the scalar subquery buys.

    ``_FANNED_OUT_SQL`` is the same query with the category reached through a
    join instead, and it is asserted here to return **four** rows on this exact
    ledger. That is the evidence that this test can fail: without the subquery
    the count it asserts is not 2.
    """
    (txn_id,) = book(db, [Line(-1_300, "CARD PURCHASE PAIRED", rule_category="dining")])
    _plant_second_identity(db, txn_id)

    identities = db.execute(
        "SELECT COUNT(*) FROM txn_identity WHERE txn_id = ?", (txn_id,)
    ).fetchone()[0]
    assert identities == 2

    rendered = db.execute(
        "SELECT COUNT(*) FROM v_transaction WHERE txn_id = ?", (txn_id,)
    ).fetchone()[0]
    assert rendered == 2, "one row per identity row, not one per identity row squared"

    fanned = db.execute(_FANNED_OUT_SQL, (txn_id,)).fetchall()
    assert len(fanned) == 4, "the shape 0006 refused really does cross-multiply"

    # And the reader on top of the view sees the same two.
    assert len(list_transactions(db, TransactionQuery(limit=MAX_PAGE_SIZE))) == 2

    # The effective category survives the duplication unchanged: the subquery
    # returns one value per transaction however many identity rows point at it.
    assert {row["category_id"] for row in db.execute("SELECT * FROM v_transaction")} == {"dining"}


def test_deleting_one_identity_row_leaves_exactly_one_rendered_line(
    db: sqlite3.Connection,
) -> None:
    """The negative case: two rows because there are two, not because of a join."""
    (txn_id,) = book(db, [Line(-1_300, "CARD PURCHASE PAIRED", rule_category="dining")])
    _plant_second_identity(db, txn_id)

    with transaction(db):
        db.execute(
            "DELETE FROM txn_identity WHERE txn_id = ? AND natural_key = ?",
            (txn_id, f"{txn_id}|other-side"),
        )

    rendered = db.execute(
        "SELECT COUNT(*) FROM v_transaction WHERE txn_id = ?", (txn_id,)
    ).fetchone()[0]
    assert rendered == 1
    assert len(db.execute(_FANNED_OUT_SQL, (txn_id,)).fetchall()) == 1, (
        "with one identity row the two shapes agree -- which is why nothing "
        "noticed the difference before"
    )


# ---------------------------------------------------------------------------
# "is this a transfer" and "what category is this" are two questions
# ---------------------------------------------------------------------------


def test_a_line_the_rules_flagged_is_a_transfer_with_no_category(
    db: sqlite3.Connection,
) -> None:
    """``classify()`` never returns a transfer category, so this shape is normal.

    It is also the only shape the rules can produce, which is why a reader that
    derived "is this a transfer" from the category's kind would miss exactly the
    transfers the rules found.
    """
    (txn_id,) = book(db, [Line(-50_000, "ONLINE TRANSFER TO SAVINGS", rule_transfer=True)])

    row = _rendered(db, txn_id)
    assert (row["is_transfer"], row["transfer_decided_by"]) == (1, "rule")
    assert row["category_id"] is None
    assert row["category_decided_by"] == "none"


def test_a_line_a_person_moved_to_the_transfer_category_has_both(
    db: sqlite3.Connection,
) -> None:
    """The other shape: a person's override answers both questions at once."""
    (txn_id,) = book(db, [Line(-50_000, "PAYMENT TO MY OTHER ACCOUNT", override="transfer")])

    row = _rendered(db, txn_id)
    assert (row["is_transfer"], row["transfer_decided_by"]) == (1, "override")
    assert (row["category_id"], row["category_decided_by"]) == ("transfer", "override")
    assert db.execute("SELECT is_transfer FROM txn WHERE id = ?", (txn_id,)).fetchone()[0] == 0


def test_the_transfer_flag_is_not_read_off_the_effective_categorys_kind(
    db: sqlite3.Connection,
) -> None:
    """Only an *override* to a transfer category sets the flag -- never a rule's.

    The line built here carries ``posting.category_id = 'transfer'`` as the
    rules' answer. **The ingest path cannot produce that**: ``classify()``
    considers only categories on the amount's own side, so it never returns a
    transfer category however well the patterns fit. It is constructed by
    calling ``set_posting_categories`` directly, precisely to show that the
    effective category's ``kind`` is not what ``v_txn_transfer`` consults --
    ``category_override`` is.

    Both sides are here: the same category id, arriving by the two routes, gives
    two different flags.
    """
    ruled, decided = book(
        db,
        [
            Line(-50_000, "CARD PURCHASE MISLABELLED", rule_category="transfer"),
            Line(-60_000, "PAYMENT TO MY OTHER ACCOUNT", override="transfer"),
        ],
    )

    by_rule = _rendered(db, ruled)
    assert (by_rule["category_id"], by_rule["category_decided_by"]) == ("transfer", "rule")
    assert by_rule["is_transfer"] == 0, "a category kind is not a transfer flag"
    assert by_rule["transfer_decided_by"] == "rule"

    by_person = _rendered(db, decided)
    assert (by_person["category_id"], by_person["is_transfer"]) == ("transfer", 1)

    # And the two aggregations follow the flag, not the kind: only the second
    # line left the cashflow figures.
    assert ledger_totals(db)["transfer_count"] == 1
    assert ledger_totals(db)["outflow_minor"] == -50_000


# ---------------------------------------------------------------------------
# TransactionQuery -- what may never reach a SQL string
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"sort": "balance"}, "cannot sort by"),
        ({"sort": "date; DROP TABLE txn"}, "cannot sort by"),
        ({"direction": "outwards"}, "direction must be"),
        ({"limit": 0}, "limit must be"),
        ({"limit": MAX_PAGE_SIZE + 1}, "limit must be"),
        ({"limit": -1}, "limit must be"),
        ({"offset": -1}, "offset must not be negative"),
    ],
)
def test_a_query_that_cannot_be_rendered_safely_is_refused(
    kwargs: dict[str, object], message: str
) -> None:
    """``sort`` and ``direction`` are interpolated, so the check is at the door.

    Raised from the caller's own line rather than at the SQL boundary, which is
    the difference between a ``ValueError`` naming the field and a query string
    carrying whatever somebody typed.
    """
    with pytest.raises(ValueError, match=message):
        TransactionQuery(**kwargs)  # type: ignore[arg-type]


def test_a_query_inside_the_allowed_range_is_accepted() -> None:
    """The negative case for all seven refusals above, including both edges."""
    default = TransactionQuery()
    assert (default.sort, default.limit, default.offset) == (DEFAULT_SORT_KEY, DEFAULT_PAGE_SIZE, 0)
    assert TransactionQuery(limit=1).limit == 1
    assert TransactionQuery(limit=MAX_PAGE_SIZE).limit == MAX_PAGE_SIZE
    assert TransactionQuery(offset=0).offset == 0
    for key in SORT_KEYS:
        assert TransactionQuery(sort=key).sort == key
    for direction in (None, "in", "out"):
        assert TransactionQuery(direction=direction).direction == direction


def test_like_argument_escapes_the_escape_before_the_wildcards() -> None:
    """Escaping the backslash last would escape the escapes it had just written.

    Pinned directly because the end-to-end evidence for it is one search string
    in :func:`test_the_text_filter_treats_percent_and_underscore_as_characters`,
    and the failure is silent: the search simply stops matching.
    """
    assert _like_argument("plain") == "%plain%"
    assert _like_argument("50%") == "%50\\%%"
    assert _like_argument("a_b") == "%a\\_b%"
    assert _like_argument("c\\d") == "%c\\\\d%"
    # The order: one backslash in, two out, and the percent escaped once -- not
    # a doubled backslash swallowing the escape that follows it.
    assert _like_argument("\\%") == "%\\\\\\%%"


# ---------------------------------------------------------------------------
# filters -- one ledger holding every shape they have to tell apart
# ---------------------------------------------------------------------------


@pytest.fixture
def mixed(db: sqlite3.Connection) -> dict[str, str]:
    """Two statements, named lines, one of every shape the filters must separate.

    Returned by name so an assertion can say *which* rows it expects rather than
    how many. The first statement's period ends in June and the second's in
    July, which is where ``statement_month`` comes from.
    """
    first = book(
        db,
        [
            Line(-1_200, "CARD PURCHASE COFFEE BAR", date="2025-05-06", rule_category="dining"),
            Line(250_000, "PAYROLL DEPOSIT ACME", date="2025-05-15", rule_category="salary"),
            Line(-50_000, "ONLINE TRANSFER TO SAVINGS", date="2025-05-20", rule_transfer=True),
            Line(-3_400, "CARD PURCHASE 50% OFF_SALE", date="2025-05-22"),
            Line(-2_500, "CARD PURCHASE FXS SHOP", date="2025-05-25"),
        ],
        sha256=SHA,
        period_start="2025-05-04",
        period_end="2025-06-03",
    )
    second = book(
        db,
        [
            Line(-7_000, "CARD PURCHASE GROCER", date="2025-06-10", rule_category="groceries"),
            Line(1_500, "INTEREST PAID", date="2025-06-30"),
        ],
        sha256=OTHER_SHA,
        period_start="2025-06-04",
        period_end="2025-07-05",
    )
    names = ("coffee", "payroll", "moved", "sale", "shop", "grocer", "interest")
    return dict(zip(names, first + second, strict=True))


def _matching(conn: sqlite3.Connection, **kwargs: object) -> set[str]:
    """Every txn id the filter matches, unpaged."""
    query = TransactionQuery(limit=MAX_PAGE_SIZE, **kwargs)  # type: ignore[arg-type]
    return set(_ids(list_transactions(conn, query)))


def test_an_unfiltered_query_returns_every_statement_line(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """The baseline every filter test below is a subtraction from."""
    assert _matching(db) == set(mixed.values())
    assert len(mixed) == 7


def test_the_text_filter_matches_a_substring_of_the_verbatim_descriptor(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """``raw_descriptor`` only: it is the bank's own line and the string on screen."""
    assert _matching(db, text="COFFEE") == {mixed["coffee"]}
    assert _matching(db, text="coffee") == {mixed["coffee"]}, "SQLite's LIKE is ASCII-insensitive"
    assert _matching(db, text="CARD PURCHASE") == {
        mixed["coffee"],
        mixed["sale"],
        mixed["shop"],
        mixed["grocer"],
    }
    assert _matching(db, text="COFFEEHOUSE") == set(), "a substring that is not there matches none"


def test_the_text_filter_treats_percent_and_underscore_as_characters(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """``LIKE ... ESCAPE`` -- otherwise a search for ``%`` returns the whole ledger.

    The ``sale`` line is the only descriptor carrying either character, so each
    search below has exactly one right answer and six wrong ones.
    """
    assert _matching(db, text="50% OFF_SALE") == {mixed["sale"]}
    assert _matching(db, text="%") == {mixed["sale"]}, "not every row"
    assert _matching(db, text="_") == {mixed["sale"]}, "not every row"

    # `F_S` is in `OFF_SALE` literally, and would also be in `FXS SHOP` if the
    # underscore were a single-character wildcard.
    assert _matching(db, text="F_S") == {mixed["sale"]}
    assert mixed["shop"] not in _matching(db, text="F_S")

    # And a percent that is not adjacent to the right characters still misses.
    assert _matching(db, text="99%") == set()


def test_the_month_filter_selects_one_statements_lines(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """``statement_month`` is the file's ``period_end``, not any transaction date.

    Every date in the first statement falls in May; its month is ``2025-06``.
    That is the property being asserted, not an accident of the fixture.
    """
    assert _matching(db, month="2025-06") == {
        mixed["coffee"],
        mixed["payroll"],
        mixed["moved"],
        mixed["sale"],
        mixed["shop"],
    }
    assert _matching(db, month="2025-07") == {mixed["grocer"], mixed["interest"]}
    assert _matching(db, month="2025-05") == set(), "the month the dates are in, and no rows"


def test_the_category_filter_selects_by_the_effective_category(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """Effective, so a person's correction moves the row between filters."""
    assert _matching(db, category="dining") == {mixed["coffee"]}
    assert _matching(db, category="salary") == {mixed["payroll"]}
    assert _matching(db, category="transfer") == set(), "no line carries it yet"

    with transaction(db):
        set_category_override(
            db, txn_id=mixed["coffee"], category_id="groceries", created_at=WHEN
        )

    assert _matching(db, category="dining") == set(), "the rules' answer no longer selects it"
    assert _matching(db, category="groceries") == {mixed["coffee"], mixed["grocer"]}


def test_the_no_category_filter_selects_exactly_the_rows_nobody_claimed(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """A sentinel, because there is no ``uncategorized`` row to select (§5.38).

    Checked against the view rather than against a hand-written list, so the two
    cannot drift; the hand-written list is asserted too, so the check is not
    comparing the query with itself.
    """
    unclaimed = _matching(db, category=NO_CATEGORY)
    assert unclaimed == {mixed["moved"], mixed["sale"], mixed["shop"], mixed["interest"]}

    from_view = {
        str(row["txn_id"])
        for row in db.execute("SELECT txn_id FROM v_transaction WHERE category_id IS NULL")
    }
    assert unclaimed == from_view

    claimed = _matching(db, category="dining") | _matching(db, category="salary")
    claimed |= _matching(db, category="groceries")
    assert unclaimed & claimed == set()
    assert unclaimed | claimed == set(mixed.values())


def test_the_no_category_sentinel_cannot_also_be_a_category_id() -> None:
    """The sentinel and the ids it sits beside must not share a value space.

    Verification found this filter spelled ``none``, which
    ``analytics.categorize``'s id pattern accepts. Nothing in the shipped rules
    file is called that, so nothing was wrong — but "no id collides with it
    today" and "no id can" are different sentences, and only the second one
    survives somebody editing the rules file. A filter that silently answers a
    different question than the one selected is precisely what this project
    refuses to leave to documentation.

    The negative case is the old spelling, kept as the demonstration that this
    assertion is not vacuous: ``none`` is a legal id and ``(none)`` is not.
    """
    from ledgerbox.analytics.categorize import _ID_RE

    assert not _ID_RE.match(NO_CATEGORY), "a rules file could declare this as a category"
    assert _ID_RE.match("none"), "and this is why the sentinel is not spelled that way"


def test_the_transfer_filter_works_in_both_directions(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """Two halves that partition the ledger, so neither can quietly lose a row."""
    flagged = _matching(db, transfer=True)
    rest = _matching(db, transfer=False)

    assert flagged == {mixed["moved"]}
    assert rest == set(mixed.values()) - flagged
    assert flagged & rest == set()
    assert flagged | rest == set(mixed.values())


def test_the_direction_filter_works_in_both_directions(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """Sign on the bank leg: what arrived, and what left."""
    arriving = _matching(db, direction="in")
    leaving = _matching(db, direction="out")

    assert arriving == {mixed["payroll"], mixed["interest"]}
    assert leaving == set(mixed.values()) - arriving
    assert arriving & leaving == set()
    assert arriving | leaving == set(mixed.values()), "no zero-amount line in this ledger"


def test_two_filters_compose_rather_than_replacing_each_other(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """Each clause is ANDed, so the pair is narrower than either alone."""
    july = _matching(db, month="2025-07")
    arriving = _matching(db, direction="in")
    both = _matching(db, month="2025-07", direction="in")

    assert both == {mixed["interest"]}
    assert both == july & arriving
    assert both < july and both < arriving, "narrower than either filter on its own"

    # A pair that matches nothing is not an error, and does not fall back to one
    # of its halves.
    assert _matching(db, month="2025-06", category="groceries") == set()


# ---------------------------------------------------------------------------
# the summary describes the filter; the page describes the page
# ---------------------------------------------------------------------------


def test_the_summary_ignores_limit_and_offset_while_the_page_obeys_them(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """A total that shrank on turning the page, under a heading saying how many matched.

    The offset-past-the-end case is the reason the two are separate statements
    rather than one windowed query: ``COUNT(*) OVER ()`` returns no row at all
    there, so the figures would read zero beside a pager saying five.
    """
    paged = TransactionQuery(direction="out", limit=2)
    unpaged = TransactionQuery(direction="out", limit=MAX_PAGE_SIZE)

    page = list_transactions(db, paged)
    everything = list_transactions(db, unpaged)
    assert len(page) == 2
    assert len(everything) == 5

    summary = summarize_transactions(db, paged)
    assert summary == summarize_transactions(db, unpaged)
    assert summary["matched"] == len(everything)
    assert summary["bank_out_minor"] == sum(
        int(row["amount_minor"]) for row in everything if int(row["amount_minor"]) < 0
    )
    assert summary["bank_in_minor"] == 0
    assert summary["bank_net_minor"] == summary["bank_in_minor"] + summary["bank_out_minor"]

    past_the_end = TransactionQuery(direction="out", limit=2, offset=99)
    assert list_transactions(db, past_the_end) == []
    assert summarize_transactions(db, past_the_end) == summary


def test_the_summary_follows_the_filter_it_was_given(
    db: sqlite3.Connection, mixed: dict[str, str]
) -> None:
    """The negative case: the figures are not the whole ledger's, restated."""
    everything = summarize_transactions(db, TransactionQuery())
    arriving = summarize_transactions(db, TransactionQuery(direction="in"))

    assert everything["matched"] == 7
    assert arriving["matched"] == 2
    assert arriving["bank_out_minor"] == 0
    assert arriving["bank_in_minor"] == 251_500
    assert everything["bank_in_minor"] == arriving["bank_in_minor"]
    assert everything["bank_out_minor"] != arriving["bank_out_minor"]


def test_the_summary_figures_are_the_bank_leg_and_not_the_ledger_totals(
    db: sqlite3.Connection,
) -> None:
    """They differ here, and that is correct rather than a defect.

    ``summarize_transactions`` measures the **bank leg**: how the matched lines
    moved this account, transfers included, because a person filtering a table
    is asking about the rows in front of them. ``ledger_totals`` measures the
    income and expense legs with transfers excluded, because it is answering
    "what was earned and spent". A flagged transfer is the cheapest ledger in
    which the two provably differ.

    This is the third cashflow measurement in the codebase. STATUS §5.45 records
    what the second one cost: a paragraph claiming two figures could not diverge
    was refuted three times, and a block-level check now exists solely to keep
    those two honest. The ``bank_`` prefix is how this one says it is not them.

    The arithmetic below holds **in this ledger** -- one own account, no opening
    entry, one flagged line, nothing superseded. It is not offered as an
    identity: ``balance_minor`` counts an opening entry that has no identity row
    and so is invisible here, and a transaction between two own accounts would
    move one figure and not the other.
    """
    book(
        db,
        [
            Line(-30_000, "CARD PURCHASE KEPT"),
            Line(-50_000, "ONLINE TRANSFER TO SAVINGS", date="2025-05-07", rule_transfer=True),
        ],
    )

    summary = summarize_transactions(db, TransactionQuery())
    totals = ledger_totals(db)

    assert summary["bank_out_minor"] == -80_000, "the bank leg keeps the transfer"
    assert totals["outflow_minor"] == -30_000, "the spending figure drops it"
    assert summary["bank_out_minor"] != totals["outflow_minor"]
    assert summary["matched"] == 2
    assert totals["txn_count"] == 1

    # In this ledger the whole difference is the flagged line, and the ledger
    # already reports that amount separately.
    assert (
        summary["bank_out_minor"] - totals["outflow_minor"]
        == totals["transfer_excluded_out_minor"]
    )


# ---------------------------------------------------------------------------
# ordering is total, or two pages of one query overlap
# ---------------------------------------------------------------------------

#: The row column each sort key names. Asserted against :data:`SORT_KEYS` below,
#: so a sort key added without a test here fails rather than passes silently.
_SORT_COLUMN = {
    "date": "date",
    "amount": "amount_minor",
    "description": "raw_descriptor",
    "category": "category_id",
    "month": "statement_month",
}


def _order_key(value: object) -> tuple[bool, object]:
    """SQLite sorts NULL first ascending and last descending; so does this."""
    return (value is not None, value)


@pytest.fixture
def same_day(db: sqlite3.Connection) -> list[str]:
    """Five lines sharing one date, so ``ORDER BY date`` alone decides nothing."""
    return book(
        db,
        [
            Line(-1_100, "CARD PURCHASE ONE", date="2025-05-06"),
            Line(-1_200, "CARD PURCHASE TWO", date="2025-05-06"),
            Line(-1_300, "CARD PURCHASE THREE", date="2025-05-06"),
            Line(-1_400, "CARD PURCHASE FOUR", date="2025-05-06"),
            Line(-1_500, "CARD PURCHASE FIVE", date="2025-05-06"),
        ],
    )


def test_rows_sharing_a_date_come_back_in_the_same_order_every_time(
    db: sqlite3.Connection, same_day: list[str]
) -> None:
    """The tiebreak ends in ``posting_id``, which is unique."""
    query = TransactionQuery(limit=MAX_PAGE_SIZE)
    runs = [_ids(list_transactions(db, query)) for _ in range(3)]

    assert len(runs[0]) == len(same_day) == 5
    assert runs[0] == runs[1] == runs[2]
    assert sorted(runs[0]) == sorted(same_day)


def test_paging_over_rows_sharing_a_date_shows_each_one_exactly_once(
    db: sqlite3.Connection, same_day: list[str]
) -> None:
    """Without a unique last key two pages can repeat a row and skip another.

    Asserted as three properties: the pages are disjoint, their union is the
    whole set, and their concatenation is the unpaged order.

    **What that trio does not prove**, said plainly because it would be easy to
    read it as proof: this ledger returns the same order with the tiebreak
    removed. Measured on SQLite 3.50.4 -- ``ORDER BY v.date DESC`` alone, pages
    of two, and of seven and fifty over three hundred tied rows -- and nothing
    repeated or went missing in any of them. So the assertions above pass on a
    query that has no total order; they describe the behaviour a person gets,
    not the property that guarantees it.

    :func:`test_the_ordering_ends_in_a_key_no_two_rows_share` asserts the
    guarantee itself, and that one does distinguish the two shapes.
    """
    unpaged = _ids(list_transactions(db, TransactionQuery(limit=MAX_PAGE_SIZE)))
    pages = [
        _ids(list_transactions(db, TransactionQuery(limit=2, offset=offset)))
        for offset in (0, 2, 4)
    ]

    assert [len(page) for page in pages] == [2, 2, 1]
    seen = [txn_id for page in pages for txn_id in page]
    assert len(seen) == len(set(seen)) == 5, "nothing repeated"
    assert set(seen) == set(same_day), "nothing missed"
    assert seen == unpaged, "and in the same order the unpaged query gives"


def test_the_ordering_ends_in_a_key_no_two_rows_share(
    db: sqlite3.Connection, same_day: list[str]
) -> None:
    """The property paging actually rests on, on rows the sort column cannot separate.

    ``date`` is identical across all five rows here, so ``ORDER BY v.date`` on
    its own leaves the engine free to return them in any order it likes and to
    change its mind between two pages of the same query. What removes that
    freedom is the tiebreak ending in ``posting_id``, a content hash and unique.

    Both halves are asserted: the sort column alone does **not** distinguish
    these rows, and the full ordering key does.
    """
    rows = list_transactions(db, TransactionQuery(sort="date", limit=MAX_PAGE_SIZE))
    assert len(rows) == 5

    assert len({row["date"] for row in rows}) == 1, "date alone separates none of them"
    full_key = [(row["date"], row["record_index"], row["posting_id"]) for row in rows]
    assert len(set(full_key)) == len(rows), "and the ordering ends in a key that separates all"


@pytest.fixture
def varied(db: sqlite3.Connection) -> list[str]:
    """Rows whose date, amount, descriptor, category and month all differ.

    Every sortable column has at least two distinct values here, including a
    NULL category, so an assertion about ordering cannot pass by being vacuous.
    """
    first = book(
        db,
        [
            Line(-1_200, "AAA FIRST", date="2025-05-06", rule_category="dining"),
            Line(250_000, "MMM MIDDLE", date="2025-05-15", rule_category="salary"),
            Line(-3_400, "ZZZ LAST", date="2025-05-22"),
        ],
        sha256=SHA,
        period_start="2025-05-04",
        period_end="2025-06-03",
    )
    second = book(
        db,
        [
            Line(-7_000, "BBB SECOND STATEMENT", date="2025-06-10", rule_category="groceries"),
            Line(1_500, "YYY SECOND STATEMENT", date="2025-06-30"),
        ],
        sha256=OTHER_SHA,
        period_start="2025-06-04",
        period_end="2025-07-05",
    )
    return first + second


def test_every_sort_key_orders_by_the_column_it_names(
    db: sqlite3.Connection, varied: list[str]
) -> None:
    """All five keys, both directions, on a ledger where each column varies."""
    assert set(_SORT_COLUMN) == set(SORT_KEYS), "a new sort key needs a case here"

    for key, column in _SORT_COLUMN.items():
        descending_rows = list_transactions(
            db, TransactionQuery(sort=key, descending=True, limit=MAX_PAGE_SIZE)
        )
        ascending_rows = list_transactions(
            db, TransactionQuery(sort=key, descending=False, limit=MAX_PAGE_SIZE)
        )
        assert len(descending_rows) == len(ascending_rows) == len(varied)

        distinct = {row[column] for row in ascending_rows}
        assert len(distinct) > 1, f"{key} would be a vacuous assertion on this ledger"

        for rows, descending in ((descending_rows, True), (ascending_rows, False)):
            values = [_order_key(row[column]) for row in rows]
            assert values == sorted(values, reverse=descending), (key, descending)

        assert _ids(descending_rows) != _ids(ascending_rows), (
            f"{key} must actually reverse, not merely accept the flag"
        )


def test_the_sort_key_whitelist_is_the_only_thing_reaching_order_by() -> None:
    """The negative case for the loop above: nothing else is sortable.

    ``ORDER BY`` cannot be bound, so the column is interpolated. This asserts
    the set it is interpolated from, which is the whole of the safety argument.
    """
    assert set(SORT_KEYS) == {"date", "amount", "description", "category", "month"}
    assert all(column.startswith("v.") for column in SORT_KEYS.values())
    assert DEFAULT_SORT_KEY in SORT_KEYS
    with pytest.raises(ValueError, match="cannot sort by"):
        TransactionQuery(sort="posting_id")


# ---------------------------------------------------------------------------
# get_transaction
# ---------------------------------------------------------------------------


def test_get_transaction_returns_the_line_and_none_for_an_unknown_id(
    db: sqlite3.Connection,
) -> None:
    (txn_id,) = book(db, [Line(-1_200, "CARD PURCHASE COFFEE BAR", rule_category="dining")])

    row = get_transaction(db, txn_id)
    assert row is not None
    assert row["txn_id"] == txn_id
    assert row["raw_descriptor"] == "CARD PURCHASE COFFEE BAR"
    assert row["amount_minor"] == -1_200
    assert row["category_id"] == "dining"

    assert get_transaction(db, "no-such-transaction") is None


def test_get_transaction_refuses_the_opening_entry_because_it_is_not_a_line(
    db: sqlite3.Connection,
) -> None:
    """It has a ``txn`` row and no ``txn_identity`` row, so it is not on a statement.

    ``sync_opening_entry`` books the balance the account already had against
    equity. Nobody can recategorise it and no statement printed it as a line, so
    reading it through ``v_transaction`` -- and answering "no such row" -- is
    correct rather than incidental.
    """
    (booked,) = book(db, [Line(-1_200, "CARD PURCHASE COFFEE BAR")])
    with transaction(db):
        upsert_balance_assertions(
            db,
            source_file_id=SHA,
            rows=[
                _Assertion(
                    id="ba-opening",
                    account_id=BANK,
                    as_of="2025-06-03",
                    commodity_id="USD",
                    amount_minor=100_000,
                )
            ],
        )
        opening = sync_opening_entry(db, account_id=BANK, currency="USD")

    assert opening is not None
    assert db.execute("SELECT COUNT(*) FROM txn WHERE id = ?", (opening,)).fetchone()[0] == 1
    assert (
        db.execute("SELECT COUNT(*) FROM txn_identity WHERE txn_id = ?", (opening,)).fetchone()[0]
        == 0
    )

    assert get_transaction(db, opening) is None
    assert get_transaction(db, booked) is not None, "and a real statement line still resolves"
    assert _ids(list_transactions(db, TransactionQuery(limit=MAX_PAGE_SIZE))) == [booked]


# ---------------------------------------------------------------------------
# the categories a person can choose from
# ---------------------------------------------------------------------------


def test_category_exists_answers_both_ways(db: sqlite3.Connection) -> None:
    """Asked before writing an override, so an unknown id names the category."""
    assert category_exists(db, "dining") is False, "nothing is mirrored on a fresh ledger"

    book(db, [Line(-1_200, "CARD PURCHASE COFFEE BAR")])

    assert category_exists(db, "dining") is True
    assert category_exists(db, "transfer") is True
    assert category_exists(db, "no-such-category") is False
    assert category_exists(db, "") is False


def test_list_categories_is_the_mirror_of_the_rules_file(db: sqlite3.Connection) -> None:
    """§5.37: the rules file defines the categories and this table mirrors them.

    Compared against ``default_rules().rows()`` rather than against a count,
    because a number here would be a second place to update when the rules file
    changes -- and ``tests/test_categorize.py`` already pins the count.
    """
    assert list_categories(db) == [], "and none of them before a statement is ingested"

    rows = default_rules().rows()
    with transaction(db):
        ensure_categories(db, rows=rows)

    listed = [(str(row["id"]), str(row["kind"]), row["parent_id"]) for row in list_categories(db)]

    assert {(id_, kind) for id_, kind, _ in listed} == {
        (id_, kind) for id_, _, kind in rows
    }
    assert len(listed) == len(rows)
    assert [parent for _, _, parent in listed] == [None] * len(rows), "flat, as rows() says"

    # Ordered by (kind, id), and the same order on the next call.
    assert listed == sorted(listed, key=lambda row: (row[1], row[0]))
    assert [str(row["id"]) for row in list_categories(db)] == [id_ for id_, _, _ in listed]

    # All three kinds are offered. Nothing is filtered by the sign of any
    # transaction: an override is a person overruling a derivation, and a
    # refunded restaurant charge really does arrive as a deposit.
    assert {kind for _, kind, _ in listed} == {"income", "expense", "transfer"}


# ---------------------------------------------------------------------------
# read_transaction -- the page and its figures from one snapshot
# ---------------------------------------------------------------------------


def test_a_read_transaction_holds_one_snapshot_across_another_connections_commit(
    db: sqlite3.Connection, git_free_tmp: Path
) -> None:
    """Two SELECTs outside a transaction are two snapshots, with WAL in between.

    That reads, in this application, as a page of transactions and a set of
    figures describing "the same" rows computed either side of an ingest -- a
    table disagreeing with its own total, arriving through the back door.

    The last assertion is the negative case: outside the read transaction the
    same two reads *do* straddle a commit, so the snapshot is what did the work
    rather than any caching.
    """
    book(db, [Line(-1_200, "CARD PURCHASE COFFEE BAR")])
    writer = open_ledger(git_free_tmp / "ledger.db")
    try:
        with read_transaction(db):
            before = summarize_transactions(db, TransactionQuery())
            book(
                writer,
                [Line(-2_500, "CARD PURCHASE SECOND", date="2025-06-10")],
                sha256=OTHER_SHA,
                period_start="2025-06-04",
                period_end="2025-07-05",
            )
            after = summarize_transactions(db, TransactionQuery())
            rows = list_transactions(db, TransactionQuery(limit=MAX_PAGE_SIZE))

        assert before == after, "the committed write is not visible inside the snapshot"
        assert before["matched"] == 1
        assert len(rows) == 1, "and the page agrees with the figures beside it"

        assert summarize_transactions(db, TransactionQuery())["matched"] == 2, (
            "the snapshot ends when the read transaction does"
        )

        book(
            writer,
            [Line(-900, "CARD PURCHASE THIRD", date="2025-07-10")],
            sha256="e" * 64,
            period_start="2025-07-06",
            period_end="2025-08-05",
        )
        assert summarize_transactions(db, TransactionQuery())["matched"] == 3, (
            "outside a read transaction each read is its own snapshot"
        )
    finally:
        writer.close()


def test_a_read_transaction_works_on_a_handle_that_cannot_take_the_write_lock(
    db: sqlite3.Connection, git_free_tmp: Path
) -> None:
    """Deferred rather than IMMEDIATE, which is the whole difference from ``transaction``.

    A ``mode=ro`` handle with ``PRAGMA query_only`` cannot take the write lock at
    all, so the contrast is asserted on the same connection: the deferred one
    opens, the immediate one raises.
    """
    booked = book(db, [Line(-1_200, "CARD PURCHASE COFFEE BAR", rule_category="dining")])
    read_only = connect_read_only(git_free_tmp / "ledger.db")
    try:
        with read_transaction(read_only):
            rows = list_transactions(read_only, TransactionQuery(limit=MAX_PAGE_SIZE))
            summary = summarize_transactions(read_only, TransactionQuery())
            one = get_transaction(read_only, booked[0])

        assert _ids(rows) == booked
        assert summary["matched"] == 1
        assert one is not None and one["category_id"] == "dining"

        with pytest.raises(sqlite3.OperationalError, match="readonly database"), transaction(
            read_only
        ):
            pass  # pragma: no cover - BEGIN IMMEDIATE raises before the body runs
    finally:
        read_only.close()
