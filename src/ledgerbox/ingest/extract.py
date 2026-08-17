# SPDX-License-Identifier: AGPL-3.0-or-later
"""PDF → positioned words. The only module that imports pdfplumber.

Everything downstream works on :class:`Span` objects, which is what makes the
bank logic testable from committed JSON fixtures instead of committed PDFs —
you can redact a span file with confidence, and you cannot redact a PDF with
confidence (content streams, embedded fonts, XMP metadata and incremental
update history all leak).

pdfplumber is MIT. PyMuPDF is AGPL-3.0 and would infect the whole project; it
is not an option here regardless of how convenient it is.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pdfplumber

#: Bumped when extraction settings change in a way that alters output.
EXTRACTOR_VERSION = "1"

#: Chase statements carry invisible layout markers in the text layer —
#: ``*start*transaction detail``, ``*end*summary`` — drawn in **white** at 1pt.
#: They are not merely noise: they are positioned at the same baseline as real
#: rows and interleave with them, so pdfplumber merges them into the same
#: words. On page 1 of a real statement the date ``01/02`` comes out as
#: ``*end*transac0tion`` + ``detail1/02``, i.e. the transaction loses its date.
#:
#: The fix is not a string blacklist (that would need updating for every marker
#: Chase invents) but a semantic one: **text that is not visible is not
#: statement content.** White-on-white is dropped before words are assembled.
_WHITE_COLORS: frozenset[tuple[float, ...]] = frozenset(
    {
        (1.0,),  # DeviceGray white
        (1.0, 1.0, 1.0),  # DeviceRGB white
        (0.0, 0.0, 0.0, 0.0),  # DeviceCMYK white
    }
)

#: pdfplumber defaults, pinned explicitly: silent changes to word grouping
#: would silently change every parse.
WORD_SETTINGS: dict[str, Any] = {
    "x_tolerance": 3,
    "y_tolerance": 3,
    "keep_blank_chars": False,
    "use_text_flow": False,
}


class ExtractionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Span:
    """One word with the box it occupies. Coordinates are PDF points.

    ``top`` grows downward from the top of the page — pdfplumber's convention,
    kept as-is so the numbers match anything you inspect with pdfplumber.
    """

    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def x_mid(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass(frozen=True, slots=True)
class Page:
    number: int  # 1-based
    width: float
    height: float
    spans: tuple[Span, ...]
    #: How many invisible characters were discarded. Diagnostics only, but a
    #: sudden change means the producer changed how it marks up the page.
    dropped_chars: int = 0

    def text(self) -> str:
        return " ".join(span.text for span in self.spans)


@dataclass(frozen=True, slots=True)
class Document:
    """Everything the parsers are allowed to see."""

    producer: str | None
    page_count: int
    pages: tuple[Page, ...]
    extractor_version: str = EXTRACTOR_VERSION

    def first_page_text(self) -> str:
        return self.pages[0].text() if self.pages else ""

    # -- serialisation: the fixture format -------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "extractor_version": self.extractor_version,
            "producer": self.producer,
            "page_count": self.page_count,
            "pages": [
                {
                    "number": page.number,
                    "width": page.width,
                    "height": page.height,
                    "dropped_chars": page.dropped_chars,
                    "spans": [asdict(span) for span in page.spans],
                }
                for page in self.pages
            ],
        }

    def to_json(self, *, indent: int = 1) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False, sort_keys=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        pages = tuple(
            Page(
                number=int(page["number"]),
                width=float(page["width"]),
                height=float(page["height"]),
                dropped_chars=int(page.get("dropped_chars", 0)),
                spans=tuple(
                    Span(
                        text=str(span["text"]),
                        x0=float(span["x0"]),
                        x1=float(span["x1"]),
                        top=float(span["top"]),
                        bottom=float(span["bottom"]),
                    )
                    for span in page["spans"]
                ),
            )
            for page in data["pages"]
        )
        return cls(
            producer=data.get("producer"),
            page_count=int(data.get("page_count", len(pages))),
            pages=pages,
            extractor_version=str(data.get("extractor_version", EXTRACTOR_VERSION)),
        )

    @classmethod
    def from_json(cls, text: str) -> Document:
        return cls.from_dict(json.loads(text))


def _round(value: float) -> float:
    """Two decimals is well below a glyph width and keeps fixtures diffable."""
    return round(float(value), 2)


def _as_tuple(color: Any) -> tuple[float, ...] | None:
    if color is None:
        return None
    if isinstance(color, int | float):
        return (float(color),)
    try:
        return tuple(float(component) for component in color)
    except (TypeError, ValueError):  # pragma: no cover — exotic colour spaces
        return None


#: Colour spaces where the components mean what they look like. In Separation
#: or DeviceN a tint of 1.0 is *full ink* — i.e. the darkest the colorant gets —
#: so reading it as "white" would delete real content. Outside these spaces the
#: rule declines to judge and keeps the character.
_DEVICE_SPACES = frozenset({"DeviceGray", "DeviceRGB", "DeviceCMYK"})


def is_visible(obj: dict[str, Any]) -> bool:
    """False for white-on-white text. Non-char objects pass through."""
    if obj.get("object_type") != "char":
        return True
    space = obj.get("ncs")
    if space is not None and str(space) not in _DEVICE_SPACES:
        return True
    return _as_tuple(obj.get("non_stroking_color")) not in _WHITE_COLORS


def extract_spans(pdf_path: str | Path) -> Document:
    """Read a PDF into positioned words.

    Raises :class:`ExtractionError` for anything that is not a readable PDF —
    the caller turns that into one failed file, never a failed batch.
    """
    path = Path(pdf_path)
    try:
        with pdfplumber.open(path) as pdf:
            producer = None
            metadata = pdf.metadata or {}
            raw_producer = metadata.get("Producer")
            if isinstance(raw_producer, bytes):  # pragma: no cover — rare encoding
                raw_producer = raw_producer.decode("utf-8", "replace")
            if raw_producer is not None:
                producer = str(raw_producer).strip()

            pages: list[Page] = []
            for index, page in enumerate(pdf.pages, start=1):
                total_chars = len(page.chars)
                visible = page.filter(is_visible)
                dropped = total_chars - len(visible.chars)
                words = visible.extract_words(**WORD_SETTINGS)
                spans = tuple(
                    Span(
                        text=word["text"],
                        x0=_round(word["x0"]),
                        x1=_round(word["x1"]),
                        top=_round(word["top"]),
                        bottom=_round(word["bottom"]),
                    )
                    for word in words
                )
                pages.append(
                    Page(
                        number=index,
                        width=_round(page.width),
                        height=_round(page.height),
                        spans=spans,
                        dropped_chars=dropped,
                    )
                )
    except ExtractionError:  # pragma: no cover
        raise
    except Exception as exc:  # pdfminer raises a wide variety of things
        raise ExtractionError(f"cannot extract text from {path.name}: {exc}") from exc

    if not pages:
        raise ExtractionError(f"{path.name} has no pages")
    if not any(page.spans for page in pages):
        raise ExtractionError(
            f"{path.name} has no text layer — this project does not do OCR, and a "
            f"Chase statement having no text layer means the format changed."
        )
    return Document(producer=producer, page_count=len(pages), pages=tuple(pages))


# ---------------------------------------------------------------------------
# Row assembly
# ---------------------------------------------------------------------------


def group_rows(spans: tuple[Span, ...], *, tolerance: float = 2.5) -> list[list[Span]]:
    """Cluster spans into visual rows by their ``top`` coordinate.

    Rows are what a human sees; the PDF's internal text order is not. Sorting
    inside a row is by ``x0`` — never by the order pdfplumber happened to emit,
    which is exactly the assumption that made the predecessor read a balance as
    an amount.
    """
    rows: list[list[Span]] = []
    for span in sorted(spans, key=lambda s: (s.top, s.x0)):
        if rows and abs(rows[-1][0].top - span.top) <= tolerance:
            rows[-1].append(span)
        else:
            rows.append([span])
    return [sorted(row, key=lambda s: s.x0) for row in rows]


def row_text(row: list[Span]) -> str:
    return " ".join(span.text for span in row)
