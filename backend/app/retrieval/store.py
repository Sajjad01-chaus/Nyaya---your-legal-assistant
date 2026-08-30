"""Vector storage behind a swappable interface.

Qdrant is the default because the brief prefers it and because its Query API
performs the dense and sparse prefetch plus RRF fusion server-side -- hybrid
retrieval becomes one round trip with one set of metadata filters, rather than
two queries fused in application code.

The interface exists for two reasons beyond taste: the evaluation harness swaps
backends to produce its comparison table, and a reviewer who wants pgvector can
set ``NYAYA_VECTOR_STORE=pgvector`` without touching code.

**Known trade-off.** Splitting vectors from relational rows means deleting a
document is two writes to two systems. Postgres alone would have made it a
single transaction with a foreign-key cascade. Deletes are therefore ordered
vectors-first and reconciled, so a partial failure leaves rows without vectors
(harmless) rather than vectors without rows (a cross-session leak).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence, runtime_checkable

logger = logging.getLogger(__name__)

DENSE = "dense"
SPARSE = "sparse"


@dataclass(slots=True)
class ScoredChunk:
    """One retrieved passage with everything the citation guard needs."""

    chunk_id: str
    score: float
    text: str
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def act_short(self) -> str:
        return str(self.payload.get("act_short", ""))

    @property
    def section_number(self) -> str:
        return str(self.payload.get("section_number", ""))

    @property
    def citation(self) -> str:
        """The inline form: ``[BNSS s.35(3)]``."""
        base = f"{self.act_short} s.{self.section_number}"
        subsection = self.payload.get("subsection")
        if subsection:
            base += f"({subsection})"
        return f"[{base}]"


@runtime_checkable
class VectorStore(Protocol):
    async def ensure_collection(self, name: str, dim: int) -> None: ...

    async def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        dense: Sequence[Sequence[float]],
        sparse: Sequence[tuple[Sequence[int], Sequence[float]]],
        payloads: Sequence[dict[str, Any]],
    ) -> int: ...

    async def hybrid_search(
        self,
        collection: str,
        dense: Sequence[float],
        sparse: tuple[Sequence[int], Sequence[float]],
        limit: int,
        *,
        filters: dict[str, Any] | None = None,
        candidates: int = 50,
    ) -> list[ScoredChunk]: ...

    async def delete_by_document(self, collection: str, document_id: str) -> int: ...

    async def count(self, collection: str) -> int: ...

    async def healthy(self) -> bool: ...


def stable_point_id(chunk_id: str) -> str:
    """Qdrant point ids must be a UUID or an unsigned int.

    Deriving the UUID from the chunk id keeps upserts idempotent: re-running
    ingestion overwrites the same points instead of duplicating the corpus.
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"nyaya:{chunk_id}"))


class QdrantStore:
    """Qdrant backend using named vectors and server-side RRF fusion."""

    # Payload fields that must be indexed for filtering to stay fast and for
    # session isolation to be enforced inside the engine rather than after it.
    KEYWORD_INDEXES = (
        "act_short",
        "chapter",
        "section_number",
        "document_id",
        "session_id",
    )

    def __init__(self, url: str, *, timeout: float = 30.0) -> None:
        from qdrant_client import AsyncQdrantClient

        self.url = url
        self.client = AsyncQdrantClient(url=url, timeout=timeout)

    async def ensure_collection(self, name: str, dim: int) -> None:
        from qdrant_client import models

        if await self.client.collection_exists(name):
            return

        await self.client.create_collection(
            collection_name=name,
            vectors_config={
                DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={
                # BM25 lives beside the dense vector so lexical and semantic
                # search share one index and one filter.
                SPARSE: models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=False)
                )
            },
        )
        for field_name in self.KEYWORD_INDEXES:
            await self.client.create_payload_index(
                collection_name=name,
                field_name=field_name,
                field_schema=models.PayloadSchemaType.KEYWORD,
            )
        logger.info("created collection", extra={"collection": name, "dim": dim})

    async def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        dense: Sequence[Sequence[float]],
        sparse: Sequence[tuple[Sequence[int], Sequence[float]]],
        payloads: Sequence[dict[str, Any]],
    ) -> int:
        from qdrant_client import models

        points = [
            models.PointStruct(
                id=stable_point_id(chunk_id),
                vector={
                    DENSE: list(dense_vec),
                    SPARSE: models.SparseVector(
                        indices=list(sparse_vec[0]), values=list(sparse_vec[1])
                    ),
                },
                payload={**payload, "chunk_id": chunk_id},
            )
            for chunk_id, dense_vec, sparse_vec, payload in zip(
                ids, dense, sparse, payloads, strict=True
            )
        ]
        await self.client.upsert(collection_name=collection, points=points, wait=True)
        return len(points)

    @staticmethod
    def _build_filter(filters: dict[str, Any] | None):
        """Translate a plain dict into a Qdrant filter.

        A list value becomes "any of"; a scalar becomes an exact match. The
        session filter is applied here rather than after retrieval, so one
        user's document can never surface in another user's candidate set.
        """
        from qdrant_client import models

        if not filters:
            return None
        conditions = []
        for key, value in filters.items():
            if value is None:
                continue
            match = (
                models.MatchAny(any=[str(v) for v in value])
                if isinstance(value, (list, tuple, set))
                else models.MatchValue(value=str(value))
            )
            conditions.append(models.FieldCondition(key=key, match=match))
        return models.Filter(must=conditions) if conditions else None

    async def hybrid_search(
        self,
        collection: str,
        dense: Sequence[float],
        sparse: tuple[Sequence[int], Sequence[float]],
        limit: int,
        *,
        filters: dict[str, Any] | None = None,
        candidates: int = 50,
    ) -> list[ScoredChunk]:
        from qdrant_client import models

        query_filter = self._build_filter(filters)
        indices, values = sparse

        response = await self.client.query_points(
            collection_name=collection,
            prefetch=[
                models.Prefetch(
                    query=list(dense), using=DENSE, limit=candidates, filter=query_filter
                ),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=list(indices), values=list(values)
                    ),
                    using=SPARSE,
                    limit=candidates,
                    filter=query_filter,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        return [
            ScoredChunk(
                chunk_id=str(point.payload.get("chunk_id", point.id)),
                score=float(point.score),
                text=str(point.payload.get("text", "")),
                payload=dict(point.payload or {}),
            )
            for point in response.points
        ]

    async def fetch_by_section(
        self,
        collection: str,
        act_short: str,
        section_number: str,
        *,
        limit: int = 12,
    ) -> list[ScoredChunk]:
        """Deterministic lookup. No vector is involved, so nothing can drift.

        "What is section 103 BNSS" must return section 103, every time, rather
        than whatever cosine similarity felt like.
        """
        records, _ = await self.client.scroll(
            collection_name=collection,
            scroll_filter=self._build_filter(
                {"act_short": act_short, "section_number": section_number}
            ),
            limit=limit,
            with_payload=True,
        )
        chunks = [
            ScoredChunk(
                chunk_id=str(r.payload.get("chunk_id", r.id)),
                score=1.0,
                text=str(r.payload.get("text", "")),
                payload=dict(r.payload or {}),
            )
            for r in records
        ]
        # A split section must be reassembled in reading order.
        chunks.sort(key=lambda c: int(c.payload.get("part_index", 0)))
        return chunks

    async def delete_by_document(self, collection: str, document_id: str) -> int:
        from qdrant_client import models

        before = await self.count_where(collection, {"document_id": document_id})
        await self.client.delete(
            collection_name=collection,
            points_selector=models.FilterSelector(
                filter=self._build_filter({"document_id": document_id})
            ),
            wait=True,
        )
        after = await self.count_where(collection, {"document_id": document_id})
        if after:
            # Surfaced rather than swallowed: orphaned vectors are the failure
            # mode that leaks one session's document into another's retrieval.
            logger.error(
                "vector purge incomplete",
                extra={"document_id": document_id, "remaining": after},
            )
        return before - after

    async def count_where(self, collection: str, filters: dict[str, Any]) -> int:
        result = await self.client.count(
            collection_name=collection,
            count_filter=self._build_filter(filters),
            exact=True,
        )
        return int(result.count)

    async def count(self, collection: str) -> int:
        result = await self.client.count(collection_name=collection, exact=True)
        return int(result.count)

    async def healthy(self) -> bool:
        try:
            await self.client.get_collections()
            return True
        except Exception:  # noqa: BLE001 - readiness must never raise
            logger.warning("qdrant unreachable", extra={"url": self.url})
            return False

    async def close(self) -> None:
        await self.client.close()
