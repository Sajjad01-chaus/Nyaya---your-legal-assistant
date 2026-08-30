"""Query routing. Deterministic paths must fire before anything semantic does."""

from __future__ import annotations

import pytest

from app.retrieval.query import Intent, Route, plan_query


class TestSectionExtraction:
    @pytest.mark.parametrize(
        "text",
        [
            "what is section 35 BNSS",
            "explain s.35 of the BNSS",
            "BNSS 35",
            "u/s 35 BNSS",
            "sec 35 bnss",
        ],
    )
    def test_section_number_survives_every_spelling(self, text: str) -> None:
        plan = plan_query(text)
        assert [m.number for m in plan.sections] == [35]

    def test_subsection_is_captured(self) -> None:
        plan = plan_query("what does section 35(3) of the BNSS require")
        assert plan.sections[0].number == 35
        assert plan.sections[0].subsection == "3"

    def test_named_act_is_attached(self) -> None:
        plan = plan_query("section 103 of the BNS")
        assert plan.sections[0].act == "BNS"

    def test_old_code_names_map_to_the_new_acts(self) -> None:
        assert plan_query("section 41 CrPC").acts == ["BNSS"]
        assert plan_query("section 302 IPC").acts == ["BNS"]


class TestActAmbiguity:
    def test_bare_section_number_is_ambiguous_across_two_acts(self) -> None:
        """s.103 is a search power in the BNSS and murder in the BNS."""
        plan = plan_query("what is section 103", known_acts=("BNSS", "BNS"))
        assert plan.sections[0].is_ambiguous
        assert plan.needs_disambiguation

    def test_single_corpus_resolves_a_bare_number(self) -> None:
        plan = plan_query("what is section 103", default_act="BNSS", known_acts=("BNSS",))
        assert plan.sections[0].act == "BNSS"
        assert not plan.needs_disambiguation

    def test_two_named_acts_do_not_get_a_guessed_owner(self) -> None:
        plan = plan_query(
            "compare section 35 of the BNSS with the BNS", known_acts=("BNSS", "BNS")
        )
        assert set(plan.acts) == {"BNSS", "BNS"}
        assert plan.sections[0].act is None


class TestRouting:
    def test_section_lookup_is_deterministic(self) -> None:
        plan = plan_query("what is section 35 BNSS")
        assert plan.route is Route.DIRECT_SECTION
        assert plan.is_deterministic

    @pytest.mark.parametrize(
        "text",
        [
            "is section 318 bailable",
            "what is the punishment for culpable homicide",
            "which court can try section 103",
            "is theft a cognizable offence",
        ],
    )
    def test_offence_questions_go_to_the_table(self, text: str) -> None:
        plan = plan_query(text)
        assert plan.route is Route.OFFENCE_TABLE
        assert plan.is_deterministic

    def test_form_request_goes_to_the_forms_index(self) -> None:
        plan = plan_query("download Form 12")
        assert plan.route is Route.FORMS
        assert plan.forms == [12]

    def test_document_question_is_session_scoped(self) -> None:
        plan = plan_query("what does my notice say", has_session_documents=True)
        assert plan.route is Route.SESSION_DOC
        assert plan.intent is Intent.DOCUMENT

    def test_deictic_without_an_upload_is_not_a_document_question(self) -> None:
        plan = plan_query("what does this mean", has_session_documents=False)
        assert plan.route is not Route.SESSION_DOC

    def test_compliance_question_queries_both_corpora(self) -> None:
        plan = plan_query(
            "does this notice comply with section 35 BNSS", has_session_documents=True
        )
        assert plan.route is Route.BOTH
        assert plan.intent is Intent.COMPARATIVE
        assert plan.sections[0].number == 35

    def test_open_question_falls_through_to_semantic(self) -> None:
        plan = plan_query("how long can the police detain someone before a magistrate")
        assert plan.route is Route.SEMANTIC
        assert not plan.is_deterministic


class TestFilters:
    def test_act_becomes_a_metadata_filter(self) -> None:
        assert plan_query("arrest powers under the BNSS").filters["act_short"] == "BNSS"

    def test_chapter_becomes_a_metadata_filter(self) -> None:
        plan = plan_query("what does Chapter V cover")
        assert plan.chapters == ["V"]
        assert plan.filters["chapter"] == "V"


class TestOutOfScope:
    @pytest.mark.parametrize(
        "text",
        [
            "what is the punishment for jaywalking in Ohio",
            "how do I bake sourdough bread",
            "who won the 2011 cricket world cup",
        ],
    )
    def test_out_of_scope_questions_carry_no_statutory_anchor(self, text: str) -> None:
        """Refusal is not the router's job.

        "the punishment for jaywalking in Ohio" is shaped exactly like a real
        offence question, and routing it to the offence table is correct: the
        table returns nothing, and the confidence gate downstream is what
        refuses. The router's contract is only that it extracts no section,
        chapter or form to anchor a deterministic fetch on -- so no path can
        return a confident answer without evidence.
        """
        plan = plan_query(text)
        assert plan.sections == []
        assert plan.chapters == []
        assert plan.forms == []
