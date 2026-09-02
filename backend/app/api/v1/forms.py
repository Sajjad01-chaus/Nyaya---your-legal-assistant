"""The statutory forms library."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import Form
from app.db.session import get_session

router = APIRouter()

FORMS_DIR = Path(settings.forms_dir)


class FormOut(BaseModel):
    form_number: int
    title: str
    filename: str
    page_start: int
    page_end: int
    page_count: int
    size_bytes: int
    sha256: str
    extraction_confidence: float
    needs_review: bool
    review_reasons: list[str] = []
    see_sections: list[int] = []
    act_short: str = "BNSS"
    download_url: str = ""

    @classmethod
    def of(cls, row: Form) -> FormOut:
        return cls(
            form_number=row.form_number,
            title=row.title,
            filename=row.filename,
            page_start=row.page_start,
            page_end=row.page_end,
            page_count=row.page_count,
            size_bytes=row.size_bytes,
            sha256=row.sha256,
            extraction_confidence=row.extraction_confidence,
            needs_review=row.needs_review,
            review_reasons=list(row.review_reasons or []),
            see_sections=list(row.see_sections or []),
            act_short=row.act_short,
            download_url=f"/api/v1/forms/{row.form_number}/download",
        )


class FormListOut(BaseModel):
    total: int
    needs_review: int
    forms: list[FormOut]


def _resolve(row: Form) -> Path:
    """Resolve the stored filename inside the forms directory only.

    The name comes from our own manifest, but resolving and re-checking the
    parent costs nothing and makes path traversal impossible even if the
    manifest were ever populated from somewhere less trustworthy.
    """
    path = (FORMS_DIR / row.filename).resolve()
    if path.parent != FORMS_DIR.resolve() or not path.is_file():
        raise HTTPException(
            404,
            detail=f"Form {row.form_number} is listed but its file is missing. "
                   "Re-run: docker compose run --rm bootstrap",
        )
    return path


@router.get("/forms", response_model=FormListOut)
async def list_forms(
    needs_review: bool | None = Query(None, description="Filter by review flag"),
    db: AsyncSession = Depends(get_session),
) -> FormListOut:
    try:
        stmt = select(Form).order_by(Form.form_number)
        if needs_review is not None:
            stmt = stmt.where(Form.needs_review == needs_review)
        rows = (await db.execute(stmt)).scalars().all()
        flagged = await db.scalar(
            select(func.count()).select_from(Form).where(Form.needs_review.is_(True))
        )
        return FormListOut(
            total=len(rows), needs_review=int(flagged or 0),
            forms=[FormOut.of(r) for r in rows],
        )
    except Exception:
        # Database unavailable; return empty list
        return FormListOut(total=0, needs_review=0, forms=[])


@router.get("/forms/search", response_model=FormListOut)
async def search_forms(
    q: str = Query(..., min_length=1, description="Title search"),
    db: AsyncSession = Depends(get_session),
) -> FormListOut:
    """Title search. Declared before /forms/{id} so the literal path wins."""
    pattern = f"%{q.strip()}%"
    stmt = (
        select(Form)
        .where(or_(Form.title.ilike(pattern), Form.filename.ilike(pattern)))
        .order_by(Form.form_number)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return FormListOut(
        total=len(rows),
        needs_review=sum(1 for r in rows if r.needs_review),
        forms=[FormOut.of(r) for r in rows],
    )


@router.get("/forms/download-all")
async def download_all(db: AsyncSession = Depends(get_session)) -> StreamingResponse:
    """Every form in one zip, built in memory and streamed."""
    rows = (await db.execute(select(Form).order_by(Form.form_number))).scalars().all()
    if not rows:
        raise HTTPException(404, detail="No forms have been extracted yet.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for row in rows:
            path = FORMS_DIR / row.filename
            if path.is_file():
                archive.write(path, arcname=row.filename)
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="bnss-second-schedule-forms.zip"'
        },
    )


@router.get("/forms/{form_number}", response_model=FormOut)
async def get_form(
    form_number: int, db: AsyncSession = Depends(get_session)
) -> FormOut:
    row = await db.scalar(select(Form).where(Form.form_number == form_number))
    if row is None:
        raise HTTPException(404, detail=f"No form numbered {form_number}.")
    return FormOut.of(row)


@router.get("/forms/{form_number}/download")
async def download_form(
    form_number: int, db: AsyncSession = Depends(get_session)
) -> FileResponse:
    row = await db.scalar(select(Form).where(Form.form_number == form_number))
    if row is None:
        raise HTTPException(404, detail=f"No form numbered {form_number}.")
    return FileResponse(
        _resolve(row), media_type="application/pdf", filename=row.filename
    )


@router.get("/forms/{form_number}/preview")
async def preview_form(
    form_number: int, db: AsyncSession = Depends(get_session)
) -> FileResponse:
    """Same bytes, rendered inline so the panel can preview before download."""
    row = await db.scalar(select(Form).where(Form.form_number == form_number))
    if row is None:
        raise HTTPException(404, detail=f"No form numbered {form_number}.")
    return FileResponse(
        _resolve(row),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{row.filename}"'},
    )


__all__ = ["router", "settings"]
