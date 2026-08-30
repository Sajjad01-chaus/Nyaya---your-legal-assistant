"""Structure-aware chunking of a bare act.

The section is the atomic unit. A section that fits the embedding budget is
never split; a section that does not is split at subsection or clause
boundaries and never mid-sentence, with every fragment carrying its parent
heading so a proviso can never be retrieved without the clause it qualifies.

Measured on the BNSS 2023: median section 716 chars, p90 2,160 chars, so
89.5% of sections emit as a single whole chunk at a 512-token budget.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .layout import PageGeometry, analyse_page, assemble_titles
from .text_utils import smart_title
from .types import Line, PageLayout

# ---------------------------------------------------------------- markers
SECTION_START = re.compile(r"^(\d{1,3})\.\s")
SUBSECTION = re.compile(r"^\((\d{1,2})\)\s")
CLAUSE_ALPHA = re.compile(r"^\(([a-z]{1,2})\)\s")
CLAUSE_ROMAN = re.compile(r"^\(([ivxl]{1,5})\)\s")
CHAPTER_HEAD = re.compile(r"^CHAPTER\s+([IVXLC]+)\s*$")
PART_HEAD = re.compile(r"^([A-H])\s*[.—-]+\s*(.{2,60})$")

PROVISO = re.compile(r"^Provided\b")
EXPLANATION = re.compile(r"^Explanation\b")
ILLUSTRATION = re.compile(r"^Illustrations?\b")
EXCEPTION = re.compile(r"^Exception\b")

# ---------------------------------------------------------------- references
# Indian drafting says "sub-section (2) of section 35", essentially never
# "section 35(2)". Measured in this act: 372 of the former, 1 of the latter.
# A regex written for the compact form captures one reference in 373.
REF_FULL = re.compile(
    r"(?:clause\s*\(([a-z]{1,2})\)\s+of\s+)?"
    r"(?:sub-section\s*\((\d{1,2})\)\s+of\s+)?"
    r"section\s+(\d{1,3})",
    re.IGNORECASE,
)
# Compact form, rare here but common in pleadings and in user-uploaded documents.
REF_COMPACT = re.compile(r"section\s+(\d{1,3})\s*\((\d{1,2})\)", re.IGNORECASE)
REF_RANGE = re.compile(r"sections\s+(\d{1,3})\s+to\s+(\d{1,3})", re.IGNORECASE)
REF_CHAPTER = re.compile(r"\bChapter\s+([IVXLC]+)\b")

# "the Bharatiya Nyaya Sanhita, 2023", "the Cattle Trespass Act, 1871", ...
ACT_MENTION = re.compile(
    r"the\s+((?:[A-Z][\w'-]*\s+){1,6}?(?:Sanhita|Adhiniyam|Act|Code))\s*,?\s*(\d{4})"
)
SENTENCE_SPLIT = re.compile(r"(?<=[.;:])\s+")

_ACT_SHORT = {
    "bharatiya nagarik suraksha sanhita": "BNSS",
    "bharatiya nyaya sanhita": "BNS",
    "bharatiya sakshya adhiniyam": "BSA",
    "code of criminal procedure": "CrPC",
    "indian penal code": "IPC",
}


def _act_short(name: str) -> str | None:
    key = re.sub(r"[\s,]+", " ", name).strip().lower()
    key = re.sub(r",?\s*\d{4}$", "", key).strip()
    return _ACT_SHORT.get(key)


@dataclass(slots=True)
class SectionRef:
    """A resolved cross-reference. ``act_short`` of None means "this act"."""

    section: int | None = None
    subsection: str | None = None
    clause: str | None = None
    act_short: str | None = None
    chapter: str | None = None

    def key(self) -> str:
        act = self.act_short or "SELF"
        if self.chapter:
            return f"{act} Ch.{self.chapter}"
        parts = [act, f" s.{self.section}"]
        if self.subsection:
            parts.append(f"({self.subsection})")
        if self.clause:
            parts.append(f"({self.clause})")
        return "".join(parts)


@dataclass(slots=True)
class Chunk:
    """One retrievable unit. Field names follow the brief's schema."""

    act: str
    act_short: str
    chapter: str | None
    chapter_title: str | None
    section_number: str
    section_title: str
    subsection: str | None
    clause: str | None
    text: str
    has_illustration: bool
    has_proviso: bool
    has_exception: bool
    page_start: int
    page_end: int
    chunk_id: str
    source_uri: str
    ingested_at: str
    # ---- beyond the brief ----
    part: str | None = None
    part_title: str | None = None
    has_explanation: bool = False
    references: list[str] = field(default_factory=list)
    external_acts: list[str] = field(default_factory=list)
    breadcrumb: str = ""
    part_index: int = 0
    part_total: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    def embedding_text(self) -> str:
        """What actually gets embedded: breadcrumb, then body.

        A clause such as "(ii) the police officer is satisfied that such arrest
        is necessary-" carries none of its own topic words and is unretrievable
        on its own text. Prefixing the parsed hierarchy is contextual retrieval
        without an LLM pass: free, deterministic, and byte-identical on re-run.
        """
        return f"{self.breadcrumb}\n\n{self.text}" if self.breadcrumb else self.text


@dataclass(slots=True)
class Section:
    number: int
    title: str
    chapter: str | None
    chapter_title: str | None
    part: str | None
    part_title: str | None
    lines: list[Line]
    page_start: int
    page_end: int

    def text(self) -> str:
        return "\n".join(ln.text for ln in self.lines)


def extract_references(text: str) -> tuple[list[SectionRef], list[str]]:
    """Pull statutory cross-references and Act names out of a body.

    The subtle part is the Act qualifier. Legal drafting distributes it across
    an enumeration -- "section 189 and section 191 of the Bharatiya Nyaya
    Sanhita, 2023" puts *both* sections in the BNS, not just the last one.
    Attaching the Act only to the section it directly follows silently
    mis-attributes the earlier members to the enclosing act, which is exactly
    the kind of error that makes a legal answer wrong while looking right.

    Resolution is therefore scoped to a sentence: an unqualified reference
    takes the next Act mention that follows it in the same sentence, if any.
    """
    refs: list[SectionRef] = []
    acts: set[str] = set()

    for sentence in SENTENCE_SPLIT.split(text):
        mentions = [
            (m.start(), f"{m.group(1)}, {m.group(2)}", _act_short(m.group(1)))
            for m in ACT_MENTION.finditer(sentence)
        ]
        acts.update(name for _, name, _ in mentions)

        local: list[tuple[int, SectionRef]] = []
        compact_spans: list[tuple[int, int]] = []

        for m in REF_COMPACT.finditer(sentence):
            compact_spans.append(m.span())
            local.append(
                (m.start(), SectionRef(section=int(m.group(1)), subsection=m.group(2)))
            )

        for m in REF_FULL.finditer(sentence):
            clause, subsection, section = m.groups()
            # The compact matcher already claimed this "section N" occurrence.
            if any(lo <= m.start() < hi for lo, hi in compact_spans):
                continue
            local.append(
                (
                    m.start(),
                    SectionRef(
                        section=int(section), subsection=subsection, clause=clause
                    ),
                )
            )

        for m in REF_RANGE.finditer(sentence):
            lo, hi = int(m.group(1)), int(m.group(2))
            if 0 < hi - lo <= 30:  # guard against an unrelated numeric pair
                local.extend(
                    (m.start(), SectionRef(section=n)) for n in range(lo, hi + 1)
                )

        for pos, ref in local:
            if ref.act_short is None:
                following = [(s, short) for s, _, short in mentions if s > pos]
                if following:
                    ref.act_short = min(following, key=lambda t: t[0])[1]
            refs.append(ref)

    for m in REF_CHAPTER.finditer(text):
        refs.append(SectionRef(chapter=m.group(1)))

    seen: set[str] = set()
    unique: list[SectionRef] = []
    for ref in refs:
        if ref.key() not in seen:
            seen.add(ref.key())
            unique.append(ref)
    return unique, sorted(acts)


class StatuteParser:
    """Walks laid-out pages and emits section-atomic chunks."""

    def __init__(
        self,
        act: str,
        act_short: str,
        source_uri: str,
        *,
        max_chars: int = 2048,  # ~512 tokens at 4 chars/token
        min_split_chars: int = 320,
    ) -> None:
        self.act = act
        self.act_short = act_short
        self.source_uri = source_uri
        self.max_chars = max_chars
        self.min_split_chars = min_split_chars

    # ------------------------------------------------------------- sections
    def walk(self, layouts: list[PageLayout]) -> list[Section]:
        """Assemble sections across page boundaries, tracking chapter and part."""
        sections: list[Section] = []
        chapter = chapter_title = part = part_title = None
        expect_chapter_title = False
        current: Section | None = None

        # (page, baseline, title) for every assembled margin title
        titles = [
            (lay.page, base, text)
            for lay in layouts
            for base, text in assemble_titles(lay.notes)
        ]
        used: set[int] = set()

        for lay in layouts:
            for line in lay.body:
                text = line.text

                if expect_chapter_title:
                    # Chapter titles may run to a second line.
                    if chapter_title and text.isupper() and not CHAPTER_HEAD.match(text):
                        chapter_title = f"{chapter_title} {text}".strip()
                        continue
                    chapter_title = text
                    expect_chapter_title = False
                    part = part_title = None
                    continue

                if (m := CHAPTER_HEAD.match(text)) is not None:
                    chapter, chapter_title = m.group(1), None
                    expect_chapter_title = True
                    current = None
                    continue

                if (m := PART_HEAD.match(text)) is not None and len(text) < 62:
                    part, part_title = m.group(1), m.group(2).strip(" .")
                    continue

                if (m := SECTION_START.match(text)) is not None and line.x0 > 130:
                    number = int(m.group(1))
                    title = self._pair_title(lay.page, line.baseline, titles, used)
                    current = Section(
                        number=number,
                        title=title,
                        chapter=chapter,
                        chapter_title=chapter_title,
                        part=part,
                        part_title=part_title,
                        lines=[line],
                        page_start=lay.page,
                        page_end=lay.page,
                    )
                    sections.append(current)
                    continue

                if current is not None:
                    current.lines.append(line)
                    current.page_end = lay.page

        return sections

    @staticmethod
    def _pair_title(
        page: int,
        baseline: float,
        titles: list[tuple[int, float, str]],
        used: set[int],
    ) -> str:
        """Pair a section with its margin title by vertical proximity.

        The note is typeset alongside the line where the section opens, so
        nearest-baseline on the same page is a stronger signal than document
        order and degrades gracefully when a note spills onto the next page.
        """
        candidates = [
            (i, base, text)
            for i, (pg, base, text) in enumerate(titles)
            if pg == page and i not in used
        ]
        if not candidates:
            candidates = [
                (i, base, text)
                for i, (pg, base, text) in enumerate(titles)
                if pg == page - 1 and i not in used
            ]
        if not candidates:
            return ""
        idx, _, text = min(candidates, key=lambda c: abs(c[1] - baseline))
        used.add(idx)
        return text

    # --------------------------------------------------------------- chunks
    def chunk(self, sections: list[Section]) -> list[Chunk]:
        now = datetime.now(timezone.utc).isoformat()
        chunks: list[Chunk] = []
        for section in sections:
            chunks.extend(self._chunk_section(section, now))
        return chunks

    def _chunk_section(self, section: Section, now: str) -> list[Chunk]:
        body = section.text()
        if len(body) <= self.max_chars:
            groups = [section.lines]
        else:
            groups = self._split_at_boundaries(section.lines)

        total = len(groups)
        out: list[Chunk] = []
        for i, lines in enumerate(groups):
            text = "\n".join(ln.text for ln in lines)
            refs, acts = extract_references(text)
            subsection = self._leading_marker(lines, SUBSECTION)
            clause = self._leading_marker(lines, CLAUSE_ALPHA)
            pages = [ln for ln in lines]
            out.append(
                Chunk(
                    act=self.act,
                    act_short=self.act_short,
                    chapter=section.chapter,
                    chapter_title=section.chapter_title,
                    section_number=str(section.number),
                    section_title=section.title,
                    subsection=subsection,
                    clause=clause,
                    text=text,
                    has_illustration=any(ILLUSTRATION.match(ln.text) for ln in lines),
                    has_proviso=any(PROVISO.match(ln.text) for ln in lines),
                    has_exception=any(EXCEPTION.match(ln.text) for ln in lines),
                    has_explanation=any(EXPLANATION.match(ln.text) for ln in lines),
                    page_start=section.page_start,
                    page_end=section.page_end,
                    chunk_id=self._chunk_id(section.number, i),
                    source_uri=self.source_uri,
                    ingested_at=now,
                    part=section.part,
                    part_title=section.part_title,
                    references=[r.key() for r in refs],
                    external_acts=acts,
                    breadcrumb=self._breadcrumb(section, subsection, clause),
                    part_index=i,
                    part_total=total,
                )
            )
            _ = pages
        return out

    def _split_at_boundaries(self, lines: list[Line]) -> list[list[Line]]:
        """Split a long section at subsection/clause starts, never mid-sentence.

        Structural apparatus (provisos, Explanations, Illustrations) is never a
        split point: it always travels with the clause above it, because a
        proviso read without its main clause states the opposite of the law.
        """
        groups: list[list[Line]] = []
        current: list[Line] = []
        size = 0

        for line in lines:
            is_boundary = bool(SUBSECTION.match(line.text) or CLAUSE_ROMAN.match(line.text))
            is_apparatus = bool(
                PROVISO.match(line.text)
                or EXPLANATION.match(line.text)
                or ILLUSTRATION.match(line.text)
                or EXCEPTION.match(line.text)
            )
            if (
                current
                and is_boundary
                and not is_apparatus
                and size + len(line.text) > self.max_chars
                and size >= self.min_split_chars
            ):
                groups.append(current)
                current, size = [], 0
            current.append(line)
            size += len(line.text) + 1

        if current:
            groups.append(current)
        return groups or [lines]

    @staticmethod
    def _leading_marker(lines: list[Line], pattern: re.Pattern[str]) -> str | None:
        for line in lines:
            if (m := pattern.match(line.text)) is not None:
                return m.group(1)
            if SECTION_START.match(line.text):
                rest = SECTION_START.sub("", line.text)
                if (m := pattern.match(rest)) is not None:
                    return m.group(1)
            break
        return None

    def _breadcrumb(self, section: Section, subsection: str | None, clause: str | None) -> str:
        crumbs = [f"{self.act_short} {self.act.split(',')[-1].strip()}"]
        if section.chapter:
            head = f"Ch.{section.chapter}"
            if section.chapter_title:
                head += f" {smart_title(section.chapter_title)}"
            crumbs.append(head)
        if section.part and section.part_title:
            crumbs.append(f"{section.part}. {section.part_title}")
        leaf = f"s.{section.number}"
        if section.title:
            leaf += f" {section.title}"
        crumbs.append(leaf)
        if subsection:
            crumbs.append(f"({subsection})")
        if clause:
            crumbs.append(f"({clause})")
        return " › ".join(crumbs)

    def _chunk_id(self, section: int, index: int) -> str:
        return f"{self.act_short.lower()}-s{section}-{index:03d}"


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_statute(
    pages_chars: list[list[dict]],
    *,
    act: str,
    act_short: str,
    source_uri: str,
    geometry: PageGeometry | None = None,
    max_chars: int = 2048,
) -> tuple[list[Section], list[Chunk]]:
    """Convenience wrapper: laid-out pages -> sections -> chunks."""
    geo = geometry or PageGeometry()
    layouts: list[PageLayout] = []
    for i, chars in enumerate(pages_chars):
        sizes = [c["size"] for c in chars]
        median = sorted(sizes)[len(sizes) // 2] if sizes else 10.0
        layouts.append(
            analyse_page(chars, i + 1, geo, has_margin_column=median >= 9.5)
        )
    parser = StatuteParser(act, act_short, source_uri, max_chars=max_chars)
    sections = parser.walk(layouts)
    return sections, parser.chunk(sections)
