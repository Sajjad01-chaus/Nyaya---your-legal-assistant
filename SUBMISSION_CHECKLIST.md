# Submission Checklist

## Pre-Submission (Today)

### 1. Local Testing ✅
- [ ] Run `docker compose up -d` and verify all services start
- [ ] Run `docker compose --profile bootstrap up` and verify ingestion completes
- [ ] Test chat: "What is the punishment for rape?"
- [ ] Test upload: Documents → drag-drop a PDF
- [ ] Test forms: Browse forms, download one
- [ ] Test search: Try "section 35"
- [ ] Test refusal: "What is the capital of France?"
- [ ] Test dark mode: Click theme toggle
- [ ] Test error: Upload a 100MB file (should show error)
- [ ] Run `bash scripts/e2e-test.sh` to verify all endpoints

### 2. Documentation Check ✅
- [ ] README.md:
  - [ ] Implementation status table (all parts A-F marked Done)
  - [ ] Quick start (docker-compose up works)
  - [ ] Environment variables table
  - [ ] API examples (curl)
  - [ ] Ollama instructions
  - [ ] Deployment procedures
  - [ ] Rollback strategy
  - [ ] Evaluation results table
  - [ ] AI usage disclosure (where AI used, which tools, sample prompts, manual coding)
- [ ] ARCHITECTURE.md (design decisions, data flow, components)
- [ ] DECISIONS.md (framework trade-offs)
- [ ] Commit history: 3+ meaningful commits (incremental, not one giant commit)

### 3. Git & Secrets Check ✅
- [ ] `git log --oneline` shows good history
- [ ] `.env` is NOT in git (only `.env.example`)
- [ ] No API keys, passwords, or credentials in code
- [ ] Run `gitleaks detect` locally (or trust CI)
- [ ] Repository is PUBLIC (test: can you open it in incognito?)

### 4. Code Quality Check ✅
- [ ] Backend: `cd backend && python -m pytest tests/ -q` (102 tests passing)
- [ ] Frontend: `cd frontend && npm run typecheck` (0 errors)
- [ ] No console errors or warnings (check browser dev tools)
- [ ] No unhandled promise rejections
- [ ] No obvious UX issues (buttons work, text readable, forms responsive)

---

## Submission Artifacts

### Required
1. **GitHub Repository Link** (public, with incremental commits)
   - URL: `https://github.com/YOUR_USERNAME/DhronAI`
   - Status: ✅ Ready

2. **Loom/Screen Recording (5-8 min)**
   - [ ] `docker compose up -d` from clean state
   - [ ] `docker compose --profile bootstrap up` (ingest)
   - [ ] Frontend loads, example questions visible
   - [ ] Ask a question: "What is the punishment for rape?"
     - [ ] Show streaming tokens appearing
     - [ ] Show citations rendered as chips
     - [ ] Click citation → source drawer opens
   - [ ] Upload a document (Documents tab, drag-drop)
   - [ ] Ask about uploaded document
   - [ ] Test refusal: ask something out-of-scope
   - [ ] Show Forms panel, download a form
   - [ ] Dark mode toggle
   - [ ] Show CI pipeline (GitHub Actions > recent runs)
   - [ ] Show eval results (README or terminal: `python eval/run_eval.py`)
   - Status: 🔴 Not recorded yet

3. **Live URL (Optional for Non-DevOps)**
   - [ ] Backend on Render/Railway: `https://nyaya-api.render.com`
   - [ ] Frontend on Vercel: `https://nyaya.vercel.app`
   - Status: 🟡 Not deployed (templates provided; optional for non-DevOps)

---

## Submission Email

**To**: hr@dhronai.com, alwynantonyben@dhronai.com, umang@dhronai.com

**Subject**: Assignment Submission — [Your Name] — [Full Stack / Backend / AI Engineer]

**Body**:
```
Hello,

Please find my submission for the Nyaya assignment:

GitHub Repository: https://github.com/YOUR_USERNAME/DhronAI
Loom Recording: https://loom.com/share/YOUR_RECORDING_ID
Live URL (if deployed): https://nyaya.vercel.app

The submission includes:
- Complete retrieval system (ingestion, embeddings, hybrid search, citation guards)
- Forms extraction pipeline (58 forms, multi-page handling)
- Frontend with dark mode, drag-drop, streaming, citations
- Backend with async workers, rate limiting, health checks
- CI/CD with GitHub Actions (lint, test, secret scan, vuln scan)
- Comprehensive evaluation (35 questions, 100% citation accuracy)
- Full documentation with API examples and AI usage disclosure

All core requirements (A-F) are complete. Ready for evaluation.

Best regards,
[Your Name]
```

---

## What Each Evaluator Will Check

### Retrieval (30% weight)
- ✅ Does structure-aware ingestion preserve Act/Chapter/Section/Subsection? YES
- ✅ Does hybrid (dense + BM25) retrieval work? YES
- ✅ Are citations validated (3-guard checks)? YES
- ✅ Does refusal path fire on low confidence? YES
- ✅ Do session-scoped documents work? YES

### Forms (20% weight)
- ✅ Are all 58 forms extracted as PDFs? YES
- ✅ Are titles scraped (not hardcoded)? YES
- ✅ Are multi-page forms detected? YES
- ✅ Is manifest.json correct? YES
- ✅ Are filenames deterministic? YES

### Frontend (20% weight)
- ✅ Does chat stream tokens? YES (SSE wired)
- ✅ Can you upload documents? YES (drag-drop)
- ✅ Do citations jump to sources? YES
- ✅ Is it responsive? YES (mobile-tested)
- ✅ Is it accessible (ARIA, keyboard)? YES
- ✅ Dark mode works? YES
- ✅ Error messages helpful? YES

### Backend (15% weight)
- ✅ Are endpoints implemented (chat, upload, search, forms)? YES
- ✅ Is ingestion async (workers)? YES
- ✅ Is rate limiting working? YES (30/min chat, 10/hr upload)
- ✅ Docker compose brings up full system? YES
- ✅ Are dependencies pinned? YES

### CI/CD (15% weight)
- ✅ Do tests run on every PR? YES
- ✅ Does Docker build on merge to main? YES
- ✅ Is there secret scanning? YES (gitleaks)
- ✅ Is there vuln scanning? YES (Trivy)
- ✅ Rollback strategy documented? YES

### Evaluation (10% weight)
- ✅ Golden set of 25-30 questions? YES (35 questions)
- ✅ Recall@5 and MRR metrics? YES (100%, 0.912)
- ✅ Citation accuracy tracked? YES (100%)
- ✅ Refusal rate on out-of-scope? YES (100%)
- ✅ End-to-end latency measured? YES (p95 44s)

### Documentation (10% weight)
- ✅ README with status table? YES
- ✅ Architecture diagram? YES
- ✅ Decision trade-offs? YES
- ✅ API examples (curl)? YES
- ✅ AI usage disclosure? YES
- ✅ What's incomplete (honest gaps)? YES
- ✅ Incremental commit history? YES

---

## If You Deploy to Cloud

### Render (Backend)
1. Fork repo to your GitHub
2. Go to https://render.com
3. Click "New" → "Web Service"
4. Connect repo → select DhronAI
5. Settings:
   - Name: `nyaya-api`
   - Environment: Docker
   - Region: (pick closest to you)
   - Instance Type: Free tier (0.5 CPU, 512MB RAM)
6. Environment variables:
   - `GROQ_API_KEY`: Your key
   - `POSTGRES_HOST`: Leave blank (Render provides)
   - `REDIS_HOST`: Leave blank
   - All others from `.env.example`
7. Deploy (~5 min)
8. Copy URL: `https://nyaya-api-abc.onrender.com`

### Vercel (Frontend)
1. Go to https://vercel.com
2. Import project → select DhronAI repo
3. Framework: Next.js (auto-detected)
4. Environment: `NEXT_PUBLIC_API_BASE_URL=https://nyaya-api-abc.onrender.com`
5. Deploy (~2 min)
6. Copy URL: `https://nyaya.vercel.app`

### Test Cloud Deployment
```bash
# From cloud URLs
curl https://nyaya-api-abc.onrender.com/api/v1/health
open https://nyaya.vercel.app
```

---

## Final Sanity Checks Before Submitting

```bash
# Terminal, in repo root:
git log --oneline -5           # Check commit history
git status                     # Nothing uncommitted
cat .gitignore | grep env      # .env should be ignored

cd backend && python -m pytest tests/ -q  # Should see "102 passed"
cd ../frontend && npm run typecheck       # Should see "0 errors"

docker compose up -d
sleep 10  # Wait for services
curl http://localhost:8000/api/v1/health
curl http://localhost:3000 | head -c 200  # Should see HTML

# Kill everything
docker compose down
```

---

## You're Ready When

✅ All tests pass locally
✅ Docker compose up works from clean clone
✅ README is complete with API examples + AI disclosure
✅ Repository is public
✅ Commit history is clean (3+ incremental commits)
✅ No secrets in git
✅ Loom recording shows all critical paths

**Then email the links to the three addresses above.**

Good luck! 🚀
