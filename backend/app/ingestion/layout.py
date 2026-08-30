"""Layout-aware text recovery for two-zone statute pages.

The Gazette of India sets a bare act as a single body column with *marginal
notes* in the outer margin. Three properties of that layout defeat naive
extraction, and each is handled here:

1.  The margin column **mirrors**. It sits left (x0 ~57) on verso pages and
    right (x0 ~486) on recto pages. Any fixed x threshold gets one side wrong.

2.  Margin notes share baselines with body lines, so a plain text extraction
    splices them into the middle of sentences::

        "...arrest any person- When police may arrest without warrant."

    That corrupts both the body text and the section title, for all 531
    sections.

3.  Headings are set in **small caps**: a 10pt initial followed by 7pt
    remainder. Those glyphs share a baseline but have different ``top``
    values, so grouping characters by ``top`` splits ``ARREST OF PERSONS``
    into ``A`` + ``RREST OF PERSONS``. Lines must be clustered by baseline.

Geometry is expressed as a :class:`PageGeometry` so the same engine can be
retargeted at another typesetting without editing this module.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from .types import ExtractMethod, Line, MarginNote, PageLayout

# A margin note that is purely an Act citation, e.g. "21 of 1860." (the IPC).
# These are structural cross-references, not part of the section title.
_ACT_REF = re.compile(r"^\d+\s+of\s+\d{4}\.?$")
_RULE_ONLY = re.compile(r"^[_\s]+$")
_PAGE_NUM_ONLY = re.compile(r"^\d{1,4}$")
_WS = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class PageGeometry:
    """Column model for a page family, in PDF points.

    Defaults are the measured values for the BNSS 2023 gazette (A4, 595x842).
    ``profile_page_geometry`` derives these from the document when the layout
    is unknown.
    """

    body_min_x: float = 115.0
    body_max_x: float = 478.0
    header_bottom: float = 70.0
    footer_top: float = 800.0
    body_size_min: float = 9.0
    baseline_tol: float = 2.5
    char_gap: float = 1.2
    run_gap: float = 8.0
    indent_step: float = 24.0


def _render(chars: list[dict], x_tol: float) -> str:
    """Join characters, inserting a space only where the glyph gap warrants it.

    ``x_tol`` is deliberately tight. pypdf's looser default splits kerned pairs
    and yields "BEHA VIOUR" / "MAGISTRA TE'S", which would corrupt 8 of the 58
    scraped form titles.
    """
    out: list[str] = []
    prev: dict | None = None
    for ch in chars:
        if prev is not None and ch["x0"] - prev["x1"] > x_tol:
            out.append(" ")
        out.append(ch["text"])
        prev = ch
    return _WS.sub(" ", "".join(out)).strip()


def _split_runs(chars: list[dict], gap: float) -> list[list[dict]]:
    """Split one baseline's characters into runs separated by wide gaps.

    Needed because a section title fragment and an Act reference can share a
    baseline in the margin. Rendered as one string they fuse into nonsense
    such as ``"Repeal and 2 of 1974"``; split into runs they classify cleanly.
    """
    if not chars:
        return []
    runs: list[list[dict]] = [[chars[0]]]
    for prev, ch in zip(chars, chars[1:]):
        if ch["x0"] - prev["x1"] > gap:
            runs.append([ch])
        else:
            runs[-1].append(ch)
    return runs


def cluster_baselines(chars: list[dict], tol: float) -> list[tuple[float, list[dict]]]:
    """Group characters into visual lines by baseline, within ``tol`` points."""
    rows: list[tuple[float, list[dict]]] = []
    for ch in sorted(chars, key=lambda c: c["bottom"]):
        if rows and abs(ch["bottom"] - rows[-1][0]) <= tol:
            rows[-1][1].append(ch)
        else:
            rows.append((ch["bottom"], [ch]))
    return [(round(b, 1), sorted(cs, key=lambda c: c["x0"])) for b, cs in rows]


def _is_margin(ch: dict, geo: PageGeometry) -> bool:
    """Margin apparatus is small type *outside* the body column.

    Both conditions are required. Size alone would capture the 7pt small-caps
    tail of a centred chapter heading; position alone would miss a long margin
    note whose final glyph overhangs the threshold -- which is exactly how
    section 279 was first lost, its line reading ``"e 279. (1) If the summons"``.
    """
    return ch["size"] < geo.body_size_min and (
        ch["x0"] < geo.body_min_x or ch["x0"] > geo.body_max_x
    )


def analyse_page(
    chars: list[dict],
    page_number: int,
    geo: PageGeometry,
    *,
    has_margin_column: bool = True,
) -> PageLayout:
    """Separate one page into body lines and margin apparatus.

    ``chars`` is pdfplumber's ``page.chars``: dicts carrying ``x0``, ``x1``,
    ``top``, ``bottom``, ``size``, ``text`` and ``fontname``.
    """
    layout = PageLayout(page=page_number, method=ExtractMethod.LAYOUT)

    in_frame = [c for c in chars if geo.header_bottom <= c["top"] <= geo.footer_top]
    if not in_frame:
        layout.confidence = 0.0
        layout.warnings.append("no characters inside the content frame")
        return layout

    for baseline, row in cluster_baselines(in_frame, geo.baseline_tol):
        body_chars = [c for c in row if not (has_margin_column and _is_margin(c, geo))]
        margin_chars = [c for c in row if has_margin_column and _is_margin(c, geo)]

        for run in _split_runs(margin_chars, geo.run_gap):
            text = _render(run, geo.char_gap)
            if not text or _RULE_ONLY.match(text) or _PAGE_NUM_ONLY.match(text):
                continue
            layout.notes.append(
                MarginNote(
                    baseline=baseline,
                    x0=round(run[0]["x0"], 1),
                    text=text,
                    is_act_ref=bool(_ACT_REF.match(text)),
                )
            )

        if body_chars:
            text = _render(body_chars, geo.char_gap)
            if not text or _RULE_ONLY.match(text) or _PAGE_NUM_ONLY.match(text):
                continue
            fonts = {c.get("fontname", "") for c in body_chars}
            layout.body.append(
                Line(
                    baseline=baseline,
                    x0=round(body_chars[0]["x0"], 1),
                    x1=round(body_chars[-1]["x1"], 1),
                    size=round(max(c["size"] for c in body_chars), 1),
                    text=text,
                    bold=any("Bold" in f for f in fonts),
                    italic=any("Italic" in f or "Oblique" in f for f in fonts),
                )
            )

    return layout


def assemble_titles(notes: list[MarginNote]) -> list[tuple[float, str]]:
    """Join margin fragments into whole section titles.

    Titles are set over several short lines and terminate in a full stop::

        "Discharge of" / "person" / "apprehended."
            -> "Discharge of person apprehended"

    A fragment ending in a hyphen is a broken word, not a word boundary, so it
    joins without a space: "search- " + "warrant" -> "search-warrant".

    Returns ``(baseline_of_first_fragment, title)`` so callers can pair each
    title with the section it sits beside, rather than relying on order alone.
    """
    titles: list[tuple[float, str]] = []
    parts: list[str] = []
    start: float | None = None

    for note in notes:
        if note.is_act_ref:
            continue
        if not parts:
            start = note.baseline
        parts.append(note.text)
        if note.text.rstrip().endswith("."):
            titles.append((start or note.baseline, _join_fragments(parts)))
            parts = []

    if parts:  # unterminated tail - keep it rather than silently dropping it
        titles.append((start or 0.0, _join_fragments(parts)))
    return titles


def _join_fragments(parts: list[str]) -> str:
    out = ""
    for part in parts:
        if not out:
            out = part
        elif out.endswith("-"):
            out += part  # hyphenated line break: "non-" + "cognizable"
        else:
            out += " " + part
    return _WS.sub(" ", out).strip().rstrip(".").strip()


def profile_page_geometry(pages_chars: list[list[dict]]) -> PageGeometry:
    """Derive the column model from the document instead of assuming it.

    Falls back to the defaults when the document is too small or too irregular
    to measure, so an unusual upload degrades to sensible behaviour rather than
    failing outright.
    """
    sizes = [c["size"] for chars in pages_chars for c in chars]
    if not sizes:
        return PageGeometry()

    body_size = statistics.median(sizes)
    body_chars = [
        c for chars in pages_chars for c in chars if abs(c["size"] - body_size) < 0.6
    ]
    if len(body_chars) < 200:
        return PageGeometry()

    xs = sorted(c["x0"] for c in body_chars)
    x1s = sorted(c["x1"] for c in body_chars)
    lo = xs[int(len(xs) * 0.02)]
    hi = x1s[int(len(x1s) * 0.98)]

    return PageGeometry(
        body_min_x=round(lo - 4.0, 1),
        body_max_x=round(hi + 4.0, 1),
        body_size_min=round(body_size - 1.0, 1),
    )
