import type { Book, HealthStatus, Session, SourceDetail } from "./types";

async function requestJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => requestJson<HealthStatus>("/api/health"),
  listBooks: () => requestJson<Book[]>("/api/books"),
  listSessions: () => requestJson<Session[]>("/api/sessions"),
  createSession: () => requestJson<Session>("/api/sessions", { method: "POST" }),
  getSession: (id: string) => requestJson<Session>(`/api/sessions/${id}`),
  deleteSession: async (id: string) => {
    const response = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) throw new Error("无法删除会话");
  },
  getSource: (id: string) => requestJson<SourceDetail>(`/api/sources/${id}`),
};

export async function streamChat(
  input: { session_id: string; question: string; selected_books: string[]; debug: boolean },
  signal: AbortSignal,
  onEvent: (event: string, data: Record<string, unknown>) => void,
): Promise<void> {
  const response = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `请求失败：${response.status}`);
  }
  if (!response.body) throw new Error("服务器没有返回流式响应");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      parseEventBlock(block, onEvent);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
}

export function parseEventBlock(
  block: string,
  onEvent: (event: string, data: Record<string, unknown>) => void,
): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return;
  onEvent(event, JSON.parse(dataLines.join("\n")) as Record<string, unknown>);
}
