"use client";

import { useState } from "react";
import type { DocumentSource, Source } from "@/lib/types";

export default function Sources({
  sources,
  documentSources,
  highlighted,
}: {
  sources: Source[];
  documentSources: DocumentSource[];
  highlighted?: string | null;
}) {
  const [copiedId, setCopiedId] = useState<string | null>(null);

  if (sources.length === 0 && documentSources.length === 0) return null;

  function copyCitation(id: string, text: string) {
    void navigator.clipboard.writeText(text).then(() => {
      setCopiedId(id);
      setTimeout(() => setCopiedId(null), 2000);
    });
  }

  return (
    <div className="sources">
      <div className="small muted" style={{ marginBottom: 4 }}>
        Retrieved passages ({sources.length + documentSources.length})
      </div>

      {sources.map((s) => (
        <div
          key={s.chunk_id}
          id={`src-${s.chunk_id}`}
          className="source"
          data-hit={s.chunk_id === highlighted}
        >
          <div className="head">
            <span className="mono">{s.citation}</span> {s.section_title}
            {s.page_start != null && (
              <span className="muted small"> - p.{s.page_start}</span>
            )}
            <button
              onClick={() => copyCitation(s.chunk_id, s.citation)}
              style={{ padding: "2px 6px", fontSize: "11px", marginLeft: 8 }}
              title="Copy citation"
              aria-label={`Copy citation ${s.citation}`}
            >
              {copiedId === s.chunk_id ? "✓" : "📋"}
            </button>
          </div>
          <div className="text">{s.text.slice(0, 320)}
            {s.text.length > 320 ? "..." : ""}
          </div>
        </div>
      ))}

      {documentSources.map((d) => (
        <div key={d.chunk_id} id={`src-${d.chunk_id}`} className="source">
          <div className="head">
            <span className="mono">[{d.filename}]</span>
            {d.page_start != null && (
              <span className="muted small"> p.{d.page_start}</span>
            )}
            <button
              onClick={() => copyCitation(d.chunk_id, `[${d.filename}]`)}
              style={{ padding: "2px 6px", fontSize: "11px", marginLeft: 8 }}
              title="Copy source reference"
              aria-label={`Copy source reference for ${d.filename}`}
            >
              {copiedId === d.chunk_id ? "✓" : "📋"}
            </button>
          </div>
          <div className="text">{d.text.slice(0, 320)}
            {d.text.length > 320 ? "..." : ""}
          </div>
        </div>
      ))}
    </div>
  );
}
