# SPDX-License-Identifier: AGPL-3.0-or-later
"""M2: statement periods, and the month that must come from the *end* day."""

from __future__ import annotations

from datetime import date

import pytest

from ledgerbox.dates import (
    DateParseError,
    months_between,
    parse_long_date,
    parse_mmdd,
    parse_statement_period,
    resolve_period_date,
    statement_month,
)


def test_parses_a_chase_style_period() -> None:
    start, end = parse_statement_period("January 01, 2025 through January 31, 2025")
    assert (start, end) == (date(2025, 1, 1), date(2025, 1, 31))


def test_parses_a_period_that_crosses_new_year() -> None:
    start, end = parse_statement_period("December 07, 2024 through January 07, 2025")
    assert (start, end) == (date(2024, 12, 7), date(2025, 1, 7))


def test_parses_abbreviated_months() -> None:
    assert parse_long_date("Sept 3, 2025") == date(2025, 9, 3)
    assert parse_long_date("Jan. 31, 2025") == date(2025, 1, 31)


def test_refuses_an_unrecognised_header() -> None:
    with pytest.raises(DateParseError):
        parse_statement_period("Statement of account, page 1 of 4")


def test_refuses_a_backwards_period() -> None:
    with pytest.raises(DateParseError):
        parse_statement_period("March 05, 2025 through February 05, 2025")


# --------------------------------------------------------------------------
# the disappearing-months bug
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (date(2025, 5, 6), date(2025, 6, 3), "2025-06"),
        (date(2025, 8, 6), date(2025, 9, 4), "2025-09"),
        (date(2025, 11, 5), date(2025, 12, 3), "2025-12"),
        (date(2024, 12, 7), date(2025, 1, 7), "2025-01"),
    ],
)
def test_month_comes_from_the_period_end(start: date, end: date, expected: str) -> None:
    """Keying on `start` is what erased 2025-06, 2025-09 and 2025-12."""
    assert statement_month(end) == expected
    assert statement_month(start) != expected


def test_statement_month_accepts_iso_text() -> None:
    assert statement_month("2025-06-03") == "2025-06"


# --------------------------------------------------------------------------
# MM/DD rows have no year of their own
# --------------------------------------------------------------------------


def test_december_row_in_a_december_to_january_period_keeps_the_earlier_year() -> None:
    start, end = date(2024, 12, 7), date(2025, 1, 7)
    assert parse_mmdd("12/28", start, end) == date(2024, 12, 28)
    assert parse_mmdd("01/02", start, end) == date(2025, 1, 2)


def test_row_outside_the_period_is_refused_not_guessed() -> None:
    start, end = date(2025, 1, 1), date(2025, 1, 31)
    assert parse_mmdd("02/15", start, end) is None
    assert resolve_period_date(2, 15, start, end) is None


def test_impossible_dates_do_not_raise() -> None:
    start, end = date(2025, 2, 1), date(2025, 3, 1)
    assert parse_mmdd("02/30", start, end) is None
    assert resolve_period_date(2, 29, start, end) is None  # 2025 is not a leap year


def test_leap_day_resolves_when_the_period_contains_it() -> None:
    assert resolve_period_date(2, 29, date(2024, 2, 1), date(2024, 3, 1)) == date(2024, 2, 29)


def test_non_mmdd_text_is_not_a_date() -> None:
    start, end = date(2025, 1, 1), date(2025, 1, 31)
    for text in ("", "abc", "1/2/2025", "12-28", "1234"):
        assert parse_mmdd(text, start, end) is None


def test_months_between_detects_a_missing_statement() -> None:
    assert months_between(date(2025, 1, 31), date(2025, 2, 28)) == 1
    assert months_between(date(2025, 1, 31), date(2025, 3, 31)) == 2  # a gap
    assert months_between(date(2024, 12, 7), date(2025, 1, 7)) == 1
