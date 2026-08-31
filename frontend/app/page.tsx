"use client";

import { useCallback, useRef, useState } from "react";
import AnswerText from "@/components/AnswerText";
import Confidence from "@/components/Confidence";
import Sources from "@/components/Sources";
import { sendFeedback, streamChat } from "@/lib/api";
import type {
  ChatDone,
  ChatMeta,
  ChatValidation,
  DocumentSource,
  Source,
} from "@/lib/types";

type Turn = {
  role: "user" | "assistant";
  text: string;
  meta?: ChatMeta;
  validation?: ChatValidation;
  done?: ChatDone;
  error?: string;
};

export default function ChatPage() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [useDocs, setUseDocs] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);
  const [rated, setRated] = useState<Record<number, number>>({});
  const abort = useRef<AbortController | null>(null);

  const patchLast = useCallback((patch: Partial<Turn>) => {
    setTurns((prev) => {
      const next = [...prev];
      next[next.length - 1] = { ...next[next.length - 1], ...patch };
      return next;
    });
  }, []);

  async function send() {
    const message = draft.trim();
    if (!message || busy) return;

    setDraft("");
    setBusy(true);
    setTurns((p) => [...p, { role: "user", text: message }, { role: "assistant", text: "" }]);

    abort.current = new AbortController();
    try {
      await streamChat(
        { message, conversation_id: conversationId, use_documents: useDocs },
        {
          onMeta: (m) => {
            setConversationId(m.conversation_id);
            patchLast({ meta: m });
          },
          onToken: (t) =>
            setTurns((prev) => {
              const next = [...prev];
              const last = next[next.length - 1];
              next[next.length - 1] = { ...last, text: last.text + t };
              return next;
            }),
          // The guard may rewrite the answer to strip invented citations, so
          // the validated text supersedes whatever streamed.
          onValidation: (v) => patchLast({ validation: v, text: v.answer }),
          onDone: (d) => patchLast({ done: d }),
          onError: (e) => patchLast({ error: e }),
        },
        abort.current.signal,
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        patchLast({ error: (e as Error).message });
      }
    } finally {
      // A stream that dies server-side ends the reader without an error event,
      // which would otherwise leave this turn stuck on "Retrieving..." forever.
      setTurns((prev) => {
        const next = [...prev];
        const last = next[next.length - 1];
        if (last && last.role === "assistant" && !last.text && !last.error) {
          next[next.length - 1] = {
            ...last,
            error: "The answer stream ended before any content arrived.",
          };
        }
        return next;
      });
      setBusy(false);
      abort.current = null;
    }
  }

  function jump(chunkId: string) {
    setHighlight(chunkId);
    document.getElementById(`src-${chunkId}`)?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }

  async function rate(i: number, rating: number) {
    setRated((r) => ({ ...r, [i]: rating }));
    try {
      await sendFeedback({ rating });
    } catch {
      /* a failed rating is not worth interrupting the reader */
    }
  }

  function reset() {
    abort.current?.abort();
    setTurns([]);
    setConversationId(null);
    setRated({});
  }

  function regenerate() {
    const lastUserTurn = [...turns].reverse().find((t) => t.role === "user");
    if (lastUserTurn) {
      setDraft(lastUserTurn.text);
      const assistantIdx = turns.length - 1;
      if (turns[assistantIdx]?.role === "assistant") {
        setTurns((prev) => prev.slice(0, -2));
      }
    }
  }

  return (
    <main>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <label className="row small muted" style={{ gap: 6 }}>
          <input
            type="checkbox"
            checked={useDocs}
            onChange={(e) => setUseDocs(e.target.checked)}
            style={{ width: "auto" }}
            aria-label="Include uploaded documents in search results"
          />
          Also search my uploaded documents
        </label>
        <button
          onClick={reset}
          disabled={turns.length === 0}
          aria-label="Start a new conversation"
        >
          New conversation
        </button>
      </div>

      {turns.length === 0 && (
        <div className="empty">
          <div style={{ marginBottom: 24 }}>
            Ask about the BNSS 2023. Answers cite the sections they rest on, and
            the assistant refuses rather than guess when retrieval is weak.
          </div>
          <div style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))" }}>
            {[
              "What is the punishment for rape under section 63?",
              "What is culpable homicide not amounting to murder?",
              "What is the procedure for arrest without warrant?",
              "What constitutes criminal intimidation under the BNSS?"
            ].map((question) => (
              <button
                key={question}
                onClick={() => {
                  setDraft(question);
                  setTimeout(() => document.querySelector("textarea")?.focus(), 0);
                }}
                style={{
                  padding: 12,
                  textAlign: "left",
                  fontSize: 13,
                  lineHeight: 1.4,
                  cursor: "pointer"
                }}
                title="Click to ask this question"
              >
                {question}
              </button>
            ))}
          </div>
        </div>
      )}

      {turns.map((t, i) => (
        <div key={i} className={`msg ${t.role}`}>
          <div className="who">{t.role === "user" ? "You" : "Nyaya"}</div>

          {t.role === "user" ? (
            <div className="body">{t.text}</div>
          ) : (
            <>
              {t.meta && (
                <div className="row small muted" style={{ gap: 8, marginBottom: 8, flexWrap: "wrap" }}>
                  <Confidence level={t.meta.confidence} score={t.meta.score} />
                  <span>route: {t.meta.route}</span>
                  {t.meta.reranked && <span>reranked</span>}
                  {t.meta.rewritten_query && (
                    <span>rewritten: &ldquo;{t.meta.rewritten_query}&rdquo;</span>
                  )}
                </div>
              )}

              {t.text ? (
                <AnswerText
                  text={t.text}
                  sources={t.meta?.sources ?? []}
                  onPick={jump}
                />
              ) : (
                !t.error && <div className="muted small">Retrieving...</div>
              )}

              {t.error && (
                <div className="err" style={{ marginTop: 8 }}>
                  <strong>Error:</strong> {t.error}
                  {t.error.includes("Retrieval") && (
                    <div style={{ fontSize: 12, marginTop: 4 }} className="muted">
                      The statute index may still be starting up. Try again in a moment.
                    </div>
                  )}
                  {t.error.includes("language model") && (
                    <div style={{ fontSize: 12, marginTop: 4 }} className="muted">
                      The LLM provider is temporarily unavailable. Check your API key or try again shortly.
                    </div>
                  )}
                </div>
              )}

              {t.validation && (t.validation.stripped?.length ?? 0) > 0 && (
                <div className="small" style={{ marginTop: 8 }}>
                  <span className="badge low">citations removed</span>{" "}
                  <span className="muted">
                    The guard stripped {t.validation.stripped!.join(", ")} - not
                    supported by the retrieved passages.
                  </span>
                </div>
              )}

              <Sources
                sources={t.meta?.sources ?? []}
                documentSources={(t.meta?.document_sources ?? []) as DocumentSource[]}
                highlighted={highlight}
              />

              {t.done && !busy && (
                <div className="row small muted" style={{ gap: 10, marginTop: 10, flexWrap: "wrap" }}>
                  <button
                    onClick={() => rate(i, 1)}
                    disabled={rated[i] !== undefined}
                    style={{ padding: "3px 9px" }}
                  >
                    Helpful
                  </button>
                  <button
                    onClick={() => rate(i, -1)}
                    disabled={rated[i] !== undefined}
                    style={{ padding: "3px 9px" }}
                  >
                    Not helpful
                  </button>
                  <button
                    onClick={() => regenerate()}
                    style={{ padding: "3px 9px" }}
                  >
                    Regenerate
                  </button>
                  {rated[i] !== undefined && <span>thanks</span>}
                  {t.done.total_ms != null && <span>{(t.done.total_ms / 1000).toFixed(1)}s</span>}
                  {t.done.ttft_ms != null && <span>ttft {Math.round(t.done.ttft_ms)}ms</span>}
                  {t.done.cost_usd != null && <span>${t.done.cost_usd.toFixed(5)}</span>}
                </div>
              )}
            </>
          )}
        </div>
      ))}

      <div className="composer">
        <textarea
          value={draft}
          placeholder="e.g. What is the procedure for arrest without a warrant?"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
          rows={2}
          aria-label="Question about the BNSS 2023"
        />
        {busy ? (
          <button
            onClick={() => abort.current?.abort()}
            aria-label="Stop the current response"
          >
            Stop
          </button>
        ) : (
          <button
            className="primary"
            onClick={() => void send()}
            disabled={!draft.trim()}
            aria-label="Send question"
          >
            Ask
          </button>
        )}
      </div>
    </main>
  );
}
