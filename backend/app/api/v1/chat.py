"""Streaming chat, conversations, and the refusal path.

One design tension worth naming. Token streaming and post-generation validation
pull against each other: by the time the guard can check an answer, the user has
already read it. Buffering the whole response to validate first would restore
correctness at the cost of the thing streaming exists for.

The resolution is to stream tokens for latency, then emit a terminal
``validation`` event carrying the verified answer. When the guard stripped an
invented citation or refused outright, the client replaces what it rendered. The
final state a user can act on is always the validated one, and the common case
(nothing to correct) costs nothing.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.api.deps import current_session, get_llm, get_retrieval_service
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import (
    chat_requests,
    citations_stripped,
    generation_latency,
    record_usage,
    refusals,
    retrieval_latency,
    time_to_first_token,
)
from app.db.models import Conversation, Message
from app.db.session import get_session, session_scope
from app.llm.guards import (
    QUOTE_FAILURE_TEXT,
    REFUSAL_TEXT,
    Verdict,
    verify_answer,
)
from app.llm.prompts import build_prompt
from app.retrieval.service import Confidence, to_prompt_dicts

router = APIRouter()
logger = get_logger(__name__)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None
    use_documents: bool = Field(
        False, description="Include this session's uploaded documents in retrieval"
    )


class ConversationOut(BaseModel):
    id: str
    title: str
    message_count: int = 0
    updated_at: str


class RenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


async def _get_or_create_conversation(
    db: AsyncSession, conversation_id: str | None, session_id: str, first_message: str
) -> Conversation:
    if conversation_id:
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.session_id == session_id,
            )
        )
        if conversation is None:
            raise HTTPException(404, detail="No such conversation.")
        return conversation

    title = first_message.strip()[:60] + ("..." if len(first_message) > 60 else "")
    conversation = Conversation(session_id=session_id, title=title or "New conversation")
    db.add(conversation)
    await db.flush()
    return conversation


@router.post("/chat")
async def chat(
    payload: ChatRequest,
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> EventSourceResponse:
    conversation = await _get_or_create_conversation(
        db, payload.conversation_id, session_id, payload.message
    )
    conversation_id = conversation.id

    db.add(Message(conversation_id=conversation_id, role="user", content=payload.message))
    await db.flush()

    history = [
        {"role": m.role, "content": m.content}
        for m in (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at)
            )
        ).scalars().all()
    ][:-1]

    async def stream() -> AsyncIterator[dict]:
        started = time.perf_counter()
        service = get_retrieval_service()

        # ---------------------------------------------------------- retrieve
        try:
            result = await service.retrieve(
                payload.message,
                session_id=session_id,
                has_documents=payload.use_documents,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("retrieval failed", error=str(exc))
            chat_requests.labels(route="error", outcome="retrieval_failed").inc()
            yield _sse("error", {
                "message": "Retrieval is unavailable right now. "
                           "The statute index may still be starting up.",
            })
            return

        for stage, ms in result.timings_ms.items():
            retrieval_latency.labels(stage=stage).observe(ms / 1000)

        statute = to_prompt_dicts(result.chunks)
        documents = to_prompt_dicts(result.document_chunks)

        yield _sse("meta", {
            "conversation_id": conversation_id,
            "route": result.route_taken,
            "confidence": result.confidence.value,
            "score": result.score,
            "reranked": result.reranked,
            "rewritten_query": result.rewritten_query,
            "disambiguation": result.disambiguation,
            "sources": [
                {
                    "chunk_id": c["chunk_id"],
                    "citation": f"[{c['act_short']} s.{c['section_number']}]",
                    "act_short": c["act_short"],
                    "section_number": c["section_number"],
                    "section_title": c["section_title"],
                    "page_start": c["page_start"],
                    "text": c["text"],
                }
                for c in statute
            ],
            "document_sources": [
                {"chunk_id": c["chunk_id"], "filename": c["filename"],
                 "page_start": c["page_start"], "text": c["text"]}
                for c in documents
            ],
        })

        # ------------------------------------------------------- refuse early
        if not result.should_answer:
            refusals.labels(reason="low_confidence").inc()
            chat_requests.labels(route=result.route_taken, outcome="refused").inc()
            text = result.disambiguation or REFUSAL_TEXT
            yield _sse("token", {"text": text})
            yield _sse("validation", {
                "verdict": "refused",
                "answer": text,
                "citations": [],
                "notes": ["retrieval confidence below threshold"],
            })
            async with session_scope() as store_db:
                store_db.add(Message(
                    conversation_id=conversation_id, role="assistant", content=text,
                    meta={"refused": True, "confidence": result.confidence.value,
                          "score": result.score, "route": result.route_taken},
                ))
            yield _sse("done", {"refused": True})
            return

        # ------------------------------------------------------------ generate
        prompt = build_prompt(payload.message, statute, documents, history=history)
        provider = get_llm()

        pieces: list[str] = []
        usage = None
        first_token_at: float | None = None

        try:
            async for chunk in provider.stream(
                prompt.system, prompt.user,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
            ):
                if chunk.text:
                    if first_token_at is None:
                        first_token_at = time.perf_counter()
                        time_to_first_token.observe(first_token_at - started)
                    pieces.append(chunk.text)
                    yield _sse("token", {"text": chunk.text})
                if chunk.usage:
                    usage = chunk.usage
        except Exception as exc:  # noqa: BLE001
            logger.exception("generation failed", error=str(exc))
            chat_requests.labels(route=result.route_taken, outcome="llm_failed").inc()
            yield _sse("error", {
                "message": "The language model did not respond. Retrieval worked - "
                           "open the sources panel to read the sections directly.",
            })
            return

        answer = "".join(pieces)
        generation_latency.observe(time.perf_counter() - started)

        # ---------------------------------------------------------- validate
        # A statutory claim must be cited. Reading the user's own upload back to
        # them is not a statutory claim, so a document-only answer is not forced
        # to produce a section reference it has no business inventing.
        report = verify_answer(
            answer, statute + documents, require_citation=bool(statute)
        )
        if report.invented:
            citations_stripped.inc(len(report.invented))
        if report.verdict is Verdict.REFUSED:
            # Distinguish "nothing to say" from "said it wrongly": a
            # misquotation is a different failure from missing evidence, and
            # telling the user which one happened is the honest thing.
            if report.unsupported_quotes:
                refusals.labels(reason="unsupported_quote").inc()
                report.answer = QUOTE_FAILURE_TEXT
            else:
                refusals.labels(reason="failed_validation").inc()
                report.answer = REFUSAL_TEXT

        cost = 0.0
        if usage:
            cost = usage.cost_usd(
                settings.cost_per_1m_input_usd, settings.cost_per_1m_output_usd
            )
            record_usage(usage.prompt_tokens, usage.completion_tokens, cost)

        yield _sse("validation", {
            "verdict": report.verdict.value,
            "answer": report.answer,
            "changed": report.answer.strip() != answer.strip(),
            "citations": [c.render() for c in report.valid],
            "stripped": [c.render() for c in report.invented],
            "notes": report.notes,
        })

        async with session_scope() as store_db:
            store_db.add(Message(
                conversation_id=conversation_id, role="assistant",
                content=report.answer,
                meta={
                    "verdict": report.verdict.value,
                    "citations": [c.render() for c in report.valid],
                    "stripped": [c.render() for c in report.invented],
                    "confidence": result.confidence.value,
                    "score": result.score,
                    "route": result.route_taken,
                    "chunk_ids": [c["chunk_id"] for c in statute],
                    "timings_ms": result.timings_ms,
                    "cost_usd": round(cost, 6),
                },
            ))

        chat_requests.labels(
            route=result.route_taken, outcome=report.verdict.value
        ).inc()
        yield _sse("done", {
            "usage": {
                "prompt_tokens": usage.prompt_tokens if usage else 0,
                "completion_tokens": usage.completion_tokens if usage else 0,
            },
            "cost_usd": round(cost, 6),
            "total_ms": round((time.perf_counter() - started) * 1000, 1),
            "ttft_ms": round((first_token_at - started) * 1000, 1) if first_token_at else None,
        })

    return EventSourceResponse(stream())


# ------------------------------------------------------------- conversations
@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> list[ConversationOut]:
    rows = (
        await db.execute(
            select(Conversation)
            .where(Conversation.session_id == session_id)
            .order_by(Conversation.updated_at.desc())
        )
    ).scalars().all()
    return [
        ConversationOut(
            id=c.id, title=c.title, message_count=len(c.messages),
            updated_at=c.updated_at.isoformat(),
        )
        for c in rows
    ]


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> dict:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.session_id == session_id
        )
    )
    if conversation is None:
        raise HTTPException(404, detail="No such conversation.")
    return {
        "id": conversation.id,
        "title": conversation.title,
        "messages": [
            {"id": m.id, "role": m.role, "content": m.content,
             "meta": m.meta, "created_at": m.created_at.isoformat()}
            for m in conversation.messages
        ],
    }


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut)
async def rename_conversation(
    conversation_id: str,
    payload: RenameRequest,
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> ConversationOut:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.session_id == session_id
        )
    )
    if conversation is None:
        raise HTTPException(404, detail="No such conversation.")
    conversation.title = payload.title
    await db.flush()
    return ConversationOut(
        id=conversation.id, title=conversation.title,
        message_count=len(conversation.messages),
        updated_at=conversation.updated_at.isoformat(),
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> None:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.session_id == session_id
        )
    )
    if conversation is None:
        raise HTTPException(404, detail="No such conversation.")
    await db.delete(conversation)
