"""Post-generation guards. The part that stops the system lying.

The brief is explicit that these must exist in code rather than in the prompt,
and the reason is sound: a prompt is a request, and a model under pressure to
be helpful will invent a section number that looks plausible. Only a check
against the retrieved context can actually prevent it.

Three guards run in order:

1. **Citation existence.** Every ``[BNSS s.103]`` in the answer must correspond
   to a chunk that retrieval actually returned. Invented citations are stripped.
2. **Quote fidelity.** Text presented as a quotation must appear in a retrieved
   chunk. A fluent paraphrase passed off as statutory wording is how a legal
   assistant does real damage, and it is invisible to guard 1.
3. **Support.** If nothing survives, the answer is refused rather than emitted
   with its citations quietly removed -- an uncited legal claim is worse than
   no answer.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from enum import Enum

# [BNSS s.35], [BNSS s.35(3)], [BNS s.103(1)], [BNSS Sch.I]
CITATION = re.compile(
    r"\[\s*(?P<act>[A-Z][A-Za-z]{1,6})\s+"
    r"(?:s\.\s*(?P<section>\d{1,3})(?:\s*\(\s*(?P<subsection>[0-9a-z]{1,3})\s*\))?"
    r"|(?P<schedule>Sch\.[IVX]+))"
    r"\s*\]"
)
QUOTED = re.compile(r"[\"“]([^\"”]{25,400})[\"”]")

_WS = re.compile(r"\s+")
_QUOTE_SIMILARITY = 0.90


class Verdict(str, Enum):
    OK = "ok"
    STRIPPED = "stripped"          # some citations invented and removed
    REFUSED = "refused"            # nothing verifiable survived


@dataclass(slots=True)
class Citation:
    act: str
    section: str | None
    subsection: str | None = None
    schedule: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.act.upper(), self.schedule or (self.section or ""))

    def render(self) -> str:
        if self.schedule:
            return f"[{self.act} {self.schedule}]"
        base = f"[{self.act} s.{self.section}"
        return f"{base}({self.subsection})]" if self.subsection else f"{base}]"


@dataclass(slots=True)
class GuardReport:
    verdict: Verdict
    answer: str
    valid: list[Citation] = field(default_factory=list)
    invented: list[Citation] = field(default_factory=list)
    unsupported_quotes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict is not Verdict.REFUSED


def parse_citations(text: str) -> list[Citation]:
    out: list[Citation] = []
    for m in CITATION.finditer(text):
        out.append(
            Citation(
                act=m.group("act"),
                section=m.group("section"),
                subsection=m.group("subsection"),
                schedule=m.group("schedule"),
            )
        )
    return out


def _normalise(text: str) -> str:
    return _WS.sub(" ", text).strip().lower()


def _quote_is_supported(quote: str, corpus: str) -> bool:
    """Exact containment first, then a near-match to tolerate elision.

    Legal quotation legitimately drops inner clauses with an ellipsis, so an
    exact substring test alone would reject honest answers. A high similarity
    floor still catches invented wording.
    """
    needle = _normalise(quote)
    if needle in corpus:
        return True
    # Ellipsis-style elision: check each fragment independently.
    fragments = [f for f in re.split(r"\.{3}|…", needle) if len(f.strip()) > 20]
    if fragments and all(f.strip() in corpus for f in fragments):
        return True
    matcher = difflib.SequenceMatcher(None, needle, corpus)
    return matcher.find_longest_match(0, len(needle), 0, len(corpus)).size >= int(
        len(needle) * _QUOTE_SIMILARITY
    )


def verify_answer(
    answer: str,
    retrieved: list[dict],
    *,
    require_citation: bool = True,
) -> GuardReport:
    """Validate an answer against the context it was supposed to be grounded in.

    ``retrieved`` carries the chunks handed to the model; each needs at least
    ``act_short``, ``section_number`` and ``text``.
    """
    allowed: set[tuple[str, str]] = set()
    for chunk in retrieved:
        act = str(chunk.get("act_short", "")).upper()
        section = str(chunk.get("section_number", ""))
        if act and section:
            allowed.add((act, section))
        schedule = chunk.get("schedule")
        if act and schedule:
            allowed.add((act, str(schedule)))

    corpus = _normalise(" ".join(str(c.get("text", "")) for c in retrieved))

    valid: list[Citation] = []
    invented: list[Citation] = []
    for citation in parse_citations(answer):
        (valid if citation.key in allowed else invented).append(citation)

    cleaned = answer
    notes: list[str] = []

    # ---- guard 1: strip citations with no support in the retrieved context
    if invented:
        for citation in invented:
            cleaned = cleaned.replace(citation.render(), "")
        cleaned = _WS.sub(" ", cleaned).replace(" ,", ",").replace(" .", ".").strip()
        notes.append(
            f"removed {len(invented)} citation(s) absent from the retrieved context: "
            + ", ".join(sorted({c.render() for c in invented}))
        )

    # ---- guard 2: quotations must actually appear in the sources
    unsupported = [
        quote
        for quote in QUOTED.findall(cleaned)
        if not _quote_is_supported(quote, corpus)
    ]
    if unsupported:
        notes.append(
            f"{len(unsupported)} quoted passage(s) do not appear in the retrieved text"
        )

    # ---- guard 3: an uncited legal claim is worse than no answer
    if unsupported:
        return GuardReport(Verdict.REFUSED, cleaned, valid, invented, unsupported, notes)
    if require_citation and not valid:
        notes.append("no verifiable citation remained after validation")
        return GuardReport(Verdict.REFUSED, cleaned, valid, invented, unsupported, notes)

    verdict = Verdict.STRIPPED if invented else Verdict.OK
    return GuardReport(verdict, cleaned, valid, invented, unsupported, notes)


REFUSAL_TEXT = (
    "I don't have a reliable basis in the indexed statute to answer that. "
    "I can only answer from the Acts I have ingested, and I won't guess at "
    "criminal law from memory.\n\n"
    "Try naming a section (for example \"section 35 BNSS\"), or ask about "
    "arrest, bail, summons, warrants, charges, trial procedure, appeals or "
    "the statutory forms."
)


def refusal(reason: str = "") -> GuardReport:
    return GuardReport(
        verdict=Verdict.REFUSED,
        answer=REFUSAL_TEXT,
        notes=[reason] if reason else [],
    )
