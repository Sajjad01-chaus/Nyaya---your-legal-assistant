"use client";

import { useState } from "react";
import Confidence from "@/components/Confidence";
import { search } from "@/lib/api";
import type { Passage, SearchResponse } from "@/lib/types";

function PassageRow({ p }: { p: Passage }) {
  return (
    <div className="source" style={{ margin: "14px 0" }}>
      <div className="head">
        <span className="mono">{p.citation}</span> {p.section_title}
        {p.subsection && <span className="muted small"> ({p.subsection})</span>}
        {p.page_start != null && <span className="muted small"> - p.{p.page_start}</span>}
        <span className="muted small"> - {p.score.toFixed(3)}</span>
      </div>
      <div className="text">{p.text}</div>
    </div>
  );
}

export default function SearchPage() {
  const [q, setQ] = useState("");
  const [topK, setTopK] = useState(6);
  const [includeDocs, setIncludeDocs] = useState(false);
  const [allowRewrite, setAllowRewrite] = useState(true);
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    if (!q.trim() || busy) return;
    setBusy(true);
    setErr(null);
    try {
      setRes(await search({
        q: q.trim(),
        top_k: topK,
        include_documents: includeDocs,
        allow_rewrite: allowRewrite,
      }));
    } catch (e) {
      setErr((e as Error).message);
      setRes(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <p className="muted small" style={{ marginTop: 0 }}>
        Retrieval without generation - the passages exactly as the reranker
        ordered them. Useful for judging whether a weak answer is the model&apos;s
        fault or retrieval&apos;s.
      </p>

      <div className="row" style={{ marginBottom: 12 }}>
        <input
          type="search"
          value={q}
          placeholder="Search the BNSS..."
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void run()}
        />
        <button className="primary" onClick={() => void run()} disabled={busy || !q.trim()}>
          {busy ? "..." : "Search"}
        </button>
      </div>

      <div className="row small muted" style={{ gap: 16, marginBottom: 20, flexWrap: "wrap" }}>
        <label className="row" style={{ gap: 6 }}>
          top_k
          <input
            type="text"
            inputMode="numeric"
            value={topK}
            onChange={(e) => setTopK(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
            style={{ width: 58, padding: "4px 8px" }}
          />
        </label>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={includeDocs} style={{ width: "auto" }}
            onChange={(e) => setIncludeDocs(e.target.checked)} />
          include my documents
        </label>
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={allowRewrite} style={{ width: "auto" }}
            onChange={(e) => setAllowRewrite(e.target.checked)} />
          allow CRAG rewrite
        </label>
      </div>

      {err && <div className="err">{err}</div>}

      {res && (
        <>
          <div className="card" style={{ marginBottom: 18 }}>
            <div className="row small" style={{ gap: 10, flexWrap: "wrap" }}>
              <Confidence level={res.confidence} score={res.score} />
              <span className="muted">route: {res.route}</span>
              <span className="muted">intent: {res.intent}</span>
              {res.reranked && <span className="muted">reranked</span>}
              {Object.entries(res.timings_ms).map(([k, v]) => (
                <span key={k} className="muted mono">{k} {Math.round(v)}ms</span>
              ))}
            </div>
            {res.rewritten_query && (
              <div className="small muted" style={{ marginTop: 8 }}>
                Rewritten to: &ldquo;{res.rewritten_query}&rdquo;
              </div>
            )}
            {res.disambiguation && (
              <div className="small" style={{ marginTop: 8 }}>{res.disambiguation}</div>
            )}
          </div>

          {res.results.length === 0 && <div className="empty">Nothing matched.</div>}
          {res.results.map((p) => <PassageRow key={p.chunk_id} p={p} />)}

          {res.document_results.length > 0 && (
            <>
              <h3 style={{ marginTop: 28, fontSize: 15 }}>From your documents</h3>
              {res.document_results.map((p) => <PassageRow key={p.chunk_id} p={p} />)}
            </>
          )}
        </>
      )}
    </main>
  );
}
