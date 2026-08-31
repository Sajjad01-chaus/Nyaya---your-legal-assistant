"""Prometheus metrics.

Covers what the brief asks for -- request count, latency histograms, embedding
time, retrieval latency, vector DB up/down, token usage, upload count, refusal
count -- plus cost per query, because a legal RAG product lives or dies on unit
economics and "we don't know what a query costs" is not an answer.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# Buckets are chosen around the measured budget: ~73ms embed, ~30ms search,
# ~344ms rerank, then generation. Default buckets hide everything interesting
# below a second.
_FAST = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5)
_SLOW = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)

chat_requests = Counter(
    "nyaya_chat_requests_total", "Chat requests received", ["route", "outcome"]
)
retrieval_latency = Histogram(
    "nyaya_retrieval_seconds", "Retrieval latency by stage", ["stage"], buckets=_FAST
)
embedding_latency = Histogram(
    "nyaya_embedding_seconds", "Embedding latency", ["kind"], buckets=_FAST
)
generation_latency = Histogram(
    "nyaya_generation_seconds", "Generation latency end to end", buckets=_SLOW
)
time_to_first_token = Histogram(
    "nyaya_ttft_seconds", "Time to first token", buckets=_SLOW
)

refusals = Counter(
    "nyaya_refusals_total", "Answers withheld for lack of evidence", ["reason"]
)
citations_stripped = Counter(
    "nyaya_citations_stripped_total", "Citations removed as unsupported"
)
injection_flags = Counter(
    "nyaya_injection_flags_total", "Injection-like phrases seen in uploads"
)

tokens_used = Counter("nyaya_tokens_total", "Tokens consumed", ["direction"])
query_cost_usd = Counter("nyaya_query_cost_usd_total", "Estimated spend in USD")

uploads = Counter("nyaya_uploads_total", "Documents uploaded", ["outcome"])
ocr_pages = Counter("nyaya_ocr_pages_total", "Pages that needed the OCR fallback")

vector_db_up = Gauge("nyaya_vector_db_up", "1 when the vector store answers")
postgres_up = Gauge("nyaya_postgres_up", "1 when Postgres answers")
indexed_chunks = Gauge("nyaya_indexed_chunks", "Chunks in a collection", ["collection"])


def record_usage(prompt_tokens: int, completion_tokens: int, cost: float) -> None:
    tokens_used.labels(direction="in").inc(prompt_tokens)
    tokens_used.labels(direction="out").inc(completion_tokens)
    query_cost_usd.inc(cost)
