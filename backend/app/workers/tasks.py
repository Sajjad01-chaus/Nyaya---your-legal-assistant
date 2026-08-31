"""Background ingestion.

A 60-page upload must not hold a request thread, so the endpoint stores the
file, enqueues a job and returns. This runs the parse -> chunk -> embed -> ready
sequence and reports progress at each stage, which is what drives the upload
indicator: the user should never have to guess whether the document is
queryable yet.
"""

from __future__ import annotations

from pathlib import Path

from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.metrics import injection_flags, ocr_pages
from app.db.models import Document, IngestJob, JobStatus
from app.db.session import session_scope
from app.ingestion.documents import parse_document
from app.llm.prompts import scan_for_injection
from app.retrieval.embeddings import Embedder, EmbedderConfig
from app.retrieval.store import QdrantStore

logger = get_logger("worker")


async def _set_progress(
    job_id: str, status: JobStatus, progress: float, detail: str = ""
) -> None:
    async with session_scope() as db:
        job = await db.get(IngestJob, job_id)
        if job is None:
            return
        job.status = status
        job.progress = round(progress, 3)
        job.stage_detail = detail
        document = await db.get(Document, job.document_id)
        if document is not None:
            document.status = status


async def ingest_document(ctx: dict, document_id: str, job_id: str) -> dict:
    """Parse, chunk, embed and index one uploaded document."""
    logger.info("ingest started", document_id=document_id, job_id=job_id)

    async with session_scope() as db:
        document = await db.get(Document, document_id)
        if document is None:
            logger.error("document vanished", document_id=document_id)
            return {"ok": False, "error": "document not found"}
        session_id = document.session_id
        filename = document.filename
        digest = document.sha256
        suffix = Path(filename).suffix.lower()

    stored = Path(settings.upload_dir) / f"{digest}{suffix}"
    if not stored.is_file():
        matches = list(Path(settings.upload_dir).glob(f"{digest}*"))
        if not matches:
            await _fail(job_id, document_id, "The uploaded file could not be found.")
            return {"ok": False}
        stored = matches[0]

    try:
        # ---------------------------------------------------------- parse
        await _set_progress(job_id, JobStatus.PARSING, 0.10, "reading the document")
        parsed = parse_document(stored, ocr_enabled=settings.ocr_enabled)

        if parsed.pages_ocr:
            ocr_pages.inc(parsed.pages_ocr)
        if not parsed.chunks:
            await _fail(
                job_id, document_id,
                "No readable text could be extracted. If this is a scan, try a "
                "clearer copy.",
            )
            return {"ok": False}

        # ------------------------------------------------- injection scan
        # Flagged, never blocked. The user is entitled to ask about a document
        # that contains an instruction-like sentence; what matters is that the
        # sentence is treated as content. The prompt fences it and the citation
        # guard runs regardless, so this is for visibility and metrics.
        flags: list[str] = []
        for chunk in parsed.chunks:
            flags.extend(scan_for_injection(chunk.text))
        flags = sorted(set(flags))
        if flags:
            injection_flags.inc(len(flags))
            logger.warning(
                "injection-like phrases in upload",
                document_id=document_id, phrases=flags,
            )

        await _set_progress(
            job_id, JobStatus.CHUNKING, 0.35,
            f"{len(parsed.chunks)} chunks from {parsed.page_count} pages",
        )

        # ---------------------------------------------------------- embed
        await _set_progress(job_id, JobStatus.EMBEDDING, 0.45, "building the index")

        embedder: Embedder = ctx["embedder"]
        store: QdrantStore = ctx["store"]
        await store.ensure_collection(
            settings.qdrant_collection_docs, embedder.config.dim
        )

        texts = [c.text for c in parsed.chunks]
        dense = embedder.embed_passages(texts)
        sparse = embedder.sparse_passages(texts)
        payloads = [
            c.to_payload(
                document_id=document_id, session_id=session_id, filename=filename
            )
            for c in parsed.chunks
        ]
        ids = [f"doc-{document_id}-{c.index:04d}" for c in parsed.chunks]

        indexed = await store.upsert(
            settings.qdrant_collection_docs, ids, dense, sparse, payloads
        )

        # ---------------------------------------------------------- ready
        async with session_scope() as db:
            document = await db.get(Document, document_id)
            job = await db.get(IngestJob, job_id)
            if document is not None:
                document.status = JobStatus.READY
                document.page_count = parsed.page_count
                document.chunk_count = indexed
                document.parse_report = parsed.report()
                document.injection_flags = flags
            if job is not None:
                job.status = JobStatus.READY
                job.progress = 1.0
                job.stage_detail = f"ready - {indexed} chunks queryable"

        logger.info(
            "ingest complete",
            document_id=document_id, chunks=indexed, pages=parsed.page_count,
            ocr_pages=parsed.pages_ocr, needs_review=parsed.needs_review,
        )
        return {"ok": True, "chunks": indexed, "pages": parsed.page_count}

    except Exception as exc:  # noqa: BLE001 - a failed job must record why
        logger.exception("ingest failed", document_id=document_id, error=str(exc))
        await _fail(job_id, document_id, f"Processing failed: {exc}")
        return {"ok": False, "error": str(exc)}


async def _fail(job_id: str, document_id: str, message: str) -> None:
    async with session_scope() as db:
        job = await db.get(IngestJob, job_id)
        if job is not None:
            job.status = JobStatus.FAILED
            job.error = message
            job.stage_detail = "failed"
        document = await db.get(Document, document_id)
        if document is not None:
            document.status = JobStatus.FAILED
            document.error = message


async def purge_document_vectors(ctx: dict, document_id: str) -> dict:
    """Reconciliation sweep for a delete whose vector purge did not complete."""
    store: QdrantStore = ctx["store"]
    removed = await store.delete_by_document(
        settings.qdrant_collection_docs, document_id
    )
    logger.info("purge sweep", document_id=document_id, removed=removed)
    return {"removed": removed}


async def startup(ctx: dict) -> None:
    configure_logging(settings.log_level, json_logs=settings.env == "production")
    ctx["store"] = QdrantStore(settings.qdrant_url)
    ctx["embedder"] = Embedder(
        EmbedderConfig(
            model=settings.embed_model,
            dim=settings.embed_dim,
            query_prefix=settings.embed_query_prefix,
            passage_prefix=settings.embed_passage_prefix,
        )
    )
    # Load the model now rather than inside the first job, so a queued upload
    # is not also paying for a cold start.
    ctx["embedder"].warm()
    logger.info("worker ready", embed_model=settings.embed_model)


async def shutdown(ctx: dict) -> None:
    store: QdrantStore | None = ctx.get("store")
    if store is not None:
        await store.close()


class WorkerSettings:
    functions = [ingest_document, purge_document_vectors]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 2               # embedding is CPU-bound; more would just thrash
    job_timeout = 900
    keep_result = 3600
