# Nyaya Architecture

## Overview

Nyaya is a retrieval-augmented generation (RAG) system over statutory text. It answers legal questions about the Bharatiya Nagarik Suraksha Sanhita, 2023 (BNSS) with citations grounded in retrieved sections.

```
User Query
    ↓
[API: /chat endpoint]
    ↓
[Retrieval Service]
├─ Dense embedding (fastembed bge-small)
├─ Sparse BM25 ranking
└─ Qdrant RRF fusion → top-k retrieval
    ↓
[Query Router]
├─ If high confidence: PROCEED
├─ If low confidence: CRAG rewrite + retry
└─ If still low: REFUSE with guidance
    ↓
[LLM Provider (Groq)]
├─ Prompt construction (system + context + query)
├─ Streaming token generation
└─ Usage tracking for cost
    ↓
[Citation Guards (3-layer)]
├─ Guard 1: Citation existence (vs retrieved chunks)
├─ Guard 2: Quote fidelity (vs source text)
└─ Guard 3: Require at least one valid citation (refuse uncited claims)
    ↓
[Validation Event → SSE]
    ↓
User reads answer + sources
```

## Component Details

### 1. Retrieval Stack

#### Vector Store: Qdrant
**Choice**: Qdrant over pgvector + alternatives

**Why Qdrant:**
- **Server-side RRF (Reciprocal Rank Fusion)**: Fuses dense and sparse scores on the server without fetching both lists to the client
- **Minimal operational overhead**: Doesn't require Postgres replication; runs standalone with built-in persistence
- **Hybrid search**: Supports both dense (cosine similarity) and sparse (BM25) queries in one round-trip
- **Flexible payloads**: Can store structured metadata (section numbers, page numbers, file references) alongside vectors

**Trade-offs:**
- Alternative: pgvector + client-side RRF fusion
  - Would need to fetch top-k from each index separately, then merge in application code
  - Adds complexity and network overhead
  - pgvector is correct, just less convenient for hybrid search

#### Dense Embeddings: fastembed + bge-small-en-v1.5
**Choice**: ONNX-based fastembed over sentence-transformers

**Why:**
- **Lightweight model**: bge-small is 33M params (vs BERT-base 110M or larger models)
- **ONNX quantized**: Runs on CPU without PyTorch/torch runtime (no 500MB+ model download)
- **Speed**: CPU inference is fast enough for retrieval (sub-second for typical queries)
- **No GPU required**: Container is ~500MB instead of ~2GB with torch

**Model choice (bge-small over other embedders):**
- bge-small is optimized for retrieval (not just similarity)
- Outperforms larger models on MTEB benchmark for retrieval tasks
- Licensed permissively (Apache 2.0)

**Trade-offs:**
- Alternative: sentence-transformers
  - Would require PyTorch (large dependency, GPU optional)
  - Same quality, larger Docker image
  - Still good, just heavier
  
- Alternative: OpenAI embeddings API
  - Higher cost per query
  - Requires external API (latency, reliability dependency)
  - Not suitable for legal documents (closed-source model, audit trail concerns)

#### Sparse Ranking: BM25
**Why BM25 inside Qdrant:**
- Captures exact keyword matches (e.g., "Section 35" should match sections mentioning "section" + "35")
- Complements dense search (which catches semantic variations)
- No model to download or maintain

**Hybrid Fusion: RRF**
- Qdrant's Reciprocal Rank Fusion combines dense (cosine) and sparse (BM25) scores
- Formula: RRF = 1/(k + rank_dense) + 1/(k + rank_sparse)
- Robust to differences in score scales between two retrieval methods

#### Reranking: ms-marco-MiniLM
**Choice**: Cross-encoder reranker on top-10 retrieval results

**Why:**
- Cross-encoders are more accurate than bi-encoders (dense) for ranking
- ms-marco-MiniLM is small (22M params) and permissively licensed
- Re-ranks top-10 to pick best 5–6 for the prompt

**Trade-off:**
- Adds one more model to load (but lightweight)
- Not used if retrieval is already very confident (optimization: skip reranking if confidence > threshold)

### 2. Query Routing & CRAG

#### Confidence Calibration
Retrieval score is passed through sigmoid to get confidence ∈ [0, 1]:
```
confidence = sigmoid(score)
```

Thresholds (tuned on golden set):
- `HIGH` (confidence > 0.7): Direct LLM generation
- `MEDIUM` (0.4–0.7): CRAG correction (single query rewrite + retry)
- `LOW` (< 0.4): Refuse with guidance

#### CRAG Correction (Correction by Asking)
If confidence is medium:
1. Rewrite query (e.g., "arrest without warrant" → "procedure for arrest without warrant" + alternate phrasings)
2. Re-retrieve with rewritten query
3. If confidence now > threshold: proceed with new results
4. Else: refuse

**Why not just tighten threshold?**
- Rewriting catches queries that are semantically valid but linguistically off
- Example: user asks "how to arrest someone" (low confidence) → rewrite to "arrest procedure" → finds it
- Better than refusing too early

### 3. LLM Provider

#### HTTP-based LLM Provider Abstraction
```python
# backend/app/llm/provider.py
class LLMProvider:
    async def stream(self, system: str, user: str, ...) -> AsyncIterator[Chunk]:
        # Groq API (HTTP) or swap with other provider
```

**Why HTTP?**
- No local LLM required (inference cost is centralized)
- Easy provider swap (Groq → Anthropic → OpenAI → self-hosted)
- Streaming support via HTTP trailers (Groq does this natively)

**Choice: Groq**
- Fastest inference (token/second) on market
- Permissive rate limits for demo/research
- Cost-per-token reasonable
- Doesn't store conversations (important for legal use)

**Temperature & Token Limits**
- Temperature: 0.3 (crisp, factual answers without hallucination)
- Max tokens: 1024 (legal answers are concise; prevent runaway generation)

### 4. Citation Guards (Post-Generation Validation)

Three sequential guards run after LLM generation, before returning to user:

#### Guard 1: Citation Existence
Every bracketed citation `[BNSS s.35]` must correspond to a chunk actually retrieved.
- Regex to find all citations in the answer
- Normalize spaces (handle U+202F no-break space that models often emit)
- Match against (act, section) pairs in retrieved chunks
- Strip citations that aren't found

**Why post-generation?**
- Prompt-based guidance alone is unreliable under pressure
- Post-generation check guarantees no invented citations survive

#### Guard 2: Quote Fidelity
Quoted passages (text in quotation marks) must appear in the source text.
- Exact substring match first
- Near-match (90% similarity) to tolerate ellipsis-style elision
- Examples: "shall be released on bail..." matches because the fragment appears in source

#### Guard 3: Require Citation
If the answer makes statutory claims, at least one valid citation must remain after guards 1 & 2.
- Exception: pure document retrieval (user's own uploads) doesn't require statutory citation
- Rationale: Uncited legal claims are worse than no answer; refuse rather than guess

**Verdict Enum:**
```python
Verdict.OK          # All citations valid, answer has evidence
Verdict.STRIPPED    # Some citations were invented and removed, but answer still valid
Verdict.REFUSED     # Nothing valid survived (unsupported quotes or no citations)
```

**Signal to User:**
- If `Verdict.STRIPPED`: Emit a `validation` event showing what was stripped
- Frontend updates the displayed answer before user reads it
- User sees "x citations removed" badge explaining why

### 5. Session & Document Management

#### Session Isolation
- User session is an httpOnly cookie (created on first request)
- Each session gets a UUID
- All user data (conversations, uploaded documents) scoped by session_id

**Why httpOnly?**
- XSS cannot steal the session cookie
- Frontend must use fetch with `credentials: 'include'` to include it
- Server validates on every request

#### Document Upload Workflow
1. User uploads file → stored on disk (SHA256 as filename)
2. Create Document row (queued status)
3. Enqueue `ingest_document` job to Redis
4. Return immediately (202 Accepted)
5. Worker processes: parse → chunk → embed → store vectors
6. Update Document row with final status (ready, failed, or error)
7. Frontend polls `/documents/{id}/status` for progress

**Why async?**
- 60-page PDF chunking + embedding takes minutes
- Synchronous request would timeout
- User sees progress bar instead of spinner

#### Rate Limiting
- `/chat`: 30 requests/minute per IP (slowapi limiter)
- `/documents/upload`: 10 requests/hour per IP
- Returns 429 Too Many Requests with error message

### 6. Ingestion Pipeline

#### One-Shot Bootstrap
```bash
docker compose --profile bootstrap up
```

Runs `scripts/ingest.py`:
1. **Create schema**: SQLAlchemy `create_all()` (idempotent)
2. **Parse PDF**: PyPDF2 (permissive license) → pages + text
3. **Chunk sections**: Walk statute structure, break on section boundaries
4. **Embed & store**: fastembed → Qdrant
5. **Extract forms**: Parse Second Schedule → save JSON files

**Why not incremental?**
- Statute is static (331 sections, 580 chunks)
- One-time ingest is sufficient
- User uploads are handled asynchronously per file

### 7. Frontend Architecture

#### Tech Stack
- **Next.js 15** App Router (React 19)
- **TypeScript** for type safety
- **CSS variables** for dark mode (light/dark/system themes)

#### Key Design Decisions

**SSE over WebSocket:**
- Chat endpoint streams tokens via Server-Sent Events (POST, not GET)
- Why POST? To send request body (message, conversation ID, etc.)
- Why not WebSocket? Simpler, no heartbeat, fits REST model

**Post-Validation Event:**
- Tokens stream immediately (low latency, good UX for reading)
- After stream ends, validation event contains final answer (may differ from streamed)
- Frontend replaces displayed answer with validated version
- User reads correct, guard-validated answer; streamed version is a preview

**Dark Mode:**
- CSS variables on `:root`
- @media (prefers-color-scheme: dark) for system preference
- data-theme attribute for explicit light/dark override
- localStorage to persist user choice

## Data Flow: Chat Request

```
1. User: "What is Section 35?"
2. Frontend: POST /chat with message, conversation_id, use_documents flag
3. API: Create user message row, commit transaction
4. Retrieval Service:
   a. Embed query (fastembed)
   b. Dense search in Qdrant + BM25 sparse → RRF fusion
   c. Check confidence
      - If high: proceed
      - If medium: CRAG rewrite + retry
      - If low: refuse
   d. Rerank top-10 with ms-marco
5. Prompt Construction: system + context (statute) + user messages + new query
6. LLM: Groq API streaming response
7. Frontend: Display tokens as they arrive
8. Validation:
   a. Normalize spaces in answer
   b. Upgrade bare references (e.g., "Section 35" → "[BNSS s.35]" if in context)
   c. Guard 1: Strip invented citations
   d. Guard 2: Check quoted text
   e. Guard 3: Require at least one valid citation
9. Database: Store assistant message with validation metadata
10. Frontend: Emit validation event, update displayed answer
11. User: Reads answer with citations, clicks citation to jump to source
```

## Deployment Considerations

### Scaling
- **Stateless API**: Can run multiple instances behind a load balancer
- **Session affinity not required**: PostgreSQL holds session data
- **Embedding cache**: Model cache volume should be shared or pre-loaded
- **Redis**: Central job queue for workers (single instance OK, Redis cluster for HA)
- **Qdrant**: Embedded (single instance) or managed cloud instance

### Monitoring
- **Prometheus metrics** on `/api/v1/metrics` (queries, latency, token usage, cost)
- **Structured logs** to stdout (JSON format, parseable by log aggregators)
- **Request tracing** via X-Request-ID header (passed through all services)

### Backup & Recovery
- **Qdrant vectors**: Snapshot volume on schedule (S3 or similar)
- **PostgreSQL**: pg_dump on schedule (or managed service backup)
- **Documents**: Stored on disk (attach persistent volume, or S3)

## Known Trade-offs & Future Work

| Trade-off | Current | Alternative | When to Reconsider |
|-----------|---------|-------------|-------------------|
| No local LLM | Groq (cloud) | llama-cpp, TGI | If inference cost becomes prohibitive or latency < 500ms critical |
| Single Qdrant instance | Embedded mode | Qdrant Cloud | If HA required; Qdrant Cloud is drop-in replacement |
| No query caching | Every query retrieves fresh | Redis cache on query hash | If same questions asked repeatedly; benchmark latency first |
| Sigmoid confidence | Empirical thresholds | Learn from eval set | If calibration drifts with new domains (right now, BNSS-specific) |
| Post-generation guards only | No prompt engineering | Tighter prompts for citations | Prompts are already tight; guards are defense-in-depth |

