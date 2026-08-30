from __future__ import annotations

import pytest

from app.ingestion.text_utils import (
    dehyphenate,
    is_garbage_text,
    normalise,
    slugify,
    smart_title,
)


class TestSmartTitle:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ARREST OF PERSONS", "Arrest of Persons"),
            ("BOND TO KEEP THE PEACE", "Bond to Keep the Peace"),
            ("WARRANT OF ARREST", "Warrant of Arrest"),
            # str.title() would give "Bail-bond"; hyphen compounds capitalise.
            ("BOND AND BAIL-BOND AFTER ARREST", "Bond and Bail-Bond After Arrest"),
        ],
    )
    def test_minor_words_stay_lower(self, raw: str, expected: str) -> None:
        assert smart_title(raw) == expected

    def test_apostrophe_is_not_a_word_boundary(self) -> None:
        # str.title() produces "Magistrate'S", which slugifies to "MagistrateS".
        assert smart_title("MAGISTRATE'S ORDER") == "Magistrate's Order"

    def test_leading_and_trailing_minor_words_are_capitalised(self) -> None:
        assert smart_title("THE PEACE OF") == "The Peace Of"


class TestSlugify:
    def test_matches_the_brief_filename_convention(self) -> None:
        title = "BOND AND BAIL-BOND FOR ATTENDANCE BEFORE OFFICER IN CHARGE"
        assert (
            slugify(smart_title(title))
            == "Bond-and-Bail-Bond-for-Attendance-before-Officer-in-Charge"
        )

    def test_possessive_closes_up(self) -> None:
        assert slugify(smart_title("MAGISTRATE'S NOTICE")) == "Magistrates-Notice"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("ORDER, ETC., OF A NUISANCE", "Order-Etc-of-a-Nuisance"),
            ("LAND OR W ATER", "Land-or-W-Ater"),
            ("A  B   C", "A-B-C"),
            ("Fine & Costs", "Fine-and-Costs"),
        ],
    )
    def test_punctuation_and_spacing(self, raw: str, expected: str) -> None:
        assert slugify(smart_title(raw)) == expected

    def test_is_filesystem_safe(self) -> None:
        slug = slugify(smart_title('ORDER: "STOP" / HALT \\ NOW *?'))
        assert not set(slug) & set(' /\\:*?"<>|')

    def test_is_deterministic(self) -> None:
        title = "WARRANT OF COMMITMENT ON FAILURE TO FIND SECURITY"
        assert slugify(smart_title(title)) == slugify(smart_title(title))

    def test_long_titles_truncate_on_a_word_boundary(self) -> None:
        slug = slugify("word " * 60, max_len=40)
        assert len(slug) <= 40
        assert not slug.endswith("-")


class TestDehyphenate:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("search- warrant", "search-warrant"),
            ("non- cognizable", "non-cognizable"),
            ("successors-in- office", "successors-in-office"),
            ("audio- video electronic", "audio-video electronic"),
        ],
    )
    def test_repairs_line_break_hyphens(self, raw: str, expected: str) -> None:
        assert dehyphenate(raw) == expected


class TestGarbageDetection:
    def test_legacy_font_mojibake_is_garbage(self) -> None:
        # Page 1 of the source PDF: Devanagari in a legacy font, no ToUnicode.
        assert is_garbage_text(
            "vlk/kkj.k Hkkx II [k.M 1 izkf/kdkj ls izdkf'kr lañ ubZ fnYyh lkseokj"
        )

    def test_real_prose_is_not_garbage(self) -> None:
        assert not is_garbage_text(
            "Any police officer may without an order from a Magistrate and "
            "without a warrant, arrest any person who commits a cognizable offence."
        )

    def test_empty_and_short_text_is_garbage(self) -> None:
        assert is_garbage_text("")
        assert is_garbage_text("   \n  ")

    def test_replacement_characters_signal_a_broken_encoding(self) -> None:
        assert is_garbage_text("The quick brown fox " + "�" * 40)


def test_normalise_collapses_justified_spacing() -> None:
    assert normalise("promise  to   any    person") == "promise to any person"


def test_normalise_unifies_smart_quotes() -> None:
    assert normalise("‘bail’ and “bond”") == "'bail' and \"bond\""
