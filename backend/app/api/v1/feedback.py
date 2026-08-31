"""Thumbs up/down with optional text, persisted."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_session
from app.db.models import Feedback
from app.db.session import get_session

router = APIRouter()


class FeedbackIn(BaseModel):
    rating: int = Field(..., description="+1 for helpful, -1 for not helpful")
    message_id: str | None = Field(None, description="Message the rating applies to")
    comment: str | None = Field(None, max_length=2000)

    def normalised_rating(self) -> int:
        return 1 if self.rating > 0 else -1


class FeedbackOut(BaseModel):
    id: str
    recorded: bool = True


@router.post("/feedback", response_model=FeedbackOut, status_code=201)
async def submit_feedback(
    payload: FeedbackIn,
    session_id: str = Depends(current_session),
    db: AsyncSession = Depends(get_session),
) -> FeedbackOut:
    row = Feedback(
        session_id=session_id,
        message_id=payload.message_id,
        rating=payload.normalised_rating(),
        comment=payload.comment,
    )
    db.add(row)
    await db.flush()
    return FeedbackOut(id=row.id)
