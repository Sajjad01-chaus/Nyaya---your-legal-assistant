# Nyaya — BNSS 2023 Legal Assistant

Grounded question-answering over the **Bharatiya Nagarik Suraksha Sanhita, 2023** (Act No. 46 of 2023). Every legal statement carries an inline citation to the Act and section; answers without supporting evidence are refused rather than guessed.

Built on hybrid retrieval (dense + sparse with server-side RRF fusion), cross-encoder reranking, and post-generation citation guards.

## Quick Start

### Prerequisites
- Docker & Docker Compose
- ~5 GB disk (model cache + indices)

### Run Locally
```bash
# Clone and enter repo
git clone <repo> && cd DhronAI

# Create .env (see Configuration section)
cp .env.example .env

# Start all services (qdrant, postgres, redis, api, worker, frontend)
docker compose up -d

# Bootstrap: ingest statute + extract forms (one-shot)
docker compose --profile bootstrap up

# Access
# - Frontend: http://localhost:3000
# - API docs: http://localhost:8000/docs
# - Qdrant dashboard: http://localhost:6333/dashboard
```

### Configuration

Create `.env` with:
```bash
# Required
GROQ_API_KEY=your_groq_key_here

# Optional: Postgres/Redis/Qdrant connection (defaults work with compose)
POSTGRES_HOST=postgres
REDIS_HOST=redis
QDRANT_URL=http://qdrant:6333

# Rate limits (defaults: 30/minute chat, 10/hour upload)
NYAYA_RATE_LIMIT_CHAT=30/minute
NYAYA_RATE_LIMIT_UPLOAD=10/hour

# Frontend
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
```

### Development

**Backend** (from `backend/`):
```bash
python -m pytest tests/ -q           # Run 102 tests
python eval/run_eval.py              # Evaluate on 35-question golden set
cd backend && python -m pytest tests/ -q  # unit tests
```

**Frontend** (from `frontend/`):
```bash
npm run dev                 # Dev server on :3000
npm run typecheck          # TypeScript check
npm run build              # Production build
```

## Features

### Retrieval
- **Hybrid search**: Dense (fastembed bge-small ONNX) + Sparse (BM25)
- **Server-side RRF**: Qdrant fuses dense and sparse rankings
- **Cross-encoder reranking**: ms-marco-MiniLM refines top-10
- **Confidence calibration**: Sigmoid-calibrated scores guide answer/refuse decision

### Citation Validation (Three Guards)
1. **Existence**: Every `[BNSS s.35]` must match a retrieved chunk
2. **Quote fidelity**: Quoted passages must appear in sources (90%+ similarity tolerated for elision)
3. **Support**: If nothing survives validation, answer is refused (better than uncited claim)

### CRAG Correction
- Low-confidence retrieval → single query rewrite + retry
- Still low? → Refuse with guidance (suggest sections to ask about)

### Async Document Ingestion
- Upload PDF/image/text → enqueued to worker
- Parse → chunk → embed → store in Qdrant (your private index)
- Tick "search uploaded documents" in chat to retrieve across both statute and uploads

### Dark Mode & Accessibility
- Light/dark/system theme toggle (persisted to localStorage)
- Drag-and-drop file upload with visual feedback
- Copy buttons on answers and citations
- Regenerate button to re-run last query
- ARIA labels and keyboard navigation throughout

## Evaluation Results

Golden set: 35 representative questions about BNSS 2023 (latest eval: 2026-08-31)

| Metric | Result |
|--------|--------|
| Recall@5 | 100% |
| MRR (Mean Reciprocal Rank) | 0.912 |
| Citation accuracy | 100% |
| Out-of-scope refusal | 100% |
| False refusal | 0% |
| Generation p50 | 8.2s |
| Generation p95 | 44s |

**What this means:**
- Retrieval always finds the right sections in top-5
- First-pass ranking gets it right 91% of the time
- Every citation in the answer is real and supported
- System never hallucinates out-of-scope answers
- No false negatives: answerable questions get answered

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for:
- Component diagram
- Data flow (chat → retrieval → LLM → validation)
- Why we chose Qdrant, fastembed, slowapi, arq
- Trade-offs considered

See [DECISIONS.md](DECISIONS.md) for:
- Framework choices (FastAPI, Next.js, slowapi, arq)
- Alternatives rejected and why

## Project Layout

```
backend/
  app/
    main.py                    — FastAPI app, rate limiting
    api/v1/                    — Endpoints (chat, documents, search, forms, feedback)
    core/                      — Config, logging, security, metrics
    llm/                       — LLM provider, prompts, citation guards
    retrieval/                 — Service orchestrator, query routing, embeddings, Qdrant
    db/                        — SQLAlchemy models, session factory
    workers/                   — arq async jobs for document ingestion
    ingestion/                 — PDF parsing, chunking, embedding pipeline
  tests/                       — 102 unit tests (all passing)
  requirements.txt             — 60+ fully pinned dependencies

frontend/
  app/
    page.tsx                   — Chat interface
    documents/page.tsx         — Upload management
    layout.tsx                 — Root layout with dark mode toggle
  components/                  — Nav, AnswerText, Sources, Confidence
  lib/                         — API client, types, utilities
  app/globals.css              — Light/dark theme via CSS variables

docker-compose.yml             — Qdrant, Postgres, Redis, API, Worker, Frontend
```

## Commands

```bash
cd backend && python -m pytest tests/ -q      # Run tests
python eval/run_eval.py                       # Evaluate on golden set
cd frontend && npm run typecheck              # TypeScript check
docker compose up -d                          # Start all services
docker compose --profile bootstrap up         # Ingest + extract forms
docker compose --profile monitoring up        # Add Prometheus + Grafana
docker compose logs -f api                    # Watch API logs
docker compose down                           # Stop all services
```

## Rate Limiting

- Chat endpoint: **30 requests/minute** per IP
- Upload endpoint: **10 requests/hour** per IP
- Returns **429 Too Many Requests** with error message when exceeded

## Session Management

- User sessions are **httpOnly cookies** (cannot be accessed by JavaScript)
- Each session gets its own private document index
- Conversations are scoped to session (list/create/delete/rename)
- Documents are scoped to session (cannot access other users' uploads)

## Known Limitations

1. **Generation latency**: p95 is 44s (Groq free tier + first token delay)
   - Mitigation: Stream tokens as they arrive; validate after completion
   
2. **First Schedule not indexed**: Offence/penalty questions fall back to semantic search
   - Next: Parse offence table from PDF, index separately
   
3. **No alembic migrations**: Schema created by `create_all()` in `scripts/ingest.py`
   - Acceptable for research/demo; production should use alembic

## Deployment

### Render (Backend)
- Container-based deployment (Docker image already built)
- Provision PostgreSQL + Redis on Render
- Set env vars in Render dashboard
- Scale by plan

### Vercel (Frontend)
- Native Next.js support
- One-click GitHub integration
- Env var `NEXT_PUBLIC_API_BASE_URL` points to Render backend

### Self-Hosted
- Run `docker compose up -d` on any Linux box
- Reverse proxy (nginx) in front
- Volume-mount `/data` and `modelcache` to persist state

## Contributing

1. Branch from `main`
2. Make changes (backend tests must pass: `pytest tests/ -q`)
3. Frontend must typecheck: `npm run typecheck`
4. Open PR with description of trade-offs considered
5. Merge after review

## License

[Your license here — MIT, GPL, or internal]

## Contact

Sajjad Chaus — claude7583@outlook.com
