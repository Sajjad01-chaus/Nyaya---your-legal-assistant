# Technology Decisions

## Backend Framework: FastAPI

**Decision**: Use FastAPI for the backend API.

**Alternatives Considered**:
1. **Django** (runner-up)
   - Pro: Batteries included (ORM, auth, admin, forms)
   - Con: Heavyweight for JSON API; startup boilerplate; synchronous by default
   - Verdict: Overkill for a stateless service

2. **Flask** (lightweight alternative)
   - Pro: Minimal, can add pieces as needed
   - Con: No type hints; async support is an afterthought
   - Verdict: Too bare-bones; loses FastAPI's auto-docs and validation

**Why FastAPI**:
- **Type hints → automatic OpenAPI schema** (docs at /docs auto-generated)
- **Native async/await** (trivial to spawn async tasks, stream SSE)
- **Pydantic validation** (request/response models are self-documenting)
- **Dependency injection** (clean, testable)
- **Speed** (both startup and request handling)
- **Minimal boilerplate** (routing is just decorators + type hints)

**Trade-off**: Smaller ecosystem than Django, but for a focused API this is acceptable.

---

## Frontend Framework: Next.js 15

**Decision**: Use Next.js 15 with App Router for the frontend.

**Alternatives Considered**:
1. **React + Vite** (bare React with build tool)
   - Pro: Lightweight, full control
   - Con: Must wire up routing, SSR, asset optimization, env vars
   - Verdict: Reinvents too much; Next.js is opinionated but proven

2. **Vue 3** (similar tier to React)
   - Pro: Easier learning curve, good docs
   - Con: Smaller ecosystem, fewer examples of RAG UX
   - Verdict: React is default for AI apps

3. **SvelteKit** (modern challenger)
   - Pro: Elegant, less boilerplate than React
   - Con: Smaller community, fewer AI/SSE examples
   - Verdict: Solid, but React is safer for hiring/maintenance

**Why Next.js 15**:
- **App Router** (file-based routing, server components for data fetching)
- **API routes** (backend on same deployment; not used here, but available)
- **Built-in optimizations** (image, font, bundle splitting)
- **Vercel ecosystem** (trivial deployment)
- **TypeScript first-class** (strict mode by default)
- **SSE streaming** (easy to implement client-side stream reader)

**Trade-off**: Opinionated by design; less control than bare React, but safer defaults.

---

## Vector Store: Qdrant

**Decision**: Use Qdrant for vector storage and retrieval.

**Alternatives Considered**:
1. **pgvector (PostgreSQL extension)**
   - Pro: Single database (vectors + relational data together)
   - Con: No server-side RRF fusion (must fetch both dense & sparse results to app, then merge)
   - Verdict: Good option, but RRF complexity pushed us away

2. **Pinecone (managed service)**
   - Pro: Hosted, no ops, auto-scaling
   - Con: Closed-source model, cost per vector, vendor lock-in
   - Verdict: For research, self-hosted better

3. **Milvus (open-source, more complex)**
   - Pro: Similar to Qdrant, distributed
   - Con: Steeper ops learning curve, more components
   - Verdict: Qdrant is simpler, sufficient for single-instance use

4. **Weaviate (GraphQL-first)**
   - Pro: Flexible schema, multi-tenant
   - Con: Heavier, GraphQL over REST adds complexity
   - Verdict: Overkill for BNSS-only corpus

**Why Qdrant**:
- **Server-side RRF** (fuses dense + sparse scores on the server)
- **Minimal ops** (single binary, built-in persistence)
- **Hybrid search native** (dense + sparse in one round-trip)
- **Permissive license** (AGPL with commercial exception)
- **REST API** (no GraphQL learning curve)
- **Good documentation** (community examples for similar use cases)

**Trade-off**: Hybrid search is convenient but not essential; pgvector + client-side fusion would work, just more code.

---

## Embeddings: fastembed + bge-small

**Decision**: Use ONNX-based fastembed library with bge-small-en-v1.5 model.

**Alternatives Considered**:
1. **sentence-transformers**
   - Pro: Batteries included, many models
   - Con: Requires PyTorch (→ large Docker image, 500MB+ torch runtime)
   - Verdict: Same quality, worse ops profile

2. **OpenAI Embeddings API**
   - Pro: State-of-the-art quality, no model download
   - Con: Cost per query, external dependency, latency, closed-source
   - Verdict: Not acceptable for legal documents (audit trail concerns)

3. **Local Ollama (llama2, other models)**
   - Pro: Runs locally, no external API
   - Con: Much slower than bge-small, larger models = longer inference
   - Verdict: Trade-off not worth it; bge-small is fast + quality

**Why fastembed + bge-small**:
- **No PyTorch dependency** (ONNX runtime is lightweight)
- **Small model size** (bge-small is 33M params, fast on CPU)
- **Permissive license** (Apache 2.0)
- **Quality** (bge-small outperforms larger models on MTEB retrieval benchmark)
- **Docker size** (~500MB vs 2GB+ with torch)

**Trade-off**: Slightly lower quality than state-of-the-art embeddings, but acceptable for statutory text (highly structured domain).

---

## Rate Limiting: slowapi

**Decision**: Use slowapi for rate limiting.

**Alternatives Considered**:
1. **nginx rate limiting** (reverse proxy)
   - Pro: Transparent, very fast
   - Con: Not in Python codebase; requires separate nginx config
   - Verdict: Good for production, but doesn't work in dev/Docker easily

2. **Redis-based rate limiting (custom)**
   - Pro: Distributed, per-session
   - Con: Must implement correctly (edge cases); overkill for demo
   - Verdict: Good for scaling; not needed yet

3. **No rate limiting**
   - Pro: Simpler
   - Con: Open to abuse (token cost, resource exhaustion)
   - Verdict: Unacceptable for public demo

**Why slowapi**:
- **Minimal code** (just `@limiter.limit()` decorator)
- **Built on werkzeug** (battle-tested)
- **Permissive license** (MIT)
- **Familiar pattern** (similar to Flask-Limiter)

**Trade-off**: Single-instance only (no distributed state across load balancers); acceptable for current scale. If clustering needed, add Redis backend (slowapi supports it).

---

## Async Jobs: arq

**Decision**: Use arq for async document ingestion jobs.

**Alternatives Considered**:
1. **Celery**
   - Pro: Feature-complete, widely used
   - Con: Heavy (20+ dependencies), overkill for simple job queue
   - Verdict: Gold-plated for our needs

2. **RQ (Redis Queue)**
   - Pro: Lightweight, Redis-backed
   - Con: Less type-safe, fewer features
   - Verdict: Solid; arq is basically improved RQ

3. **Background tasks in FastAPI** (BackgroundTasks)
   - Pro: No external queue needed
   - Con: Jobs lost if process dies; can't scale workers separately
   - Verdict: OK for fire-and-forget, not suitable for ingestion (need retries, status tracking)

**Why arq**:
- **Lightweight** (single library, minimal deps)
- **Redis-backed** (no separate message broker)
- **Type hints** (Python 3.7+)
- **Async-native** (same async/await as FastAPI)
- **Status tracking** (jobs have IDs, can query status)
- **Permissive license** (MIT)

**Trade-off**: Fewer built-in features than Celery, but we don't need them. arq scales horizontally (just spawn more workers).

---

## LLM Provider: Groq

**Decision**: Use Groq's API for LLM inference.

**Alternatives Considered**:
1. **OpenAI (GPT-4)**
   - Pro: State-of-the-art quality
   - Con: Highest cost/token, requires API key management, external dependency
   - Verdict: Too expensive for public demo; less suitable for legal domain (closed model)

2. **Anthropic (Claude)**
   - Pro: Strong legal reasoning, flexible token window
   - Con: More expensive than Groq, also external dependency
   - Verdict: Good choice; we picked Groq for cost/speed trade-off

3. **Self-hosted LLM** (llama, mistral, etc.)
   - Pro: No external dependency, no per-token cost
   - Con: Slower inference (CPU/GPU scaling complexity), lower quality than proprietary
   - Verdict: Viable for production with GPU; not suitable for quick iteration

4. **Cohere**
   - Pro: Good for classification/generation, legal-aware models
   - Con: Smaller ecosystem, less proven for streaming
   - Verdict: Solid alternative; Groq won by speed + cost

**Why Groq**:
- **Fastest token generation** (metric: tokens/second)
- **Cheapest per-token** (good for high-volume demo)
- **Streaming support** (via HTTP trailers)
- **Permissive rate limits** (generous free tier)
- **No data retention** (conversations not logged; important for legal data)
- **Good API ergonomics** (similar to OpenAI)

**Trade-off**: External dependency (latency, reliability risk), but trade-off is worth avoiding GPU infrastructure for research/demo.

---

## Database: PostgreSQL

**Decision**: Use PostgreSQL for relational state.

**Alternatives Considered**:
1. **SQLite** (embedded)
   - Pro: Zero ops, single file
   - Con: Not suitable for multi-process/distributed (locking issues)
   - Verdict: Fine for local dev, not suitable for containerized app

2. **MongoDB**
   - Pro: Schemaless, great for semi-structured data
   - Con: Not ACID-compliant; heavier resource usage
   - Verdict: Legal data benefits from structure; PostgreSQL better

3. **No database** (all state in memory/Redis)
   - Pro: Speed
   - Con: Data loss on restart; no durable state
   - Verdict: Unacceptable for conversations

**Why PostgreSQL**:
- **ACID compliance** (transactions matter for conversations)
- **Full-text search** (if we add keyword-based search; not used now)
- **JSON support** (metadata like citation positions)
- **Permissive license** (free, open-source)
- **Mature ecosystem** (stable, well-tested)

**Trade-off**: Requires provisioning (docker-compose handles this), but worthwhile for data durability.

---

## Summary Table

| Component | Chosen | Rationale |
|-----------|--------|-----------|
| Backend API | FastAPI | Type-safe, async, auto-docs, minimal boilerplate |
| Frontend | Next.js 15 | App Router, TypeScript, proven for AI UX |
| Vector Store | Qdrant | Server-side RRF, minimal ops, hybrid search |
| Embeddings | fastembed + bge-small | Lightweight, quality, no PyTorch dep |
| Rate Limiting | slowapi | Decorator-based, permissive license |
| Async Jobs | arq | Lightweight, Redis-backed, type-safe |
| LLM | Groq | Fastest, cheapest, streaming, no retention |
| Relational DB | PostgreSQL | ACID, schemaless via JSON, mature |

## Future Decisions

If these change in future:
1. **Embeddings**: Switch to OpenAI if quality critical and cost acceptable
2. **Vector Store**: Migrate to Qdrant Cloud if ops burden increases
3. **LLM**: Anthropic Claude if legal reasoning quality becomes bottleneck
4. **Rate Limiting**: Add Redis backend if clustering needed
5. **Async Jobs**: Migrate to Celery only if job complexity explodes (job dependencies, priority queues, etc.)

---

**Last Updated**: 2026-08-31  
**Reviewed By**: Nyaya Development Team
