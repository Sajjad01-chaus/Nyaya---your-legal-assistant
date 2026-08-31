"use client";

import { useState } from "react";
import type { Source } from "@/lib/types";

// Citations come back rendered as "[BNSS s.35]" or "[BNSS s.35(2)]". Turning
// them into controls is the whole point of a citation-grounded answer: the
// reader gets to check the claim against the statute in one click.
const CITE = /\[([A-Za-z]+)\s+s\.([0-9]+[A-Za-z]*(?:\([^)]*\))*)\]/g;

export default function AnswerText({
  text,
  sources,
  onPick,
}: {
  text: string;
  sources: Source[];
  onPick?: (chunkId: string) => void;
}) {
  const [copied, setCopied] = useState(false);

  const parts: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;

  CITE.lastIndex = 0;
  while ((m = CITE.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));

    const [full, act, section] = m;
    // Sub-clauses cite the parent section, so match on the numeric stem.
    const stem = section.split("(")[0];
    const hit = sources.find(
      (s) => s.act_short === act && s.section_number === stem,
    );

    parts.push(
      <span
        key={`${m.index}-${full}`}
        className="cite"
        title={hit ? hit.section_title : "Not among the retrieved passages"}
        onClick={() => hit && onPick?.(hit.chunk_id)}
      >
        {full}
      </span>,
    );
    last = m.index + full.length;
  }
  if (last < text.length) parts.push(text.slice(last));

  function copyAnswer() {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div>
      <div className="body">{parts}</div>
      <button
        onClick={copyAnswer}
        style={{ padding: "3px 9px", fontSize: "12px", marginTop: 8 }}
        title="Copy answer to clipboard"
        aria-label={copied ? "Answer copied to clipboard" : "Copy answer to clipboard"}
      >
        {copied ? "Copied!" : "Copy"}
      </button>
    </div>
  );
}
