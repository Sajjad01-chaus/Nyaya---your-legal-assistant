"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteDocument,
  documentStatus,
  listDocuments,
  uploadDocument,
} from "@/lib/api";
import type { DocumentOut } from "@/lib/types";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.txt";
const MAX_MB = 25;
const TERMINAL = ["ready", "failed", "error"];

function bytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

export default function DocumentsPage() {
  const [docs, setDocs] = useState<DocumentOut[]>([]);
  const [progress, setProgress] = useState<Record<string, { pct: number; stage: string }>>({});
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setDocs(await listDocuments());
    } catch (e) {
      setErr((e as Error).message);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  // Ingestion is queued to a worker, so anything not yet terminal needs polling.
  useEffect(() => {
    const pending = docs.filter((d) => !TERMINAL.includes(d.status));
    if (pending.length === 0) return;

    const timer = setInterval(async () => {
      let settled = false;
      for (const d of pending) {
        try {
          const s = await documentStatus(d.id);
          setProgress((p) => ({
            ...p,
            [d.id]: { pct: Math.round(s.progress * 100), stage: s.stage_detail },
          }));
          if (TERMINAL.includes(s.status)) settled = true;
        } catch {
          /* keep polling the rest */
        }
      }
      if (settled) void refresh();
    }, 1500);

    return () => clearInterval(timer);
  }, [docs, refresh]);

  async function onFiles(files: FileList | null) {
    if (!files?.length) return;
    setErr(null);
    setBusy(true);
    for (const file of Array.from(files)) {
      if (file.size > MAX_MB * 1024 * 1024) {
        setErr(`${file.name} is ${bytes(file.size)}; the limit is ${MAX_MB} MB.`);
        continue;
      }
      try {
        await uploadDocument(file);
      } catch (e) {
        setErr(`${file.name}: ${(e as Error).message}`);
      }
    }
    setBusy(false);
    if (input.current) input.current.value = "";
    void refresh();
  }

  function handleDrag(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  }

  function handleDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    void onFiles(e.dataTransfer.files);
  }

  async function remove(id: string) {
    try {
      await deleteDocument(id);
      setDocs((d) => d.filter((x) => x.id !== id));
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  return (
    <main>
      <p className="muted small" style={{ marginTop: 0 }}>
        Upload a PDF, image, or text file and it is chunked into your own private
        index. Tick &ldquo;also search my uploaded documents&rdquo; in chat to
        retrieve across them alongside the statute. Max {MAX_MB} MB.
      </p>

      <div
        className="card"
        style={{
          marginBottom: 20,
          borderStyle: dragActive ? "dashed" : "solid",
          borderColor: dragActive ? "var(--accent)" : "var(--border)",
          backgroundColor: dragActive ? "var(--accent-soft)" : "var(--panel)",
          transition: "all 0.2s",
          cursor: "pointer",
        }}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
      >
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          multiple
          disabled={busy}
          onChange={(e) => void onFiles(e.target.files)}
          style={{ cursor: "pointer" }}
          aria-label="Upload documents"
        />
        <div className="small muted" style={{ marginTop: 8 }}>
          {dragActive ? "Drop files here" : "Or drag files here"}
        </div>
        {busy && <div className="small muted" style={{ marginTop: 8 }}>Uploading...</div>}
      </div>

      {err && <div className="err" style={{ marginBottom: 14 }}>{err}</div>}

      {docs.length === 0 ? (
        <div className="empty">No documents yet.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>File</th><th>Status</th><th>Pages</th><th>Chunks</th>
              <th>Size</th><th></th>
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => {
              const p = progress[d.id];
              return (
                <tr key={d.id}>
                  <td>
                    {d.filename}
                    {d.injection_flags.length > 0 && (
                      <div className="small">
                        <span className="badge low">prompt-injection flags</span>{" "}
                        <span className="muted">{d.injection_flags.join(", ")}</span>
                      </div>
                    )}
                    {d.error && <div className="err small">{d.error}</div>}
                  </td>
                  <td style={{ minWidth: 150 }}>
                    <span className="small">{d.status}</span>
                    {!TERMINAL.includes(d.status) && p && (
                      <>
                        <div className="bar" style={{ marginTop: 5 }}>
                          <i style={{ width: `${p.pct}%` }} />
                        </div>
                        <div className="small muted">{p.stage}</div>
                      </>
                    )}
                  </td>
                  <td>{d.page_count || "-"}</td>
                  <td>{d.chunk_count || "-"}</td>
                  <td className="small muted">{bytes(d.size_bytes)}</td>
                  <td>
                    <button style={{ padding: "3px 9px" }} onClick={() => void remove(d.id)}>
                      Delete
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </main>
  );
}
