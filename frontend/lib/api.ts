import type {
  ChatDone,
  ChatMeta,
  ChatValidation,
  Conversation,
  DocumentOut,
  FormListOut,
  FormOut,
  JobStatusOut,
  SearchResponse,
  UploadResponse,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * Every call must carry the session cookie. The backend mints an httpOnly
 * cookie on first contact and that cookie owns the caller's conversations and
 * uploads, so a request without credentials silently becomes a new session.
 */
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (!res.ok) throw new Error(await describe(res));
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

async function describe(res: Response): Promise<string> {
  try {
    const body = await res.json();
    return body?.detail ?? `${res.status} ${res.statusText}`;
  } catch {
    return `${res.status} ${res.statusText}`;
  }
}

export type ChatHandlers = {
  onMeta?: (m: ChatMeta) => void;
  onToken?: (text: string) => void;
  onValidation?: (v: ChatValidation) => void;
  onDone?: (d: ChatDone) => void;
  onError?: (message: string) => void;
};

/**
 * Chat is SSE over POST, so EventSource is out — it only does GET. Read the
 * body stream and parse frames by hand.
 */
export async function streamChat(
  body: { message: string; conversation_id?: string | null; use_documents?: boolean },
  handlers: ChatHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/v1/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok || !res.body) {
    handlers.onError?.(await describe(res));
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line. sse-starlette writes CRLF, so splitting
    // on a bare LF matches nothing and no frame is ever dispatched.
    const frames = buffer.split(/\r\n\r\n|\n\n|\r\r/);
    buffer = frames.pop() ?? "";

    for (const frame of frames) dispatch(frame, handlers);
  }
  if (buffer.trim()) dispatch(buffer, handlers);
}

function dispatch(frame: string, handlers: ChatHandlers): void {
  let event = "message";
  const dataLines: string[] = [];

  for (const line of frame.split(/\r\n|\n|\r/)) {
    if (line.startsWith(':')) continue; // keep-alive comment
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0) return;

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return;
  }

  switch (event) {
    case "meta":
      handlers.onMeta?.(payload as ChatMeta);
      break;
    case "token":
      handlers.onToken?.((payload as { text: string }).text);
      break;
    case "validation":
      handlers.onValidation?.(payload as ChatValidation);
      break;
    case "done":
      handlers.onDone?.(payload as ChatDone);
      break;
    case "error":
      handlers.onError?.(
        (payload as { detail?: string; message?: string }).detail ??
          (payload as { message?: string }).message ??
          "The server ended the stream.",
      );
      break;
  }
}

// ----------------------------------------------------------------- endpoints

export const listConversations = () =>
  request<Conversation[]>("/api/v1/conversations");

export const getConversation = (id: string) =>
  request<{ id: string; title: string; messages: unknown[] }>(
    `/api/v1/conversations/${id}`,
  );

export const renameConversation = (id: string, title: string) =>
  request<Conversation>(`/api/v1/conversations/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });

export const deleteConversation = (id: string) =>
  request<void>(`/api/v1/conversations/${id}`, { method: "DELETE" });

export const search = (body: {
  q: string;
  top_k?: number;
  include_documents?: boolean;
  allow_rewrite?: boolean;
}) =>
  request<SearchResponse>("/api/v1/search", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const listDocuments = () => request<DocumentOut[]>("/api/v1/documents");

export const documentStatus = (id: string) =>
  request<JobStatusOut>(`/api/v1/documents/${id}/status`);

export const deleteDocument = (id: string) =>
  request<void>(`/api/v1/documents/${id}`, { method: "DELETE" });

export async function uploadDocument(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  // No Content-Type header: the browser must set the multipart boundary.
  const res = await fetch(`${API_BASE}/api/v1/documents/upload`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) throw new Error(await describe(res));
  return (await res.json()) as UploadResponse;
}

export const listForms = () => request<FormListOut>("/api/v1/forms");

export const searchForms = (q: string) =>
  request<FormListOut>(`/api/v1/forms/search?q=${encodeURIComponent(q)}`);

export const getForm = (n: number) => request<FormOut>(`/api/v1/forms/${n}`);

export const sendFeedback = (body: {
  rating: number;
  message_id?: string | null;
  comment?: string | null;
}) =>
  request<{ id: string; recorded: boolean }>("/api/v1/feedback", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const formDownloadUrl = (n: number) =>
  `${API_BASE}/api/v1/forms/${n}/download`;

export const formPreviewUrl = (n: number) =>
  `${API_BASE}/api/v1/forms/${n}/preview`;

export const formsDownloadAllUrl = () => `${API_BASE}/api/v1/forms/download-all`;
