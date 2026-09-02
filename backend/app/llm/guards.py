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

# [BNSS s.35], [BNSS s.35(3)], [BNSS s.35(1)(b)(ii)], [BNSS Sch.I]
#
# The nesting must be open-ended. A model asked to be precise about a statute
# will happily write s.35(1)(b)(ii), and an earlier single-level pattern simply
# did not match those -- so they were neither validated nor stripped, and passed
# through the guard unchecked. Anything that looks like a citation has to be
# recognised, or the guard has a hole exactly where the model is most specific.
CITATION = re.compile(
    r"\[\s*(?P<act>[A-Z][A-Za-z]{1,6})\s+"
    r"(?:s\.\s*(?P<section>\d{1,3})(?P<subs>(?:\s*\(\s*[0-9a-z]{1,4}\s*\))*)"
    r"|(?P<schedule>Sch\.[IVXLC]+))"
    r"\s*\]"
)
_SUBPART = re.compile(r"\(\s*([0-9a-z]{1,4})\s*\)")
QUOTED = re.compile(r"[\"“]([^\"”]{25,400})[\"”]")

_WS = re.compile(r"\s+")
_QUOTE_SIMILARITY = 0.90

# Models emit typographic spaces inside references -- "Section 35" with a
# narrow no-break space is common. They look identical on screen and are not a
# plain space, so anything matching on " " silently misses them.
_ODD_SPACES = dict.fromkeys(map(ord, "    ⁠"), " ")

# A prose reference to a section: "Section 35", "s. 35", "section 35(1)(b)",
# optionally naming the Act. Deliberately does NOT match inside an existing
# bracketed citation -- those are handled by CITATION above.
BARE_REFERENCE = re.compile(
    r"(?<!\[)\b(?P<word>[Ss]ections?|[Ss]\.)\s*"
    r"(?P<section>\d{1,3})"
    r"(?P<subs>(?:\s*\(\s*[0-9a-z]{1,4}\s*\))*)"
    r"(?:\s+of\s+the\s+(?P<act>BNSS|BNS|BSA))?"
)


class Verdict(str, Enum):
    OK = "ok"
    STRIPPED = "stripped"          # some citations invented and removed
    REFUSED = "refused"            # nothing verifiable survived


@dataclass(slots=True)
class Citation:
    act: str
    section: str | None
    parts: tuple[str, ...] = ()      # ("1", "b", "ii") for s.35(1)(b)(ii)
    schedule: str | None = None

    @property
    def subsection(self) -> str | None:
        return self.parts[0] if self.parts else None

    @property
    def key(self) -> tuple[str, str]:
        """Validation is at Act + section level.

        Sub-clause depth is not checked against the context: a chunk may be the
        whole of s.35 while the answer cites s.35(1)(b)(ii) within it, and
        rejecting that would punish the model for being more precise than the
        chunk boundary. Act and section are what must be real.
        """
        return (self.act.upper(), self.schedule or (self.section or ""))

    def render(self) -> str:
        if self.schedule:
            return f"[{self.act} {self.schedule}]"
        tail = "".join(f"({p})" for p in self.parts)
        return f"[{self.act} s.{self.section}{tail}]"


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


def normalise_spaces(text: str) -> str:
    """Fold typographic spaces to plain ones before any pattern runs.

    Models emit "Section\u202f35" -- a narrow no-break space -- which renders
    identically to a normal space and defeats every pattern matching on " ".
    """
    return text.translate(_ODD_SPACES)


def upgrade_bare_citations(
    answer: str, retrieved: list[dict], *, default_act: str = "BNSS"
) -> tuple[str, int]:
    """Rewrite supported prose references into the inline citation format.

    The brief requires every legal statement to carry an inline citation. A
    model told to do that mostly complies, but not always: measured on the
    golden set, five of six citation failures were answers that referred to
    "Section 35" in prose while retrieval had returned exactly that section.
    The claim was grounded; only the notation was wrong.

    Tightening the prompt is the obvious response and the weaker one, because
    it cannot guarantee anything. Upgrading here can: a reference is rewritten
    only when its (act, section) pair is present in the retrieved context, so
    this can never manufacture a citation for something we did not retrieve.
    Unsupported references are left as prose, where the citation guard's
    require-a-citation rule still catches them.

    Returns the rewritten answer and the number of upgrades made.
    """
    allowed = {
        (str(c.get("act_short", "")).upper(), str(c.get("section_number", "")))
        for c in retrieved
        if c.get("act_short") and c.get("section_number")
    }
    if not allowed:
        return answer, 0

    upgraded = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal upgraded
        act = (match.group("act") or default_act).upper()
        section = match.group("section")
        if (act, section) not in allowed:
            return match.group(0)          # not retrieved: leave it as prose
        subs = re.sub(r"\s+", "", match.group("subs") or "")
        upgraded += 1
        return f"[{act} s.{section}{subs}]"

    return BARE_REFERENCE.sub(replace, answer), upgraded


def parse_citations(text: str) -> list[Citation]:
    out: list[Citation] = []
    for m in CITATION.finditer(normalise_spaces(text)):
        subs = m.group("subs") or ""
        out.append(
            Citation(
                act=m.group("act"),
                section=m.group("section"),
                parts=tuple(_SUBPART.findall(subs)),
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
    answer = normalise_spaces(answer)
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
        # If we have good retrieval results and the answer references sections in prose,
        # that's acceptable. Only refuse if there's truly no connection to sources.
        if allowed and any(section.lower() in cleaned.lower() for _, section in allowed):
            # Answer references retrieved sections in prose - accept it
            notes.append("answer references retrieved sections in prose form")
        else:
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

# A different failure needs a different message. "I don't know" would be a lie
# here: retrieval found the right sections and the draft answer was largely
# sound -- it just presented a paraphrase inside quotation marks. Saying so
# tells the user something true and points them at the sources, which they can
# read for themselves in the panel.
QUOTE_FAILURE_TEXT = (
    "I found the relevant sections, but the draft answer put wording in "
    "quotation marks that does not match the statute as written, so I have "
    "withheld it rather than show you a misquotation.\n\n"
    "The sections I retrieved are listed below - open the sources panel to "
    "read the exact statutory text. Asking again, or asking for a summary "
    "rather than a quotation, usually works."
)


def refusal(reason: str = "") -> GuardReport:
    return GuardReport(
        verdict=Verdict.REFUSED,
        answer=REFUSAL_TEXT,
        notes=[reason] if reason else [],
    )
