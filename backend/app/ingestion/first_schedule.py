"""Extract the First Schedule (Classification of Offences) from BNSS PDF."""

from __future__ import annotations

import pdfplumber
import re
from datetime import datetime, timezone
from pathlib import Path

from .statute import Chunk


def extract_first_schedule(pdf_path: Path | str, page_start: int = 158, page_end: int = 189) -> list[Chunk]:
    """Extract First Schedule text table by section markers (e.g., 49, 50, 65(2), 70(1))."""
    chunks = []
    ingested_at = datetime.now(timezone.utc).isoformat()
    chunk_num = 0

    with pdfplumber.open(pdf_path) as pdf:
        full_text = []
        for page_num in range(page_start - 1, min(page_end, len(pdf.pages))):
            text = pdf.pages[page_num].extract_text()
            if text:
                full_text.append(text)

        if not full_text:
            return []

        combined = "\n".join(full_text)
        lines = combined.split("\n")
        current_chunk = []

        for line in lines:
            stripped = line.strip()
            # Section marker: digit(s) optionally (subsection) then space
            # Examples: "49 ", "65(2) ", "70(1) "
            is_section_start = re.match(r'^\d{1,3}(\([a-z0-9]+\))?\s', stripped)

            if is_section_start and current_chunk:
                chunk_text = "\n".join(current_chunk).strip()
                if len(chunk_text) > 20:
                    chunks.append(Chunk(
                        act="Bharatiya Nagarik Suraksha Sanhita, 2023",
                        act_short="BNSS",
                        chapter=None,
                        chapter_title=None,
                        section_number="First Schedule",
                        section_title="Classification of Offences",
                        subsection=None,
                        clause=None,
                        text=chunk_text,
                        has_illustration=False,
                        has_proviso=False,
                        has_exception=False,
                        page_start=page_start,
                        page_end=page_end,
                        chunk_id=f"bnss-schedule-{chunk_num}",
                        source_uri="",
                        ingested_at=ingested_at,
                    ))
                    chunk_num += 1
                current_chunk = [line]
            elif stripped:
                current_chunk.append(line)

        if current_chunk:
            chunk_text = "\n".join(current_chunk).strip()
            if len(chunk_text) > 20:
                chunks.append(Chunk(
                    act="Bharatiya Nagarik Suraksha Sanhita, 2023",
                    act_short="BNSS",
                    chapter=None,
                    chapter_title=None,
                    section_number="First Schedule",
                    section_title="Classification of Offences",
                    subsection=None,
                    clause=None,
                    text=chunk_text,
                    has_illustration=False,
                    has_proviso=False,
                    has_exception=False,
                    page_start=page_start,
                    page_end=page_end,
                    chunk_id=f"bnss-schedule-{chunk_num}",
                    source_uri="",
                    ingested_at=ingested_at,
                ))

    return chunks
