# SPDX-License-Identifier: AGPL-3.0-or-later
"""M6: single-sided statement rows → balanced double-entry postings.

The tests that matter here are structural, not arithmetic. Every one of them
corresponds to a way the predecessor produced a number that looked fine:
transfers double-counted because nothing forced the two legs to be one
transaction, same-day duplicates collapsed because identity ignored occurrence,
ids that changed between runs so a rebuild could not be compared to anything.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from ledgerbox.ingest.parsers.base import (
    ParsedStatement,
    Provenance,
    StatementSummary,
    StatementTxn,
)
from ledgerbox.ledger.identity import (
    NATURAL_KEY_VERSION,
    account_id_for,
    balance_assertion_id,
    natural_key,
)
from ledgerbox.ledger.posting import (
    BANK_LEG_SEQ,
    COUNTER_LEG_SEQ,
    EXPENSE_ACCOUNT_ID,
    INCOME_ACCOUNT_ID,
    SOURCE_SYSTEM,
    ImbalancedPostingError,
    PostingRow,
    StatementEntries,
    account_name_for,
    build_entries,
    counter_account_for,
)
from ledgerbox.reconcile.checks import PASS, SKIP, check_double_entry

ACCOUNT_ID = "assets:chase:checking:1234"

# The 13-statement corpus, as measured. Hardcoded rather than recomputed from
# the parsed statements: a regression test that derives its expectation from
# the thing under test asserts only that the code agrees with itself.
REAL_TXN_COUNT = 415
# The real corpus's monetary expectations live beside the corpus in the
# untracked expected-totals.json read by the `real_expected` fixture.


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------


def txn(
    *,
    day: int = 2,
    amount: int = -1244,
    description: str = "Card Purchase 01/01 Vendor CA Card 4321",
    balance: int | None = None,
    index: int = 0,
    month: int = 1,
) -> StatementTxn:
    return StatementTxn(
        posted_date=date(2025, month, day),
        description=description,
        amount_minor=amount,
        balance_minor=balance,
        row_index=index,
        provenance=Provenance(page=2, top=100.0, x0=434.8, x1=462.9, bottom=108.0),
    )


def statement(
    transactions: list[StatementTxn],
    *,
    beginning: int = 51237,
    ending: int | None = None,
    period: tuple[date, date] = (date(2025, 1, 1), date(2025, 1, 31)),
    institution: str = "Chase",
    subtype: str = "checking",
    mask: str | None = "1234",
) -> ParsedStatement:
    total = sum(t.amount_minor for t in transactions)
    return ParsedStatement(
        institution=institution,
        account_mask=mask,
        account_subtype=subtype,
        currency="USD",
        period_start=period[0],
        period_end=period[1],
        summary=StatementSummary(
            beginning_balance_minor=beginning,
            ending_balance_minor=beginning + total if ending is None else ending,
            components={"Deposits and Additions": max(total, 0)},
        ),
        transactions=tuple(transactions),
        parser_id="chase_checking",
        parser_version="1",
    )


def all_postings(built: StatementEntries) -> list[tuple[str, int, str]]:
    """Flattened into the ``(txn_id, amount_minor, currency)`` triples the
    reconciler's check 0 consumes, so this module's output is verified by the
    same code that will guard the database."""
    return [
        (entry.txn_id, posting.amount_minor, posting.currency)
        for entry in built.entries
        for posting in entry.postings
    ]


# ---------------------------------------------------------------------------
# zero sum — the reason double entry is here at all
# ---------------------------------------------------------------------------


def test_every_transaction_has_two_postings_summing_to_zero() -> None:
    built = build_entries(
        statement(
            [
                txn(day=2, amount=4800, description="Zelle Payment From A Name 10000000001"),
                txn(day=3, amount=-1244, index=1),
                txn(day=4, amount=-125000, description="Payment To Chase Card 1234", index=2),
            ]
        )
    )
    assert len(built.entries) == 3
    for entry in built.entries:
        assert len(entry.postings) == 2
        assert sum(posting.amount_minor for posting in entry.postings) == 0


def test_reconcile_check_zero_accepts_the_whole_batch() -> None:
    rows = [txn(day=d, amount=(-1 if d % 2 else 1) * d * 137, index=d) for d in range(1, 12)]
    built = build_entries(statement(rows))
    result = check_double_entry(all_postings(built))
    assert result.status == PASS
    assert result.detail["groups"] == len(built.entries)


def test_amounts_are_integers_only() -> None:
    """No float, no Decimal — the one rule that has no exceptions."""
    built = build_entries(statement([txn(amount=4800), txn(amount=-1244, index=1)]))
    for entry in built.entries:
        for posting in entry.postings:
            assert type(posting.amount_minor) is int
    for assertion in built.balance_assertions:
        assert type(assertion.amount_minor) is int


# ---------------------------------------------------------------------------
# which account the other leg lands in
# ---------------------------------------------------------------------------


def test_positive_rows_credit_income_negative_rows_debit_expenses() -> None:
    rows = [txn(day=2, amount=4800, description="Deposit"), txn(day=3, amount=-1244, index=1)]
    built = build_entries(statement(rows))
    deposit, purchase = built.entries

    assert [p.account_id for p in deposit.postings] == [ACCOUNT_ID, INCOME_ACCOUNT_ID]
    assert [p.amount_minor for p in deposit.postings] == [4800, -4800]

    assert [p.account_id for p in purchase.postings] == [ACCOUNT_ID, EXPENSE_ACCOUNT_ID]
    assert [p.amount_minor for p in purchase.postings] == [-1244, 1244]


def test_bank_leg_is_seq_zero_and_keeps_the_statements_sign() -> None:
    built = build_entries(statement([txn(amount=-1244)]))
    bank, counter = built.entries[0].postings
    assert (bank.seq, counter.seq) == (BANK_LEG_SEQ, COUNTER_LEG_SEQ) == (0, 1)
    assert bank.account_id == built.account_id
    assert bank.amount_minor == -1244  # exactly what the statement printed


def test_zero_amount_rows_still_balance() -> None:
    """A $0.00 row is degenerate, not an error; it must not crash or unbalance."""
    built = build_entries(statement([txn(amount=0, description="Reversal")]))
    assert counter_account_for(0) == EXPENSE_ACCOUNT_ID
    assert sum(p.amount_minor for p in built.entries[0].postings) == 0


def test_imbalance_would_raise() -> None:
    """The guard is unreachable through build_entries; prove it is not a no-op."""
    from ledgerbox.ledger.posting import _require_zero_sum

    with pytest.raises(ImbalancedPostingError):
        _require_zero_sum(
            "t",
            (
                PostingRow("t:0", 0, ACCOUNT_ID, 100, "USD"),
                PostingRow("t:1", 1, EXPENSE_ACCOUNT_ID, -99, "USD"),
            ),
        )


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------


def test_two_identical_rows_on_one_day_are_two_transactions() -> None:
    """Two $4.75 coffees, same merchant, same day. Not a duplicate."""
    coffee = {"day": 4, "amount": -475, "description": "Card Purchase 01/04 Starbucks #123"}
    built = build_entries(statement([txn(**coffee), txn(**coffee, index=1)]))

    first, second = built.entries
    assert first.identity.occurrence_index == 0
    assert second.identity.occurrence_index == 1
    assert first.txn_id != second.txn_id
    assert first.postings[0].id != second.postings[0].id


def test_occurrence_numbering_follows_statement_order_and_resets_per_key() -> None:
    rows = [
        txn(day=4, amount=-475, description="STARBUCKS", index=0),
        txn(day=4, amount=-475, description="starbucks  ", index=1),  # same after folding
        txn(day=4, amount=-476, description="STARBUCKS", index=2),
        txn(day=5, amount=-475, description="STARBUCKS", index=3),
    ]
    built = build_entries(statement(rows))
    assert [e.identity.occurrence_index for e in built.entries] == [0, 1, 0, 0]


def test_txn_id_is_the_natural_key_and_postings_derive_from_it() -> None:
    row = txn(day=2, amount=-1244, description="Card Purchase 01/01 Vendor CA Card 4321")
    built = build_entries(statement([row]))
    entry = built.entries[0]

    expected = natural_key(ACCOUNT_ID, "2025-01-02", -1244, row.description, 0)
    assert entry.txn_id == expected == entry.identity.natural_key
    assert [p.id for p in entry.postings] == [f"{expected}:0", f"{expected}:1"]


def test_identity_row_records_the_source_system_and_no_fitid() -> None:
    built = build_entries(statement([txn()]))
    identity = built.entries[0].identity
    assert identity.source_system == SOURCE_SYSTEM == "pdf"
    assert identity.source_id is None  # Chase PDFs carry no FITID
    assert identity.natural_key_version == NATURAL_KEY_VERSION
    assert identity.account_id == built.account_id


def test_raw_descriptor_is_verbatim() -> None:
    """Not upper-cased, not collapsed, not trimmed — folding lives in the key."""
    messy = "  Card Purchase   With Pin 01/01  Ｈouse of Sushi  "
    built = build_entries(statement([txn(description=messy)]))
    entry = built.entries[0]

    assert entry.identity.raw_descriptor == messy
    assert entry.narration == messy
    assert entry.identity.natural_key != messy


def test_payee_is_null_because_p0_does_not_extract_merchants() -> None:
    built = build_entries(statement([txn(description="Card Purchase 01/01 Vendor CA Card 4321")]))
    assert built.entries[0].payee is None


def test_record_index_counts_rows_from_zero() -> None:
    built = build_entries(statement([txn(day=d, amount=-d * 100, index=d - 1) for d in (2, 3, 4)]))
    assert [e.record_index for e in built.entries] == [0, 1, 2]
    assert [e.identity.record_index for e in built.entries] == [0, 1, 2]


def test_dates_are_iso_8601_strings() -> None:
    built = build_entries(statement([txn(day=2), txn(day=15, index=1)]))
    assert [e.date for e in built.entries] == ["2025-01-02", "2025-01-15"]


# ---------------------------------------------------------------------------
# determinism — the rebuild invariant depends on it
# ---------------------------------------------------------------------------


def test_identical_input_produces_byte_identical_ids() -> None:
    parsed = statement(
        [
            txn(day=2, amount=4800, description="Zelle Payment From A Name 10000000001"),
            txn(day=3, amount=-1244, index=1),
            txn(day=3, amount=-1244, index=2),  # occurrence 1 of the same key
        ]
    )
    first = build_entries(parsed)
    second = build_entries(parsed)

    assert first == second  # frozen dataclasses compare field by field
    assert [e.txn_id for e in first.entries] == [e.txn_id for e in second.entries]
    assert [p.id for e in first.entries for p in e.postings] == [
        p.id for e in second.entries for p in e.postings
    ]
    assert [a.id for a in first.balance_assertions] == [a.id for a in second.balance_assertions]


def test_ids_do_not_depend_on_object_identity() -> None:
    """Rebuilt from separate objects with equal content — same ids."""
    rows = [txn(day=2, amount=4800), txn(day=3, amount=-1244, index=1)]
    a = build_entries(statement(rows))
    b = build_entries(statement([replace(row) for row in rows]))
    assert a == b


# ---------------------------------------------------------------------------
# balance assertions
# ---------------------------------------------------------------------------


def test_opening_assertion_is_dated_the_day_before_the_period_starts() -> None:
    built = build_entries(statement([txn(amount=4800)], beginning=51237))
    opening, closing = built.balance_assertions

    assert opening.as_of == "2024-12-31"  # period_start 2025-01-01, minus one day
    assert opening.amount_minor == 51237
    assert closing.as_of == "2025-01-31"
    assert closing.amount_minor == 51237 + 4800
    assert opening.commodity_id == closing.commodity_id == "USD"
    assert opening.account_id == closing.account_id == built.account_id
    assert opening.id == balance_assertion_id(built.account_id, "2024-12-31", "USD")


def test_adjacent_statements_share_one_assertion_at_the_seam() -> None:
    """January's closing and February's opening are the same (account, day).

    The database's UNIQUE(account_id, as_of, commodity_id) turns that into a
    free cross-statement check: two independently parsed PDFs must agree about
    the balance on the day they share.
    """
    january = build_entries(
        statement(
            [txn(day=5, amount=4800)],
            beginning=51237,
            period=(date(2025, 1, 1), date(2025, 1, 31)),
        )
    )
    february = build_entries(
        statement(
            [txn(month=2, day=5, amount=-1244)],
            beginning=51237 + 4800,
            period=(date(2025, 2, 1), date(2025, 2, 28)),
        )
    )

    january_close = january.balance_assertions[1]
    february_open = february.balance_assertions[0]

    assert january_close.as_of == february_open.as_of == "2025-01-31"
    assert january_close.id == february_open.id
    assert january_close.amount_minor == february_open.amount_minor


def test_a_disagreement_at_the_seam_keeps_the_same_id() -> None:
    """Same id, different amount — which is what makes the clash detectable."""
    january = build_entries(statement([], beginning=82015, ending=85726))
    february = build_entries(
        statement([], beginning=1, ending=1, period=(date(2025, 2, 1), date(2025, 2, 28)))
    )
    assert january.balance_assertions[1].id == february.balance_assertions[0].id
    assert january.balance_assertions[1].amount_minor != february.balance_assertions[0].amount_minor


# ---------------------------------------------------------------------------
# account naming
# ---------------------------------------------------------------------------


def test_account_id_and_name_agree() -> None:
    built = build_entries(statement([txn()]))
    assert built.account_id == account_id_for("Chase", "checking", "1234") == ACCOUNT_ID
    assert built.account_name == "Assets:Chase:Checking:1234"
    assert built.institution == "Chase"
    assert built.subtype == "checking"
    assert built.mask == "1234"
    assert built.currency == "USD"


def test_a_missing_mask_drops_the_display_segment_but_not_the_id_segment() -> None:
    built = build_entries(statement([txn()], mask=None))
    assert built.account_id == "assets:chase:checking:default"
    assert built.account_name == "Assets:Chase:Checking"
    assert built.mask is None


def test_names_are_slugged_from_the_same_source_as_ids() -> None:
    assert account_name_for("JPMorgan Chase Bank, N.A.", "checking", "1234").startswith("Assets:")
    assert account_name_for("Chase", "credit_card", "1234") == "Assets:Chase:Credit-Card:1234"


# ---------------------------------------------------------------------------
# degenerate input
# ---------------------------------------------------------------------------


def test_a_statement_with_no_transactions_still_anchors_both_balances() -> None:
    built = build_entries(statement([], beginning=51237, ending=51237))
    assert built.entries == ()
    assert len(built.balance_assertions) == 2
    assert [a.as_of for a in built.balance_assertions] == ["2024-12-31", "2025-01-31"]
    # Nothing to balance is reported as SKIP, never as a silent pass.
    assert check_double_entry(all_postings(built)).status == SKIP


def test_an_empty_description_becomes_null_narration_but_keeps_the_descriptor() -> None:
    built = build_entries(statement([txn(description="")]))
    assert built.entries[0].narration is None
    assert built.entries[0].identity.raw_descriptor == ""


# ---------------------------------------------------------------------------
# the real corpus — skipped, never failed, when the statements are absent
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def real_built(real_parsed: list) -> list[StatementEntries]:
    return [build_entries(parsed) for parsed in real_parsed]


def test_real_corpus_produces_one_transaction_per_row(real_built: list[StatementEntries]) -> None:
    entries = [entry for built in real_built for entry in built.entries]
    postings = [p for entry in entries for p in entry.postings]

    assert len(real_built) == 13
    assert len(entries) == REAL_TXN_COUNT
    assert len(postings) == REAL_TXN_COUNT * 2


def test_real_corpus_transaction_ids_are_unique(real_built: list[StatementEntries]) -> None:
    """415 distinct rows, 415 distinct keys — no collision, no over-merging."""
    ids = [entry.txn_id for built in real_built for entry in built.entries]
    assert len(set(ids)) == REAL_TXN_COUNT

    posting_ids = [p.id for built in real_built for e in built.entries for p in e.postings]
    assert len(set(posting_ids)) == REAL_TXN_COUNT * 2


def test_real_corpus_balances(real_built: list[StatementEntries]) -> None:
    triples = [triple for built in real_built for triple in all_postings(built)]
    result = check_double_entry(triples)
    assert result.status == PASS, result.message
    assert result.detail["groups"] == REAL_TXN_COUNT


def test_real_corpus_totals_match_the_measured_corpus(
    real_built: list[StatementEntries], real_expected: dict[str, int]
) -> None:
    """Split by sign on the *bank* leg, which is the statement's own number."""
    bank_legs = [
        posting.amount_minor
        for built in real_built
        for entry in built.entries
        for posting in entry.postings
        if posting.seq == BANK_LEG_SEQ
    ]
    assert sum(a for a in bank_legs if a > 0) == real_expected["deposits_minor"]
    assert sum(a for a in bank_legs if a < 0) == real_expected["withdrawals_minor"]

    # The counter legs are the exact mirror: no leakage between income and
    # expenses, which is the shape of the predecessor's 4.57x income error.
    counter_legs = [
        posting.amount_minor
        for built in real_built
        for entry in built.entries
        for posting in entry.postings
        if posting.seq == COUNTER_LEG_SEQ
    ]
    assert sum(counter_legs) == -sum(bank_legs)
    assert sum(a for a in counter_legs if a < 0) == -real_expected["deposits_minor"]


def test_real_corpus_counter_accounts_are_only_the_two_seeded_ones(
    real_built: list[StatementEntries],
) -> None:
    counter_accounts = {
        posting.account_id
        for built in real_built
        for entry in built.entries
        for posting in entry.postings
        if posting.seq == COUNTER_LEG_SEQ
    }
    assert counter_accounts <= {INCOME_ACCOUNT_ID, EXPENSE_ACCOUNT_ID}


def test_real_corpus_seams_agree_between_consecutive_statements(
    real_built: list[StatementEntries],
) -> None:
    """The free cross-statement check, run for real: 12 seams, no disagreement."""
    by_id: dict[str, int] = {}
    clashes = []
    for built in real_built:
        for assertion in built.balance_assertions:
            previous = by_id.get(assertion.id)
            if previous is not None and previous != assertion.amount_minor:
                clashes.append((assertion.as_of, previous, assertion.amount_minor))
            by_id[assertion.id] = assertion.amount_minor
    assert clashes == []
    # 13 statements sharing 12 seams: 26 rows collapse to 14 distinct assertions.
    assert len(by_id) == 14


def test_real_corpus_is_deterministic(real_parsed: list) -> None:
    assert [build_entries(p) for p in real_parsed] == [build_entries(p) for p in real_parsed]
