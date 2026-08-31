# Nyaya — DhronAI assignment

RAG system over the **Bharatiya Nagarik Suraksha Sanhita, 2023** (BNSS).
Grounded statutory Q&A with citations, plus Second Schedule form extraction.

## The corpus trap (read this first)

`data/raw/bnss_2023.pdf` was shipped by the assignment named
`BNS bare act 2023.pdf`. **It is not the BNS.** It is the BNSS 2023, Act No. 46
of 2023. The brief hints that noticing this counts in our favour. Full page map
and the five confirmed parsing traps live in the memory file
`nyaya-corpus-is-bnss-not-bns.md` — read it before touching ingestion.

## Layout

```
backend/app/
  ingestion/   layout.py -> statute.py -> pipeline.py, documents.py (user uploads)
  forms/       extractor.py  (Second Schedule, 58 forms -> data/forms/)
  retrieval/   embeddings.py (fastembed/ONNX) store.py (Qdrant, server-side RRF)
               query.py (router) service.py (orchestrator, CRAG, confidence)
  llm/         provider.py (HTTP, swap by env) prompts.py guards.py (citations)
  api/v1/      chat search documents forms feedback health
  workers/     tasks.py (arq, async ingestion)
  db/          models.py, session.py
frontend/      Next.js 15 App Router: chat, search, documents, forms
eval/          golden_set.jsonl (35 q), run_eval.py, results/
scripts/       ingest.py, bootstrap.sh
```

The frontend talks to the API cross-origin and the session is an **httpOnly
cookie**, so every request needs `credentials: "include"`. Chat is SSE over
POST (events `meta`/`token`/`validation`/`done`/`error`), so `EventSource`
cannot be used - `lib/api.ts` reads the body stream and parses frames.

Two things that have already caused silent failures here:
- `sse-starlette` writes **CRLF**. An SSE parser splitting frames on `"

"`
  matches nothing and drops every event without erroring.
- `/chat` must **commit** the conversation before returning the streaming
  response. The generator persists the assistant turn on its own session, and a
  merely-flushed row is invisible to it, so the foreign key fails after the
  answer has already streamed.

Vector store is **Qdrant** (the brief prefers it). `pgvector` stays pinned only
for the alternate VectorStore backend. Postgres holds relational state.

## Commands

```bash
cd backend && python -m pytest tests/ -q      # 102 tests, all passing
python eval/run_eval.py                        # writes eval/results/latest.json
cd frontend && npm run dev                     # UI on :3000, expects API on :8000
cd frontend && npm run typecheck && npm run build
docker compose up -d                           # qdrant, postgres, redis, api, worker
docker compose --profile bootstrap up          # migrate + ingest + extract forms
docker compose --profile monitoring up         # prometheus :9090, grafana :3001
```

## Where it stands

### ✅ COMPLETE — Ready for Submission (as of 2026-08-31)

**Core System** (100% ✅):
- Ingestion: 531/531 sections, 0 gaps
- Forms extraction: 59 files in `data/forms/`
- Retrieval: Qdrant + RRF (dense + sparse), cross-encoder reranking
- Query routing: CRAG correction for medium-confidence queries
- Citation guards: 3-layer validation (existence, quote fidelity, support)
- FastAPI surface: All endpoints (chat, documents, search, forms, feedback, health)
- Async ingestion: arq workers + Redis queue
- Evaluation: 35-question golden set, comprehensive metrics

**Recent Fixes (this session)**:
- **Citation accuracy**: Fixed 78.6% → **100%**
  - Root cause: Typographic spaces (U+202F) defeated regex; fixed with `normalise_spaces()`
  - Added `upgrade_bare_citations()` to rewrite prose refs to brackets when grounded
- **Rate limiting**: Wired slowapi (30/minute chat, 10/hour upload) to endpoints
- **Frontend UX**: Dark mode toggle, drag-drop upload, copy buttons, regenerate, ARIA accessibility
- **Documentation**: README (quick start + eval results), ARCHITECTURE.md (design choices), DECISIONS.md (framework trade-offs)
- **CI/CD**: GitHub Actions (test.yml, build.yml, deploy.yml)

**Latest eval** (35 questions, `eval/results/latest.json`):
- Recall@5: **100%**
- MRR: **0.912**
- Citation accuracy: **100%** (fixed from 78.6%)
- Out-of-scope refusal: **100%**
- False refusal: **0%**
- Generation p50: 8.2s
- Generation p95: 44s

### 🟡 Optional/Lower Priority

1. **Generation latency** (p95 44s) — Groq free tier + model loading; acceptable for demo
2. **Alembic migrations** — Schema via `create_all()` in `ingest.py`; idempotent, works for docker
3. **API/integration tests** — `tests/api/` and `tests/integration/` empty; 102 unit tests covering core
4. **Fix missing fetch_corpus.sh reference** — Minor: error path unreachable (corpus already ingested)
5. **First Schedule offence table** — Offence questions fall back to semantic search; nice-to-have

## Conventions

- Dependencies are **fully pinned**, and only to versions actually verified.
- PDF libraries are permissive-licence only. PyMuPDF is AGPL and is avoided.
- No torch, no sentence-transformers; ONNX via fastembed keeps the image small.
- Commit messages: `type(scope): summary`, lowercase, no trailing period.
