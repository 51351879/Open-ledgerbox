# SPDX-License-Identifier: AGPL-3.0-or-later
"""M6: the repository layer.

The load-bearing test is :func:`test_ingesting_the_same_batch_three_times_writes_nothing_new`
— P0's acceptance criterion says ingesting one PDF three times must leave the
row counts identical, and every other test here exists to make sure that
property was bought honestly rather than by dropping rows.

The entry / assertion / review-item shapes are reproduced as local test doubles
on purpose: ``repo`` reads them by duck typing and must not import the modules
that produce them, so the tests must not either. If the real shapes drift, the
integration layer breaks loudly and these tests keep describing the contract.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from ledgerbox.db.connection import transaction
from ledgerbox.db.migrate import open_ledger
from ledgerbox.db.repo import (
    BalanceAssertionConflict,
    CategoryKindConflict,
    categorized_rows,
    category_exists,
    clear_category_override,
    delete_statement,
    ensure_account,
    ensure_categories,
    find_source_file,
    get_category_override,
    insert_entries,
    insert_raw_records,
    insert_source_file,
    ledger_totals,
    list_categories,
    list_category_overrides,
    overlapping_statements,
    replace_review_items,
    row_counts,
    set_category_override,
    set_posting_categories,
    statement_deletion_facts,
    sync_opening_entry,
    upsert_balance_assertions,
)

SHA = "a" * 64
OTHER_SHA = "b" * 64
BANK = "assets:chase:checking:1234"
INCOME = "income:uncategorized"
EXPENSES = "expenses:uncategorized"


@pytest.fixture
def db(git_free_tmp: Path) -> Iterator[sqlite3.Connection]:
    conn = open_ledger(git_free_tmp / "ledger.db")
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# test doubles — the shapes repo reads by duck typing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FakePosting:
    id: str
    seq: int
    account_id: str
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class FakeIdentity:
    account_id: str
    source_system: str
    source_id: str | None
    natural_key: str
    natural_key_version: int
    occurrence_index: int
    raw_descriptor: str
    record_index: int


@dataclass(frozen=True, slots=True)
class FakeEntry:
    txn_id: str
    date: str
    payee: str | None
    narration: str | None
    record_index: int
    postings: tuple[FakePosting, ...]
    identity: FakeIdentity


@dataclass(frozen=True, slots=True)
class FakeAssertion:
    id: str
    account_id: str
    as_of: str
    commodity_id: str
    amount_minor: int


@dataclass(frozen=True, slots=True)
class FakeReviewItem:
    id: str
    source_file_id: str
    severity: str
    check_id: str
    detail: str


def make_entry(
    record_index: int,
    *,
    date: str = "2025-06-02",
    amount_minor: int = 10_000,
    descriptor: str = "CARD PURCHASE COFFEE",
    occurrence_index: int = 0,
    source_id: str | None = None,
    bank_account: str = BANK,
) -> FakeEntry:
    """One statement line, booked double-entry: bank leg plus its counter-leg.

    ``natural_key`` stands in for the real content hash; what matters to repo is
    only that identical content produces an identical key and that
    ``occurrence_index`` participates in it.
    """
    key = f"nk|{bank_account}|{date}|{amount_minor}|{descriptor}|{occurrence_index}"
    counter = INCOME if amount_minor > 0 else EXPENSES
    return FakeEntry(
        txn_id=key,
        date=date,
        payee=None,
        narration=descriptor,
        record_index=record_index,
        postings=(
            FakePosting(f"{key}:0", 0, bank_account, amount_minor, "USD"),
            FakePosting(f"{key}:1", 1, counter, -amount_minor, "USD"),
        ),
        identity=FakeIdentity(
            account_id=bank_account,
            source_system="pdf",
            source_id=source_id,
            natural_key=key,
            natural_key_version=1,
            occurrence_index=occurrence_index,
            raw_descriptor=descriptor,
            record_index=record_index,
        ),
    )


def ingest(
    conn: sqlite3.Connection,
    entries: list[FakeEntry],
    *,
    sha256: str = SHA,
    assertions: list[FakeAssertion] | None = None,
    items: list[FakeReviewItem] | None = None,
    period_start: str | None = "2025-05-04",
    period_end: str | None = "2025-06-03",
):
    """The whole unit of work, exactly as the integration layer will wrap it.

    The period is a parameter because deletion is the first thing in this
    project that reads it: which statement owns a shared balance, and whether
    two statements overlap, are both questions about these two dates.
    """
    with transaction(conn):
        source_file_id = insert_source_file(
            conn,
            sha256=sha256,
            rel_path=f"2025/06/{sha256[:8]}.pdf",
            media_type="application/pdf",
            byte_len=1234,
            institution="chase",
            period_start=period_start,
            period_end=period_end,
            ingested_at="2026-01-01T00:00:00+00:00",
        )
        insert_raw_records(
            conn,
            source_file_id=source_file_id,
            payloads=[(e.record_index, "stmttrn", "{}") for e in entries],
            parser_id="chase_checking",
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
        counts = insert_entries(conn, source_file_id=source_file_id, entries=entries)
        if assertions:
            upsert_balance_assertions(conn, source_file_id=source_file_id, rows=assertions)
        if items is not None:
            replace_review_items(conn, source_file_id=source_file_id, items=items)
    return counts


# ---------------------------------------------------------------------------
# source_file
# ---------------------------------------------------------------------------


def test_source_file_is_content_addressed_and_idempotent(db: sqlite3.Connection) -> None:
    assert find_source_file(db, SHA) is None

    with transaction(db):
        first = insert_source_file(
            db,
            sha256=SHA,
            rel_path="2025/06/original.pdf",
            media_type="application/pdf",
            byte_len=10,
            institution="chase",
            period_start="2025-05-04",
            period_end="2025-06-03",
            ingested_at="2026-01-01T00:00:00+00:00",
        )
    with transaction(db):
        second = insert_source_file(
            db,
            sha256=SHA,
            rel_path="2025/06/filed-somewhere-else.pdf",
            media_type="application/pdf",
            byte_len=10,
            institution="chase",
            period_start="2025-05-04",
            period_end="2025-06-03",
            ingested_at="2026-02-02T00:00:00+00:00",
        )

    assert first == second == SHA
    assert db.execute("SELECT COUNT(*) FROM source_file").fetchone()[0] == 1
    row = find_source_file(db, SHA)
    assert row is not None
    # The first filing is the true one; a second attempt does not rewrite it.
    assert row["rel_path"] == "2025/06/original.pdf"
    assert row["ingested_at"] == "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# account
# ---------------------------------------------------------------------------


def test_ensure_account_never_overwrites_a_user_edit(db: sqlite3.Connection) -> None:
    with transaction(db):
        ensure_account(
            db,
            account_id=BANK,
            name="Chase Checking",
            kind="asset",
            subtype="checking",
            currency="USD",
            institution="chase",
            mask="1234",
        )
    with transaction(db):  # the user renames it
        db.execute("UPDATE account SET name = 'Rent Account' WHERE id = ?", (BANK,))
    with transaction(db):  # the next statement arrives
        ensure_account(
            db,
            account_id=BANK,
            name="Chase Checking",
            kind="asset",
            subtype="checking",
            currency="USD",
            institution="chase",
            mask="1234",
        )

    rows = db.execute("SELECT name, is_own_account FROM account WHERE id = ?", (BANK,)).fetchall()
    assert len(rows) == 1
    assert rows[0]["name"] == "Rent Account"
    # ledger_totals depends on this default being 1 for real accounts.
    assert rows[0]["is_own_account"] == 1


def test_ensure_account_still_rejects_a_bad_kind(db: sqlite3.Connection) -> None:
    """ON CONFLICT DO NOTHING, not INSERT OR IGNORE: CHECKs must still bite."""
    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        ensure_account(
            db,
            account_id="assets:bogus",
            name="Bogus",
            kind="not-a-kind",
            subtype=None,
            currency="USD",
            institution=None,
            mask=None,
        )


# ---------------------------------------------------------------------------
# raw_record
# ---------------------------------------------------------------------------


def test_insert_raw_records_is_idempotent(db: sqlite3.Connection) -> None:
    payloads = [(i, "stmttrn", f'{{"row": {i}}}') for i in range(3)]
    with transaction(db):
        insert_source_file(
            db,
            sha256=SHA,
            rel_path="2025/06/f.pdf",
            media_type="application/pdf",
            byte_len=1,
            institution="chase",
            period_start="2025-05-04",
            period_end="2025-06-03",
            ingested_at="2026-01-01T00:00:00+00:00",
        )
        first = insert_raw_records(
            db, source_file_id=SHA, payloads=payloads, parser_id="p", parser_version="1"
        )
    with transaction(db):
        second = insert_raw_records(
            db, source_file_id=SHA, payloads=payloads, parser_id="p", parser_version="1"
        )

    assert (first, second) == (3, 0)
    assert db.execute("SELECT COUNT(*) FROM raw_record").fetchone()[0] == 3
    assert db.execute("SELECT id FROM raw_record ORDER BY id").fetchone()[0] == f"{SHA}:00000"


# ---------------------------------------------------------------------------
# the acceptance criterion
# ---------------------------------------------------------------------------


def test_ingesting_the_same_batch_three_times_writes_nothing_new(db: sqlite3.Connection) -> None:
    entries = [
        make_entry(0, amount_minor=250_000, descriptor="PAYROLL ACME CORP"),
        make_entry(1, amount_minor=-4_75, descriptor="CARD PURCHASE COFFEE"),
        make_entry(2, amount_minor=-120_00, descriptor="ELECTRONIC PAYMENT UTILITY"),
    ]

    first = ingest(db, entries)
    assert (first.txns, first.postings, first.identities) == (3, 6, 3)
    assert first.skipped_duplicates == 0
    after_first = row_counts(db)

    for _ in range(2):
        again = ingest(db, entries)
        assert again.skipped_duplicates == len(entries)
        assert (again.txns, again.postings, again.identities) == (0, 0, 0)
        assert row_counts(db) == after_first

    assert after_first["txn"] == 3
    assert after_first["posting"] == 6
    assert after_first["txn_identity"] == 3
    assert after_first["raw_record"] == 3
    assert after_first["source_file"] == 1


def test_repeated_amounts_on_one_day_are_two_transactions(db: sqlite3.Connection) -> None:
    """Two identical $4.75 coffees are not a duplicate — occurrence_index says so.

    This is the test that stops idempotency from being bought by silently
    dropping real rows.
    """
    entries = [
        make_entry(0, amount_minor=-4_75, descriptor="CARD PURCHASE COFFEE", occurrence_index=0),
        make_entry(1, amount_minor=-4_75, descriptor="CARD PURCHASE COFFEE", occurrence_index=1),
    ]
    counts = ingest(db, entries)

    assert counts.skipped_duplicates == 0
    assert counts.txns == 2
    assert db.execute("SELECT COUNT(*) FROM txn").fetchone()[0] == 2
    assert [
        row[0] for row in db.execute("SELECT occurrence_index FROM txn_identity ORDER BY 1")
    ] == [0, 1]
    assert ledger_totals(db)["outflow_minor"] == -9_50

    # And re-ingesting them is still a no-op.
    before = row_counts(db)
    assert ingest(db, entries).skipped_duplicates == 2
    assert row_counts(db) == before


def test_a_different_statement_adds_rows(db: sqlite3.Connection) -> None:
    """Idempotency must not degenerate into "never writes anything twice"."""
    ingest(db, [make_entry(0, date="2025-06-02", amount_minor=-4_75)])
    counts = ingest(
        db,
        [make_entry(0, date="2025-07-02", amount_minor=-4_75)],
        sha256=OTHER_SHA,
    )
    assert counts.txns == 1
    assert db.execute("SELECT COUNT(*) FROM txn").fetchone()[0] == 2
    assert db.execute("SELECT COUNT(*) FROM source_file").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# integrity the database itself enforces
# ---------------------------------------------------------------------------


def test_a_posting_to_an_unknown_account_is_refused(db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        ingest(db, [make_entry(0, bank_account="assets:no-such-account")])
    assert row_counts(db)["posting"] == 0


def test_a_float_amount_is_refused_before_it_reaches_sqlite(db: sqlite3.Connection) -> None:
    """STRICT would launder 100.0 into 100; the boundary check is in repo."""
    entry = make_entry(0)
    broken = FakeEntry(
        txn_id=entry.txn_id,
        date=entry.date,
        payee=entry.payee,
        narration=entry.narration,
        record_index=entry.record_index,
        postings=(
            FakePosting("p:0", 0, BANK, 100.0, "USD"),  # type: ignore[arg-type]
            FakePosting("p:1", 1, INCOME, -100, "USD"),
        ),
        identity=entry.identity,
    )
    with pytest.raises(TypeError, match="minor units"):
        ingest(db, [broken])
    assert row_counts(db)["posting"] == 0


def test_a_failure_mid_batch_rolls_the_whole_statement_back(db: sqlite3.Connection) -> None:
    """One PDF goes in whole or not at all — the transaction is the caller's."""
    ingest(db, [make_entry(0, amount_minor=1_000)])
    before = row_counts(db)

    with pytest.raises(RuntimeError, match="boom"), transaction(db):
        insert_raw_records(
            db,
            source_file_id=SHA,
            payloads=[(1, "stmttrn", "{}"), (2, "stmttrn", "{}")],
            parser_id="p",
            parser_version="1",
        )
        insert_entries(
            db,
            source_file_id=SHA,
            entries=[make_entry(1, amount_minor=2_000), make_entry(2, amount_minor=3_000)],
        )
        raise RuntimeError("boom")

    assert row_counts(db) == before


# ---------------------------------------------------------------------------
# balance assertions
# ---------------------------------------------------------------------------


def _assertion(amount_minor: int, *, as_of: str = "2025-06-03") -> FakeAssertion:
    return FakeAssertion(
        id=f"ba-{BANK}-{as_of}",
        account_id=BANK,
        as_of=as_of,
        commodity_id="USD",
        amount_minor=amount_minor,
    )


def test_an_identical_balance_assertion_is_a_no_op(db: sqlite3.Connection) -> None:
    entries = [make_entry(0)]
    ingest(db, entries, assertions=[_assertion(543_86)])
    before = row_counts(db)
    ingest(db, entries, assertions=[_assertion(543_86)])

    assert row_counts(db) == before
    assert db.execute("SELECT COUNT(*) FROM balance_assertion").fetchone()[0] == 1


def test_a_contradictory_balance_assertion_stops_the_ingest(db: sqlite3.Connection) -> None:
    entries = [make_entry(0)]
    ingest(db, entries, assertions=[_assertion(543_86)])

    with pytest.raises(BalanceAssertionConflict) as excinfo:
        ingest(db, entries, assertions=[_assertion(999_99)])

    assert excinfo.value.existing_minor == 543_86
    assert excinfo.value.incoming_minor == 999_99
    # The stored fact survives, and the failed ingest wrote nothing.
    assert db.execute("SELECT amount_minor FROM balance_assertion").fetchone()[0] == 543_86
    assert db.execute("SELECT COUNT(*) FROM balance_assertion").fetchone()[0] == 1


def test_a_second_date_is_a_second_assertion(db: sqlite3.Connection) -> None:
    ingest(db, [make_entry(0)], assertions=[_assertion(100_00, as_of="2025-06-03")])
    ingest(db, [make_entry(0)], assertions=[_assertion(200_00, as_of="2025-07-03")])
    assert db.execute("SELECT COUNT(*) FROM balance_assertion").fetchone()[0] == 2


# ---------------------------------------------------------------------------
# review items
# ---------------------------------------------------------------------------


def _item(check_id: str, severity: str = "block") -> FakeReviewItem:
    return FakeReviewItem(
        id=f"ri-{check_id}",
        source_file_id=SHA,
        severity=severity,
        check_id=check_id,
        detail='{"message": "nope"}',
    )


def test_review_items_do_not_accumulate_across_ingests(db: sqlite3.Connection) -> None:
    entries = [make_entry(0)]
    items = [_item("balance_chain"), _item("transaction_count", "warn")]

    ingest(db, entries, items=items)
    ingest(db, entries, items=items)
    ingest(db, entries, items=items)

    assert db.execute("SELECT COUNT(*) FROM review_item").fetchone()[0] == 2


def test_a_check_that_now_passes_leaves_the_queue(db: sqlite3.Connection) -> None:
    entries = [make_entry(0)]
    ingest(db, entries, items=[_item("balance_chain"), _item("period_totals")])
    ingest(db, entries, items=[_item("period_totals")])

    assert [row[0] for row in db.execute("SELECT check_id FROM review_item")] == ["period_totals"]


def test_a_dismissed_item_is_not_resurrected(db: sqlite3.Connection) -> None:
    entries = [make_entry(0)]
    items = [_item("balance_chain"), _item("transaction_count", "warn")]
    ingest(db, entries, items=items)

    with transaction(db):
        db.execute("UPDATE review_item SET status = 'dismissed' WHERE check_id = 'balance_chain'")

    ingest(db, entries, items=items)

    statuses = dict(db.execute("SELECT check_id, status FROM review_item").fetchall())
    assert statuses == {"balance_chain": "dismissed", "transaction_count": "open"}


def test_review_items_belonging_to_another_file_are_refused(db: sqlite3.Connection) -> None:
    stray = FakeReviewItem(
        id="ri-stray",
        source_file_id=OTHER_SHA,
        severity="block",
        check_id="balance_chain",
        detail="{}",
    )
    with pytest.raises(ValueError, match="belongs to"):
        ingest(db, [make_entry(0)], items=[stray])
    assert row_counts(db)["review_item"] == 0


# ---------------------------------------------------------------------------
# category — the mirror of the rules file, and the one write path
# ---------------------------------------------------------------------------

DINING = ("dining", None, "expense")
SALARY = ("salary", None, "income")


def test_ensure_categories_creates_each_id_exactly_once(db: sqlite3.Connection) -> None:
    with transaction(db):
        first = ensure_categories(db, rows=[DINING, SALARY])
    with transaction(db):
        second = ensure_categories(db, rows=[DINING, SALARY])

    assert (first, second) == (2, 0)
    assert row_counts(db)["category"] == 2


def test_ensure_categories_refuses_a_category_that_changed_sides(
    db: sqlite3.Connection,
) -> None:
    """Postings already point at it; flipping the kind moves history silently."""
    with transaction(db):
        ensure_categories(db, rows=[DINING])

    with pytest.raises(CategoryKindConflict, match="dining"), transaction(db):
        ensure_categories(db, rows=[("dining", None, "income")])

    assert db.execute("SELECT kind FROM category WHERE id='dining'").fetchone()[0] == "expense"


def test_set_posting_categories_counts_only_the_rows_it_changed(
    db: sqlite3.Connection,
) -> None:
    """The count is what tells an operator whether editing a rule did anything."""
    entry = make_entry(0, amount_minor=-1200, descriptor="CHIPOTLE 0001")
    ingest(db, [entry])
    bank_leg = entry.postings[0].id

    with transaction(db):
        ensure_categories(db, rows=[DINING])
        first = set_posting_categories(db, assignments={bank_leg: "dining"})
    with transaction(db):
        second = set_posting_categories(db, assignments={bank_leg: "dining"})

    assert (first, second) == (1, 0)
    stored = db.execute("SELECT category_id FROM posting WHERE id = ?", (bank_leg,)).fetchone()
    assert stored[0] == "dining"


def test_set_posting_categories_can_clear_one_back_to_null(db: sqlite3.Connection) -> None:
    """NULL is a real answer -- "no rule claimed this" -- not a missing value."""
    entry = make_entry(0, amount_minor=-1200, descriptor="CHIPOTLE 0001")
    ingest(db, [entry])
    bank_leg = entry.postings[0].id

    with transaction(db):
        ensure_categories(db, rows=[DINING])
        set_posting_categories(db, assignments={bank_leg: "dining"})
    with transaction(db):
        cleared = set_posting_categories(db, assignments={bank_leg: None})

    assert cleared == 1
    stored = db.execute("SELECT category_id FROM posting WHERE id = ?", (bank_leg,)).fetchone()
    assert stored[0] is None


def test_set_posting_categories_raises_rather_than_updating_nothing(
    db: sqlite3.Connection,
) -> None:
    """An UPDATE matching no rows is a re-categorisation that reports success."""
    ingest(db, [make_entry(0)])
    with pytest.raises(LookupError, match="no posting"), transaction(db):
        set_posting_categories(db, assignments={"not-a-posting": None})


def test_categorized_rows_reads_the_bank_leg_and_the_verbatim_descriptor(
    db: sqlite3.Connection,
) -> None:
    """Same leg and same column the ingest path classifies, or the two drift."""
    entry = make_entry(0, amount_minor=-1200, descriptor="CHIPOTLE 0001")
    ingest(db, [entry])

    rows = categorized_rows(db)

    assert len(rows) == 1
    assert rows[0]["posting_id"] == entry.postings[0].id
    assert rows[0]["amount_minor"] == -1200
    assert rows[0]["raw_descriptor"] == "CHIPOTLE 0001"
    assert rows[0]["rule_category_id"] is None


# ---------------------------------------------------------------------------
# category_override — the user's manual decision
#
# The mechanism is one sentence: "this transaction's category is X". Overriding
# to a category whose kind is 'transfer' says "this *is* a transfer"; overriding
# to any income or expense category says "it is *not* a transfer, it is X".
# Both directions are exercised below, because a mechanism that has only ever
# been used one way has only been tested one way.
#
# The literal "transfer" is written out here rather than imported from
# analytics.categorize.TRANSFER_CATEGORY_ID on purpose: it is a frozen contract
# between the two, and a test that imports the producer stops testing that the
# contract holds and starts assuming it.
# ---------------------------------------------------------------------------

TRANSFER = ("transfer", None, "transfer")
WHEN = "2026-01-01T00:00:00+00:00"
LATER = "2026-02-02T00:00:00+00:00"


def _booked(
    db: sqlite3.Connection,
    *,
    amount_minor: int = 10_000,
    descriptor: str = "CARD PURCHASE COFFEE",
) -> str:
    """One booked transaction plus the two categories these tests override to."""
    entry = make_entry(0, amount_minor=amount_minor, descriptor=descriptor)
    ingest(db, [entry])
    with transaction(db):
        ensure_categories(db, rows=[DINING, TRANSFER])
    return entry.txn_id


def test_an_override_is_readable_after_it_is_set(db: sqlite3.Connection) -> None:
    """The user marks one line a transfer, and the ledger remembers which line."""
    txn_id = _booked(db, amount_minor=-250_00, descriptor="ONLINE TRANSFER TO SAV")

    with transaction(db):
        changed = set_category_override(
            db, txn_id=txn_id, category_id="transfer", created_at=WHEN
        )

    assert changed is True
    row = get_category_override(db, txn_id)
    assert row is not None
    assert row["txn_id"] == txn_id
    assert row["category_id"] == "transfer"
    assert row["category_kind"] == "transfer"
    assert row["created_at"] == WHEN
    assert row_counts(db)["category_override"] == 1


def test_an_override_to_an_expense_category_is_the_same_mechanism(
    db: sqlite3.Connection,
) -> None:
    """"Not a transfer" is said by naming what it is instead — no sentinel value.

    The negative direction of the same feature: a rule that wrongly called this
    a transfer is overruled by the user saying "it is dining".
    """
    txn_id = _booked(db, amount_minor=-12_00, descriptor="CHIPOTLE 0001")

    with transaction(db):
        set_category_override(db, txn_id=txn_id, category_id="dining", created_at=WHEN)

    row = get_category_override(db, txn_id)
    assert row is not None
    assert (row["category_id"], row["category_kind"]) == ("dining", "expense")


def test_setting_the_same_override_twice_reports_no_change(db: sqlite3.Connection) -> None:
    """False is the operator's only evidence that the second click did nothing."""
    txn_id = _booked(db)

    with transaction(db):
        first = set_category_override(db, txn_id=txn_id, category_id="transfer", created_at=WHEN)
    with transaction(db):
        second = set_category_override(db, txn_id=txn_id, category_id="transfer", created_at=LATER)

    assert (first, second) == (True, False)
    row = get_category_override(db, txn_id)
    assert row is not None
    # Nothing was decided the second time, so nothing — including the timestamp
    # of the decision — moved.
    assert row["created_at"] == WHEN
    assert row_counts(db)["category_override"] == 1


def test_changing_the_override_to_another_category_reports_the_change(
    db: sqlite3.Connection,
) -> None:
    """One row per transaction, and the later decision is the one in force."""
    txn_id = _booked(db)

    with transaction(db):
        set_category_override(db, txn_id=txn_id, category_id="transfer", created_at=WHEN)
    with transaction(db):
        changed = set_category_override(db, txn_id=txn_id, category_id="dining", created_at=LATER)

    assert changed is True
    row = get_category_override(db, txn_id)
    assert row is not None
    assert row["category_id"] == "dining"
    assert row["created_at"] == LATER, "the timestamp dates the decision in force"
    assert row_counts(db)["category_override"] == 1


def test_clearing_an_override_hands_the_answer_back_to_the_rules(
    db: sqlite3.Connection,
) -> None:
    txn_id = _booked(db)
    with transaction(db):
        set_category_override(db, txn_id=txn_id, category_id="transfer", created_at=WHEN)

    with transaction(db):
        cleared = clear_category_override(db, txn_id=txn_id)
    with transaction(db):
        again = clear_category_override(db, txn_id=txn_id)

    assert (cleared, again) == (True, False)
    assert get_category_override(db, txn_id) is None
    assert row_counts(db)["category_override"] == 0


def test_clearing_an_override_that_was_never_set_is_false_not_an_error(
    db: sqlite3.Connection,
) -> None:
    """Unlike setting one: the intended state is reached either way."""
    txn_id = _booked(db)
    with transaction(db):
        assert clear_category_override(db, txn_id=txn_id) is False
        assert clear_category_override(db, txn_id="not-a-txn") is False


def test_an_override_on_an_unknown_txn_raises_rather_than_doing_nothing(
    db: sqlite3.Connection,
) -> None:
    """A decision that vanishes silently is worse than one that fails loudly."""
    _booked(db)

    with pytest.raises(LookupError, match="no txn"), transaction(db):
        set_category_override(db, txn_id="not-a-txn", category_id="transfer")

    assert row_counts(db)["category_override"] == 0


def test_an_override_to_an_unknown_category_is_refused_by_the_foreign_key(
    db: sqlite3.Connection,
) -> None:
    """Asserted, not assumed: the FK is what the LookupError above defers to.

    And it is distinguishable from the unknown-transaction case, which is the
    whole reason that one is raised in Python.
    """
    txn_id = _booked(db)

    with pytest.raises(sqlite3.IntegrityError), transaction(db):
        set_category_override(db, txn_id=txn_id, category_id="no-such-category")

    assert row_counts(db)["category_override"] == 0
    assert get_category_override(db, txn_id) is None


def test_an_override_writes_the_override_and_the_rule_it_teaches(
    db: sqlite3.Connection,
) -> None:
    """It records a decision and what the decision taught -- and nothing else.

    A writer that also flipped ``txn.is_transfer`` would be a second definition
    of what counts as a transfer (STATUS §5.29), and it is the copy that goes
    stale. The learned rule is not such a copy: it is the same decision at the
    template grain, which is why it is written in the same transaction.
    """
    txn_id = _booked(db, amount_minor=-250_00, descriptor="ONLINE TRANSFER TO SAV")
    before = row_counts(db)

    with transaction(db):
        set_category_override(db, txn_id=txn_id, category_id="transfer", created_at=WHEN)

    assert db.execute("SELECT is_transfer FROM txn WHERE id = ?", (txn_id,)).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM posting WHERE txn_id = ? AND category_id IS NOT NULL", (txn_id,)
    ).fetchone()[0] == 0
    assert row_counts(db) == {**before, "category_override": 1, "learned_rule": 1}


def test_list_category_overrides_is_ordered_by_content_not_by_when_it_was_clicked(
    db: sqlite3.Connection,
) -> None:
    """Deterministic ordering: a content hash, never the order the user clicked."""
    entries = [
        make_entry(0, amount_minor=250_000, descriptor="PAYROLL ACME CORP"),
        make_entry(1, amount_minor=-4_75, descriptor="CARD PURCHASE COFFEE"),
        make_entry(2, amount_minor=-120_00, descriptor="ELECTRONIC PAYMENT UTILITY"),
    ]
    ingest(db, entries)
    written = [e.txn_id for e in entries]

    with transaction(db):
        ensure_categories(db, rows=[DINING, TRANSFER])
        for offset, txn_id in enumerate(written):
            set_category_override(
                db, txn_id=txn_id, category_id="transfer", created_at=f"2026-01-0{offset + 1}"
            )

    listed = [row["txn_id"] for row in list_category_overrides(db)]

    assert listed == sorted(written)
    assert listed != written, "otherwise this test would pass on insertion order alone"
    assert len(listed) == 3


def test_list_category_overrides_is_empty_on_a_ledger_nobody_has_corrected(
    db: sqlite3.Connection,
) -> None:
    """The other half: the list is user data, so it starts out with none.

    This is also why a rebuilt ledger is empty here — nothing in ``archive/``
    can produce a row in this table.
    """
    _booked(db)
    assert list_category_overrides(db) == []


# ---------------------------------------------------------------------------
# the raw column and the effective one (P2 M4, migration 0006)
#
# ``v_transaction.category_id`` became the *effective* category, so
# ``categorized_rows`` grew ``rule_category_id`` to keep reaching the raw
# ``posting.category_id`` — the same exception ``rule_is_transfer`` already was
# (§5.51), and per that section's own note the two are now the only two reads in
# ``src/`` that go past the views for a raw column.
#
# The view side of this lives in ``tests/test_transactions.py``. What is here is
# the half that belongs to this module: the one function that must *not* follow
# the substitution.
# ---------------------------------------------------------------------------

GROCERIES = ("groceries", None, "expense")


def test_categorized_rows_reports_the_rules_answer_while_the_view_reports_the_effective_one(
    db: sqlite3.Connection,
) -> None:
    """One ledger, two questions, and only one of them is about the rules.

    ``categorized_rows`` feeds ``reapply-rules``, which is about to ask "would
    the rules answer differently now". Reading the effective value there would
    count a person's override as a row the rules want to move, and ``--dry-run``
    would then promise a number the run that follows does not produce (§5.51).

    Both directions are here in one test: before anybody overrules anything the
    two answers are the same value, which is why swapping one for the other went
    unnoticed for a milestone.
    """
    entry = make_entry(0, amount_minor=-12_00, descriptor="CHIPOTLE 0001")
    ingest(db, [entry])
    with transaction(db):
        ensure_categories(db, rows=[DINING, GROCERIES])
        set_posting_categories(db, assignments={entry.postings[0].id: "dining"})

    before_view = db.execute(
        "SELECT category_id, category_decided_by FROM v_transaction"
    ).fetchone()
    assert categorized_rows(db)[0]["rule_category_id"] == "dining"
    assert (before_view["category_id"], before_view["category_decided_by"]) == ("dining", "rule")

    with transaction(db):
        set_category_override(db, txn_id=entry.txn_id, category_id="groceries", created_at=WHEN)

    rows = categorized_rows(db)
    assert len(rows) == 1
    assert rows[0]["rule_category_id"] == "dining", "the rules' own previous answer"
    assert rows[0]["rule_is_transfer"] == 0, "and the other raw column, for the same reason"

    after_view = db.execute(
        "SELECT category_id, category_decided_by FROM v_transaction"
    ).fetchone()
    assert (after_view["category_id"], after_view["category_decided_by"]) == (
        "groceries",
        "override",
    )
    assert rows[0]["rule_category_id"] != after_view["category_id"]


# ---------------------------------------------------------------------------
# the categories a person is offered
# ---------------------------------------------------------------------------


def test_list_categories_reports_the_arrangement_a_user_made(db: sqlite3.Connection) -> None:
    """It selects ``parent_id`` too, and ``ensure_categories`` never overwrites it.

    ``tests/test_transactions.py`` asserts that this function mirrors the rules
    file, whose rows always carry ``parent_id`` NULL. That leaves the third
    column it selects unexercised, and the rule it depends on — an id already
    present keeps its arrangement, because a user's arrangement is theirs —
    untested from this end.
    """
    assert list_categories(db) == [], "nothing before the first statement"

    with transaction(db):
        ensure_categories(db, rows=[DINING, GROCERIES, SALARY])
    with transaction(db):  # the user files one under the other
        db.execute("UPDATE category SET parent_id = 'groceries' WHERE id = 'dining'")
    with transaction(db):  # the next statement arrives
        assert ensure_categories(db, rows=[DINING, GROCERIES, SALARY]) == 0

    listed = [(row["id"], row["kind"], row["parent_id"]) for row in list_categories(db)]
    assert listed == [
        ("dining", "expense", "groceries"),
        ("groceries", "expense", None),
        ("salary", "income", None),
    ]
    assert category_exists(db, "dining") is True
    assert category_exists(db, "no-such-category") is False


# ---------------------------------------------------------------------------
# totals
# ---------------------------------------------------------------------------


def test_ledger_totals_measure_the_income_and_expense_legs(db: sqlite3.Connection) -> None:
    """Counting both legs would report $0.00 forever, and look consistent doing it."""
    ingest(
        db,
        [
            make_entry(0, amount_minor=250_000, descriptor="PAYROLL ACME CORP"),
            make_entry(1, amount_minor=-4_75, descriptor="CARD PURCHASE COFFEE"),
            make_entry(2, amount_minor=-120_00, descriptor="ELECTRONIC PAYMENT UTILITY"),
        ],
    )

    totals = ledger_totals(db)
    assert totals["inflow_minor"] == 250_000
    assert totals["outflow_minor"] == -124_75
    assert totals["net_minor"] == 250_000 - 124_75
    assert totals["txn_count"] == 3
    assert all(isinstance(v, int) for v in totals.values())
    # Every posting summed would be zero — that is what double entry means.
    assert db.execute("SELECT SUM(amount_minor) FROM posting").fetchone()[0] == 0
    assert totals["net_minor"] != 0


def test_an_opening_balance_moves_the_balance_but_is_not_income(
    db: sqlite3.Connection,
) -> None:
    """One of the two reasons income is measured on the income leg.

    An opening balance moves the bank account and earns nothing. Measured on
    the bank leg it would read as a $500 payday.
    """
    ingest(db, [make_entry(0, amount_minor=250_00, descriptor="PAYROLL ACME CORP")])
    with transaction(db):
        db.execute(
            "INSERT INTO balance_assertion "
            "(id, account_id, as_of, commodity_id, amount_minor) "
            "VALUES ('ba1', ?, '2024-12-31', 'USD', 50000)",
            (BANK,),
        )
        sync_opening_entry(db, account_id=BANK, currency="USD")

    totals = ledger_totals(db)
    assert totals["inflow_minor"] == 250_00, "an opening balance is not income"
    assert totals["balance_minor"] == 50000 + 250_00, "but it is part of the balance"


def test_a_transfer_moves_the_balance_but_is_not_income(db: sqlite3.Connection) -> None:
    """The other reason. P1 marks transfers; these totals are already immune."""
    ingest(db, [make_entry(0, amount_minor=250_00, descriptor="PAYROLL ACME CORP")])
    before = ledger_totals(db)
    with transaction(db):
        db.execute("UPDATE txn SET is_transfer = 1")
    after = ledger_totals(db)

    assert before["inflow_minor"] == 250_00
    assert after["inflow_minor"] == 0
    assert after["transfer_count"] == 1
    assert after["balance_minor"] == before["balance_minor"], "a transfer still moved money"


def test_the_opening_entry_is_derived_not_taken_from_whoever_arrived_first(
    db: sqlite3.Connection,
) -> None:
    """It must depend on the earliest assertion, never on ingest order."""
    with transaction(db):
        ensure_account(
            db, account_id=BANK, name="Assets:Chase:Checking:1234", kind="asset",
            subtype="checking", currency="USD", institution="Chase", mask="1234",
        )
        db.execute(
            "INSERT INTO balance_assertion (id, account_id, as_of, commodity_id, amount_minor) "
            "VALUES ('late', ?, '2025-01-31', 'USD', 90332)",
            (BANK,),
        )
        first = sync_opening_entry(db, account_id=BANK, currency="USD")

    assert db.execute("SELECT date FROM txn WHERE id = ?", (first,)).fetchone()[0] == "2025-01-31"

    # An older statement arrives later: the account's opening event moved.
    with transaction(db):
        db.execute(
            "INSERT INTO balance_assertion (id, account_id, as_of, commodity_id, amount_minor) "
            "VALUES ('early', ?, '2024-12-31', 'USD', 51237)",
            (BANK,),
        )
        second = sync_opening_entry(db, account_id=BANK, currency="USD")

    assert second != first
    assert db.execute("SELECT COUNT(*) FROM txn WHERE id = ?", (first,)).fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM posting WHERE txn_id = ?", (first,)).fetchone()[0] == 0
    assert ledger_totals(db)["balance_minor"] == 51237

    # Idempotent: running it again changes nothing.
    counts = row_counts(db)
    with transaction(db):
        assert sync_opening_entry(db, account_id=BANK, currency="USD") == second
    assert row_counts(db) == counts


def test_ledger_totals_on_an_empty_ledger(db: sqlite3.Connection) -> None:
    """Whole-dict equality on purpose: a new key has to be noticed here.

    `TotalsOut` is built by splatting this mapping, so a field added on one
    side and not the other is a runtime error in the API rather than a missing
    number on a page. Comparing key by key would let the two drift.
    """
    assert ledger_totals(db) == {
        "inflow_minor": 0,
        "outflow_minor": 0,
        "net_minor": 0,
        "txn_count": 0,
        # Not 0, and the difference is the whole point of the field's type. The
        # three sums above are measurements over an empty set and are truthfully
        # zero -- nothing came in, because nothing is here. A balance is a
        # position, and this ledger has no evidence of one; printing $0.00 would
        # assert that an account held nothing on a day nothing was recorded
        # about, which `/api/health` already refuses to do by sending a null
        # `totals` rather than a zeroed one.
        "balance_minor": None,
        "transfer_count": 0,
        "transfer_excluded_in_minor": 0,
        "transfer_excluded_out_minor": 0,
    }


# ---------------------------------------------------------------------------
# deletion (P2 M3)
#
# The unit-level half. `tests/test_forget.py` drives these through
# `ingest.forget` over ledgers built from parsed statements; here they are
# asked directly, with periods chosen so that each rule is the only thing that
# could produce the answer.
#
# `_assertion_heirs` is exercised through its two public callers rather than
# imported: it is the shared decision behind "what the operator is told will
# happen" and "what happens", and a test of the private function would pass
# even if one of the two stopped calling it.
# ---------------------------------------------------------------------------

THIRD_SHA = "c" * 64

#: Two consecutive statements. The seam is 2025-06-03: May's closing balance is
#: June's opening balance, one row, printed by both (docs/STATUS.md §5.7).
MAY = ("2025-05-04", "2025-06-03")
JUNE = ("2025-06-04", "2025-07-03")
SEAM = "2025-06-03"


def _two_consecutive_statements(db: sqlite3.Connection) -> tuple[str, str]:
    """May then June. Returns their transaction ids, in that order."""
    may = make_entry(0, date="2025-05-20", amount_minor=-25_00)
    june = make_entry(0, date="2025-06-20", amount_minor=-10_00, descriptor="CARD PURCHASE TEA")
    ingest(
        db,
        [may],
        assertions=[_assertion(100_00, as_of="2025-05-03"), _assertion(75_00, as_of=SEAM)],
        period_start=MAY[0],
        period_end=MAY[1],
    )
    ingest(
        db,
        [june],
        assertions=[_assertion(75_00, as_of=SEAM), _assertion(65_00, as_of="2025-07-03")],
        sha256=OTHER_SHA,
        period_start=JUNE[0],
        period_end=JUNE[1],
    )
    return may.txn_id, june.txn_id


def _owner(db: sqlite3.Connection, as_of: str) -> str | None:
    row = db.execute(
        "SELECT source_file_id FROM balance_assertion WHERE as_of = ?", (as_of,)
    ).fetchone()
    return None if row is None else row["source_file_id"]


def test_delete_statement_removes_everything_that_file_brought(db: sqlite3.Connection) -> None:
    """One statement in, one statement out, and the reference rows stay.

    ``account`` and ``category`` are created at ingest and are idempotent
    mirrors of things outside the ledger (docs/STATUS.md §5.37) — an account row
    with no postings left is not wrong, and deleting it would be this function
    deciding something about a table no statement owns.
    """
    baseline = row_counts(db)
    entry = make_entry(0, amount_minor=-12_00, descriptor="CHIPOTLE 0001")
    ingest(
        db,
        [entry],
        assertions=[_assertion(75_00, as_of=SEAM)],
        items=[_item("balance_chain")],
    )
    with transaction(db):
        ensure_categories(db, rows=[DINING])
        set_category_override(db, txn_id=entry.txn_id, category_id="dining")

    with transaction(db):
        counts = delete_statement(db, SHA)

    after = row_counts(db)
    for table in (
        "source_file",
        "raw_record",
        "txn",
        "posting",
        "txn_identity",
        "balance_assertion",
        "review_item",
        "category_override",
    ):
        assert after[table] == baseline[table] == 0, f"{table} still holds rows"
    assert after["account"] > baseline["account"], "the account row is reference data and stays"
    assert after["category"] == 1, "so is the rules file's mirror"

    assert counts.txns == 1
    assert counts.postings == 2
    assert counts.identities == 1
    assert counts.raw_records == 1
    assert counts.review_items == 1
    assert counts.category_overrides == 1
    assert counts.balance_assertions_removed == 1
    assert counts.balance_assertions_reassigned == 0
    assert counts.opening_txn_ids == (), "nothing is left to derive an opening entry from"


def test_delete_statement_leaves_the_other_statement_alone(db: sqlite3.Connection) -> None:
    may_txn, june_txn = _two_consecutive_statements(db)

    with transaction(db):
        counts = delete_statement(db, SHA)

    assert [row[0] for row in db.execute("SELECT id FROM source_file")] == [OTHER_SHA]
    booked = {row[0] for row in db.execute("SELECT id FROM txn")}
    assert june_txn in booked
    assert may_txn not in booked
    assert db.execute(
        "SELECT COUNT(*) FROM raw_record WHERE source_file_id = ?", (OTHER_SHA,)
    ).fetchone()[0] == 1
    # And an opening entry has appeared, because the account's earliest
    # surviving assertion is now the seam rather than May's opening figure.
    assert counts.opening_txn_ids and booked == {june_txn, counts.opening_txn_ids[0]}
    assert db.execute("SELECT date FROM txn WHERE id = ?", (counts.opening_txn_ids[0],)).fetchone()[
        0
    ] == SEAM


def test_a_dismissed_review_item_is_deleted_with_its_statement(db: sqlite3.Connection) -> None:
    """``replace_review_items`` deliberately never touches a resolved item.

    Deletion is the one operation that must: the item has a foreign key to a
    ``source_file`` row that is going away, and a decision about a statement
    nobody has any more is not a decision about anything.
    """
    ingest(db, [make_entry(0)], items=[_item("balance_chain"), _item("period_totals")])
    with transaction(db):
        db.execute("UPDATE review_item SET status = 'dismissed' WHERE check_id = 'period_totals'")

    with transaction(db):
        counts = delete_statement(db, SHA)

    assert counts.review_items == 2
    assert row_counts(db)["review_item"] == 0


def test_deleting_a_statement_that_owns_no_assertion_removes_none(
    db: sqlite3.Connection,
) -> None:
    """The zero case, which is what a refused statement looks like."""
    ingest(db, [], items=[_item("unknown_layout")], period_start=None, period_end=None)

    with transaction(db):
        counts = delete_statement(db, SHA)

    assert (counts.balance_assertions_removed, counts.balance_assertions_reassigned) == (0, 0)
    assert (counts.txns, counts.postings, counts.identities) == (0, 0, 0)
    assert counts.review_items == 1


# --- _assertion_heirs, through statement_deletion_facts and delete_statement ---


def test_the_assertion_on_the_seam_is_kept_and_its_provenance_moves(
    db: sqlite3.Connection,
) -> None:
    """June still prints this balance, so the row must survive the loss of May.

    Ingesting June alone into an empty database would produce exactly this row;
    a deletion that removed it would leave a ledger a rebuild does not agree
    with, which is the one thing deleting has to not do.
    """
    _two_consecutive_statements(db)
    assert _owner(db, SEAM) == SHA, "the statement that closes on the day owns it (§5.7)"

    facts = statement_deletion_facts(db, SHA)
    assert facts.balance_assertions == 2
    assert facts.balance_assertions_shared == 1, "the seam; the other is May's own opening"

    with transaction(db):
        counts = delete_statement(db, SHA)

    assert (counts.balance_assertions_removed, counts.balance_assertions_reassigned) == (1, 1)
    assert _owner(db, SEAM) == OTHER_SHA
    assert db.execute(
        "SELECT amount_minor FROM balance_assertion WHERE as_of = ?", (SEAM,)
    ).fetchone()[0] == 75_00, "the number is a fact the bank printed; only the provenance moved"
    assert _owner(db, "2025-05-03") is None, "nobody else printed that day"


def test_the_count_and_the_deletion_cannot_disagree_about_the_heirs(
    db: sqlite3.Connection,
) -> None:
    """Deleting the *later* statement: its own closing balance has no heir.

    The mirror of the test above, and the reason both callers share one
    implementation — the sentence shown before the deletion and the rows changed
    by it are the same decision, taken once.
    """
    _two_consecutive_statements(db)

    facts = statement_deletion_facts(db, OTHER_SHA)
    assert (facts.balance_assertions, facts.balance_assertions_shared) == (1, 0)

    with transaction(db):
        counts = delete_statement(db, OTHER_SHA)

    assert counts.balance_assertions_removed == facts.balance_assertions
    assert counts.balance_assertions_reassigned == facts.balance_assertions_shared
    assert _owner(db, SEAM) == SHA, "May owned the seam and still does"
    assert _owner(db, "2025-07-03") is None


def test_the_heir_is_the_statement_that_closes_on_the_day_before_the_one_that_opens_after(
    db: sqlite3.Connection,
) -> None:
    """The §5.7 tie-break, asked of the deletion path.

    Two statements could inherit the seam: one whose period *ends* on it and one
    whose period starts the day after. Ownership has to go the same way it went
    when the row was first written, or a rebuild and a deletion would leave two
    different ledgers over the same archive. The rule lives in an ``ORDER BY``,
    which is exactly the clause SQLite will not resolve an outer column in, so
    it is worth watching it choose.
    """
    # Ingested in this order so that the statement being deleted ends up owning
    # the row: `upsert_balance_assertions` hands it to whichever closes on the
    # day, and the last such writer wins.
    ingest(
        db,
        [make_entry(0, date="2025-05-10", amount_minor=-1_00, descriptor="CLOSES ON THE SEAM")],
        assertions=[_assertion(75_00, as_of=SEAM)],
        sha256=OTHER_SHA,
        period_start="2025-05-01",
        period_end=SEAM,
    )
    ingest(
        db,
        [make_entry(0, date="2025-06-20", amount_minor=-2_00, descriptor="OPENS AFTER THE SEAM")],
        assertions=[_assertion(75_00, as_of=SEAM)],
        sha256=THIRD_SHA,
        period_start=JUNE[0],
        period_end=JUNE[1],
    )
    ingest(
        db,
        [make_entry(0, date="2025-05-20", amount_minor=-3_00, descriptor="THE ONE BEING DELETED")],
        assertions=[_assertion(75_00, as_of=SEAM)],
        period_start=MAY[0],
        period_end=SEAM,
    )
    assert _owner(db, SEAM) == SHA

    with transaction(db):
        delete_statement(db, SHA)

    assert _owner(db, SEAM) == OTHER_SHA, "closing on the day beats opening the day after"


# --- overlapping_statements ---


def test_consecutive_statements_do_not_overlap(db: sqlite3.Connection) -> None:
    """The normal case, and the one the real corpus is in."""
    _two_consecutive_statements(db)
    assert overlapping_statements(db, SHA) == []
    assert overlapping_statements(db, OTHER_SHA) == []


def test_two_statements_covering_the_same_days_overlap_both_ways(
    db: sqlite3.Connection,
) -> None:
    ingest(db, [make_entry(0)], period_start=MAY[0], period_end=MAY[1])
    ingest(
        db,
        [make_entry(0, date="2025-05-25", descriptor="CARD PURCHASE TEA")],
        sha256=OTHER_SHA,
        period_start="2025-05-20",
        period_end="2025-06-20",
    )

    assert [row["source_file_id"] for row in overlapping_statements(db, SHA)] == [OTHER_SHA]
    assert [row["source_file_id"] for row in overlapping_statements(db, OTHER_SHA)] == [SHA]


def test_periods_that_share_a_single_day_overlap(db: sqlite3.Connection) -> None:
    """The boundary the comparison is written to include.

    One shared day is one day on which both statements could print the same
    transaction, which is the whole condition. Consecutive statements do not hit
    this: the next period starts the day *after* the previous one ends.
    """
    ingest(db, [make_entry(0)], period_start=MAY[0], period_end=MAY[1])
    ingest(
        db,
        [make_entry(0, date="2025-06-03", descriptor="CARD PURCHASE TEA")],
        sha256=OTHER_SHA,
        period_start=SEAM,
        period_end="2025-07-03",
    )

    assert [row["source_file_id"] for row in overlapping_statements(db, SHA)] == [OTHER_SHA]


def test_a_statement_with_no_period_neither_overlaps_nor_is_overlapped(
    db: sqlite3.Connection,
) -> None:
    """A statement refused before a period could be read has no transactions.

    It cannot share one with anybody, and treating "unknown period" as "overlaps
    everything" would refuse the deletion this milestone exists for.
    """
    ingest(db, [make_entry(0)], period_start=MAY[0], period_end=MAY[1])
    ingest(db, [], sha256=OTHER_SHA, period_start=None, period_end=None)

    assert overlapping_statements(db, OTHER_SHA) == []
    assert overlapping_statements(db, SHA) == [], "and it is invisible from the other side too"


def test_overlapping_statements_of_an_id_that_is_not_there_is_empty(
    db: sqlite3.Connection,
) -> None:
    ingest(db, [make_entry(0)])
    assert overlapping_statements(db, OTHER_SHA) == []


def test_statement_deletion_facts_refuses_an_id_that_is_not_there(
    db: sqlite3.Connection,
) -> None:
    with pytest.raises(LookupError, match="no archived statement"):
        statement_deletion_facts(db, OTHER_SHA)


# --- sync_opening_entry, the arm deletion reaches ---


def test_the_opening_entry_is_removed_when_the_last_assertion_goes(
    db: sqlite3.Connection,
) -> None:
    """The half that could not happen while statements only ever arrived.

    The function used to return early on "no assertions left" and leave whatever
    opening entry was already there: an equity leg asserting a balance no
    document in the ledger claims any more. `balance_assertions` passes (nothing
    left to check), `double_entry` passes (the orphan sums to zero), and
    `balance_minor` reports money that is not there — while re-ingesting the
    remaining archive into an empty database produces no such row.
    """
    with transaction(db):
        ensure_account(
            db, account_id=BANK, name="Assets:Chase:Checking:1234", kind="asset",
            subtype="checking", currency="USD", institution="Chase", mask="1234",
        )
        db.execute(
            "INSERT INTO balance_assertion (id, account_id, as_of, commodity_id, amount_minor) "
            "VALUES ('only', ?, '2025-01-31', 'USD', 90332)",
            (BANK,),
        )
        opening = sync_opening_entry(db, account_id=BANK, currency="USD")

    assert opening is not None
    assert ledger_totals(db)["balance_minor"] == 90332

    with transaction(db):
        db.execute("DELETE FROM balance_assertion")
        gone = sync_opening_entry(db, account_id=BANK, currency="USD")

    assert gone is None
    assert row_counts(db)["txn"] == 0
    assert row_counts(db)["posting"] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM posting WHERE account_id = 'equity:opening-balances'"
    ).fetchone()[0] == 0
    # `None`, not 0: with the last assertion gone the opening entry goes with it
    # and no own-account posting is left, so the ledger has nothing to say about
    # the balance. That is a stronger statement of what this test always meant --
    # §5.56 is about the opposite failure, an opening entry still asserting a
    # balance no surviving document claims.
    assert ledger_totals(db)["balance_minor"] is None


def test_sync_opening_entry_on_an_account_that_never_had_one_writes_nothing(
    db: sqlite3.Connection,
) -> None:
    """The same arm, reached from the other side: nothing to remove, nothing to add."""
    with transaction(db):
        ensure_account(
            db, account_id=BANK, name="Assets:Chase:Checking:1234", kind="asset",
            subtype="checking", currency="USD", institution="Chase", mask="1234",
        )
    before = row_counts(db)

    with transaction(db):
        assert sync_opening_entry(db, account_id=BANK, currency="USD") is None

    assert row_counts(db) == before


def test_a_second_accounts_opening_entry_is_not_disturbed(db: sqlite3.Connection) -> None:
    """The stale-entry sweep is scoped to the account it was asked about.

    It finds opening entries structurally — an equity posting sharing a
    transaction with this account — and ``delete_statement`` calls it once per
    own account. Scoping it wrongly would make the last call undo the others.
    """
    other = "assets:chase:savings:5678"
    with transaction(db):
        for account_id, name in (
            (BANK, "Assets:Chase:Checking:1234"),
            (other, "Assets:Chase:Savings:5678"),
        ):
            ensure_account(
                db, account_id=account_id, name=name, kind="asset", subtype="checking",
                currency="USD", institution="Chase", mask=None,
            )
        for row_id, account_id, amount in (("a", BANK, 100_00), ("b", other, 200_00)):
            db.execute(
                "INSERT INTO balance_assertion "
                "(id, account_id, as_of, commodity_id, amount_minor) "
                "VALUES (?, ?, '2025-01-31', 'USD', ?)",
                (row_id, account_id, amount),
            )
        sync_opening_entry(db, account_id=BANK, currency="USD")
        kept = sync_opening_entry(db, account_id=other, currency="USD")

    assert ledger_totals(db)["balance_minor"] == 300_00

    with transaction(db):
        db.execute("DELETE FROM balance_assertion WHERE account_id = ?", (BANK,))
        assert sync_opening_entry(db, account_id=BANK, currency="USD") is None

    assert db.execute("SELECT COUNT(*) FROM txn WHERE id = ?", (kept,)).fetchone()[0] == 1
    assert ledger_totals(db)["balance_minor"] == 200_00


def test_row_counts_covers_every_table(db: sqlite3.Connection) -> None:
    counts = row_counts(db)
    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert set(counts) == tables
    assert counts["schema_migration"] > 0
    assert counts["account"] == 3  # the seeded counter-accounts
