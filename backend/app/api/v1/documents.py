"""Document upload and lifecycle.

Ingestion is asynchronous: a 60-page upload must not hold the request thread,
so the endpoint stores the file, enqueues a job and returns immediately with
both ids. Progress is polled from the status endpoint, which is what drives the
parse -> chunk -> embed -> ready indicator in the UI.

Ownership is enforced on every read. A document belonging to another session
returns 404, not 403, because 403 confirms that the id exists.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_session, get_store
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import uploads
from app.core.security import is_encrypted_pdf, validate_upload
from app.db.models import Document, IngestJob, JobStatus
from app.db.session import get_session

router = APIRouter()
logger = get_logger(__name__)

UPLOAD_DIR = Path(settings.upload_dir)


class UploadResponse(BaseModel):
    document_id: str
    job_id: str
    status: str
    filename: str
    size_bytes: int


class DocumentOut(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    page_count: int
    chunk_count: int
    status: str
    error: str | None = None
    injection_flags: list[str] = []


class JobStatusOut(BaseModel):
    document_id: str
    job_id: str
    status: str
    progress: float
    stage_detail: str
    error: str | None = None


async def _owned(db: AsyncSession, document_id: str, session_id: str) -> Document:
    document = await db.scalar(
        select(Document).where(
            Document.id == document_id, Document.session_id == session_id
        )
    )
    if document is None:
        # Deliberately indistinguishable from "no such document".
        raise HTTPException(404, detail="No such document.")
    return document


@router.post("/documents/upload", response_model=UploadResponse, status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> UploadResponse:
    payload = await file.read()

    verdict = validate_upload(
        payload[:4096],
        len(payload),
        allowed=settings.allowed_mime_set,
        max_bytes=settings.max_upload_bytes,
    )
    if not verdict.ok:
        uploads.labels(outcome="rejected").inc()
        raise HTTPException(400, detail=verdict.reason)

    if verdict.detected_type == "application/pdf" and is_encrypted_pdf(payload):
        uploads.labels(outcome="rejected").inc()
        raise HTTPException(
            400,
            detail="This PDF is password-protected and cannot be read. "
                   "Remove the password and upload it again.",
        )

    digest = hashlib.sha256(payload).hexdigest()
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    stored = UPLOAD_DIR / f"{digest}{Path(file.filename or '').suffix.lower()}"
    stored.write_bytes(payload)

    document = Document(
        session_id=session_id,
        filename=file.filename or "upload",
        content_type=verdict.detected_type or "application/octet-stream",
        size_bytes=len(payload),
        sha256=digest,
        status=JobStatus.QUEUED,
    )
    db.add(document)
    await db.flush()

    job = IngestJob(document_id=document.id, status=JobStatus.QUEUED)
    db.add(job)
    await db.flush()

    # Enqueue rather than parse inline: a 60-page PDF would otherwise block the
    # worker thread for the length of an embedding run.
    try:
        from arq.connections import RedisSettings, create_pool

        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await pool.enqueue_job("ingest_document", document.id, job.id)
        await pool.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.error("enqueue failed", document_id=document.id, error=str(exc))
        job.status = JobStatus.FAILED
        job.error = "Could not queue the document for processing."
        document.status = JobStatus.FAILED
        document.error = job.error
        uploads.labels(outcome="enqueue_failed").inc()
        raise HTTPException(
            503, detail="Upload accepted but processing is unavailable. Try again shortly."
        ) from exc

    uploads.labels(outcome="accepted").inc()
    logger.info(
        "document uploaded",
        document_id=document.id,
        bytes=len(payload),
        content_type=verdict.detected_type,
    )
    return UploadResponse(
        document_id=document.id,
        job_id=job.id,
        status=JobStatus.QUEUED.value,
        filename=document.filename,
        size_bytes=document.size_bytes,
    )


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    rows = (
        await db.execute(
            select(Document)
            .where(Document.session_id == session_id)
            .order_by(Document.created_at.desc())
        )
    ).scalars().all()
    return [
        DocumentOut(
            id=d.id, filename=d.filename, content_type=d.content_type,
            size_bytes=d.size_bytes, page_count=d.page_count,
            chunk_count=d.chunk_count, status=d.status.value, error=d.error,
            injection_flags=list(d.injection_flags or []),
        )
        for d in rows
    ]


@router.get("/documents/{document_id}/status", response_model=JobStatusOut)
async def document_status(
    document_id: str,
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> JobStatusOut:
    document = await _owned(db, document_id, session_id)
    job = await db.scalar(
        select(IngestJob)
        .where(IngestJob.document_id == document.id)
        .order_by(IngestJob.created_at.desc())
    )
    if job is None:
        raise HTTPException(404, detail="No ingestion job for this document.")
    return JobStatusOut(
        document_id=document.id, job_id=job.id, status=job.status.value,
        progress=job.progress, stage_detail=job.stage_detail, error=job.error,
    )


@router.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: str,
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> DocumentOut:
    d = await _owned(db, document_id, session_id)
    return DocumentOut(
        id=d.id, filename=d.filename, content_type=d.content_type,
        size_bytes=d.size_bytes, page_count=d.page_count, chunk_count=d.chunk_count,
        status=d.status.value, error=d.error, injection_flags=list(d.injection_flags or []),
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> None:
    """Purge the vectors, then the rows, then the file.

    Vectors go first on purpose. The two stores cannot be updated atomically,
    so the ordering is chosen to fail safe: an interruption leaves rows without
    vectors, which is inert, rather than vectors without rows, which would let
    a deleted document keep surfacing in retrieval.
    """
    document = await _owned(db, document_id, session_id)

    removed = await get_store().delete_by_document(
        settings.qdrant_collection_docs, document.id
    )
    logger.info("purged vectors", document_id=document.id, vectors_removed=removed)

    stored = Path(document.sha256)
    for candidate in UPLOAD_DIR.glob(f"{stored.name}*"):
        candidate.unlink(missing_ok=True)

    await db.delete(document)   # cascades to ingest_jobs
