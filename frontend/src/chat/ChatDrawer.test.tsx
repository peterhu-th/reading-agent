import { act, fireEvent, render, waitFor } from "@testing-library/react";
import type { ComponentProps } from "react";

import { api, streamChat } from "../api";
import type { Session } from "../types";
import { ChatDrawer } from "./ChatDrawer";

vi.mock("../api", () => ({ api: { listSessions: vi.fn(), createSession: vi.fn() }, streamChat: vi.fn() }));

function session(id: string): Session {
  return { session_id: id, title: id, turns: [], explicit_book_titles: [], updated_at: "" };
}

type Stream = { signal: AbortSignal; event: Parameters<typeof streamChat>[2]; finish: () => void };
let streams: Stream[];

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
  streams = [];
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.mocked(api.listSessions).mockResolvedValue([session("old-session")]);
  vi.mocked(api.createSession).mockResolvedValue(session("new-session"));
  vi.mocked(streamChat).mockImplementation((_input, signal, event) => new Promise<void>((finish) => streams.push({ signal, event, finish })));
});

async function setup(overrides: Partial<ComponentProps<typeof ChatDrawer>> = {}) {
  const consumed = vi.fn();
  const view = render(<ChatDrawer open width={400} health={null} book={null} chapter={null} selection={null} suggestedQuestion="" onClose={() => undefined} onWidth={() => undefined} onCitation={() => undefined} onSelectionConsumed={consumed} {...overrides} />);
  await waitFor(() => expect(sessionStorage.getItem("reading-chat-session")).toBe("old-session"));
  const send = (question: string) => {
    fireEvent.change(view.getByPlaceholderText("直接询问这本书…"), { target: { value: question } });
    fireEvent.click(view.getByTitle("发送"));
  };
  return { view, consumed, send };
}

test("new session cancels the old stream and ignores its late events and completion", async () => {
  const { view, consumed, send } = await setup();
  send("旧会话的问题");
  await act(async () => { fireEvent.click(view.getByTitle("新对话")); });
  expect(streams[0].signal.aborted).toBe(true);
  expect(sessionStorage.getItem("reading-chat-session")).toBe("new-session");

  send("新会话的问题");
  expect(vi.mocked(streamChat).mock.calls[1][0].session_id).toBe("new-session");
  await act(async () => {
    streams[1].event("answer_delta", { text: "新会话的回答" });
    streams[0].event("answer_delta", { text: "旧流的迟到内容" });
    streams[0].event("error", { message: "旧流的迟到错误" });
    streams[0].event("complete", { session: session("old-session") });
    streams[0].finish();
  });
  expect(view.queryByText("旧流的迟到内容")).not.toBeInTheDocument();
  expect(view.queryByText("旧流的迟到错误")).not.toBeInTheDocument();
  expect(view.getByText("新会话的回答")).toBeInTheDocument();
  expect(view.getByTitle("停止")).toBeInTheDocument();
  expect(consumed).not.toHaveBeenCalled();

  await act(async () => { streams[1].finish(); });
  send("继续新会话");
  expect(vi.mocked(streamChat).mock.calls[2][0].session_id).toBe("new-session");
  await act(async () => { streams[2].finish(); });
});

test("a stopped request cannot append to or finish the next message in the same session", async () => {
  const { view, send } = await setup();
  send("第一个问题");
  act(() => streams[0].event("answer_delta", { text: "已停止的回答" }));
  fireEvent.click(view.getByTitle("停止"));
  expect(streams[0].signal.aborted).toBe(true);
  send("第二个问题");
  await act(async () => {
    streams[1].event("answer_delta", { text: "第二个回答" });
    streams[0].event("answer_delta", { text: "不应追加" });
    streams[0].finish();
  });
  expect(view.getByText("已停止的回答")).toBeInTheDocument();
  expect(view.getByText("第二个回答")).toBeInTheDocument();
  expect(view.queryByText(/不应追加/)).not.toBeInTheDocument();
  expect(view.getByTitle("停止")).toBeInTheDocument();
  await act(async () => { streams[1].finish(); });
});

test("book-scoped questions retain the chapter and paragraph location of selected text", async () => {
  const { send } = await setup({
    book: { book_id: "book", title: "选文书籍", author: "作者", book_type: "fiction", chapter_count: 8, paragraph_count: 80 },
    chapter: { book_id: "book", chapter_index: 7, chapter_title: "选文所在章", depth: 0, paragraph_count: 10, preview: "" },
    selection: { text: "待解释选文", paragraphs: [3, 4], startEditId: "p3", endEditId: "p4", startOffset: 0, endOffset: 5 },
  });
  send("解释这段文字");
  expect(vi.mocked(streamChat).mock.calls[0][0]).toMatchObject({ scope: "book", book_id: "book", chapter_index: 7, selected_text: "待解释选文", selected_paragraphs: [3, 4] });
  await act(async () => { streams[0].finish(); });
});
