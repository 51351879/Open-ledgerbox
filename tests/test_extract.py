# SPDX-License-Identifier: AGPL-3.0-or-later
"""M3: PDF → positioned words."""

from __future__ import annotations

from pathlib import Path

import pytest
from synth import simple_statement

from ledgerbox.ingest.extract import (
    Document,
    ExtractionError,
    Span,
    extract_spans,
    group_rows,
    is_visible,
    row_text,
)

PRODUCER_MARKER = "OpenText Output Transformation Engine"


# --------------------------------------------------------------------------
# row assembly
# --------------------------------------------------------------------------


def test_rows_are_ordered_top_then_left_regardless_of_input_order() -> None:
    spans = (
        Span("BALANCE", 500.6, 534.7, 100.0, 108.0),
        Span("01/02", 36.2, 61.2, 120.0, 128.0),
        Span("DATE", 36.2, 55.2, 100.0, 108.0),
        Span("857.26", 507.0, 534.7, 120.0, 128.0),
    )
    rows = group_rows(spans)
    assert [row_text(row) for row in rows] == ["DATE BALANCE", "01/02 857.26"]


def test_slightly_uneven_baselines_stay_in_one_row() -> None:
    spans = (
        Span("a", 10.0, 20.0, 100.0, 108.0),
        Span("b", 30.0, 40.0, 101.4, 109.4),
        Span("c", 50.0, 60.0, 112.0, 120.0),
    )
    assert [row_text(row) for row in group_rows(spans)] == ["a b", "c"]


# --------------------------------------------------------------------------
# invisible text
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("color", "visible"),
    [
        ((1.0,), False),  # DeviceGray white
        ((1.0, 1.0, 1.0), False),  # DeviceRGB white
        ((0.0, 0.0, 0.0, 0.0), False),  # DeviceCMYK white
        ((0.0,), True),
        ((0.0, 0.0, 0.0), True),
        ((0.5,), True),
        (None, True),
    ],
)
def test_white_text_is_dropped(color: object, visible: bool) -> None:
    assert is_visible({"object_type": "char", "non_stroking_color": color}) is visible


def test_non_char_objects_are_untouched() -> None:
    assert is_visible({"object_type": "rect", "non_stroking_color": (1.0,)}) is True


# --------------------------------------------------------------------------
# serialisation — the fixture format
# --------------------------------------------------------------------------


def test_document_round_trips_through_json() -> None:
    original = simple_statement()
    restored = Document.from_json(original.to_json())
    assert restored == original


def test_json_is_readable_and_carries_coordinates() -> None:
    text = simple_statement().to_json()
    assert '"x0"' in text and '"top"' in text
    assert "extractor_version" in text


# --------------------------------------------------------------------------
# failure modes
# --------------------------------------------------------------------------


def test_a_non_pdf_raises_rather_than_returning_nothing(tmp_path: Path) -> None:
    bogus = tmp_path / "not-a.pdf"
    bogus.write_bytes(b"this is not a PDF at all")
    with pytest.raises(ExtractionError):
        extract_spans(bogus)


def test_an_empty_file_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    with pytest.raises(ExtractionError):
        extract_spans(empty)


def test_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ExtractionError):
        extract_spans(tmp_path / "nope.pdf")


# --------------------------------------------------------------------------
# the real corpus
# --------------------------------------------------------------------------


def test_real_statements_have_a_text_layer(real_statements: list[Path]) -> None:
    """If this ever fails, the format changed — this project does not do OCR.

    Note the page count is *not* fixed: PROJECT_SUMMARY §5 records "4 pages
    each", but three of the thirteen (2025-07, 2025-08, 2025-11) have 2. Page
    count is not a layout signal and nothing may depend on it.
    """
    counts = set()
    for path in real_statements:
        doc = extract_spans(path)
        counts.add(doc.page_count)
        assert doc.page_count >= 2
        assert PRODUCER_MARKER in (doc.producer or "")
        assert all(page.spans for page in doc.pages)
    assert counts == {2, 4}


def test_invisible_layout_markers_are_removed(real_statements: list[Path]) -> None:
    """`*end*transaction detail` interleaves with a real date at the same y.

    Left in, page 1's last transaction loses its date entirely: the row reads
    `*end*transac0tion detail1/02 …`. The markers are white 1pt text.
    """
    for path in real_statements:
        doc = extract_spans(path)
        for page in doc.pages:
            text = page.text()
            assert "*start*" not in text
            assert "*end*" not in text
        assert any(page.dropped_chars > 0 for page in doc.pages)


def test_every_real_transaction_row_starts_with_a_date(real_statements: list[Path]) -> None:
    """The check that would have caught the interleaved-marker corruption."""
    import re

    for path in real_statements:
        doc = extract_spans(path)
        for page in doc.pages:
            rows = group_rows(page.spans)
            header = next(
                (
                    i
                    for i, r in enumerate(rows)
                    if [s.text for s in r][:2] == ["DATE", "DESCRIPTION"]
                ),
                None,
            )
            if header is None:
                continue
            for row in rows[header + 1 :]:
                first = row[0].text
                if re.match(r"^\d{1,2}/\d{1,2}$", first):
                    assert len(row) > 1
                assert not first.startswith("*")
