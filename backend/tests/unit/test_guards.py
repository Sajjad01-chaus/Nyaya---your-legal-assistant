"""Citation and quotation guards. These decide whether the system can lie."""

from __future__ import annotations

from app.llm.guards import Verdict, parse_citations, verify_answer

CONTEXT = [
    {
        "act_short": "BNSS",
        "section_number": "35",
        "text": (
            "Any police officer may without an order from a Magistrate and "
            "without a warrant, arrest any person who commits, in the presence "
            "of a police officer, a cognizable offence."
        ),
    },
    {
        "act_short": "BNSS",
        "section_number": "58",
        "text": (
            "No police officer shall detain in custody a person arrested without "
            "warrant for a longer period than under all the circumstances of the "
            "case is reasonable, and such period shall not exceed twenty-four hours."
        ),
    },
]


class TestCitationParsing:
    def test_parses_the_inline_format(self) -> None:
        cites = parse_citations("Arrest is governed by [BNSS s.35] and [BNSS s.58].")
        assert [(c.act, c.section) for c in cites] == [("BNSS", "35"), ("BNSS", "58")]

    def test_parses_a_subsection(self) -> None:
        (cite,) = parse_citations("See [BNSS s.35(3)].")
        assert (cite.section, cite.subsection) == ("35", "3")

    def test_parses_a_schedule_citation(self) -> None:
        (cite,) = parse_citations("Classified in [BNSS Sch.I].")
        assert cite.schedule == "Sch.I"

    def test_parses_arbitrarily_nested_subclauses(self) -> None:
        """Regression: a single-level pattern made these invisible to the guard.

        A model asked to be precise writes s.35(1)(b)(ii). Unmatched citations
        are neither validated nor stripped, so the hole sat exactly where the
        answer was most specific.
        """
        (cite,) = parse_citations("The condition is [BNSS s.35(1)(b)(ii)].")
        assert (cite.act, cite.section, cite.parts) == ("BNSS", "35", ("1", "b", "ii"))
        assert cite.render() == "[BNSS s.35(1)(b)(ii)]"


class TestInventedCitations:
    def test_a_real_citation_passes(self) -> None:
        report = verify_answer(
            "A police officer may arrest without a warrant [BNSS s.35].", CONTEXT
        )
        assert report.verdict is Verdict.OK
        assert len(report.valid) == 1

    def test_an_invented_section_is_stripped(self) -> None:
        """The model's s.999 does not exist in the retrieved context."""
        report = verify_answer(
            "Arrest without warrant is permitted [BNSS s.35], subject to "
            "conditions [BNSS s.999].",
            CONTEXT,
        )
        assert report.verdict is Verdict.STRIPPED
        assert "[BNSS s.999]" not in report.answer
        assert "[BNSS s.35]" in report.answer
        assert [c.section for c in report.invented] == ["999"]

    def test_right_number_but_wrong_act_is_invented(self) -> None:
        """s.35 exists in the context for the BNSS, not the BNS."""
        report = verify_answer("Rape is defined at [BNS s.35].", CONTEXT)
        assert report.verdict is Verdict.REFUSED
        assert [c.act for c in report.invented] == ["BNS"]

    def test_answer_with_no_citation_at_all_is_refused(self) -> None:
        report = verify_answer(
            "The police can arrest you whenever they consider it necessary.", CONTEXT
        )
        assert report.verdict is Verdict.REFUSED

    def test_uncited_answer_allowed_when_not_required(self) -> None:
        report = verify_answer("Hello.", CONTEXT, require_citation=False)
        assert report.verdict is Verdict.OK

    def test_deep_subclause_of_a_real_section_is_valid(self) -> None:
        """s.35 is in context as a whole; citing inside it is more precise, not wrong."""
        report = verify_answer(
            "Arrest requires the officer to be satisfied of necessity "
            "[BNSS s.35(1)(b)(ii)].",
            CONTEXT,
        )
        assert report.verdict is Verdict.OK
        assert [c.render() for c in report.valid] == ["[BNSS s.35(1)(b)(ii)]"]

    def test_deep_subclause_of_an_invented_section_is_stripped(self) -> None:
        report = verify_answer("See [BNSS s.999(1)(a)] and [BNSS s.35].", CONTEXT)
        assert report.verdict is Verdict.STRIPPED
        assert "[BNSS s.999(1)(a)]" not in report.answer
        assert "[BNSS s.35]" in report.answer


class TestQuoteFidelity:
    def test_a_genuine_quotation_passes(self) -> None:
        report = verify_answer(
            'The section says "arrest any person who commits, in the presence '
            'of a police officer, a cognizable offence" [BNSS s.35].',
            CONTEXT,
        )
        assert report.verdict is Verdict.OK

    def test_a_fabricated_quotation_is_refused(self) -> None:
        """Guard 1 cannot catch this: the citation is real, the quote is not."""
        report = verify_answer(
            'The section states that "a police officer may detain any person '
            'for up to seventy-two hours without informing a Magistrate" '
            "[BNSS s.58].",
            CONTEXT,
        )
        assert report.verdict is Verdict.REFUSED
        assert report.unsupported_quotes

    def test_elision_with_an_ellipsis_is_accepted(self) -> None:
        report = verify_answer(
            'It provides that "No police officer shall detain in custody a person '
            'arrested without warrant ... such period shall not exceed twenty-four '
            'hours" [BNSS s.58].',
            CONTEXT,
        )
        assert report.verdict is Verdict.OK

    def test_short_phrases_are_not_treated_as_quotations(self) -> None:
        report = verify_answer(
            'The test is whether the offence is "cognizable" [BNSS s.35].', CONTEXT
        )
        assert report.verdict is Verdict.OK


class TestEmptyContext:
    def test_no_retrieval_means_refusal(self) -> None:
        report = verify_answer("Section 420 covers cheating [BNS s.420].", [])
        assert report.verdict is Verdict.REFUSED
        assert not report.ok
