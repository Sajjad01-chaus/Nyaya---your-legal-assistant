"""Nyaya API."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import Response

from app.core.config import settings
from app.core.logging import configure_logging, get_logger, request_id_var
from app.core.metrics import chat_requests

configure_logging(settings.log_level, json_logs=settings.env == "production")
logger = get_logger()

limiter = Limiter(key_func=get_remote_address)

# Import routes AFTER limiter is created to avoid circular imports
from app.api.v1 import chat, documents, feedback, forms, health, search


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    logger.info(
        "starting",
        env=settings.env,
        vector_store=settings.vector_store,
        embed_model=settings.embed_model,
        llm_provider=settings.llm_provider,
    )
    # Initialize database tables on startup
    from app.db.session import create_all
    await create_all()
    # Ingest PDF if not already done
    try:
        from app.ingestion.pipeline import ingest_statute
        await ingest_statute()
        logger.info("statute ingestion complete")
    except Exception as e:
        logger.warning(f"statute ingestion skipped: {e}")
    yield
    from app.api.deps import get_store

    await get_store().close()
    logger.info("stopped")


app = FastAPI(
    title="Nyaya API",
    version="0.1.0",
    description=(
        "Retrieval over the Bharatiya Nagarik Suraksha Sanhita, 2023. "
        "Every legal statement carries an inline citation to Act and section; "
        "answers without supporting context are refused rather than guessed."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda r, e: JSONResponse(
    status_code=429,
    content={
        "error": "rate_limit_exceeded",
        "message": "Too many requests. Please try again later.",
    },
))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"]
    if settings.env == "development"
    else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context(request: Request, call_next):  # noqa: ANN001, ANN201
    """Stamp a request id, log the outcome, and never leak a stack trace."""
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    request_id_var.set(request_id)
    started = time.perf_counter()

    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "unhandled error", path=request.url.path, method=request.method, error=str(exc)
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "Something went wrong on our side. The request id below "
                           "will let us find it in the logs.",
                "request_id": request_id,
            },
            headers={"X-Request-ID": request_id},
        )

    duration = time.perf_counter() - started
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration * 1000, 1),
    )
    return response


@app.get("/api/v1/metrics", include_in_schema=True, tags=["ops"])
async def metrics() -> Response:
    """Prometheus scrape endpoint."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(health.router, prefix="/api/v1", tags=["ops"])
app.include_router(chat.router, prefix="/api/v1", tags=["chat"])
app.include_router(documents.router, prefix="/api/v1", tags=["documents"])
app.include_router(search.router, prefix="/api/v1", tags=["search"])
app.include_router(forms.router, prefix="/api/v1", tags=["forms"])
app.include_router(feedback.router, prefix="/api/v1", tags=["feedback"])


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "name": "Nyaya",
        "docs": "/docs",
        "health": "/api/v1/health",
        "corpus": "Bharatiya Nagarik Suraksha Sanhita, 2023",
    }


__all__ = ["app", "chat_requests"]
