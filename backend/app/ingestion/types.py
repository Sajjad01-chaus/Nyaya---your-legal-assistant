"""Shared types for the ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class PageKind(str, Enum):
    """What L1 profiling decided a page is, which selects the L2 parser."""

    TEXT_PROSE = "text_prose"
    TEXT_TABLE_RULED = "text_table_ruled"
    TEXT_TABLE_UNRULED = "text_table_unruled"
    TEXT_FORM = "text_form"
    SCANNED = "scanned"
    GARBAGE_TEXT = "garbage_text"  # a text layer exists but does not decode to language
    EMPTY = "empty"


class ExtractMethod(str, Enum):
    LAYOUT = "layout"
    TABLE_LATTICE = "table_lattice"
    TABLE_STREAM = "table_stream"
    FORM = "form"
    OCR = "ocr"


@dataclass(slots=True)
class Line:
    """A visual line: characters sharing a baseline, ordered left to right."""

    baseline: float
    x0: float
    x1: float
    size: float
    text: str
    bold: bool = False
    italic: bool = False

    @property
    def indent_level(self) -> int:
        """Body indent ladder, empirically 118/142/166/190 at 24pt steps.

        Returned as a step index from the leftmost body column. This is a *hint*
        only: the leading marker (``35.``/``(1)``/``(a)``/``(i)``) is what
        actually decides structural depth, because a wrapped clause line lands
        at the same x as a fresh paragraph start.
        """
        return max(0, round((self.x0 - 118.0) / 24.0))


@dataclass(slots=True)
class MarginNote:
    """8pt text in the outer column: a section title fragment or an Act reference.

    The column mirrors: it sits left (x0 ~57) on verso pages and right
    (x0 ~486) on recto pages, and it interleaves into body lines, so it must be
    separated before the body text can be read.
    """

    baseline: float
    x0: float
    text: str
    is_act_ref: bool = False


@dataclass(slots=True)
class PageProfile:
    """L1 measurements. Cheap, and the sole input to parser selection."""

    page: int
    width: float
    height: float
    char_count: int
    printable_ratio: float
    replacement_ratio: float
    median_font_size: float
    font_names: tuple[str, ...]
    x0_clusters: tuple[float, ...]
    horizontal_rules: int
    vertical_rules: int
    image_coverage: float
    has_margin_column: bool
    kind: PageKind = PageKind.TEXT_PROSE

    @property
    def needs_ocr(self) -> bool:
        return self.kind in (PageKind.SCANNED, PageKind.GARBAGE_TEXT, PageKind.EMPTY)


@dataclass(slots=True)
class PageLayout:
    """L2 output for one page: body separated from margin apparatus."""

    page: int
    body: list[Line] = field(default_factory=list)
    notes: list[MarginNote] = field(default_factory=list)
    method: ExtractMethod = ExtractMethod.LAYOUT
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)

    @property
    def titles(self) -> list[MarginNote]:
        return [n for n in self.notes if not n.is_act_ref]

    @property
    def act_refs(self) -> list[MarginNote]:
        return [n for n in self.notes if n.is_act_ref]

    def text(self) -> str:
        return "\n".join(line.text for line in self.body)
