# SPDX-License-Identifier: AGPL-3.0-or-later
"""Idempotency: what makes two rows the same transaction.

The key is a content hash of five fields joined by ``\\x1f`` (US, "unit
separator" — a byte that cannot occur in a bank's description text).

Four things this gets right that a naive implementation does not:

1. **There is a separator.** ``sha1(date + memo + amount)`` — what
   ``ofxstatement`` does — collides: ``("ABC", "12")`` and ``("ABC1", "2")``
   hash identically.
2. **``occurrence_index`` is part of the key.** Two $4.75 coffees on the same
   day are two transactions, not a duplicate.
3. **It hashes content, never a row number.** Line order changes between
   downloads; identity must not.
4. **``natural_key`` and the bank's own id coexist and are never merged.**
   FITIDs are only unique within one institution+account, the OFX spec itself
   ships ``CORRECTFITID`` to supersede them, and a pending row changes its id
   when it posts.

Changing :func:`normalize_descriptor` changes every key ever produced. That is
what ``NATURAL_KEY_VERSION`` is for: bump it, keep the old rows, re-key
forward. Never edit the normalisation in place.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Sequence

NATURAL_KEY_VERSION = 1

#: ASCII 0x1F, "unit separator". Not typeable, not present in statement text.
SEP = "\x1f"

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_descriptor(description: str) -> str:
    """Fold away only what is certainly noise: unicode form, case, whitespace.

    Kept deliberately shy. Stripping card fragments or trailing store numbers
    would merge genuinely distinct rows, and the verbatim text is preserved in
    ``txn_identity.raw_descriptor`` anyway — normalisation never happens in
    place.
    """
    folded = unicodedata.normalize("NFKC", description)
    return _WHITESPACE_RE.sub(" ", folded).strip().upper()


def natural_key(
    account_id: str,
    posted_date: str,
    amount_minor: int,
    description: str,
    occurrence_index: int = 0,
) -> str:
    """The idempotency key. ``posted_date`` is ISO 8601; amount is minor units."""
    if SEP in account_id:  # pragma: no cover — ids are ours, not the bank's
        raise ValueError("account_id must not contain the unit separator")
    raw = SEP.join(
        [
            account_id,
            posted_date,
            str(int(amount_minor)),
            normalize_descriptor(description),
            str(int(occurrence_index)),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def assign_occurrence_indexes(
    rows: Iterable[tuple[str, int, str]],
) -> list[int]:
    """Number identical ``(date, amount, description)`` triples 0, 1, 2, …

    Order-dependent by construction: the n-th identical row in a statement is
    occurrence n. That is stable as long as the statement is, which is the
    whole point of re-reading the same PDF.
    """
    seen: defaultdict[tuple[str, int, str], int] = defaultdict(int)
    indexes: list[int] = []
    for posted_date, amount_minor, description in rows:
        key = (posted_date, amount_minor, normalize_descriptor(description))
        indexes.append(seen[key])
        seen[key] += 1
    return indexes


# ---------------------------------------------------------------------------
# Deterministic row ids
#
# Every id below is a pure function of content. Rebuilding the database from
# archive/ must produce byte-identical rows (timestamps aside) — random UUIDs
# would make the rebuild invariant untestable.
# ---------------------------------------------------------------------------


def source_file_id(sha256: str) -> str:
    return sha256


def raw_record_id(source_file_sha256: str, record_index: int) -> str:
    return f"{source_file_sha256}:{record_index:05d}"


def txn_id(natural_key_hex: str) -> str:
    return natural_key_hex


def posting_id(txn_id_value: str, seq: int) -> str:
    return f"{txn_id_value}:{seq}"


def balance_assertion_id(account_id: str, as_of: str, commodity_id: str) -> str:
    return _digest(account_id, as_of, commodity_id)


def opening_txn_id(account_id: str, as_of: str, amount_minor: int) -> str:
    """The synthetic entry that gives an account its starting balance.

    Content-addressed like everything else, so a rebuild produces the same row
    — and so re-deriving it after an earlier statement arrives replaces the old
    one rather than accumulating a second.
    """
    return _digest("opening", account_id, as_of, str(int(amount_minor)))


def review_item_id(source_file_id_value: str, check_id: str, *parts: object) -> str:
    return _digest(source_file_id_value, check_id, *(str(p) for p in parts))


def account_id_for(institution: str, subtype: str, mask: str | None) -> str:
    """``('chase', 'checking', '1234')`` → ``'chase:checking:1234'``.

    Slugged, lower-case, stable across re-ingests of the same account.
    """
    parts: Sequence[str] = [p for p in (institution, subtype, mask or "default") if p]
    slug = ":".join(re.sub(r"[^a-z0-9]+", "-", p.lower()).strip("-") for p in parts)
    return f"assets:{slug}"


def _digest(*parts: str) -> str:
    return hashlib.sha256(SEP.join(parts).encode("utf-8")).hexdigest()
