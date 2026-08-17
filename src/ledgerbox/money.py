# SPDX-License-Identifier: AGPL-3.0-or-later
"""Money as integers. No floats, anywhere, ever.

SQLite documents it plainly: of all the cent values, only ``.00 .25 .50 .75``
are exactly representable in binary floating point. Everything here is an
``int`` count of minor units (cents for USD).

Parsing is deliberately strict — a bare integer is **not** an amount. The
predecessor's regex made the decimals optional, which means a check number
sitting in the description column would have been read as a dollar figure. No
such row exists in the current corpus; that is luck, not safety.
"""

from __future__ import annotations

import re

#: ``1,234.56`` or ``1234.56`` — grouped or plain, but the cents are mandatory.
#: ``re.ASCII`` because bare ``\d`` also matches Arabic-Indic and full-width
#: digits, which no US bank statement contains and which nothing downstream
#: expects.
_DIGITS_RE = re.compile(r"^(?P<int>\d{1,3}(?:,\d{3})*|\d+)\.(?P<frac>\d{2})$", re.ASCII)

#: Cheap pre-filter for scanning a page: something with two decimal places.
AMOUNT_HINT_RE = re.compile(r"\d\.\d{2}\b", re.ASCII)


class AmountParseError(ValueError):
    pass


def parse_amount_minor(text: str, *, scale: int = 2) -> int:
    """``"-$1,234.56"`` → ``-123456``. Raises on anything ambiguous.

    Accepted: an optional minus and an optional ``$`` in either order, digit
    grouping by threes or none at all, exactly two decimal places.

    Rejected on purpose: bare integers (``1234``), one or three decimals,
    parenthesised negatives (``(5.00)``), trailing minus (``5.00-``), and
    anything with stray characters. A layout that uses those should fail
    loudly here rather than be guessed at.
    """
    if scale != 2:  # pragma: no cover — no non-cent currency in P0
        raise NotImplementedError("only 2-decimal currencies are supported in P0")

    raw = text.strip()
    if not raw:
        raise AmountParseError("empty amount")

    negative = False
    body = raw
    if body.startswith("-"):
        negative, body = True, body[1:].lstrip()
    if body.startswith("$"):
        body = body[1:].lstrip()
        if body.startswith("-"):
            if negative:
                raise AmountParseError(f"two minus signs: {text!r}")
            negative, body = True, body[1:].lstrip()

    match = _DIGITS_RE.match(body)
    if match is None:
        raise AmountParseError(f"not an amount: {text!r}")

    whole = int(match.group("int").replace(",", ""))
    minor = whole * 100 + int(match.group("frac"))
    return -minor if negative else minor


def try_parse_amount_minor(text: str) -> int | None:
    """Same, but ``None`` instead of an exception — for scanning candidates."""
    try:
        return parse_amount_minor(text)
    except AmountParseError:
        return None


def looks_like_amount(text: str) -> bool:
    return try_parse_amount_minor(text) is not None


def format_minor(minor: int, *, symbol: str = "$", grouping: bool = True) -> str:
    """``-1244`` → ``-$12.44``.

    The sign goes *outside* the symbol. The predecessor rendered ``$-12.44``.
    """
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(minor), 100)
    body = f"{whole:,}" if grouping else str(whole)
    return f"{sign}{symbol}{body}.{frac:02d}"


def decimal_str(minor: int) -> str:
    """``-1244`` → ``-12.44``. Plain text for exports and CSV."""
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(minor), 100)
    return f"{sign}{whole}.{frac:02d}"
