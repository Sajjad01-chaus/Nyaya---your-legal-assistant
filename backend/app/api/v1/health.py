"""Liveness and readiness.

Liveness answers "is the process up". Readiness answers "can this instance
actually serve a request", which for a RAG service means the vector store
responds, the database responds, the models are resident, and the corpus is
indexed. An instance that is listening but has an empty index is not ready.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app.api.deps import get_embedder, get_llm, get_reranker, get_store
from app.core.config import settings
from app.core.metrics import indexed_chunks, postgres_up, vector_db_up
from app.db.session import healthy as db_healthy

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str = "0.1.0"


class Component(BaseModel):
    ok: bool
    detail: str = ""


class ReadyResponse(BaseModel):
    ready: bool
    components: dict[str, Component]


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness. Deliberately free of dependencies, so a database blip does not
    cause the orchestrator to restart a perfectly healthy process."""
    return HealthResponse(status="ok")


@router.get("/health/ready", response_model=ReadyResponse)
async def ready(response: Response) -> ReadyResponse:
    components: dict[str, Component] = {}

    store = get_store()
    db_ok, vector_ok = await asyncio.gather(db_healthy(), store.healthy())

    postgres_up.set(1 if db_ok else 0)
    vector_db_up.set(1 if vector_ok else 0)
    components["postgres"] = Component(ok=db_ok, detail="" if db_ok else "unreachable")
    components["vector_store"] = Component(
        ok=vector_ok, detail="" if vector_ok else f"unreachable at {settings.qdrant_url}"
    )

    # The corpus must actually be indexed. A listening instance with an empty
    # collection would answer every question with a refusal.
    corpus_ok = False
    detail = "not indexed - run: docker compose run --rm bootstrap"
    if vector_ok:
        try:
            count = await store.count(settings.qdrant_collection_statute)
            indexed_chunks.labels(
                collection=settings.qdrant_collection_statute
            ).set(count)
            corpus_ok = count > 0
            detail = f"{count} chunks indexed" if corpus_ok else detail
        except Exception as exc:  # noqa: BLE001
            detail = f"collection missing: {exc}"
    components["corpus"] = Component(ok=corpus_ok, detail=detail)

    # Models are loaded lazily; force them so the first real request is not the
    # one that pays the download.
    try:
        get_embedder().warm()
        if settings.rerank_enabled:
            get_reranker().warm()
        components["models"] = Component(ok=True, detail=settings.embed_model)
    except Exception as exc:  # noqa: BLE001
        components["models"] = Component(ok=False, detail=str(exc)[:200])

    llm_ok = await get_llm().healthy()
    components["llm"] = Component(
        ok=llm_ok,
        detail=f"{settings.llm_provider}:{settings.llm_model}"
        if llm_ok
        else f"{settings.llm_provider} unreachable or key missing",
    )

    # The LLM is excluded from the readiness verdict on purpose: retrieval,
    # search and the forms library all work without it, and a provider outage
    # should degrade the service rather than take the instance out of rotation.
    ready_now = all(
        components[name].ok for name in ("postgres", "vector_store", "corpus", "models")
    )
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadyResponse(ready=ready_now, components=components)
