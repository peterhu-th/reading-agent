import { afterEach, expect, test, vi } from "vitest";

import { api, parseEventBlock } from "./api";

afterEach(() => vi.unstubAllGlobals());

test("parses a server-sent event", () => {
  const received: Array<[string, Record<string, unknown>]> = [];
  parseEventBlock('event: answer_delta\ndata: {"text":"你好"}', (event, data) => received.push([event, data]));
  expect(received).toEqual([["answer_delta", { text: "你好" }]]);
});

test("uses the POST chapter action and explains stale-backend 405 errors", async () => {
  const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Method Not Allowed" }), {
    status: 405,
    headers: { "Content-Type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetchMock);

  await expect(api.deleteChapter("book", 2)).rejects.toThrow("重新运行 python scripts/run_web.py");
  expect(fetchMock).toHaveBeenCalledWith("/api/editor/books/book/chapters/2/delete", { method: "POST" });
});

test("reader tools use book-scoped resources and encode paginated search parameters", async () => {
  const fetchMock = vi.fn().mockImplementation(async () => new Response("{}", { headers: { "Content-Type": "application/json" } }));
  vi.stubGlobal("fetch", fetchMock);
  await api.searchBook("book/a", "词 & 字", undefined, 50, 50);
  const search = new URL(fetchMock.mock.calls[0][0], "http://localhost");
  expect(search.pathname).toBe("/api/reader/books/book%2Fa/search");
  expect(Object.fromEntries(search.searchParams)).toEqual({ q: "词 & 字", offset: "50", limit: "50" });
  await api.addAnnotation({ book_id: "book/a", chapter_index: 1, start_edit_id: "first", end_edit_id: "last", start_offset: 0, end_offset: 2, selected_text: "选文", color: "yellow", comment: "笔记" });
  expect(fetchMock.mock.calls[1][0]).toBe("/api/reader/books/book%2Fa/annotations");
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).not.toHaveProperty("book_id");
  await api.deleteBookmark("book/a", "mark");
  expect(fetchMock).toHaveBeenLastCalledWith("/api/reader/books/book%2Fa/bookmarks/mark", { method: "DELETE" });
});

test("note exports retain the server's UTF-8 download filename and body", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("# 中文笔记", { headers: {
    "Content-Type": "text/markdown; charset=utf-8",
    "Content-Disposition": "attachment; filename*=UTF-8''%E4%B8%AD%E6%96%87%E7%AC%94%E8%AE%B0.md",
  } })));
  const result = await api.exportNotes("book", "markdown");
  expect(result.filename).toBe("中文笔记.md");
  const contents = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsText(result.blob);
  });
  expect(contents).toBe("# 中文笔记");
});
