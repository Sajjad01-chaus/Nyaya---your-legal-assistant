"""Parsing for arbitrary uploaded documents.

An FIR, a notice, an agreement or a judgment has none of the structure a bare
act has: no section ladder, no marginal notes, no predictable typesetting. So
this path keeps only what generalises -- profile each page, pick a parser, and
chunk on paragraph boundaries with the page number carried through so a citation
back into the user's own document can name a page.

Header and footer removal is by frequency rather than by fixed geometry: text
that repeats at the same vertical position across most pages is furniture,
whatever the document is.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pdfplumber

from app.core.logging import get_logger

from .text_utils import dehyphenate, is_garbage_text, normalise
from .types import PageKind

logger = get_logger(__name__)

MIN_CHUNK_CHARS = 200
MAX_CHUNK_CHARS = 1600
OVERLAP_CHARS = 150
_PARAGRAPH = re.compile(r"\n\s*\n")


@dataclass(slots=True)
class DocumentChunk:
    text: str
    page_start: int
    page_end: int
    index: int
    method: str = "text"
    confidence: float = 1.0

    def to_payload(self, *, document_id: str, session_id: str, filename: str) -> dict:
        return {
            "document_id": document_id,
            "session_id": session_id,
            "filename": filename,
            "text": self.text,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "chunk_index": self.index,
            "extraction_method": self.method,
            "extraction_confidence": self.confidence,
            # Kept so a user-document citation is visually distinct from a
            # statutory one and can never be mistaken for authority.
            "act_short": "DOC",
            "section_number": f"p{self.page_start}",
            "section_title": filename,
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }


@dataclass(slots=True)
class DocumentParseResult:
    chunks: list[DocumentChunk] = field(default_factory=list)
    page_count: int = 0
    pages_ocr: int = 0
    pages_garbage: int = 0
    mean_confidence: float = 1.0
    needs_review: bool = False
    warnings: list[str] = field(default_factory=list)

    def report(self) -> dict:
        return {
            "page_count": self.page_count,
            "pages_ocr": self.pages_ocr,
            "pages_garbage": self.pages_garbage,
            "chunk_count": len(self.chunks),
            "mean_confidence": round(self.mean_confidence, 3),
            "needs_review": self.needs_review,
            "warnings": self.warnings,
        }


def _repeated_furniture(pages: list[list[str]], threshold: float = 0.6) -> set[str]:
    """Lines appearing on most pages are headers, footers or watermarks.

    Generic by construction: no fixed y-coordinate, no document-specific rule.
    """
    if len(pages) < 3:
        return set()
    counts: Counter[str] = Counter()
    for lines in pages:
        for line in set(lines[:3] + lines[-3:]):
            stripped = line.strip()
            if 3 < len(stripped) < 120:
                counts[stripped] += 1
    cutoff = max(2, int(len(pages) * threshold))
    return {line for line, count in counts.items() if count >= cutoff}


def _ocr_page(page) -> tuple[str, float]:  # noqa: ANN001
    """Rasterise and OCR a page whose text layer is missing or unusable."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:  # pragma: no cover - OCR is optional at dev time
        return "", 0.0

    try:
        image = page.to_image(resolution=200).original
        if not isinstance(image, Image.Image):
            return "", 0.0
        data = pytesseract.image_to_data(
            image, output_type=pytesseract.Output.DICT
        )
        words = [w for w in data["text"] if w.strip()]
        confidences = [
            int(c) for c in data["conf"] if str(c).lstrip("-").isdigit() and int(c) >= 0
        ]
        text = " ".join(words)
        confidence = (sum(confidences) / len(confidences) / 100) if confidences else 0.0
        return text, round(confidence, 3)
    except Exception as exc:  # noqa: BLE001 - OCR failure must not kill ingestion
        logger.warning("ocr failed", error=str(exc))
        return "", 0.0


def parse_document(path: Path | str, *, ocr_enabled: bool = True) -> DocumentParseResult:
    """Extract text from an arbitrary PDF and chunk it for retrieval."""
    path = Path(path)
    result = DocumentParseResult()

    page_texts: list[tuple[int, str, str, float]] = []   # page, text, method, confidence

    with pdfplumber.open(path) as pdf:
        result.page_count = len(pdf.pages)
        raw_lines: list[list[str]] = []

        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=1.5) or ""
            method, confidence = "text", 1.0

            if is_garbage_text(text):
                # Missing text layer, or one that decodes to noise. Both need
                # the pixels, not the bytes.
                result.pages_garbage += 1
                if ocr_enabled:
                    ocr_text, ocr_confidence = _ocr_page(page)
                    if ocr_text.strip():
                        text, method, confidence = ocr_text, "ocr", ocr_confidence
                        result.pages_ocr += 1
                    else:
                        confidence = 0.0
                else:
                    confidence = 0.0

            raw_lines.append([ln.strip() for ln in text.split("\n") if ln.strip()])
            page_texts.append((index, text, method, confidence))

    furniture = _repeated_furniture(raw_lines)
    if furniture:
        logger.info("dropping repeated furniture", lines=len(furniture))

    cleaned: list[tuple[int, str, str, float]] = []
    for index, text, method, confidence in page_texts:
        lines = [ln for ln in text.split("\n") if ln.strip() not in furniture]
        cleaned.append((index, dehyphenate("\n".join(lines)), method, confidence))

    result.chunks = _chunk_pages(cleaned)

    confidences = [c.confidence for c in result.chunks] or [0.0]
    result.mean_confidence = sum(confidences) / len(confidences)
    result.needs_review = (
        result.mean_confidence < 0.75
        or result.pages_garbage > 0
        or not result.chunks
    )
    if result.pages_garbage:
        result.warnings.append(
            f"{result.pages_garbage} page(s) had no usable text layer; "
            f"{result.pages_ocr} recovered by OCR"
        )
    if not result.chunks:
        result.warnings.append("no readable text was extracted")

    return result


def _chunk_pages(pages: list[tuple[int, str, str, float]]) -> list[DocumentChunk]:
    """Chunk on paragraph boundaries, with a little overlap across the seam.

    Overlap is small and applies only where a split lands mid-argument. Legal
    documents carry meaning across sentence boundaries far more than across
    paragraph ones, so paragraphs are the natural cut and the overlap is
    insurance rather than strategy.
    """
    chunks: list[DocumentChunk] = []
    buffer = ""
    start_page = pages[0][0] if pages else 1
    end_page = start_page
    method, confidence = "text", 1.0

    def flush() -> None:
        nonlocal buffer, start_page, end_page
        text = normalise(buffer)
        if len(text) >= MIN_CHUNK_CHARS or (text and not chunks):
            chunks.append(
                DocumentChunk(
                    text=text, page_start=start_page, page_end=end_page,
                    index=len(chunks), method=method, confidence=confidence,
                )
            )
        buffer = ""

    for page_no, text, page_method, page_confidence in pages:
        if not text.strip():
            continue
        method, confidence = page_method, page_confidence
        for paragraph in _PARAGRAPH.split(text):
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            if buffer and len(buffer) + len(paragraph) > MAX_CHUNK_CHARS:
                end_page = page_no
                tail = buffer[-OVERLAP_CHARS:]
                flush()
                buffer = tail + " "
                start_page = page_no
            if not buffer:
                start_page = page_no
            buffer += paragraph + "\n\n"
        end_page = page_no

    if buffer.strip():
        flush()
    return chunks


__all__ = ["DocumentChunk", "DocumentParseResult", "PageKind", "parse_document"]
