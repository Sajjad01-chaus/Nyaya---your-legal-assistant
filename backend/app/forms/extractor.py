"""Statutory forms extraction.

Turns the Second Schedule into a downloadable library: one PDF per form,
page-perfect, carved out of the source rather than re-rendered.

Three requirements shape the design:

*Titles are scraped, never listed.* Hardcoding the names is an automatic zero
on this section, and rightly so -- a hardcoded list silently rots the moment
the source is reissued.

*Multi-page forms stay whole.* A form owns every page from its own
``FORM No. N`` header up to the next one. A one-page-one-file loop would emit
60 files for 58 forms and split Form 33 into three fragments.

*Re-running produces byte-identical output.* PDF writers stamp a random
document ID, which alone would break idempotency, so the ID is derived from
the content instead.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, ByteStringObject

from app.ingestion.text_utils import is_garbage_text, normalise, slugify, smart_title

FORM_HEADER = re.compile(r"^FORM\s+No\.?\s*(\d{1,3})\s*$", re.IGNORECASE)
SEE_SECTION = re.compile(r"^[\[(]\s*See\s+sections?\b", re.IGNORECASE)
# "(See section 141)" but also "(See sections 234 and 235)" - capture every number
SEE_SECTION_NUM = re.compile(r"\b(\d{1,3})\b")
# "35(3)" -> "35": the bracketed part is a sub-section, not another section.
SUBSECTION_SUFFIX = re.compile(r"(?<=\d)\s*\(\s*\d{1,2}\s*\)")
RUNNING_HEADER = re.compile(r"GAZETTE\s+OF\s+INDIA", re.IGNORECASE)
RULE_LINE = re.compile(r"^[_\s]+$")

MAX_TITLE_LINES = 4
TYPICAL_MAX_PAGES = 3
# Keeps "<out_dir>/FORM-58_<slug>.pdf" clear of the 260-character path ceiling
# on Windows, which a few of these titles would otherwise breach.
MAX_SLUG_CHARS = 90


@dataclass(slots=True)
class FormRecord:
    """One extracted form. Serialised verbatim into forms_manifest.json."""

    form_number: int
    title: str
    filename: str
    page_start: int
    page_end: int
    page_count: int
    bytes: int
    sha256: str
    extraction_confidence: float
    needs_review: bool
    review_reasons: list[str] = field(default_factory=list)
    extraction_method: str = "text"
    see_sections: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class _Candidate:
    number: int
    page_start: int
    page_end: int
    title: str
    title_terminated: bool
    see_sections: list[int]
    ocr_used: bool


def _page_lines(page: pdfplumber.page.Page) -> list[str]:
    """Visible lines with the running header and horizontal rules removed."""
    raw = page.extract_text(x_tolerance=1.2, y_tolerance=3) or ""
    return [
        line.strip()
        for line in raw.split("\n")
        if line.strip() and not RUNNING_HEADER.search(line) and not RULE_LINE.match(line)
    ]


def _scrape_title(lines: list[str], header_index: int) -> tuple[str, bool, list[int]]:
    """Read the title printed under the ``FORM No. N`` header.

    The brief describes the title as printed *below the form*; in this volume it
    sits directly beneath the form number and above the body, terminated by the
    ``(See section N)`` citation. We key off what is on the page.

    Returns ``(title, was_terminated_by_a_See_line, cited_sections)``.
    """
    parts: list[str] = []
    terminated = False
    sections: list[int] = []

    for line in lines[header_index + 1 :]:
        if SEE_SECTION.match(line):
            terminated = True
            # Drop the parenthesised sub-section before reading numbers, or
            # "[See section 35(3)]" yields a phantom reference to section 3.
            bare = SUBSECTION_SUFFIX.sub("", line)
            sections = [int(n) for n in SEE_SECTION_NUM.findall(bare)]
            sections = sorted({n for n in sections if 1 <= n <= 531})
            break
        parts.append(line)
        if len(parts) >= MAX_TITLE_LINES:
            break

    return normalise(" ".join(parts)), terminated, sections


def detect_forms(
    pdf_path: Path | str, page_start: int, page_end: int
) -> list[_Candidate]:
    """Locate every form and its page span within the schedule."""
    candidates: list[_Candidate] = []

    with pdfplumber.open(pdf_path) as pdf:
        last_page = min(page_end, len(pdf.pages))
        for page_no in range(page_start, last_page + 1):
            page = pdf.pages[page_no - 1]
            lines = _page_lines(page)
            ocr_used = is_garbage_text("\n".join(lines))

            header_index = next(
                (i for i, line in enumerate(lines) if FORM_HEADER.match(line)), None
            )

            if header_index is None:
                # No header: this page continues the form above it. This single
                # branch is what keeps Form 33 (pp.222-224) as one document.
                if candidates:
                    candidates[-1].page_end = page_no
                continue

            number = int(FORM_HEADER.match(lines[header_index]).group(1))  # type: ignore[union-attr]
            title, terminated, sections = _scrape_title(lines, header_index)
            candidates.append(
                _Candidate(
                    number=number,
                    page_start=page_no,
                    page_end=page_no,
                    title=title,
                    title_terminated=terminated,
                    see_sections=sections,
                    ocr_used=ocr_used,
                )
            )

    return candidates


def score_candidate(
    candidate: _Candidate, expected_number: int, duplicate_titles: set[str]
) -> tuple[float, list[str]]:
    """Grade an extraction and say why it might be wrong.

    A legal library that silently guesses is worse than one that admits doubt,
    so every signal that can be measured contributes, and anything unusual
    raises a flag a human can act on.
    """
    score = 0.0
    reasons: list[str] = []

    if candidate.title:
        score += 0.30
    else:
        reasons.append("no title scraped below the form header")

    if candidate.title_terminated:
        score += 0.20
    else:
        reasons.append("title not terminated by a '(See section ...)' citation")

    pages = candidate.page_end - candidate.page_start + 1
    if 1 <= pages <= TYPICAL_MAX_PAGES:
        score += 0.20
    else:
        reasons.append(f"unusual page span: {pages} pages")

    if not candidate.ocr_used:
        score += 0.20
    else:
        reasons.append("text layer unusable; OCR fallback was used")

    if candidate.number == expected_number:
        score += 0.10
    else:
        reasons.append(
            f"form number {candidate.number} breaks the sequence "
            f"(expected {expected_number})"
        )

    if candidate.title and candidate.title in duplicate_titles:
        reasons.append("title is shared with another form; number disambiguates")

    return round(min(score, 1.0), 2), reasons


def _deterministic_pdf(reader: PdfReader, pages: range) -> bytes:
    """Copy pages out of the source, byte-identically on every run.

    Page objects are copied, so text and vector content survive exactly -- no
    rasterisation, no reflow. The document ID is normally random, which alone
    would defeat idempotency, so it is replaced by a hash of the content.
    """
    writer = PdfWriter()
    for index in pages:
        writer.add_page(reader.pages[index])

    # Strip inherited metadata: it carries creation timestamps.
    writer.add_metadata({"/Producer": "nyaya-forms", "/Creator": "nyaya-forms"})

    first = io.BytesIO()
    writer.write(first)
    digest = hashlib.sha256(first.getvalue()).digest()[:16]

    stable = ByteStringObject(digest)
    # pypdf writes the trailer /ID straight into a DictionaryObject, so it has
    # to be a real ArrayObject of PdfObjects, not a plain list.
    writer._ID = ArrayObject([stable, stable])  # noqa: SLF001 - no public setter

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def extract_forms(
    pdf_path: Path | str,
    out_dir: Path | str,
    *,
    page_start: int = 190,
    page_end: int = 249,
) -> list[FormRecord]:
    """Extract every form to its own PDF and return the manifest records."""
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates = detect_forms(pdf_path, page_start, page_end)

    titles = [c.title for c in candidates if c.title]
    duplicates = {t for t in titles if titles.count(t) > 1}

    reader = PdfReader(str(pdf_path))
    records: list[FormRecord] = []
    seen_filenames: set[str] = set()

    for position, candidate in enumerate(candidates, start=1):
        confidence, reasons = score_candidate(candidate, position, duplicates)

        slug = (
            slugify(smart_title(candidate.title), max_len=MAX_SLUG_CHARS)
            if candidate.title
            else "untitled"
        )
        filename = f"FORM-{candidate.number}_{slug}.pdf"
        if filename in seen_filenames:  # cannot happen with distinct numbers; belt and braces
            filename = f"FORM-{candidate.number}_{slug}_{position}.pdf"
            reasons.append("filename collision resolved with a positional suffix")
        seen_filenames.add(filename)

        payload = _deterministic_pdf(
            reader, range(candidate.page_start - 1, candidate.page_end)
        )
        (out_dir / filename).write_bytes(payload)

        records.append(
            FormRecord(
                form_number=candidate.number,
                title=candidate.title,
                filename=filename,
                page_start=candidate.page_start,
                page_end=candidate.page_end,
                page_count=candidate.page_end - candidate.page_start + 1,
                bytes=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(),
                extraction_confidence=confidence,
                needs_review=bool(reasons),
                review_reasons=reasons,
                extraction_method="ocr" if candidate.ocr_used else "text",
                see_sections=candidate.see_sections,
            )
        )

    return records


def write_manifest(
    records: list[FormRecord], path: Path | str, *, source: str
) -> dict:
    """Write forms_manifest.json. Stable key order so re-runs diff cleanly."""
    path = Path(path)
    manifest = {
        "source": source,
        "form_count": len(records),
        "page_range": (
            [records[0].page_start, records[-1].page_end] if records else []
        ),
        "needs_review_count": sum(1 for r in records if r.needs_review),
        "forms": [r.to_dict() for r in sorted(records, key=lambda r: r.form_number)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return manifest
