// Mirrors the Pydantic models in backend/app/api/v1/. Kept hand-written rather
// than generated so a backend rename shows up as a type error here.

export type Confidence = "high" | "medium" | "low";

export interface Source {
  chunk_id: string;
  citation: string;
  act_short: string;
  section_number: string;
  section_title: string;
  page_start: number | null;
  text: string;
}

export interface DocumentSource {
  chunk_id: string;
  filename: string;
  page_start: number | null;
  text: string;
}

/** SSE `meta` — arrives before the first token. */
export interface ChatMeta {
  conversation_id: string;
  route: string;
  confidence: Confidence;
  score: number;
  reranked: boolean;
  rewritten_query: string | null;
  disambiguation: string | null;
  sources: Source[];
  document_sources: DocumentSource[];
}

/** SSE `validation` — the citation guard's verdict on the finished answer. */
export interface ChatValidation {
  verdict: string;
  answer: string;
  changed?: boolean;
  citations: string[];
  stripped?: string[];
  notes: string[];
}

/** SSE `done` — usage and timings, or `{refused: true}`. */
export interface ChatDone {
  refused?: boolean;
  usage?: { prompt_tokens: number; completion_tokens: number };
  cost_usd?: number;
  total_ms?: number;
  ttft_ms?: number | null;
}

export interface Passage {
  chunk_id: string;
  score: number;
  act_short: string;
  section_number: string;
  section_title: string;
  subsection: string | null;
  page_start: number | null;
  citation: string;
  text: string;
}

export interface SearchResponse {
  query: string;
  route: string;
  intent: string;
  confidence: string;
  score: number;
  reranked: boolean;
  rewritten_query: string | null;
  disambiguation: string | null;
  timings_ms: Record<string, number>;
  results: Passage[];
  document_results: Passage[];
}

export interface DocumentOut {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  page_count: number;
  chunk_count: number;
  status: string;
  error: string | null;
  injection_flags: string[];
}

export interface JobStatusOut {
  document_id: string;
  job_id: string;
  status: string;
  progress: number;
  stage_detail: string;
  error: string | null;
}

export interface UploadResponse {
  document_id: string;
  job_id: string;
  status: string;
  filename: string;
  size_bytes: number;
}

export interface FormOut {
  form_number: number;
  title: string;
  filename: string;
  page_start: number;
  page_end: number;
  page_count: number;
  size_bytes: number;
  sha256: string;
  extraction_confidence: number;
  needs_review: boolean;
  review_reasons: string[];
  see_sections: number[];
  act_short: string;
  download_url: string;
}

export interface FormListOut {
  total: number;
  needs_review: number;
  forms: FormOut[];
}

export interface Conversation {
  id: string;
  title: string;
  message_count: number;
  updated_at: string;
}
