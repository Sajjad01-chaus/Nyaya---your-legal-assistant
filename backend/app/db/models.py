"""Relational schema.

Postgres holds everything except the vectors: conversations, uploaded-document
metadata, ingestion jobs, feedback, the forms manifest and the First Schedule
offence table. Qdrant holds the embeddings.

The one place that split is felt is document deletion, which cannot be a single
transaction. Rows cascade here; vectors are removed first and reconciled, so a
partial failure leaves rows without vectors rather than vectors without rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class JobStatus(str, PyEnum):
    QUEUED = "queued"
    PARSING = "parsing"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    READY = "ready"
    FAILED = "failed"


# --------------------------------------------------------------- conversations
class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))          # user | assistant
    content: Mapped[str] = mapped_column(Text)
    # Citations, retrieved chunk ids, timings, confidence, token usage and cost.
    # Kept so the source drawer can be rebuilt on reload without re-retrieving.
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# ------------------------------------------------------------------ documents
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # Ownership is enforced on every read; a mismatched session gets a 404,
    # never the document and never a 403 that would confirm it exists.
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), default=JobStatus.QUEUED
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Per-page extraction method, confidence and needs_review flags.
    parse_report: Mapped[dict] = mapped_column(JSONB, default=dict)
    injection_flags: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    jobs: Mapped[list[IngestJob]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_documents_session_created", "session_id", "created_at"),
    )


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="job_status"), default=JobStatus.QUEUED
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)   # 0..1
    stage_detail: Mapped[str] = mapped_column(String(200), default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    document: Mapped[Document] = relationship(back_populates="jobs")


# ------------------------------------------------------------------- feedback
class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    rating: Mapped[int] = mapped_column(Integer)               # +1 or -1
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


# ---------------------------------------------------------------------- forms
class Form(Base):
    """The forms manifest, served by the API and diffed against theirs."""

    __tablename__ = "forms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    form_number: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(400))
    filename: Mapped[str] = mapped_column(String(300))
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    extraction_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons: Mapped[list] = mapped_column(JSONB, default=list)
    see_sections: Mapped[list] = mapped_column(JSONB, default=list)
    act_short: Mapped[str] = mapped_column(String(12), default="BNSS")


# ------------------------------------------------------- First Schedule table
class OffenceClassification(Base):
    """One row of the First Schedule: how an offence is classified.

    Answers "is BNS s.318 bailable, and which court tries it?" with a SELECT
    rather than a similarity search, which is the difference between a fact and
    a plausible-looking guess.
    """

    __tablename__ = "offence_classifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    act_short: Mapped[str] = mapped_column(String(12), index=True, default="BNS")
    section_number: Mapped[str] = mapped_column(String(16), index=True)
    subsection: Mapped[str | None] = mapped_column(String(16), nullable=True)
    offence: Mapped[str] = mapped_column(Text)
    punishment: Mapped[str] = mapped_column(Text)
    cognizable: Mapped[str] = mapped_column(String(64))
    bailable: Mapped[str] = mapped_column(String(64))
    triable_by: Mapped[str] = mapped_column(Text)
    page: Mapped[int] = mapped_column(Integer)
    extraction_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (
        UniqueConstraint(
            "act_short", "section_number", "subsection", name="uq_offence_section"
        ),
        Index("ix_offence_lookup", "act_short", "section_number"),
    )


# ------------------------------------------------------------------- corpus
class IngestedAct(Base):
    """What the bootstrap actually indexed, so /health/ready can prove it."""

    __tablename__ = "ingested_acts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    act_short: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    act: Mapped[str] = mapped_column(String(200))
    source_sha256: Mapped[str] = mapped_column(String(64))
    section_count: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    embed_model: Mapped[str] = mapped_column(String(120))
    parse_report: Mapped[dict] = mapped_column(JSONB, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
