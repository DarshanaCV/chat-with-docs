export const API_URL = (process.env.NEXT_PUBLIC_API_URL ?? "/api").replace(/\/$/, "");

let visitorReady: Promise<void> | undefined;

async function apiFetch(path: string, options?: RequestInit): Promise<Response> {
  visitorReady ??= fetch(`${API_URL}/visitor`, { credentials: "include", cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error("Could not start a visitor session");
    })
    .catch((error) => {
      visitorReady = undefined;
      throw error;
    });
  await visitorReady;
  const response = await fetch(`${API_URL}${path}`, { ...options, credentials: "include" });
  if (response.status === 401) visitorReady = undefined;
  return response;
}

export interface UploadResponse {
  message: string;
  filename: string;
  document_id: string;
  content_type: "document" | "image";
}

export interface Source {
  chunk_id: string;
  content_type: "text" | "table" | "figure";
  filename: string;
  page_number: number;
  score: number;
  content: string;
  table_number?: number;
  figure_number?: number | string;
  caption?: string;
  image_path?: string;
  image_url?: string;
  rows?: string[][];
}

export interface AskResponse {
  question: string;
  response: string;
  sources: Source[];
}

export async function uploadFile(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await apiFetch("/upload", {
    method: "POST",
    body: formData,
  });

  if (!res.ok) throw new Error(await getErrorMessage(res, "Upload failed"));
  return res.json();
}

export async function askQuestion(sessionId: string, question: string): Promise<AskResponse> {
  const res = await apiFetch("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, question }),
  });

  if (!res.ok) throw new Error(await getErrorMessage(res, "Ask failed"));
  return res.json();
}

export interface DocumentInfo {
  document_id: string;
  filename: string;
  content_type: "document" | "image";
}

export interface SessionSummary {
  session_id: string;
  title: string;
  message_count: number;
  document_count: number;
  created_at: string;
  updated_at: string;
}

export interface SessionDetail extends SessionSummary {
  messages: { role: "user" | "assistant" | "attachment" | "attachment_removed"; content: string; sources?: Source[]; created_at: string }[];
  documents: DocumentInfo[];
}

export async function attachDocument(sessionId: string, documentId: string, filename: string, contentType: "document" | "image"): Promise<DocumentInfo[]> {
  const res = await apiFetch(`/sessions/${sessionId}/documents`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ document_id: documentId, filename, content_type: contentType }),
  });
  if (!res.ok) throw new Error(await getErrorMessage(res, "Could not attach document"));
  return res.json();
}

export async function removeDocument(sessionId: string, documentId: string): Promise<DocumentInfo[]> {
  const res = await apiFetch(`/sessions/${sessionId}/documents/${documentId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await getErrorMessage(res, "Could not remove document"));
  return res.json();
}

export async function listSessions(): Promise<SessionSummary[]> {
  const res = await apiFetch("/sessions");
  if (!res.ok) throw new Error(await getErrorMessage(res, "Could not load sessions"));
  return res.json();
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  const res = await apiFetch(`/sessions/${sessionId}`);
  if (!res.ok) throw new Error(await getErrorMessage(res, "Could not load session"));
  return res.json();
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await apiFetch(`/sessions/${sessionId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(await getErrorMessage(res, "Could not delete session"));
}

async function getErrorMessage(response: Response, fallback: string): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (typeof body === "object" && body !== null && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      if (typeof detail === "string") return detail;
    }
  } catch {
    // Use the status below when the server did not return JSON.
  }

  return `${fallback}: ${response.status} ${response.statusText}`;
}
