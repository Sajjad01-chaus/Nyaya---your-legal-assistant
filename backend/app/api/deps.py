"""Shared dependencies: singletons, session identity, rate limiting."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import session_id_var
from app.core.security import (
    SESSION_COOKIE,
    new_session_id,
    sign_session,
    verify_session,
)
from app.db.session import get_session
from app.llm.provider import LLMProvider, build_provider
from app.retrieval.embeddings import Embedder, EmbedderConfig, Reranker
from app.retrieval.service import RetrievalService
from app.retrieval.store import QdrantStore


# Models are expensive to construct and safe to share, so one instance each per
# process. Loading is lazy inside the objects; the readiness probe forces it.
@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    return Embedder(
        EmbedderConfig(
            model=settings.embed_model,
            dim=settings.embed_dim,
            query_prefix=settings.embed_query_prefix,
            passage_prefix=settings.embed_passage_prefix,
        )
    )


@lru_cache(maxsize=1)
def get_reranker() -> Reranker:
    return Reranker(settings.rerank_model)


@lru_cache(maxsize=1)
def get_store() -> QdrantStore:
    return QdrantStore(settings.qdrant_url)


@lru_cache(maxsize=1)
def get_llm() -> LLMProvider:
    return build_provider(settings)


@lru_cache(maxsize=1)
def get_retrieval_service() -> RetrievalService:
    return RetrievalService(
        store=get_store(),
        embedder=get_embedder(),
        reranker=get_reranker(),
        statute_collection=settings.qdrant_collection_statute,
        docs_collection=settings.qdrant_collection_docs,
        confidence_high=settings.confidence_high,
        confidence_low=settings.confidence_low,
        rerank_top_k=settings.rerank_top_k,
        rerank_keep=settings.rerank_keep,
        candidates=settings.hybrid_candidates,
    )


async def current_session(request: Request, response: Response) -> str:
    """Resolve the caller's session, minting one on first contact.

    Anonymous by design: the token owns conversations and uploads and carries
    no personal data.
    """
    raw = request.cookies.get(SESSION_COOKIE, "")
    session_id = verify_session(raw, settings.session_secret)

    if session_id is None:
        session_id = new_session_id()
        response.set_cookie(
            SESSION_COOKIE,
            sign_session(session_id, settings.session_secret),
            max_age=60 * 60 * 24 * 30,
            httponly=True,
            samesite="lax",
            secure=settings.env == "production",
        )

    session_id_var.set(session_id)
    return session_id


DbSession = Depends(get_session)
CurrentSession = Depends(current_session)

__all__ = [
    "AsyncSession",
    "CurrentSession",
    "DbSession",
    "current_session",
    "get_embedder",
    "get_llm",
    "get_reranker",
    "get_retrieval_service",
    "get_store",
]
