# SPDX-License-Identifier: AGPL-3.0-or-later
"""One deterministic template per descriptor: the unit learning happens at.

The same merchant never prints byte-identical descriptors twice -- dates, card
fragments and reference numbers vary per visit -- so anything keyed on exact
bytes almost never fires again. The template masks exactly those per-visit
digit runs and keeps everything that identifies the counterparty. It must stay
conservative: two descriptors that share a template will share one learned
answer, so merging too much lets one person's answer claim another's money.

``TEMPLATE_VERSION`` is stored beside every template ever persisted. The
masking here may only change together with a version bump, because a stored
template is only comparable to one derived by the same rules.
"""

from __future__ import annotations

import re

TEMPLATE_VERSION = 1

# Runs of two or more digits are per-visit noise: dates, card fragments,
# reference numbers, store numbers. Single digits stay because brands carry
# them (7-ELEVEN); losing the occasional two-digit brand is the safe direction,
# masking a payee's name never is -- names differ by letters, which survive.
_DIGIT_RUN = re.compile(r"\d{2,}")
_MASK_ONLY = re.compile(r"[#\s/\-.*]*$")
_WHITESPACE = re.compile(r"\s+")


def descriptor_template(raw: str) -> str:
    """Derive the version-:data:`TEMPLATE_VERSION` template for one descriptor.

    Deterministic, idempotent, and empty when the descriptor identifies nobody:
    a string that is all dates and reference numbers yields ``""``, and the
    empty template is the caller's signal that learning must not key on it.
    """
    masked = _DIGIT_RUN.sub("#", raw.upper())
    collapsed = _WHITESPACE.sub(" ", masked).strip()
    if _MASK_ONLY.fullmatch(collapsed):
        return ""
    return collapsed
