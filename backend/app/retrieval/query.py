"""Query understanding: work out what was asked before deciding how to answer it.

Runs deterministically first. A statute question is full of exact identifiers,
and a regex that recognises "s.103 BNSS" is both faster and more reliable than
asking a model to classify it. The LLM classifier is a fallback for the
genuinely ambiguous remainder, not the first line.

The disambiguation that matters here: **a section number means nothing without
an Act**. Section 103 of the BNSS is "Persons in charge of closed place to
allow search"; section 103 of the BNS is culpable homicide amounting to murder.
Answering the wrong one is not a ranking error, it is a wrong answer about
criminal law, so an unqualified section number is recorded as ambiguous rather
than silently resolved against whichever corpus happens to be loaded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Intent(str, Enum):
    SECTION_LOOKUP = "section_lookup"      # "what is section 35 BNSS"
    OFFENCE_LOOKUP = "offence_lookup"      # "is s.318 bailable", "punishment for..."
    FORM_LOOKUP = "form_lookup"            # "give me Form 12"
    DOCUMENT = "document"                  # "what does my notice say"
    COMPARATIVE = "comparative"            # "does this notice comply with s.35"
    DEFINITIONAL = "definitional"          # "what is a cognizable offence"
    PROCEDURAL = "procedural"              # "how long can police detain someone"
    UNKNOWN = "unknown"


class Route(str, Enum):
    DIRECT_SECTION = "direct_section"
    OFFENCE_TABLE = "offence_table"
    FORMS = "forms"
    SESSION_DOC = "session_doc"
    SEMANTIC = "semantic"
    BOTH = "both"                          # statute + the user's document


# ---------------------------------------------------------------- patterns
ACT_ALIASES: dict[str, str] = {
    "bnss": "BNSS", "nagarik suraksha": "BNSS", "nagarik suraksha sanhita": "BNSS",
    "crpc": "BNSS", "criminal procedure": "BNSS", "procedure code": "BNSS",
    "bns": "BNS", "nyaya sanhita": "BNS", "nyaya": "BNS",
    "ipc": "BNS", "penal code": "BNS",
    "bsa": "BSA", "sakshya": "BSA", "sakshya adhiniyam": "BSA", "evidence act": "BSA",
}

# "section 35", "s.35", "sec 35", "u/s 35", "section 35(3)", "section 35 (3)"
SECTION_PAT = re.compile(
    r"(?:\bu/?s\b|\bsections?\b|\bsec\b|\bs\.)\s*"
    r"(\d{1,3})\s*(?:\(\s*(\d{1,2})\s*\))?",
    re.IGNORECASE,
)
# a bare "BNS 103" / "BNSS 35" with no "section" keyword at all
BARE_ACT_SECTION = re.compile(
    r"\b(bnss|bns|bsa|ipc|crpc)\s*[-. ]?\s*(\d{1,3})\b", re.IGNORECASE
)
CHAPTER_PAT = re.compile(r"\bchapter\s+([ivxlc]+)\b", re.IGNORECASE)
FORM_PAT = re.compile(r"\bform\s*(?:no\.?\s*)?(\d{1,3})\b", re.IGNORECASE)

OFFENCE_WORDS = re.compile(
    r"\b(bailable|non-?bailable|cognizable|non-?cognizable|punishment|penalty|"
    r"sentence|triable|which court|how many years|imprisonment for)\b",
    re.IGNORECASE,
)
# "what is a cognizable offence" asks what the term means; "what is the
# punishment for theft" asks for an attribute of a specific offence. Both open
# with "what is", so the article is what separates them.
TERM_DEFINITION = re.compile(
    r"^\s*what\s+(is|are)\s+(a|an)\s+[\w\s-]*\b"
    r"(bailable|non-?bailable|cognizable|non-?cognizable|warrant-case|"
    r"summons-case|offence|complaint|inquiry|investigation)\b",
    re.IGNORECASE,
)
DEICTIC = re.compile(
    r"\b(this|my|the attached|the uploaded|the document|the notice|the fir|"
    r"the agreement|the judgment|it)\b",
    re.IGNORECASE,
)
COMPLIANCE = re.compile(
    r"\b(compl(y|ies|iant|iance)|valid|conform|consistent with|in line with|"
    r"as required by|breach|violat)\w*\b",
    re.IGNORECASE,
)
DEFINITIONAL = re.compile(
    r"^\s*(what (is|are|does)|define|definition of|meaning of|who is)\b", re.IGNORECASE
)
FORM_WORDS = re.compile(r"\b(form|proforma|template|download)\b", re.IGNORECASE)


@dataclass(slots=True)
class SectionMention:
    number: int
    subsection: str | None = None
    act: str | None = None          # None -> the Act was not stated

    @property
    def is_ambiguous(self) -> bool:
        return self.act is None


@dataclass(slots=True)
class QueryPlan:
    """Everything the retriever needs, decided before any vector is touched."""

    raw: str
    normalised: str
    intent: Intent
    route: Route
    sections: list[SectionMention] = field(default_factory=list)
    chapters: list[str] = field(default_factory=list)
    forms: list[int] = field(default_factory=list)
    acts: list[str] = field(default_factory=list)
    has_session_documents: bool = False
    needs_disambiguation: bool = False
    filters: dict[str, object] = field(default_factory=dict)

    @property
    def is_deterministic(self) -> bool:
        """True when the answer is a lookup, so reranking is pointless."""
        return self.route in (Route.DIRECT_SECTION, Route.OFFENCE_TABLE, Route.FORMS)


def _normalise(text: str) -> str:
    text = text.replace("’", "'").replace("—", " ").replace("–", " ")
    return re.sub(r"\s+", " ", text).strip()


def _detect_acts(text: str) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for alias, short in ACT_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered) and short not in found:
            found.append(short)
    return found


def _detect_sections(text: str, acts: list[str]) -> list[SectionMention]:
    """Find section mentions and attach an Act only where one is actually stated."""
    mentions: list[SectionMention] = []
    seen: set[tuple[int, str | None, str | None]] = set()

    for match in BARE_ACT_SECTION.finditer(text):
        act = ACT_ALIASES.get(match.group(1).lower())
        number = int(match.group(2))
        key = (number, None, act)
        if key not in seen:
            seen.add(key)
            mentions.append(SectionMention(number=number, act=act))

    for match in SECTION_PAT.finditer(text):
        number = int(match.group(1))
        subsection = match.group(2)
        # Attach the Act only when exactly one was named; two named Acts in one
        # question is a comparison, and guessing which owns the number is worse
        # than admitting the ambiguity.
        act = acts[0] if len(acts) == 1 else None
        key = (number, subsection, act)
        if key in seen or any(m.number == number and m.act for m in mentions):
            continue
        seen.add(key)
        mentions.append(SectionMention(number=number, subsection=subsection, act=act))

    return mentions


def plan_query(
    text: str,
    *,
    has_session_documents: bool = False,
    default_act: str | None = None,
    known_acts: tuple[str, ...] = ("BNSS",),
) -> QueryPlan:
    """Classify a question and choose its retrieval route.

    ``default_act`` resolves a bare section number when only one Act is indexed;
    with several loaded, the ambiguity is surfaced instead of guessed.
    """
    normalised = _normalise(text)
    acts = _detect_acts(normalised)
    sections = _detect_sections(normalised, acts)
    chapters = [m.group(1).upper() for m in CHAPTER_PAT.finditer(normalised)]
    forms = [int(m.group(1)) for m in FORM_PAT.finditer(normalised)]

    if default_act and len(known_acts) == 1:
        for mention in sections:
            if mention.act is None:
                mention.act = default_act

    ambiguous = any(m.is_ambiguous for m in sections) and len(known_acts) > 1

    refers_to_document = has_session_documents and bool(DEICTIC.search(normalised))
    checks_compliance = bool(COMPLIANCE.search(normalised))

    # ---- intent, most specific signal first
    if forms and FORM_WORDS.search(normalised):
        intent, route = Intent.FORM_LOOKUP, Route.FORMS
    elif refers_to_document and (checks_compliance or sections):
        intent, route = Intent.COMPARATIVE, Route.BOTH
    elif refers_to_document:
        intent, route = Intent.DOCUMENT, Route.SESSION_DOC
    elif TERM_DEFINITION.match(normalised):
        # asks what a term means, not what an offence attracts
        intent, route = Intent.DEFINITIONAL, Route.SEMANTIC
    elif OFFENCE_WORDS.search(normalised):
        # "is s.318 bailable", "what is the punishment for theft" - the First
        # Schedule answers these exactly, from a table, with no model involved.
        intent, route = Intent.OFFENCE_LOOKUP, Route.OFFENCE_TABLE
    elif sections and not checks_compliance:
        intent, route = Intent.SECTION_LOOKUP, Route.DIRECT_SECTION
    elif DEFINITIONAL.match(normalised):
        intent, route = Intent.DEFINITIONAL, Route.SEMANTIC
    else:
        intent, route = Intent.PROCEDURAL, Route.SEMANTIC

    filters: dict[str, object] = {}
    if acts:
        filters["act_short"] = acts if len(acts) > 1 else acts[0]
    if chapters:
        filters["chapter"] = chapters if len(chapters) > 1 else chapters[0]

    return QueryPlan(
        raw=text,
        normalised=normalised,
        intent=intent,
        route=route,
        sections=sections,
        chapters=chapters,
        forms=forms,
        acts=acts,
        has_session_documents=has_session_documents,
        needs_disambiguation=ambiguous,
        filters=filters,
    )
