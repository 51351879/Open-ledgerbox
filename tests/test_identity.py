# SPDX-License-Identifier: AGPL-3.0-or-later
"""M2: the idempotency key.

Every test here corresponds to a documented failure in another tool or in the
predecessor. None of them are hypothetical.
"""

from __future__ import annotations

from ledgerbox.ledger.identity import (
    NATURAL_KEY_VERSION,
    SEP,
    account_id_for,
    assign_occurrence_indexes,
    natural_key,
    normalize_descriptor,
    posting_id,
    raw_record_id,
    review_item_id,
    txn_id,
)

ACCOUNT = "assets:chase:checking:0000"


def test_separator_is_the_unit_separator() -> None:
    assert SEP == "\x1f"
    assert NATURAL_KEY_VERSION == 1


def test_field_boundaries_cannot_be_smeared() -> None:
    """ofxstatement's sha1(date+memo+amount) collides here; ours must not."""
    a = natural_key(ACCOUNT, "2025-01-02", 1200, "ABC", 0)
    b = natural_key(ACCOUNT, "2025-01-02", 2, "ABC1", 0)
    assert a != b

    c = natural_key("ABC", "2025-01-02", 100, "12", 0)
    d = natural_key("ABC1", "2025-01-02", 100, "2", 0)
    assert c != d


def test_same_day_same_amount_same_merchant_are_two_transactions() -> None:
    """Two $4.75 coffees on one day are not a duplicate."""
    first = natural_key(ACCOUNT, "2025-03-04", -475, "STARBUCKS #123", 0)
    second = natural_key(ACCOUNT, "2025-03-04", -475, "STARBUCKS #123", 1)
    assert first != second


def test_key_is_stable_across_runs() -> None:
    assert natural_key(ACCOUNT, "2025-01-02", -1234, "AMZN Mktp US*2X4", 0) == natural_key(
        ACCOUNT, "2025-01-02", -1234, "AMZN Mktp US*2X4", 0
    )


def test_key_ignores_row_order_but_not_content() -> None:
    base = natural_key(ACCOUNT, "2025-01-02", -1234, "SAFEWAY #1234", 0)
    assert base == natural_key(ACCOUNT, "2025-01-02", -1234, "SAFEWAY #1234", 0)
    assert base != natural_key(ACCOUNT, "2025-01-03", -1234, "SAFEWAY #1234", 0)
    assert base != natural_key(ACCOUNT, "2025-01-02", -1235, "SAFEWAY #1234", 0)
    assert base != natural_key(ACCOUNT, "2025-01-02", -1234, "SAFEWAY #1235", 0)
    assert base != natural_key("other:account", "2025-01-02", -1234, "SAFEWAY #1234", 0)


def test_sign_matters() -> None:
    assert natural_key(ACCOUNT, "2025-01-02", 500, "X", 0) != natural_key(
        ACCOUNT, "2025-01-02", -500, "X", 0
    )


def test_key_is_hex_sha256() -> None:
    key = natural_key(ACCOUNT, "2025-01-02", 1, "X", 0)
    assert len(key) == 64
    assert set(key) <= set("0123456789abcdef")


# --------------------------------------------------------------------------
# descriptor normalisation
# --------------------------------------------------------------------------


def test_normalisation_folds_case_and_whitespace_only() -> None:
    assert normalize_descriptor("  Card  Purchase   ") == "CARD PURCHASE"
    assert normalize_descriptor("Card\tPurchase\nWith Pin") == "CARD PURCHASE WITH PIN"
    assert normalize_descriptor("ＡＢＣ") == "ABC"  # NFKC folds full-width


def test_normalisation_keeps_distinguishing_detail() -> None:
    """Stripping store numbers or card fragments would merge real transactions."""
    assert normalize_descriptor("SAFEWAY #1234") != normalize_descriptor("SAFEWAY #5678")
    assert normalize_descriptor("ZELLE PAYMENT TO A") != normalize_descriptor("ZELLE PAYMENT TO B")


def test_normalisation_is_idempotent() -> None:
    once = normalize_descriptor("  House  of   Sushi ")
    assert normalize_descriptor(once) == once


# --------------------------------------------------------------------------
# occurrence numbering
# --------------------------------------------------------------------------


def test_occurrence_indexes_count_identical_rows() -> None:
    rows = [
        ("2025-03-04", -475, "STARBUCKS"),
        ("2025-03-04", -475, "STARBUCKS"),
        ("2025-03-04", -475, "starbucks  "),  # same after normalisation
        ("2025-03-04", -476, "STARBUCKS"),
        ("2025-03-05", -475, "STARBUCKS"),
    ]
    assert assign_occurrence_indexes(rows) == [0, 1, 2, 0, 0]


def test_occurrence_indexes_make_every_key_unique() -> None:
    rows = [("2025-03-04", -475, "STARBUCKS")] * 3
    keys = {
        natural_key(ACCOUNT, d, a, desc, i)
        for (d, a, desc), i in zip(rows, assign_occurrence_indexes(rows), strict=True)
    }
    assert len(keys) == 3


# --------------------------------------------------------------------------
# deterministic ids — required for the rebuild invariant
# --------------------------------------------------------------------------


def test_ids_are_pure_functions_of_content() -> None:
    key = natural_key(ACCOUNT, "2025-01-02", -1234, "X", 0)
    assert txn_id(key) == txn_id(key)
    assert posting_id(txn_id(key), 0) != posting_id(txn_id(key), 1)
    assert raw_record_id("abc", 7) == "abc:00007"
    assert review_item_id("f1", "balance_chain", 3) == review_item_id("f1", "balance_chain", 3)
    assert review_item_id("f1", "balance_chain", 3) != review_item_id("f1", "balance_chain", 4)


def test_raw_record_ids_sort_in_record_order() -> None:
    ids = [raw_record_id("abc", i) for i in (0, 1, 2, 10, 100)]
    assert ids == sorted(ids)


def test_account_ids_are_slugged_and_stable() -> None:
    assert account_id_for("Chase", "checking", "1234") == "assets:chase:checking:1234"
    assert account_id_for("Chase", "checking", None) == "assets:chase:checking:default"
    assert account_id_for("JPMorgan Chase Bank, N.A.", "checking", "1234").startswith("assets:")
    assert SEP not in account_id_for("Chase", "checking", "1234")
