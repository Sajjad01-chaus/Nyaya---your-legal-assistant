"""Embedding and reranking, run locally on ONNX.

The brief requires open-weight embeddings that we host ourselves; only
generation may call a hosted API. It also warns that shipping CUDA torch for a
CPU model is a finding rather than a detail, so inference runs on onnxruntime
via fastembed: no torch, no CUDA wheels, roughly 500MB of image instead of 2.5GB.

Two details that quietly halve recall if you get them wrong:

*Prefixes.* e5-family models are trained with ``query:`` and ``passage:``
markers and lose a large slice of recall without them; bge-v1.5 wants neither
on passages. The prefixes are configuration, not constants, so switching
models in the evaluation harness cannot silently mis-encode a corpus.

*Asymmetry.* A query and a passage must be encoded through the same model but
not necessarily the same prefix, so the two paths are separate methods rather
than one shared call.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Sequence

if TYPE_CHECKING:  # heavy imports stay out of the module import path
    from fastembed import SparseTextEmbedding, TextEmbedding
    from fastembed.rerank.cross_encoder import TextCrossEncoder

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmbedderConfig:
    model: str = "BAAI/bge-small-en-v1.5"
    dim: int = 384
    query_prefix: str = ""
    passage_prefix: str = ""
    batch_size: int = 32


@dataclass(slots=True)
class EmbedStats:
    """Throughput is logged so cold-start cost is a documented one-time job."""

    texts: int = 0
    seconds: float = 0.0

    @property
    def per_second(self) -> float:
        return self.texts / self.seconds if self.seconds else 0.0


class Embedder:
    """Dense + sparse encoders, loaded lazily and shared across the process.

    Model construction downloads weights and costs seconds, so it happens once,
    behind a lock, and never during module import -- readiness probes gate on
    :meth:`warm` instead.
    """

    def __init__(self, config: EmbedderConfig | None = None) -> None:
        self.config = config or EmbedderConfig()
        self._dense: TextEmbedding | None = None
        self._sparse: SparseTextEmbedding | None = None
        self._lock = threading.Lock()
        self.stats = EmbedStats()

    # ---------------------------------------------------------------- loading
    @property
    def dense(self) -> TextEmbedding:
        if self._dense is None:
            with self._lock:
                if self._dense is None:
                    from fastembed import TextEmbedding

                    started = time.perf_counter()
                    self._dense = TextEmbedding(model_name=self.config.model)
                    logger.info(
                        "loaded dense embedder",
                        extra={
                            "model": self.config.model,
                            "load_seconds": round(time.perf_counter() - started, 2),
                        },
                    )
        return self._dense

    @property
    def sparse(self) -> SparseTextEmbedding:
        """BM25 as a sparse vector, so lexical and dense search share one index.

        Statute queries are full of exact identifiers -- "section 318",
        "BNS 103" -- which dense vectors are poor at. Keeping the lexical side
        inside Qdrant means one query, and one set of metadata filters, covers
        both halves of the hybrid.
        """
        if self._sparse is None:
            with self._lock:
                if self._sparse is None:
                    from fastembed import SparseTextEmbedding

                    self._sparse = SparseTextEmbedding(model_name="Qdrant/bm25")
        return self._sparse

    def warm(self) -> None:
        """Force both models into memory. Called by the readiness probe."""
        _ = self.dense, self.sparse

    # ------------------------------------------------------------- encoding
    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        prefixed = [f"{self.config.passage_prefix}{t}" for t in texts]
        started = time.perf_counter()
        vectors = [v.tolist() for v in self.dense.embed(prefixed, batch_size=self.config.batch_size)]
        elapsed = time.perf_counter() - started
        self.stats.texts += len(texts)
        self.stats.seconds += elapsed
        logger.info(
            "embedded passages",
            extra={"count": len(texts), "seconds": round(elapsed, 2),
                   "per_second": round(len(texts) / elapsed, 1) if elapsed else 0},
        )
        return vectors

    def embed_query(self, text: str) -> list[float]:
        prefixed = f"{self.config.query_prefix}{text}"
        return next(iter(self.dense.query_embed(prefixed))).tolist()

    def sparse_passages(self, texts: Sequence[str]) -> list[tuple[list[int], list[float]]]:
        return [
            (v.indices.tolist(), v.values.tolist())
            for v in self.sparse.embed(list(texts), batch_size=self.config.batch_size)
        ]

    def sparse_query(self, text: str) -> tuple[list[int], list[float]]:
        vector = next(iter(self.sparse.query_embed(text)))
        return vector.indices.tolist(), vector.values.tolist()


class Reranker:
    """Cross-encoder rerank of the fused candidate set.

    Applied conditionally. When fusion already returns a dominant top hit the
    rerank cannot change the ordering that matters, and skipping it removes
    roughly 200ms from the critical path; the evaluation harness measures both
    policies rather than assuming.
    """

    def __init__(self, model: str = "Xenova/ms-marco-MiniLM-L-6-v2") -> None:
        self.model_name = model
        self._model: TextCrossEncoder | None = None
        self._lock = threading.Lock()

    @property
    def model(self) -> TextCrossEncoder:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    self._model = TextCrossEncoder(model_name=self.model_name)
        return self._model

    def warm(self) -> None:
        _ = self.model

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        return list(self.model.rerank(query, list(documents)))

    def rerank(
        self, query: str, documents: Sequence[str], keep: int
    ) -> list[tuple[int, float]]:
        """Return ``(original_index, score)`` for the best ``keep`` documents."""
        scores = self.score(query, documents)
        ranked = sorted(enumerate(scores), key=lambda pair: pair[1], reverse=True)
        return ranked[:keep]


def reciprocal_rank_fusion(
    rankings: Iterable[Sequence[str]], k: int = 60
) -> dict[str, float]:
    """Fuse several ranked id lists into one score map.

    Used for the pgvector backend and for the evaluation harness's dense-only
    baseline; the Qdrant backend fuses server-side instead.
    """
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores
