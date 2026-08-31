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

## Implementation Status

| Part | Requirement | Status | Notes |
|------|-------------|--------|-------|
| **A1** | Structure-aware ingestion | ✅ Done | 531/531 sections, preserves Act/Chapter/Section/Subsection |
| **A2** | Open-source embeddings | ✅ Done | fastembed + bge-small ONNX (33M params, no PyTorch) |
| **A3** | Hybrid retrieval + reranking | ✅ Done | Qdrant RRF (dense + BM25), ms-marco cross-encoder |
| **A4** | Citation guards | ✅ Done | 3-layer validation (100% accuracy), refusal path |
| **A5** | Two corpora + routing | ✅ Done | Session-scoped documents, hybrid retrieval |
| **B** | Forms extraction | ✅ Done | 58 forms, multi-page detection, OCR fallback |
| **C** | Frontend UX | ✅ Done | SSE streaming, drag-drop, dark mode, accessibility, forms panel |
| **D** | Backend & API | ✅ Done | FastAPI, async workers (arq), rate limiting, health checks |
| **E** | CI/CD | ✅ Done | GitHub Actions (test, build, secret scan, vuln scan) |
| **F** | Evaluation | ✅ Done | 35-question golden set, recall@5 100%, citation 100% |
| **Docs** | README, ARCHITECTURE, DECISIONS | ✅ Done | Complete with API examples, AI usage, rollback strategy |
| **Deployment** | Docker Compose | ✅ Done | docker-compose up works; Render/Vercel templates |
| **Extras** | Dark mode, regenerate, copy buttons | ✅ Done | Full dark/light/system support, all UX polish |

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

## API Examples

### Chat (Streaming SSE)
```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "What is the punishment for rape under section 63?",
    "conversation_id": null,
    "use_documents": false
  }' \
  --no-buffer
```

### Upload Document
```bash
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -F "file=@notice.pdf" \
  -H "Content-Type: multipart/form-data"
```

Returns: `{"document_id": "...", "job_id": "...", "status": "queued", ...}`

### Search Retrieval
```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H "Content-Type: application/json" \
  -d '{
    "q": "What is section 35?",
    "top_k": 5,
    "include_documents": false
  }'
```

### List Forms
```bash
curl http://localhost:8000/api/v1/forms
```

### Download Single Form
```bash
curl -O http://localhost:8000/api/v1/forms/1/download
```

### Download All Forms (Zip)
```bash
curl -O http://localhost:8000/api/v1/forms/download-all
```

## Deployment & Rollback

### Render (Backend)
1. Fork this repo and connect to Render
2. Create a new Web Service, select Docker
3. Set env vars (GROQ_API_KEY, POSTGRES_*, REDIS_*)
4. Deploy (takes ~5 min first time)
5. **To rollback**: Deployments tab → previous version → Redeploy

### Vercel (Frontend)
1. Import this repo to Vercel
2. Set `NEXT_PUBLIC_API_BASE_URL` to your Render backend
3. Deploy (instant)
4. **To rollback**: Deployments → previous → Rollback to this

### Self-Hosted
```bash
git clone <repo>
cd DhronAI
cp .env.example .env
# Edit .env with your Groq key
docker compose up -d
docker compose --profile bootstrap up  # One-shot: ingest + extract
```

See `.github/workflows/deploy.yml` for automated rollback procedures.

## AI Usage Disclosure

### Where AI was used
- **React components** (chat, documents, search, forms pages): AI-generated with manual refinement
- **CSS & styling** (globals.css, dark mode toggle): AI-generated
- **Docker setup** (Dockerfile, docker-compose.yml): AI-generated with verification
- **API routes & validation**: Partially AI-generated, fully reviewed and tested
- **TypeScript types & models**: AI-generated from schema
- **Backend core logic**:
  - Structure-aware chunker (ingestion/layout.py): Hand-written; AI couldn't preserve subsection boundaries correctly
  - Citation guards (llm/guards.py): Hand-written with AI assistance on edge cases
  - Hybrid retrieval (retrieval/query.py): Hand-written; AI struggled with RRF fusion logic
  - LLM provider interface: Partially AI-generated

### AI tools used
- **Claude Code** (60%): Chat pages, components, styling, Docker setup, initial API scaffolds
- **Manual coding** (40%): Chunking logic, guards, retrieval fusion, complex validation

### Sample prompts
1. "Build a React chat component with SSE streaming, multi-turn history, and a source drawer that shows retrieved passages"
   - Output: Good React structure; I added proper error handling and accessibility labels
2. "Create a Next.js dark mode toggle that persists to localStorage"
   - Output: Correct implementation; no changes needed
3. "Write a structure-aware PDF chunker that respects section boundaries and keeps provisos attached"
   - Output: Too naive (didn't detect subsection boundaries); I rewrote the core logic by hand
4. "Implement RRF fusion for dense + sparse retrieval scores"
   - Output: Correct formula, but didn't handle edge cases (empty results, score normalization); I hardened it
5. "Generate FastAPI endpoints for chat, upload, search, forms"
   - Output: Good scaffolds; I wired auth, async jobs, citation validation, rate limiting

### Where manual coding was needed
1. **Structure-aware chunking**: Every LLM tried naive recursive splitting. The assignment requires preserving Act/Chapter/Section/Subsection hierarchy. I wrote the state machine by hand.
2. **Multi-page form detection**: AI produced single-page loops. I added the logic to detect page ranges and stitch multi-page forms.
3. **Citation guard edge cases**: Model invented citations; I added three-layer validation, typographic space normalization, and quote fidelity checks.
4. **Hybrid retrieval**: Dense + BM25 fusion requires careful score normalization. AI's first attempt didn't handle all edge cases; I debugged and hardened the RRF logic.
5. **Session scoping**: Uploaded documents must never leak between users. I implemented HMAC-SHA256 signing and explicit session checks on every retrieval.
6. **Rate limiting**: Wired slowapi decorators to endpoints with per-endpoint limits.

### What AI got right
- React/Next.js component structure and data flow
- CSS dark mode implementation (already existed; I just added the toggle)
- Docker multi-stage builds and docker-compose networking
- TypeScript type definitions
- Async/await patterns for FastAPI
- SSE stream parsing on the frontend
- Evaluation harness logic

### What AI struggled with
- Understanding PDF structure (needed hand-written parser)
- Edge cases in validation logic (needed human judgment)
- Performance optimization (needed benchmarking)
- Understanding statutory text nuances (legal domain knowledge)

**Conclusion**: AI is excellent for scaffolding and iterating. The parts that required domain knowledge (legal structure, citation validation, retrieval fusion) were either hand-written or heavily revised. This is a ~60% AI + 40% manual split, with the manual 40% being the critical path.

## Contact

Sajjad Chaus — claude7583@outlook.com
