# SPDX-License-Identifier: AGPL-3.0-or-later
"""Parser registry and layout identification.

**Unknown layout means refuse.** Not "try the closest parser", not "guess from
the filename". A document nobody recognises goes to the review queue with its
producer string attached, because the alternative — confident output from a
parser written for a different bank — is the exact failure mode this project
exists to prevent.
"""

from __future__ import annotations

from .extract import Document
from .parsers.base import Parser
from .parsers.chase_checking import PARSER as CHASE_CHECKING

#: Order matters only for reporting; `matches()` must be mutually exclusive.
PARSERS: tuple[Parser, ...] = (CHASE_CHECKING,)


class UnknownLayout(RuntimeError):
    """No parser recognised the document."""

    def __init__(self, doc: Document) -> None:
        self.producer = doc.producer
        self.page_count = doc.page_count
        super().__init__(
            f"no parser recognises this document "
            f"(producer={doc.producer!r}, pages={doc.page_count}). "
            f"Supported: {', '.join(p.parser_id for p in PARSERS)}."
        )


def identify(doc: Document) -> Parser | None:
    matched = [parser for parser in PARSERS if parser.matches(doc)]
    if not matched:
        return None
    if len(matched) > 1:  # pragma: no cover — a registry bug, not a data problem
        names = ", ".join(p.parser_id for p in matched)
        raise RuntimeError(f"ambiguous layout: {names} all claim this document")
    return matched[0]


def identify_or_raise(doc: Document) -> Parser:
    parser = identify(doc)
    if parser is None:
        raise UnknownLayout(doc)
    return parser


def get(parser_id: str) -> Parser:
    for parser in PARSERS:
        if parser.parser_id == parser_id:
            return parser
    raise KeyError(parser_id)
