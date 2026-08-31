"use client";

import { useCallback, useEffect, useState } from "react";
import {
  formDownloadUrl,
  formPreviewUrl,
  formsDownloadAllUrl,
  listForms,
  searchForms,
} from "@/lib/api";
import type { FormListOut, FormOut } from "@/lib/types";

function kb(n: number): string {
  return n < 1024 * 1024 ? `${(n / 1024).toFixed(0)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

function FormCard({ f, onOpen }: { f: FormOut; onOpen: (f: FormOut) => void }) {
  return (
    <div className="card">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div className="mono muted">Form {f.form_number}</div>
        {f.needs_review && <span className="badge medium">needs review</span>}
      </div>

      <div style={{ fontWeight: 550, margin: "6px 0 8px", fontSize: 14 }}>{f.title}</div>

      <div className="small muted">
        pp. {f.page_start}-{f.page_end} ({f.page_count}) - {kb(f.size_bytes)}
      </div>

      {f.see_sections.length > 0 && (
        <div className="small muted" style={{ marginTop: 4 }}>
          see s. {f.see_sections.join(", ")}
        </div>
      )}

      {f.needs_review && f.review_reasons.length > 0 && (
        <div className="small muted" style={{ marginTop: 4 }}>
          {f.review_reasons.join("; ")}
        </div>
      )}

      <div className="row" style={{ gap: 8, marginTop: 12 }}>
        <button style={{ padding: "4px 10px" }} onClick={() => onOpen(f)}>
          Preview
        </button>
        <a className="btn" style={{ padding: "4px 10px" }}
           href={formDownloadUrl(f.form_number)} target="_blank" rel="noreferrer">
          Download
        </a>
      </div>
    </div>
  );
}

export default function FormsPage() {
  const [data, setData] = useState<FormListOut | null>(null);
  const [q, setQ] = useState("");
  const [onlyReview, setOnlyReview] = useState(false);
  const [open, setOpen] = useState<FormOut | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async (query: string) => {
    setErr(null);
    try {
      setData(query.trim() ? await searchForms(query.trim()) : await listForms());
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => { void load(""); }, [load]);

  const forms = (data?.forms ?? []).filter((f) => !onlyReview || f.needs_review);

  return (
    <main>
      <p className="muted small" style={{ marginTop: 0 }}>
        The 58 statutory forms of the Second Schedule, split out of the bare act
        as individual PDFs. Confidence and review flags come from the extractor.
      </p>

      <div className="row" style={{ marginBottom: 12 }}>
        <input
          type="search"
          value={q}
          placeholder="Search forms by title or section..."
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && void load(q)}
        />
        <button onClick={() => void load(q)}>Search</button>
        {q && <button onClick={() => { setQ(""); void load(""); }}>Clear</button>}
      </div>

      <div className="row small muted" style={{ gap: 16, marginBottom: 20, flexWrap: "wrap" }}>
        {data && <span>{data.total} forms, {data.needs_review} flagged for review</span>}
        <label className="row" style={{ gap: 6 }}>
          <input type="checkbox" checked={onlyReview} style={{ width: "auto" }}
            onChange={(e) => setOnlyReview(e.target.checked)} />
          only those needing review
        </label>
        <a className="btn small" style={{ padding: "3px 10px" }}
           href={formsDownloadAllUrl()} target="_blank" rel="noreferrer">
          Download all (zip)
        </a>
      </div>

      {err && (
        <div className="err" style={{ padding: 12, marginBottom: 14, borderRadius: 8, border: "1px solid var(--low)" }}>
          <strong>Error loading forms:</strong> {err}
        </div>
      )}

      {!data ? (
        <div className="empty" style={{ animation: "pulse 2s infinite" }}>
          <div style={{ marginBottom: 12 }}>Loading forms...</div>
          <div style={{ fontSize: 12 }} className="muted">This may take a moment on first load.</div>
        </div>
      ) : forms.length === 0 ? (
        <div className="empty">No forms matched your search.</div>
      ) : (
        <div className="grid">
          {forms.map((f) => <FormCard key={f.form_number} f={f} onOpen={setOpen} />)}
        </div>
      )}

      {open && (
        <div
          onClick={() => setOpen(null)}
          style={{
            position: "fixed", inset: 0, background: "rgba(0,0,0,0.55)",
            display: "flex", alignItems: "center", justifyContent: "center",
            padding: 24, zIndex: 50,
          }}
        >
          <div
            className="card"
            onClick={(e) => e.stopPropagation()}
            style={{ width: "min(900px, 100%)", height: "min(85vh, 100%)", display: "flex", flexDirection: "column" }}
          >
            <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
              <strong style={{ fontSize: 14 }}>
                Form {open.form_number} - {open.title}
              </strong>
              <button style={{ padding: "3px 10px" }} onClick={() => setOpen(null)}>Close</button>
            </div>
            <iframe
              src={formPreviewUrl(open.form_number)}
              title={`Form ${open.form_number}`}
              style={{ flex: 1, width: "100%", border: "1px solid var(--border)", borderRadius: 8 }}
            />
          </div>
        </div>
      )}
    </main>
  );
}
