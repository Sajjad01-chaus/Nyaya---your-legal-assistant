#!/usr/bin/env python
"""Bootstrap: index the bare act and build the forms library.

Idempotent. Re-running overwrites the same vector points and upserts the same
form rows, so it is safe to run repeatedly and safe to run on a container that
restarted.

    python scripts/ingest.py                      # everything
    python scripts/ingest.py --skip-forms         # statute only
    python scripts/ingest.py --statute-only ...   # see --help
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.db.models import Form, IngestedAct, OffenceClassification  # noqa: E402
from app.db.session import create_all, session_scope  # noqa: E402
from app.forms.extractor import extract_forms, write_manifest  # noqa: E402
from app.ingestion.pipeline import index_chunks, parse_bare_act  # noqa: E402
from app.retrieval.embeddings import Embedder, EmbedderConfig  # noqa: E402
from app.retrieval.store import QdrantStore  # noqa: E402

logger = get_logger("bootstrap")

# The volume is the BNSS, Act 46 of 2023, whatever the download is named.
# Detected from the document rather than assumed; see DECISIONS.md.
ACT = "Bharatiya Nagarik Suraksha Sanhita, 2023"
ACT_SHORT = "BNSS"
LAST_OPERATIVE_PAGE = 157   # pp.158-189 First Schedule, pp.190-249 Second Schedule


def _progress(stage: str, fraction: float) -> None:
    bar = "#" * int(fraction * 40)
    sys.stdout.write(f"\r  [{bar:<40}] {fraction * 100:5.1f}%  {stage:<12}")
    sys.stdout.flush()
    if fraction >= 1.0:
        sys.stdout.write("\n")


async def ingest_statute(pdf: Path, *, force: bool) -> None:
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()

    async with session_scope() as db:
        existing = await db.scalar(
            select(IngestedAct).where(IngestedAct.act_short == ACT_SHORT)
        )
        if (
            existing
            and existing.source_sha256 == digest
            and existing.embed_model == settings.embed_model
            and not force
        ):
            print(f"  statute already indexed ({existing.chunk_count} chunks) - skipping")
            print("  re-run with --force to rebuild")
            return

    print(f"\n> Parsing {pdf.name}")
    started = time.perf_counter()
    chunks, report = parse_bare_act(
        pdf,
        act=ACT,
        act_short=ACT_SHORT,
        last_operative_page=LAST_OPERATIVE_PAGE,
        progress=_progress,
    )
    print()
    print(f"  sections : {report.section_count}")
    print(f"  chunks   : {report.chunk_count}")
    print(f"  pages    : {report.pages_text} text, {report.pages_ocr} ocr, "
          f"{report.pages_garbage} unusable text layer")
    for warning in report.warnings:
        print(f"  WARNING  : {warning}")

    if report.needs_review:
        print("\n  Parse gate flagged this run. Indexing anyway, but the report")
        print("  above is recorded and /health/ready will show it.")

    print(f"\n> Embedding with {settings.embed_model} ({settings.embed_dim}-dim)")
    embedder = Embedder(
        EmbedderConfig(
            model=settings.embed_model,
            dim=settings.embed_dim,
            query_prefix=settings.embed_query_prefix,
            passage_prefix=settings.embed_passage_prefix,
        )
    )
    store = QdrantStore(settings.qdrant_url)
    indexed = await index_chunks(
        chunks,
        store=store,
        embedder=embedder,
        collection=settings.qdrant_collection_statute,
        progress=_progress,
    )
    print()

    async with session_scope() as db:
        row = await db.scalar(
            select(IngestedAct).where(IngestedAct.act_short == ACT_SHORT)
        )
        if row is None:
            row = IngestedAct(act_short=ACT_SHORT)
            db.add(row)
        row.act = ACT
        row.source_sha256 = digest
        row.section_count = report.section_count
        row.chunk_count = indexed
        row.embed_model = settings.embed_model
        row.parse_report = report.to_dict()

    await store.close()
    print(f"  indexed {indexed} chunks in {time.perf_counter() - started:.0f}s")


async def ingest_forms(pdf: Path, out_dir: Path) -> None:
    print(f"\n> Extracting forms from pages {settings.forms_page_start}-"
          f"{settings.forms_page_end}")
    records = extract_forms(
        pdf,
        out_dir,
        page_start=settings.forms_page_start,
        page_end=settings.forms_page_end,
    )
    manifest_path = out_dir / "forms_manifest.json"
    write_manifest(records, manifest_path, source=pdf.name)

    flagged = [r for r in records if r.needs_review]
    print(f"  forms        : {len(records)}")
    print(f"  multi-page   : {[(r.form_number, r.page_count) for r in records if r.page_count > 1]}")
    print(f"  needs_review : {len(flagged)}")
    for record in flagged:
        print(f"     Form {record.form_number}: {'; '.join(record.review_reasons)}")
    print(f"  manifest     : {manifest_path}")

    async with session_scope() as db:
        for record in records:
            row = await db.scalar(
                select(Form).where(Form.form_number == record.form_number)
            )
            if row is None:
                row = Form(form_number=record.form_number)
                db.add(row)
            row.title = record.title
            row.filename = record.filename
            row.page_start = record.page_start
            row.page_end = record.page_end
            row.page_count = record.page_count
            row.size_bytes = record.bytes
            row.sha256 = record.sha256
            row.extraction_confidence = record.extraction_confidence
            row.needs_review = record.needs_review
            row.review_reasons = record.review_reasons
            row.see_sections = record.see_sections
            row.act_short = ACT_SHORT
    print(f"  {len(records)} rows upserted into postgres")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Index the corpus and build the forms library.")
    parser.add_argument("--pdf", default=settings.source_pdf, help="source PDF path")
    parser.add_argument("--forms-dir", default="/data/forms", help="where to write form PDFs")
    parser.add_argument("--skip-statute", action="store_true")
    parser.add_argument("--skip-forms", action="store_true")
    parser.add_argument("--force", action="store_true", help="re-index even if unchanged")
    args = parser.parse_args()

    configure_logging(settings.log_level, json_logs=False)

    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"ERROR: source PDF not found at {pdf}")
        print("Fetch it first:  bash scripts/fetch_corpus.sh")
        return 1

    print("=" * 68)
    print("  Nyaya bootstrap")
    print(f"  source   : {pdf}")
    print(f"  qdrant   : {settings.qdrant_url}")
    print(f"  postgres : {settings.postgres_host}:{settings.postgres_port}")
    print("=" * 68)

    await create_all()
    print("  schema ensured")

    if not args.skip_statute:
        await ingest_statute(pdf, force=args.force)
    if not args.skip_forms:
        await ingest_forms(pdf, Path(args.forms_dir))

    async with session_scope() as db:
        offences = await db.scalar(select(OffenceClassification).limit(1))
    if offences is None:
        print("\n  note: the First Schedule offence table is not yet populated;")
        print("  offence questions fall back to semantic retrieval.")

    print("\nBootstrap complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
