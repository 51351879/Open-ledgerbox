# SPDX-License-Identifier: AGPL-3.0-or-later
"""Statement periods and the MM/DD problem.

Two rules earn their own module:

1. **A statement's month is the month of its period's *end* day.** Chase
   periods do not start on the 1st. Keying on the start day made 2025-06,
   2025-09 and 2025-12 disappear from the predecessor's output entirely — 13
   statements collapsed into 10 months and nobody noticed.
2. **Transaction rows carry only MM/DD.** The year comes from the period, and
   a December row inside a December→January period belongs to the *earlier*
   year. Guessing "the period's year" is wrong once a year.
"""

from __future__ import annotations

import re
from datetime import date

MONTHS: dict[str, int] = {
    name.lower(): number
    for number, name in enumerate(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ],
        start=1,
    )
}

_LONG_DATE_RE = re.compile(
    r"(?P<month>[A-Za-z]{3,9})\.?\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4})"
)

#: "January 01, 2025 through January 31, 2025"
_PERIOD_RE = re.compile(
    r"(?P<start>[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})"
    r"\s*(?:through|thru|to|–|—|-)\s*"
    r"(?P<end>[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE,
)

_MMDD_RE = re.compile(r"^(?P<month>\d{1,2})/(?P<day>\d{1,2})$")


class DateParseError(ValueError):
    pass


def _month_number(name: str) -> int:
    key = name.strip().rstrip(".").lower()
    if key in MONTHS:
        return MONTHS[key]
    for full, number in MONTHS.items():  # accept "Jan", "Sept"
        if full.startswith(key) and len(key) >= 3:
            return number
    raise DateParseError(f"unknown month: {name!r}")


def parse_long_date(text: str) -> date:
    """``"January 31, 2025"`` → ``date(2025, 1, 31)``."""
    match = _LONG_DATE_RE.search(text)
    if match is None:
        raise DateParseError(f"not a date: {text!r}")
    return date(
        int(match.group("year")),
        _month_number(match.group("month")),
        int(match.group("day")),
    )


def parse_statement_period(text: str) -> tuple[date, date]:
    """Pull ``(start, end)`` out of a statement header line.

    Raises rather than guessing: an unrecognised header means an unknown
    layout, and unknown means refuse.
    """
    match = _PERIOD_RE.search(text)
    if match is None:
        raise DateParseError(f"no statement period found in {text[:120]!r}")
    start = parse_long_date(match.group("start"))
    end = parse_long_date(match.group("end"))
    if end < start:
        raise DateParseError(f"period ends before it starts: {start} → {end}")
    return start, end


def statement_month(period_end: date | str) -> str:
    """``date(2025, 6, 4)`` → ``"2025-06"``. Always keyed on the *end* day."""
    if isinstance(period_end, str):
        return period_end[:7]
    return f"{period_end.year:04d}-{period_end.month:02d}"


def resolve_period_date(
    month: int, day: int, period_start: date, period_end: date
) -> date | None:
    """Give a MM/DD row its year, using the period as the only evidence.

    Returns ``None`` when no candidate year lands inside the period — the
    caller turns that into a review item instead of picking one.
    """
    for year in sorted({period_start.year, period_end.year}):
        try:
            candidate = date(year, month, day)
        except ValueError:  # 02/30, or 02/29 in a non-leap year
            continue
        if period_start <= candidate <= period_end:
            return candidate
    return None


def parse_mmdd(text: str, period_start: date, period_end: date) -> date | None:
    """``"12/28"`` inside a 2024-12→2025-01 period → ``date(2024, 12, 28)``."""
    match = _MMDD_RE.match(text.strip())
    if match is None:
        return None
    return resolve_period_date(
        int(match.group("month")), int(match.group("day")), period_start, period_end
    )


def iso(value: date) -> str:
    return value.isoformat()


def months_between(start: date, end: date) -> int:
    """Whole calendar months from *start* to *end*, for period-gap checks."""
    return (end.year - start.year) * 12 + (end.month - start.month)
