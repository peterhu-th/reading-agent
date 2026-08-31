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
