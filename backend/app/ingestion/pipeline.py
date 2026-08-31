"""End-to-end ingestion: PDF in, indexed corpus out.

Used by the bootstrap script for the bare act and by the worker for user
uploads. Both paths share the same profiling, chunking and embedding, so a
scanned FIR and a typeset gazette differ in which parser runs, not in what
happens afterwards.

Idempotent by construction. Point ids derive from chunk ids, so re-running
overwrites rather than duplicating, and the caller can re-run bootstrap freely.
"""

from __future__ import annotations

import hashlib
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pdfplumber

from app.core.logging import get_logger

from .layout import PageGeometry
from .statute import Chunk, parse_statute
from .text_utils import is_garbage_text
from .types import PageKind, PageProfile

logger = get_logger(__name__)

ProgressFn = Callable[[str, float], None]


@dataclass(slots=True)
class ParseReport:
    """What happened, per page, so quality is inspectable rather than assumed."""

    source: str
    sha256: str
    page_count: int
    pages_text: int = 0
    pages_ocr: int = 0
    pages_garbage: int = 0
    pages_empty: int = 0
    section_count: int = 0
    chunk_count: int = 0
    mean_confidence: float = 1.0
    needs_review: bool = False
    warnings: list[str] = field(default_factory=list)
    per_page: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "sha256": self.sha256,
            "page_count": self.page_count,
            "pages_text": self.pages_text,
            "pages_ocr": self.pages_ocr,
            "pages_garbage": self.pages_garbage,
            "pages_empty": self.pages_empty,
            "section_count": self.section_count,
            "chunk_count": self.chunk_count,
            "mean_confidence": round(self.mean_confidence, 3),
            "needs_review": self.needs_review,
            "warnings": self.warnings,
        }


def profile_page(page, page_number: int) -> PageProfile:  # noqa: ANN001
    """L1: cheap measurements that decide which parser runs.

    Never trusts the presence of text as evidence that the text is usable --
    the hard case is a text layer that exists and is wrong.
    """
    chars = page.chars
    text = page.extract_text(x_tolerance=1.2) or ""
    sizes = [c["size"] for c in chars]
    median_size = statistics.median(sizes) if sizes else 0.0

    printable = sum(ch.isprintable() for ch in text)
    printable_ratio = printable / len(text) if text else 0.0
    replacement_ratio = text.count("�") / len(text) if text else 0.0

    lines = page.lines or []
    horizontal = sum(1 for line in lines if abs(line["y0"] - line["y1"]) < 1)
    vertical = sum(1 for line in lines if abs(line["x0"] - line["x1"]) < 1)

    image_area = sum(
        (img["x1"] - img["x0"]) * (img["bottom"] - img["top"]) for img in (page.images or [])
    )
    coverage = image_area / (page.width * page.height) if page.width else 0.0

    x0s = sorted({round(c["x0"] / 10) * 10 for c in chars})

    if not chars:
        kind = PageKind.EMPTY
    elif is_garbage_text(text):
        # Either no usable text layer or a lying one; both go to OCR.
        kind = PageKind.SCANNED if coverage > 0.5 else PageKind.GARBAGE_TEXT
    elif vertical >= 2 and horizontal >= 2:
        kind = PageKind.TEXT_TABLE_RULED
    elif median_size < 9.0 and len(x0s) >= 5:
        kind = PageKind.TEXT_TABLE_UNRULED
    else:
        kind = PageKind.TEXT_PROSE

    return PageProfile(
        page=page_number,
        width=round(page.width, 1),
        height=round(page.height, 1),
        char_count=len(chars),
        printable_ratio=round(printable_ratio, 3),
        replacement_ratio=round(replacement_ratio, 4),
        median_font_size=round(median_size, 1),
        font_names=tuple(sorted({c.get("fontname", "") for c in chars})[:6]),
        x0_clusters=tuple(x0s[:12]),
        horizontal_rules=horizontal,
        vertical_rules=vertical,
        image_coverage=round(coverage, 3),
        has_margin_column=median_size >= 9.5,
        kind=kind,
    )


def profile_document(pdf_path: Path | str) -> tuple[list[PageProfile], ParseReport]:
    """L0 + L1 over a whole document."""
    pdf_path = Path(pdf_path)
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()

    profiles: list[PageProfile] = []
    with pdfplumber.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages):
            profiles.append(profile_page(page, index + 1))

    report = ParseReport(
        source=pdf_path.name, sha256=digest, page_count=len(profiles)
    )
    for profile in profiles:
        if profile.kind is PageKind.EMPTY:
            report.pages_empty += 1
        elif profile.kind is PageKind.SCANNED:
            report.pages_ocr += 1
        elif profile.kind is PageKind.GARBAGE_TEXT:
            report.pages_garbage += 1
        else:
            report.pages_text += 1
        report.per_page.append(
            {"page": profile.page, "kind": profile.kind.value,
             "chars": profile.char_count, "size": profile.median_font_size}
        )
    return profiles, report


def parse_bare_act(
    pdf_path: Path | str,
    *,
    act: str,
    act_short: str,
    last_operative_page: int,
    max_chars: int = 2048,
    progress: ProgressFn | None = None,
) -> tuple[list[Chunk], ParseReport]:
    """Parse the operative text of a bare act into chunks."""
    pdf_path = Path(pdf_path)
    started = time.perf_counter()

    if progress:
        progress("profiling", 0.05)
    profiles, report = profile_document(pdf_path)

    if progress:
        progress("parsing", 0.20)
    with pdfplumber.open(pdf_path) as pdf:
        pages_chars = [
            pdf.pages[i].chars for i in range(min(last_operative_page, len(pdf.pages)))
        ]

    sections, chunks = parse_statute(
        pages_chars,
        act=act,
        act_short=act_short,
        source_uri=f"file://{pdf_path.name}",
        geometry=PageGeometry(),
        max_chars=max_chars,
    )

    report.section_count = len(sections)
    report.chunk_count = len(chunks)

    # ---- L6 gate: a parse that lost sections must fail loudly, not index quietly
    numbers = sorted(s.number for s in sections)
    if numbers:
        missing = [n for n in range(1, numbers[-1] + 1) if n not in set(numbers)]
        if missing:
            report.warnings.append(f"missing section numbers: {missing[:20]}")
            report.needs_review = True
    untitled = [s.number for s in sections if not s.title]
    if untitled:
        report.warnings.append(f"sections with no title: {untitled[:20]}")
        report.needs_review = True
    if report.pages_garbage:
        report.warnings.append(
            f"{report.pages_garbage} page(s) had an unusable text layer"
        )

    _ = profiles
    logger.info(
        "parsed bare act",
        act=act_short,
        sections=len(sections),
        chunks=len(chunks),
        seconds=round(time.perf_counter() - started, 1),
        needs_review=report.needs_review,
    )
    if progress:
        progress("parsed", 0.40)
    return chunks, report


def chunk_payload(chunk: Chunk) -> dict:
    """The Qdrant payload: everything retrieval filters on or cites."""
    return {
        "act": chunk.act,
        "act_short": chunk.act_short,
        "chapter": chunk.chapter or "",
        "chapter_title": chunk.chapter_title or "",
        "part": chunk.part or "",
        "part_title": chunk.part_title or "",
        "section_number": chunk.section_number,
        "section_title": chunk.section_title,
        "subsection": chunk.subsection,
        "clause": chunk.clause,
        "text": chunk.text,
        "breadcrumb": chunk.breadcrumb,
        "has_proviso": chunk.has_proviso,
        "has_explanation": chunk.has_explanation,
        "has_illustration": chunk.has_illustration,
        "has_exception": chunk.has_exception,
        "references": chunk.references,
        "external_acts": chunk.external_acts,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "part_index": chunk.part_index,
        "part_total": chunk.part_total,
        "source_uri": chunk.source_uri,
        "ingested_at": chunk.ingested_at,
    }


async def index_chunks(
    chunks: list[Chunk],
    *,
    store,
    embedder,
    collection: str,
    batch_size: int = 64,
    progress: ProgressFn | None = None,
) -> int:
    """Embed and upsert. Re-running overwrites the same points."""
    await store.ensure_collection(collection, embedder.config.dim)

    total = 0
    started = time.perf_counter()
    for offset in range(0, len(chunks), batch_size):
        batch = chunks[offset : offset + batch_size]
        texts = [c.embedding_text() for c in batch]

        dense = embedder.embed_passages(texts)
        sparse = embedder.sparse_passages(texts)

        total += await store.upsert(
            collection,
            ids=[c.chunk_id for c in batch],
            dense=dense,
            sparse=sparse,
            payloads=[chunk_payload(c) for c in batch],
        )
        if progress:
            progress("embedding", 0.4 + 0.6 * (offset + len(batch)) / max(len(chunks), 1))
        logger.info(
            "indexed batch",
            collection=collection,
            done=total,
            of=len(chunks),
            elapsed_s=round(time.perf_counter() - started, 1),
        )

    logger.info(
        "indexing complete",
        collection=collection,
        chunks=total,
        seconds=round(time.perf_counter() - started, 1),
        per_second=round(total / max(time.perf_counter() - started, 0.001), 1),
    )
    return total
