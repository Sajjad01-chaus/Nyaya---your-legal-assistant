"""Raw retrieval, exposed for debugging and for the evaluation harness.

Deliberately returns the routing decision, the confidence and the per-stage
timings alongside the passages. Being able to see *why* a query was routed and
how confident retrieval was is what makes a bad answer diagnosable instead of
mysterious, and it is what the eval harness scores against without paying for
generation.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import current_session, get_retrieval_service
from app.retrieval.service import to_prompt_dicts

router = APIRouter()


class SearchRequest(BaseModel):
    q: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(6, ge=1, le=50)
    include_documents: bool = Field(
        False, description="Also search this session's uploaded documents"
    )
    allow_rewrite: bool = Field(
        True, description="Permit one CRAG rewrite-and-retry on a weak result"
    )


class Passage(BaseModel):
    chunk_id: str
    score: float
    act_short: str
    section_number: str
    section_title: str
    subsection: str | None = None
    page_start: int | None = None
    citation: str
    text: str


class SearchResponse(BaseModel):
    query: str
    route: str
    intent: str
    confidence: str
    score: float
    reranked: bool
    rewritten_query: str | None = None
    disambiguation: str | None = None
    timings_ms: dict[str, float]
    results: list[Passage]
    document_results: list[Passage] = []


def _to_passages(chunks) -> list[Passage]:  # noqa: ANN001
    out: list[Passage] = []
    for chunk in chunks:
        out.append(
            Passage(
                chunk_id=chunk.chunk_id,
                score=round(float(chunk.score), 4),
                act_short=chunk.act_short,
                section_number=chunk.section_number,
                section_title=str(chunk.payload.get("section_title", "")),
                subsection=chunk.payload.get("subsection"),
                page_start=chunk.payload.get("page_start"),
                citation=chunk.citation,
                text=chunk.text,
            )
        )
    return out


@router.post("/search", response_model=SearchResponse)
async def search(
    payload: SearchRequest, session_id: str = Depends(current_session)
) -> SearchResponse:
    service = get_retrieval_service()
    result = await service.retrieve(
        payload.q,
        session_id=session_id,
        has_documents=payload.include_documents,
        allow_rewrite=payload.allow_rewrite,
    )
    _ = to_prompt_dicts  # kept importable for the eval harness

    return SearchResponse(
        query=payload.q,
        route=result.route_taken,
        intent=result.plan.intent.value if result.plan else "unknown",
        confidence=result.confidence.value,
        score=result.score,
        reranked=result.reranked,
        rewritten_query=result.rewritten_query,
        disambiguation=result.disambiguation,
        timings_ms=result.timings_ms,
        results=_to_passages(result.chunks[: payload.top_k]),
        document_results=_to_passages(result.document_chunks[: payload.top_k]),
    )
