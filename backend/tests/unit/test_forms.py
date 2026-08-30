"""Forms pipeline.

The fixture is laid out so the multi-page case is exercised without the full
corpus: fixture pages 8, 9, 10 are source pages 222, 223, 224 -- Form 33 and
its two continuation pages, which carry no ``FORM No.`` header of their own.
Fixture page 11 is source page 225 (Form 34), proving the run terminates.
"""

from __future__ import annotations

import json

import pytest

from app.forms.extractor import (
    _scrape_title,
    detect_forms,
    extract_forms,
    score_candidate,
    write_manifest,
)

FORMS_FIRST, FORMS_LAST = 7, 12  # form pages within the fixture


@pytest.fixture(scope="module")
def candidates(sample_pdf_path):
    return detect_forms(sample_pdf_path, FORMS_FIRST, FORMS_LAST)


class TestFormDetection:
    def test_finds_each_form_header(self, candidates) -> None:
        assert [c.number for c in candidates] == [1, 33, 34, 58]

    def test_multi_page_form_is_kept_whole(self, candidates) -> None:
        """The requirement a one-page-one-file loop fails."""
        form33 = next(c for c in candidates if c.number == 33)
        assert (form33.page_start, form33.page_end) == (8, 10)

    def test_continuation_pages_do_not_become_their_own_forms(self, candidates) -> None:
        assert len(candidates) == 4, "a continuation page was mistaken for a form"

    def test_single_page_form_after_a_multi_page_one(self, candidates) -> None:
        form34 = next(c for c in candidates if c.number == 34)
        assert form34.page_start == form34.page_end == 11


class TestTitleScraping:
    def test_titles_come_from_the_page(self, candidates) -> None:
        titles = {c.number: c.title for c in candidates}
        assert titles[1] == "NOTICE FOR APPEARANCE BY THE POLICE"
        assert titles[33] == "CHARGES"
        assert titles[34] == "SUMMONS TO WITNESS"

    def test_title_stops_at_the_see_section_citation(self) -> None:
        lines = [
            "FORM No. 7",
            "ORDER OF ATTACHMENT TO COMPEL THE ATTENDANCE OF A WITNESS",
            "(See section 85)",
            "To the officer in charge of the police station at......",
        ]
        title, terminated, sections = _scrape_title(lines, 0)
        assert title == "ORDER OF ATTACHMENT TO COMPEL THE ATTENDANCE OF A WITNESS"
        assert terminated is True
        assert sections == [85]

    def test_subsection_is_not_read_as_a_section(self) -> None:
        """'[See section 35(3)]' cites s.35, not s.35 and s.3."""
        _, _, sections = _scrape_title(["FORM No.1", "NOTICE", "[See section 35(3)]"], 0)
        assert sections == [35]

    def test_plural_citation_captures_every_section(self) -> None:
        _, _, sections = _scrape_title(
            ["FORM No. 33", "CHARGES", "(See sections 234, 235 and 236)"], 0
        )
        assert sections == [234, 235, 236]


class TestConfidenceScoring:
    def test_clean_extraction_scores_full_marks(self, candidates) -> None:
        form1 = next(c for c in candidates if c.number == 1)
        score, reasons = score_candidate(form1, expected_number=1, duplicate_titles=set())
        assert score == 1.0
        assert reasons == []

    def test_missing_title_is_flagged(self, candidates) -> None:
        form1 = next(c for c in candidates if c.number == 1)
        form1.title = ""
        try:
            score, reasons = score_candidate(form1, 1, set())
            assert score < 1.0
            assert any("no title" in r for r in reasons)
        finally:
            form1.title = "NOTICE FOR APPEARANCE BY THE POLICE"

    def test_sequence_break_is_flagged(self, candidates) -> None:
        form33 = next(c for c in candidates if c.number == 33)
        _, reasons = score_candidate(form33, expected_number=2, duplicate_titles=set())
        assert any("breaks the sequence" in r for r in reasons)

    def test_shared_title_is_flagged_but_not_penalised(self, candidates) -> None:
        form1 = next(c for c in candidates if c.number == 1)
        score, reasons = score_candidate(form1, 1, {form1.title})
        assert score == 1.0  # the form number still disambiguates the filename
        assert any("shared with another form" in r for r in reasons)


@pytest.fixture(scope="module")
def extracted(sample_pdf_path, tmp_path_factory):
    out = tmp_path_factory.mktemp("forms")
    records = extract_forms(
        sample_pdf_path, out, page_start=FORMS_FIRST, page_end=FORMS_LAST
    )
    return records, out


class TestExtraction:

    def test_filenames_follow_the_convention(self, extracted) -> None:
        records, _ = extracted
        by_num = {r.form_number: r.filename for r in records}
        assert by_num[1] == "FORM-1_Notice-for-Appearance-by-the-Police.pdf"
        assert by_num[33] == "FORM-33_Charges.pdf"

    def test_filenames_are_filesystem_safe(self, extracted) -> None:
        records, _ = extracted
        for record in records:
            assert not set(record.filename) & set(' /\\:*?"<>|')

    def test_multi_page_form_produces_one_file_of_three_pages(self, extracted) -> None:
        records, out_dir = extracted
        form33 = next(r for r in records if r.form_number == 33)
        assert form33.page_count == 3
        assert (out_dir / form33.filename).exists()

        from pypdf import PdfReader

        assert len(PdfReader(str(out_dir / form33.filename)).pages) == 3

    def test_output_keeps_a_real_text_layer(self, extracted) -> None:
        """Pages are copied, not rasterised, so the text survives."""
        records, out_dir = extracted
        form1 = next(r for r in records if r.form_number == 1)

        from pypdf import PdfReader

        text = PdfReader(str(out_dir / form1.filename)).pages[0].extract_text()
        assert "NOTICE FOR APPEARANCE" in text.upper()

    def test_rerun_is_byte_identical(self, sample_pdf_path, tmp_path) -> None:
        """Idempotency: PDF writers stamp a random /ID unless it is pinned."""
        first = extract_forms(sample_pdf_path, tmp_path / "a", page_start=FORMS_FIRST,
                              page_end=FORMS_LAST)
        second = extract_forms(sample_pdf_path, tmp_path / "b", page_start=FORMS_FIRST,
                               page_end=FORMS_LAST)
        assert [r.sha256 for r in first] == [r.sha256 for r in second]

    def test_sha256_matches_the_file_on_disk(self, extracted) -> None:
        import hashlib

        records, out_dir = extracted
        for record in records:
            payload = (out_dir / record.filename).read_bytes()
            assert hashlib.sha256(payload).hexdigest() == record.sha256
            assert len(payload) == record.bytes


class TestManifest:
    def test_manifest_carries_every_required_field(self, sample_pdf_path, tmp_path) -> None:
        records = extract_forms(
            sample_pdf_path, tmp_path, page_start=FORMS_FIRST, page_end=FORMS_LAST
        )
        path = tmp_path / "forms_manifest.json"
        write_manifest(records, path, source="bnss_sample.pdf")

        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["form_count"] == 4
        required = {
            "form_number", "title", "filename", "page_start", "page_end",
            "bytes", "sha256", "extraction_confidence", "needs_review",
        }
        assert required <= set(manifest["forms"][0])

    def test_manifest_is_sorted_by_form_number(self, sample_pdf_path, tmp_path) -> None:
        records = extract_forms(
            sample_pdf_path, tmp_path, page_start=FORMS_FIRST, page_end=FORMS_LAST
        )
        path = tmp_path / "m.json"
        manifest = write_manifest(records, path, source="x")
        numbers = [f["form_number"] for f in manifest["forms"]]
        assert numbers == sorted(numbers)
