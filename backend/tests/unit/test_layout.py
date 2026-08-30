"""Layout engine: the three hazards that defeat naive extraction."""

from __future__ import annotations

import re

import pytest

from app.ingestion.layout import (
    PageGeometry,
    analyse_page,
    assemble_titles,
    cluster_baselines,
)

GEO = PageGeometry()


def _layout(sample_pages, fixture_page: int):
    chars = sample_pages[fixture_page - 1]
    sizes = sorted(c["size"] for c in chars)
    median = sizes[len(sizes) // 2]
    return analyse_page(chars, fixture_page, GEO, has_margin_column=median >= 9.5)


class TestMarginColumnSeparation:
    def test_recto_notes_do_not_leak_into_the_body(self, sample_pages) -> None:
        """Source page 13: notes sit right (x0 ~486) and share body baselines."""
        lay = _layout(sample_pages, 2)
        body = lay.text()
        assert "When police may arrest" not in body
        assert "35. (1) Any police officer may without an order" in body

    def test_verso_notes_mirror_to_the_left(self, sample_pages) -> None:
        """Source page 16: the same apparatus, now at x0 ~57."""
        lay = _layout(sample_pages, 3)
        titles = [t for _, t in assemble_titles(lay.notes)]
        assert "Arrest how made" in titles
        assert "Arrest how made" not in lay.text()

    def test_note_spliced_onto_a_body_line_is_split_off(self, sample_pages) -> None:
        """Source page 20: 'Discharge of' shares a baseline with s.60's opening."""
        lay = _layout(sample_pages, 4)
        opening = next(l for l in lay.body if l.text.startswith("60."))
        assert opening.text.startswith("60. No person who has been arrested")
        assert "Discharge" not in opening.text

    def test_overflowing_note_does_not_swallow_a_section(self, sample_pages) -> None:
        """Source page 88: the 'e' of 'Non-appearance' overhangs into the body band.

        Classifying on x alone loses section 279 entirely; the line reads
        "e 279. (1) If the summons...". Size must be part of the test.
        """
        lay = _layout(sample_pages, 5)
        starts = [l.text for l in lay.body if re.match(r"^279\.\s", l.text)]
        assert starts, "section 279 was not recovered"
        assert not starts[0].startswith("e ")

    def test_act_references_are_separated_from_titles(self, sample_pages) -> None:
        lay = _layout(sample_pages, 4)
        assert {n.text for n in lay.act_refs} >= {"18 of 2013.", "21 of 1860."}
        assert all("of 1860" not in t for _, t in assemble_titles(lay.notes))


class TestSmallCaps:
    def test_chapter_heading_keeps_its_initial(self, sample_pages) -> None:
        """A 10pt initial and a 7pt remainder share a baseline but not a top.

        Grouping by ``top`` yields 'A' + 'RREST OF PERSONS'.
        """
        lay = _layout(sample_pages, 2)
        assert "ARREST OF PERSONS" in lay.text()

    def test_masthead_small_caps(self, sample_pages) -> None:
        lay = _layout(sample_pages, 1)
        text = lay.text()
        assert "NO. 46 OF 2023" in text
        assert "THE BHARATIYA NAGARIK SURAKSHA SANHITA, 2023" in text


class TestTitleAssembly:
    def test_fragments_join_until_a_full_stop(self, sample_pages) -> None:
        lay = _layout(sample_pages, 4)
        titles = [t for _, t in assemble_titles(lay.notes)]
        assert "Discharge of person apprehended" in titles
        assert "Power, on escape, to pursue and retake" in titles

    def test_hyphenated_fragments_join_without_a_space(self, sample_pages) -> None:
        lay = _layout(sample_pages, 5)
        titles = [t for _, t in assemble_titles(lay.notes)]
        assert any("Non-appearance" in t for t in titles)
        assert not any("Non- appearance" in t for t in titles)


class TestFurnitureRemoval:
    @pytest.mark.parametrize("fixture_page", [2, 3, 4, 5])
    def test_running_header_and_rules_are_dropped(
        self, sample_pages, fixture_page: int
    ) -> None:
        text = _layout(sample_pages, fixture_page).text()
        assert "GAZETTE OF INDIA" not in text
        assert "____" not in text


def test_cluster_baselines_groups_within_tolerance() -> None:
    chars = [
        {"bottom": 100.0, "x0": 10, "x1": 15, "size": 10, "text": "A"},
        {"bottom": 100.9, "x0": 15, "x1": 22, "size": 7, "text": "B"},
        {"bottom": 118.0, "x0": 10, "x1": 15, "size": 10, "text": "C"},
    ]
    rows = cluster_baselines(chars, tol=2.5)
    assert len(rows) == 2
    assert [c["text"] for c in rows[0][1]] == ["A", "B"]


def test_indent_level_maps_the_ladder() -> None:
    from app.ingestion.types import Line

    mk = lambda x: Line(baseline=0, x0=x, x1=x + 10, size=10, text="x")  # noqa: E731
    assert mk(118.0).indent_level == 0
    assert mk(142.0).indent_level == 1
    assert mk(166.0).indent_level == 2
    assert mk(190.0).indent_level == 3
