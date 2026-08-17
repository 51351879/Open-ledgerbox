# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ingest: archive, identify, extract, parse."""

from .archive import ArchivedFile, ArchiveError, archive_file, find_archived
from .extract import Document, ExtractionError, Page, Span, extract_spans, group_rows

# `pipeline` is deliberately not re-exported here: it imports the db and ledger
# layers, and pulling those in on `import ledgerbox.ingest` would make a cycle
# out of what is currently a one-way dependency.

__all__ = [
    "ArchiveError",
    "ArchivedFile",
    "Document",
    "ExtractionError",
    "Page",
    "Span",
    "archive_file",
    "extract_spans",
    "find_archived",
    "group_rows",
]
