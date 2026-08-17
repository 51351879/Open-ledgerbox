# SPDX-License-Identifier: AGPL-3.0-or-later
"""M2: amount parsing. Integer minor units, and a regex that demands cents."""

from __future__ import annotations

import pytest

from ledgerbox.money import (
    AmountParseError,
    decimal_str,
    format_minor,
    looks_like_amount,
    parse_amount_minor,
    try_parse_amount_minor,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0.00", 0),
        ("4.75", 475),
        ("1,234.56", 123456),
        ("1234.56", 123456),
        ("30,000.00", 3000000),  # five figures: the thousands separator twice
        ("$9,876.54", 987654),
        ("-12.44", -1244),
        ("-$1,234.56", -123456),
        ("$-1,234.56", -123456),
        ("  2,345.67  ", 234567),
        ("58,725.12", 5872512),
        ("999,999,999.99", 99999999999),
    ],
)
def test_parses(text: str, expected: int) -> None:
    assert parse_amount_minor(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "1234",  # ← a check number. The predecessor's regex accepted this.
        "0",
        "12",
        "12.3",
        "12.345",
        "1,23.45",  # bad grouping
        "12,34.56",
        "$",
        "",
        "   ",
        "abc",
        "12.34.56",
        "(5.00)",  # parenthesised negative: unknown layout, refuse
        "5.00-",  # trailing minus: same
        "--5.00",
        "-$-5.00",
        "1 234.56",
        "12.34USD",
        "USD12.34",
        "1.2e3",
    ],
)
def test_rejects(text: str) -> None:
    with pytest.raises(AmountParseError):
        parse_amount_minor(text)
    assert try_parse_amount_minor(text) is None
    assert not looks_like_amount(text)


def test_check_number_is_not_an_amount() -> None:
    """The single most important rejection in this module."""
    assert try_parse_amount_minor("1234") is None
    assert try_parse_amount_minor("1234.00") == 123400


def test_result_is_always_int_never_float() -> None:
    for text in ("0.10", "0.20", "0.30", "1.15", "2.675", "8.70"):
        value = try_parse_amount_minor(text)
        assert value is None or isinstance(value, int)
    # 0.1 + 0.2 != 0.3 in binary floating point; in minor units it is exact.
    assert parse_amount_minor("0.10") + parse_amount_minor("0.20") == parse_amount_minor("0.30")


def test_sums_stay_exact_over_thirteen_statements_worth_of_values() -> None:
    """Thirteen amounts of the shape a statement prints, added without drift.

    The values are invented. They used to be the operator's thirteen real
    monthly deposit totals, which was a decision nobody had made: individually
    each is an aggregate, but the row of them is a year of income with its shape
    intact, and one month stood out enough to identify. See docs/STATUS.md §6.5
    -- the counterparty names always got replaced and the numbers never did.

    What this test is actually for is the arithmetic, and invented values
    exercise it identically. The real figures are still checked, against the
    statements themselves, by the tests gated on LEDGERBOX_REAL_FIXTURES.
    """
    monthly = [
        "1,111.11", "2,222.22", "3,333.33", "4,444.44", "5,555.55",
        "6,666.66", "7,777.77", "8,888.88", "9,999.99", "10,101.01",
        "11,111.11", "12,121.21", "999.99",
    ]
    assert sum(parse_amount_minor(m) for m in monthly) == 8433327

    # The predecessor's arithmetic in binary floating point, for contrast: the
    # same thirteen values via float() and round() do not have to land here.
    assert sum(parse_amount_minor(m) for m in monthly) == parse_amount_minor("84,333.27")


def test_formats_sign_outside_the_symbol() -> None:
    assert format_minor(-1244) == "-$12.44"  # the predecessor produced "$-12.44"
    assert format_minor(1244) == "$12.44"
    assert format_minor(0) == "$0.00"
    assert format_minor(5872512) == "$58,725.12"
    assert format_minor(-5893752) == "-$58,937.52"
    assert format_minor(28871) == "$288.71"


def test_decimal_str_round_trips() -> None:
    for minor in (0, 1, -1, 475, -1244, 5872512, -5893752):
        assert parse_amount_minor(decimal_str(minor)) == minor
