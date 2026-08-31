import type { AnnotationColor, DatabaseUpdateState, EditorState, HealthStatus, ReaderAnnotation, ReaderBook, ReaderChapter, ReaderMutation, ReaderPage, ReaderSearchHit, Session, ChatScope } from "./types";

async function requestJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    if (response.status === 405) {
      throw new Error("当前 Web 后端仍是旧版本，尚未加载此操作。请停止服务后重新运行 python scripts/run_web.py。");
    }
    throw new Error(body.detail ?? `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => requestJson<HealthStatus>("/api/health"),
  listBooks: () => requestJson<ReaderBook[]>("/api/reader/books"),
  listChapters: (id: string) => requestJson<ReaderChapter[]>(`/api/reader/books/${id}/chapters`),
  getChapter: (id: string, chapter: number, offset = 0, limit = 80, focus?: number) => {
    const params = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    if (focus !== undefined) params.set("focus_paragraph", String(focus));
    return requestJson<ReaderPage>(`/api/reader/books/${id}/chapters/${chapter}?${params}`);
  },
  searchBook: (id: string, query: string, chapter?: number) => {
    const params = new URLSearchParams({ q: query });
    if (chapter !== undefined) params.set("chapter_index", String(chapter));
    return requestJson<ReaderSearchHit[]>(`/api/reader/books/${id}/search?${params}`);
  },
  editorState: () => requestJson<EditorState>("/api/editor/state"),
  applyEditorOperation: (bookId: string, label: string, mutations: ReaderMutation[]) => requestJson<EditorState>("/api/editor/operations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ book_id: bookId, label, mutations }),
  }),
  addAnnotation: (input: {
    book_id: string;
    chapter_index: number;
    start_edit_id: string;
    end_edit_id: string;
    start_offset: number;
    end_offset: number;
    selected_text: string;
    color: AnnotationColor;
    comment: string;
  }) => requestJson<{ annotation: ReaderAnnotation; state: EditorState }>("/api/editor/annotations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }),
  deleteAnnotation: (id: string) => requestJson<EditorState>(`/api/editor/annotations/${id}`, { method: "DELETE" }),
  deleteChapter: (bookId: string, chapterIndex: number) => requestJson<EditorState>(`/api/editor/books/${bookId}/chapters/${chapterIndex}/delete`, { method: "POST" }),
  renameChapter: (bookId: string, chapterIndex: number, title: string) => requestJson<EditorState>(`/api/editor/books/${bookId}/chapters/${chapterIndex}/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  }),
  undoEditor: () => requestJson<EditorState>("/api/editor/undo", { method: "POST" }),
  saveEditor: () => requestJson<EditorState>("/api/editor/save", { method: "POST" }),
  startDatabaseUpdate: () => requestJson<DatabaseUpdateState>("/api/database/update", { method: "POST" }),
  databaseUpdateState: () => requestJson<DatabaseUpdateState>("/api/database/update"),
  listSessions: () => requestJson<Session[]>("/api/sessions"),
  createSession: () => requestJson<Session>("/api/sessions", { method: "POST" }),
  deleteSession: async (id: string) => {
    const response = await fetch(`/api/sessions/${id}`, { method: "DELETE" });
    if (!response.ok && response.status !== 404) throw new Error("无法删除会话");
  },
};

export interface ChatStreamInput {
  session_id: string;
  question: string;
  selected_books: string[];
  scope: ChatScope;
  book_id: string;
  book_title: string;
  chapter_index: number | null;
  selected_text: string;
  selected_paragraphs: number[];
  debug: boolean;
}

export async function streamChat(input: ChatStreamInput, signal: AbortSignal, onEvent: (event: string, data: Record<string, unknown>) => void): Promise<void> {
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
      parseEventBlock(buffer.slice(0, boundary), onEvent);
      buffer = buffer.slice(boundary + 2);
      boundary = buffer.indexOf("\n\n");
    }
    if (done) break;
  }
}

export function parseEventBlock(block: string, onEvent: (event: string, data: Record<string, unknown>) => void): void {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length) onEvent(event, JSON.parse(dataLines.join("\n")) as Record<string, unknown>);
}
