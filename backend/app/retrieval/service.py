"""Retrieval orchestration: route, retrieve, correct, or refuse.

The design principle is that the system should be *unable* to answer without
evidence, rather than instructed not to. Three mechanisms do that work:

*Deterministic routes.* "What is section 35 BNSS" is answered by fetching
section 35. No model chooses the section, so no model can choose wrongly.

*A calibrated confidence.* Fusion scores are not comparable between queries, so
the semantic path always reranks and takes the cross-encoder logit through a
sigmoid. That gives one number, on one scale, that thresholds can be set
against and the evaluation harness can calibrate.

*A correction step before refusal.* A single weak retrieval is often a phrasing
problem rather than a coverage problem, so a below-threshold result triggers one
rewrite and retry. Only if that also fails do we refuse -- which keeps the
refusal meaningful instead of merely sensitive.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, Sequence

from .query import QueryPlan, Route, plan_query
from .store import ScoredChunk

logger = logging.getLogger(__name__)


class Confidence(str, Enum):
    HIGH = "high"          # answer normally
    MODERATE = "moderate"  # answer, but flag the uncertainty
    LOW = "low"            # rewrite and retry, then refuse


@dataclass(slots=True)
class RetrievalResult:
    chunks: list[ScoredChunk] = field(default_factory=list)
    document_chunks: list[ScoredChunk] = field(default_factory=list)
    plan: QueryPlan | None = None
    confidence: Confidence = Confidence.LOW
    score: float = 0.0
    route_taken: str = ""
    reranked: bool = False
    rewritten_query: str | None = None
    disambiguation: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def should_answer(self) -> bool:
        return self.confidence is not Confidence.LOW and bool(
            self.chunks or self.document_chunks
        )


class OffenceTable(Protocol):
    """The First Schedule, served relationally rather than by similarity."""

    async def lookup(
        self, *, section: str | None, query: str, limit: int
    ) -> list[ScoredChunk]: ...


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# Roughly the cross-encoder's own 512-token window, in characters. Trimming
# harder than the model requires is actively harmful: at 900 chars the eval
# produced three false refusals where retrieval had already found the right
# section. "Can a woman be arrested after sunset?" scored 0.026 against s.43,
# whose answer sits at sub-section (5) -- past the cut, so the scorer never saw
# it. The chunker caps sections near this length anyway, so most pass whole.
_RERANK_CHARS = 1800


def _rerank_view(chunk) -> str:  # noqa: ANN001
    """What the cross-encoder actually scores.

    The breadcrumb must be included. Chunks are *embedded* as breadcrumb + body,
    so dense search can match a section by its title -- but reranking the bare
    body throws that signal away, and the cross-encoder then never sees that
    s.35 is titled "When police may arrest without warrant". Measured on this
    corpus, that single omission dropped s.35 out of the top 3 for the query
    "when can a police officer arrest someone without a warrant".
    """
    breadcrumb = str(chunk.payload.get("breadcrumb", "")).strip()
    body = chunk.text[:_RERANK_CHARS]
    return f"{breadcrumb}\n{body}" if breadcrumb else body


# Filler that adds no retrieval signal; dropped when rewriting a weak query.
_STOPWORDS = re.compile(
    r"\b(please|kindly|can you|could you|tell me|i want to know|explain|"
    r"what is|what are|about|the|a|an|regarding|in india|indian law)\b",
    re.IGNORECASE,
)


def rewrite_query(question: str, plan: QueryPlan) -> str:
    """Strip conversational filler and re-anchor on statutory vocabulary.

    Deliberately not an LLM call. A rewrite that costs a round trip defeats the
    point of using it as a fast recovery step, and on statute queries the useful
    signal is almost always the legal nouns that survive this.
    """
    stripped = _STOPWORDS.sub(" ", question)
    stripped = re.sub(r"\s+", " ", stripped).strip(" ?.,")

    anchors: list[str] = []
    for mention in plan.sections:
        anchors.append(f"section {mention.number}")
    anchors.extend(f"Chapter {c}" for c in plan.chapters)

    combined = " ".join([stripped, *anchors]).strip()
    return combined or question


class RetrievalService:
    def __init__(
        self,
        *,
        store,
        embedder,
        reranker,
        statute_collection: str,
        docs_collection: str,
        offence_table: OffenceTable | None = None,
        confidence_high: float = 0.55,
        confidence_low: float = 0.30,
        rerank_top_k: int = 30,
        rerank_keep: int = 6,
        candidates: int = 50,
        default_act: str = "BNSS",
        known_acts: tuple[str, ...] = ("BNSS",),
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.reranker = reranker
        self.statute_collection = statute_collection
        self.docs_collection = docs_collection
        self.offence_table = offence_table
        self.confidence_high = confidence_high
        self.confidence_low = confidence_low
        self.rerank_top_k = rerank_top_k
        self.rerank_keep = rerank_keep
        self.candidates = candidates
        self.default_act = default_act
        self.known_acts = known_acts

    # ------------------------------------------------------------------ api
    async def retrieve(
        self,
        question: str,
        *,
        session_id: str | None = None,
        has_documents: bool = False,
        allow_rewrite: bool = True,
    ) -> RetrievalResult:
        started = time.perf_counter()
        plan = plan_query(
            question,
            has_session_documents=has_documents,
            default_act=self.default_act,
            known_acts=self.known_acts,
        )
        result = await self._execute(plan, question, session_id)
        result.plan = plan

        # ---- CRAG: one correction attempt before giving up
        if (
            allow_rewrite
            and result.confidence is Confidence.LOW
            and plan.route in (Route.SEMANTIC, Route.OFFENCE_TABLE, Route.BOTH)
        ):
            rewritten = rewrite_query(question, plan)
            if rewritten.lower() != question.lower():
                logger.info("crag rewrite", extra={"from": question, "to": rewritten})
                retry_plan = plan_query(
                    rewritten,
                    has_session_documents=has_documents,
                    default_act=self.default_act,
                    known_acts=self.known_acts,
                )
                retry = await self._execute(retry_plan, rewritten, session_id)
                if retry.score > result.score:
                    retry.plan = plan
                    retry.rewritten_query = rewritten
                    result = retry

        if plan.needs_disambiguation:
            numbers = ", ".join(f"section {m.number}" for m in plan.sections)
            result.disambiguation = (
                f"{numbers} exists in more than one Act I have indexed "
                f"({', '.join(self.known_acts)}), and they are different laws. "
                "Tell me which Act you mean."
            )

        result.timings_ms["total"] = round((time.perf_counter() - started) * 1000, 1)
        return result

    # -------------------------------------------------------------- routing
    async def _execute(
        self, plan: QueryPlan, question: str, session_id: str | None
    ) -> RetrievalResult:
        if plan.route is Route.DIRECT_SECTION:
            return await self._direct_section(plan)
        if plan.route is Route.OFFENCE_TABLE:
            return await self._offence_lookup(plan, question)
        if plan.route is Route.SESSION_DOC:
            return await self._semantic(plan, question, session_id, statute=False)
        if plan.route is Route.BOTH:
            return await self._both(plan, question, session_id)
        return await self._semantic(plan, question, session_id, statute=True)

    async def _direct_section(self, plan: QueryPlan) -> RetrievalResult:
        """Exact fetch. Deterministic by construction, so confidence is not a guess."""
        started = time.perf_counter()
        chunks: list[ScoredChunk] = []
        for mention in plan.sections:
            act = mention.act or self.default_act
            chunks.extend(
                await self.store.fetch_by_section(
                    self.statute_collection, act, str(mention.number)
                )
            )
        elapsed = round((time.perf_counter() - started) * 1000, 1)
        return RetrievalResult(
            chunks=chunks,
            confidence=Confidence.HIGH if chunks else Confidence.LOW,
            score=1.0 if chunks else 0.0,
            route_taken="direct_section",
            reranked=False,
            timings_ms={"lookup": elapsed},
        )

    async def _offence_lookup(self, plan: QueryPlan, question: str) -> RetrievalResult:
        """First Schedule first; fall back to semantic when it has no answer."""
        if self.offence_table is not None:
            started = time.perf_counter()
            section = str(plan.sections[0].number) if plan.sections else None
            rows = await self.offence_table.lookup(
                section=section, query=question, limit=self.rerank_keep
            )
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            if rows:
                return RetrievalResult(
                    chunks=rows,
                    confidence=Confidence.HIGH,
                    score=1.0,
                    route_taken="offence_table",
                    reranked=False,
                    timings_ms={"table": elapsed},
                )
        return await self._semantic(plan, question, None, statute=True)

    async def _both(
        self, plan: QueryPlan, question: str, session_id: str | None
    ) -> RetrievalResult:
        """A compliance question needs the rule and the document side by side."""
        statute = await self._semantic(plan, question, None, statute=True)
        documents = await self._semantic(plan, question, session_id, statute=False)
        return RetrievalResult(
            chunks=statute.chunks,
            document_chunks=documents.document_chunks,
            confidence=max(
                statute.confidence, documents.confidence, key=_confidence_rank
            ),
            score=max(statute.score, documents.score),
            route_taken="both",
            reranked=statute.reranked or documents.reranked,
            timings_ms={**statute.timings_ms, **documents.timings_ms},
        )

    async def _semantic(
        self,
        plan: QueryPlan,
        question: str,
        session_id: str | None,
        *,
        statute: bool,
    ) -> RetrievalResult:
        timings: dict[str, float] = {}

        started = time.perf_counter()
        dense = self.embedder.embed_query(question)
        sparse = self.embedder.sparse_query(question)
        timings["embed"] = round((time.perf_counter() - started) * 1000, 1)

        filters: dict[str, object] = dict(plan.filters) if statute else {}
        collection = self.statute_collection if statute else self.docs_collection
        if not statute:
            if not session_id:
                return RetrievalResult(route_taken="session_doc", score=0.0)
            # Session isolation is a filter inside the engine, so another
            # session's document never enters the candidate set at all.
            filters["session_id"] = session_id

        started = time.perf_counter()
        candidates = await self.store.hybrid_search(
            collection,
            dense,
            sparse,
            limit=self.rerank_top_k,
            filters=filters or None,
            candidates=self.candidates,
        )
        timings["search"] = round((time.perf_counter() - started) * 1000, 1)

        if not candidates:
            return RetrievalResult(
                route_taken="semantic" if statute else "session_doc",
                confidence=Confidence.LOW,
                score=0.0,
                timings_ms=timings,
            )

        started = time.perf_counter()
        ranked = self.reranker.rerank(
            question,
            [_rerank_view(c) for c in candidates],
            keep=self.rerank_keep,
        )
        timings["rerank"] = round((time.perf_counter() - started) * 1000, 1)

        top_chunks = [candidates[index] for index, _ in ranked]
        for (index, raw_score), chunk in zip(ranked, top_chunks):
            chunk.score = raw_score
            _ = index

        # Cross-encoder logits are unbounded; a sigmoid puts every query on the
        # same 0-1 scale so one threshold pair means the same thing everywhere.
        score = _sigmoid(ranked[0][1]) if ranked else 0.0
        # Statutory passages and passages from the user's own document must not
        # share a field. The prompt labels them differently -- one is authority,
        # the other is evidence about the user's situation -- and the citation
        # guard treats them differently too. Putting document text into the
        # statute slot would present an FIR as though it were law.
        return RetrievalResult(
            chunks=top_chunks if statute else [],
            document_chunks=[] if statute else top_chunks,
            confidence=self._grade(score, statute=statute),
            score=round(score, 4),
            route_taken="semantic" if statute else "session_doc",
            reranked=True,
            timings_ms=timings,
        )

    def _grade(self, score: float, *, statute: bool = True) -> Confidence:
        """Grade retrieval. The two corpora do not share a refusal contract.

        Refusing exists to stop the system asserting law it has no basis for.
        It has no business stopping the system reading a user's own upload back
        to them. The document route is only chosen when the question is
        explicitly deictic -- "this notice", "my FIR" -- so the user has already
        told us which document they mean; retrieving any chunk of it is the
        evidence, and a cross-encoder score measures topical similarity rather
        than entitlement to answer.

        Gating documents on the statutory threshold refused questions the
        system could obviously answer: on the end-to-end run a one-chunk notice
        scored below the floor and "What does this notice require me to do?"
        was declined against the very document the user had just uploaded.
        """
        if not statute:
            return Confidence.HIGH if score > 0.0 else Confidence.LOW
        if score >= self.confidence_high:
            return Confidence.HIGH
        if score >= self.confidence_low:
            return Confidence.MODERATE
        return Confidence.LOW


def _confidence_rank(confidence: Confidence) -> int:
    return {Confidence.LOW: 0, Confidence.MODERATE: 1, Confidence.HIGH: 2}[confidence]


def to_prompt_dicts(chunks: Sequence[ScoredChunk]) -> list[dict]:
    """Shape retrieved chunks for the prompt builder and the citation guard."""
    return [
        {
            "act_short": chunk.act_short,
            "section_number": chunk.section_number,
            "section_title": chunk.payload.get("section_title", ""),
            "subsection": chunk.payload.get("subsection"),
            "page_start": chunk.payload.get("page_start"),
            "chunk_id": chunk.chunk_id,
            "score": chunk.score,
            "text": chunk.text,
            "filename": chunk.payload.get("filename"),
            "document_id": chunk.payload.get("document_id"),
        }
        for chunk in chunks
    ]
