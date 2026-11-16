// Types and calls for the backend API. Everything the UI knows about the server is here.

export type DocStatus =
  | "queued" | "extracting" | "chunking" | "indexing" | "ready" | "failed" | "superseded";

export interface Doc {
  id: number;
  filename: string;
  version_no: number;
  status: DocStatus;
  error: string | null;
  title: string | null;
  doc_version: string | null;
  doc_owner: string | null;
  chunk_count: number | null;
  size_bytes: number;
  uploaded_at: string;
  indexed_at: string | null;
  previous_version_live: boolean;
  attempts: number | null;
}

export interface UploadResult {
  outcome: "created" | "replaced" | "duplicate" | "rejected";
  filename: string;
  message: string;
}

export interface Passage {
  document: string;
  filename: string;
  document_version: string | null;
  document_owner: string | null;
  upload_no: number;
  uploaded_at: string;
  location: { label: string };
  passage: string;
  score: number;
  coverage: number;
  matched_terms: string[];
  missing_terms: string[];
  quote?: string;
  highlight?: [number, number];
}

export interface Answer {
  question: string;
  supported: boolean;
  answer: string;
  reason: string;
  sources: Passage[];
  related: Passage[];
  closest: Passage[];
  retriever: string;
  answerer: string;
  threshold: number;
  documents_indexed: number;
  took_ms: number;
}

export interface Me {
  username: string;
  retriever: string;
  answerer: string;
  accepted_types: string[];
  max_upload_mb: number;
}

export class Unauthorised extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin", ...init });
  if (response.status === 401) throw new Unauthorised();
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed (${response.status})`);
  }
  return response.json();
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  me: () => call<Me>("/api/me"),
  login: (username: string, password: string) => call("/api/login", json({ username, password })),
  logout: () => call("/api/logout", { method: "POST" }),
  ask: (question: string) => call<Answer>("/api/ask", json({ question })),
  documents: () => call<{ documents: Doc[] }>("/api/documents"),
  remove: (id: number) => call(`/api/documents/${id}`, { method: "DELETE" }),
  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return call<{ results: UploadResult[] }>("/api/documents", { method: "POST", body: form });
  },
};
