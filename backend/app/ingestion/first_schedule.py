"""Extract the First Schedule (Classification of Offences) from BNSS PDF.

Pages 158-189 contain a 6-column table:
  1. Section number (BNS)
  2. Section title / offence
  3. Punishment
  4. Cognizable/Non-cognizable
  5. Bailable/Non-bailable
  6. Trial court

This is where crime definitions and punishments live. Parse it into indexable chunks.
"""

from __future__ import annotations

import pdfplumber
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .statute import Chunk


@dataclass(slots=True)
class OffenceRow:
    """One row from the First Schedule offence table."""
    section: str
    title: str
    punishment: str
    cognizable: str
    bail: str
    court: str


def extract_first_schedule(pdf_path: Path | str, page_start: int = 158, page_end: int = 189) -> list[Chunk]:
    """Extract First Schedule with offense-boundary chunking for legal documents.

    Each offense entry becomes its own chunk instead of 50-line fixed chunks.
    Pattern: Section number at line start (e.g., "64(1)", "65", "70(2)") marks new offense.
    """
    chunks = []
    ingested_at = datetime.now(timezone.utc).isoformat()

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
        chunk_num = 0

        for line in lines:
            stripped = line.strip()

            # Check if line starts an offense: digit(s) at start, optionally with subsection like (1) or (2)
            if re.match(r'^\d+(\([0-9a-z]{1,3}\))?[\s]', stripped) and current_chunk:
                # Save previous chunk
                chunk_text = "\n".join(current_chunk).strip()
                if len(chunk_text) > 30:  # Minimum chunk size
                    chunks.append(Chunk(
                        act="Bharatiya Nagarik Suraksha Sanhita, 2023",
                        act_short="BNSS",
                        chapter=None,
                        chapter_title=None,
                        section_number="First Schedule",
                        section_title="Classification of Offences - Offence Schedule",
                        subsection=None,
                        clause=None,
                        text=chunk_text,
                        has_illustration=False,
                        has_proviso=False,
                        has_exception=False,
                        page_start=page_start,
                        page_end=page_end,
                        chunk_id=f"bnss-schedule-offense-{chunk_num}",
                        source_uri="",
                        ingested_at=ingested_at,
                    ))
                    chunk_num += 1
                current_chunk = [line]
            else:
                current_chunk.append(line)

        # Save final chunk
        if current_chunk:
            chunk_text = "\n".join(current_chunk).strip()
            if len(chunk_text) > 30:
                chunks.append(Chunk(
                    act="Bharatiya Nagarik Suraksha Sanhita, 2023",
                    act_short="BNSS",
                    chapter=None,
                    chapter_title=None,
                    section_number="First Schedule",
                    section_title="Classification of Offences - Offence Schedule",
                    subsection=None,
                    clause=None,
                    text=chunk_text,
                    has_illustration=False,
                    has_proviso=False,
                    has_exception=False,
                    page_start=page_start,
                    page_end=page_end,
                    chunk_id=f"bnss-schedule-offense-{chunk_num}",
                    source_uri="",
                    ingested_at=ingested_at,
                ))

    return chunks
