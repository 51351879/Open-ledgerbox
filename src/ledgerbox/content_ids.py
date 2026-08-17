# SPDX-License-Identifier: AGPL-3.0-or-later
"""Canonical content identifiers shared by local audit workflows."""

from __future__ import annotations

import hashlib
import json

HASH_PREFIX = "sha256:"


def content_hash(value: object) -> str:
    """Return one stable SHA-256 id for a JSON-shaped value."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return HASH_PREFIX + hashlib.sha256(encoded).hexdigest()
