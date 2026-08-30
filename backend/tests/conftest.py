"""Shared fixtures.

``bnss_sample.pdf`` holds twelve pages lifted from the source gazette, chosen to
cover every layout hazard the parser has to survive. Committing it means the
parser assertions run in CI without the (gitignored) full corpus.

    fixture page -> source page
        1  ->   1   masthead; Hindi in a legacy font with no ToUnicode map
        2  ->  13   recto: margin notes on the right, chapter opening
        3  ->  16   verso: margin notes mirrored to the left
        4  ->  20   margin note spliced onto a body line
        5  ->  88   s.279, whose margin note overflows the column threshold
        6  -> 158   First Schedule opening; 8pt table type, no ruling lines
        7  -> 190   Second Schedule, Form 1
        8  -> 222   Form 33 page 1 of 3
        9  -> 223   Form 33 page 2 of 3  (no FORM header - continuation)
       10  -> 224   Form 33 page 3 of 3  (no FORM header - continuation)
       11  -> 225   Form 34, proving the run ended
       12  -> 249   Form 58 and the colophon
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_PDF = FIXTURES / "bnss_sample.pdf"

# fixture page number -> page number in the source gazette
SOURCE_PAGE = {1: 1, 2: 13, 3: 16, 4: 20, 5: 88, 6: 158,
               7: 190, 8: 222, 9: 223, 10: 224, 11: 225, 12: 249}


@pytest.fixture(scope="session")
def sample_pdf_path() -> Path:
    assert SAMPLE_PDF.exists(), f"missing test fixture: {SAMPLE_PDF}"
    return SAMPLE_PDF


@pytest.fixture(scope="session")
def sample_pages(sample_pdf_path: Path) -> list[list[dict]]:
    """Per-page character lists, as pdfplumber yields them."""
    with pdfplumber.open(sample_pdf_path) as pdf:
        return [page.chars for page in pdf.pages]


@pytest.fixture(scope="session")
def full_pdf_path() -> Path | None:
    """The complete corpus, when present. Tests using it skip when it is not."""
    for candidate in (
        Path("/data/raw/bnss_2023.pdf"),
        Path(__file__).resolve().parents[3] / "data" / "raw" / "bnss_2023.pdf",
    ):
        if candidate.exists():
            return candidate
    return None
