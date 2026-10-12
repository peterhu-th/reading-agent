import { act, fireEvent, render } from "@testing-library/react";

import App from "./App";
import { api } from "./api";
import type { EditorState, ReaderBook, ReaderChapter, ReaderLocation, ReaderPage } from "./types";

vi.mock("./api", () => ({ api: {
  listBooks: vi.fn(), listChapters: vi.fn(), getChapter: vi.fn(), health: vi.fn(), editorState: vi.fn(), applyEditorOperation: vi.fn(), saveEditor: vi.fn(),
  listAnnotations: vi.fn(), listBookmarks: vi.fn(), searchBook: vi.fn(), annotationLocation: vi.fn(), paragraphLocation: vi.fn(), addBookmark: vi.fn(), deleteChapter: vi.fn(),
} }));
vi.mock("./chat/ChatDrawer", () => ({ ChatDrawer: () => null }));

const firstBook: ReaderBook = { book_id: "first", title: "第一本书", author: "作者", book_type: "fiction", chapter_count: 2, paragraph_count: 2 };
const otherBook: ReaderBook = { ...firstBook, book_id: "other", title: "另一本书" };
const state: EditorState = { dirty: false, undo_count: 0, last_operation: "", database_updating: false, database_status: "idle", database_message: "" };

function chapter(book: ReaderBook, index: number): ReaderChapter {
  return { book_id: book.book_id, chapter_index: index, chapter_title: `第 ${index + 1} 章`, depth: 0, paragraph_count: 2, preview: "" };
}

function page(book: ReaderBook, index: number, offset = 0): ReaderPage {
  return {
    book, chapter: chapter(book, index),
    paragraphs: [{ paragraph_index: offset, edit_id: `${book.book_id}:${index}:${offset}`, text: offset ? "追加的正文" : `${book.title}第 ${index + 1} 章正文`, kind: "paragraph", annotations: [] }],
    offset, next_offset: null, previous_chapter_index: index === 0 ? null : 0, next_chapter_index: index === 0 ? 1 : null,
  };
}

let observerCallbacks: Array<() => void>;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  observerCallbacks = [];
  vi.stubGlobal("IntersectionObserver", class {
    constructor(private callback: (entries: Array<{ isIntersecting: boolean }>) => void) {}
    observe() { observerCallbacks.push(() => this.callback([{ isIntersecting: true }])); }
    disconnect() {}
  });
  vi.mocked(api.listBooks).mockResolvedValue([firstBook, otherBook]);
  vi.mocked(api.listChapters).mockImplementation(async (id) => [chapter(id === "first" ? firstBook : otherBook, 0), chapter(id === "first" ? firstBook : otherBook, 1)]);
  vi.mocked(api.getChapter).mockImplementation(async (id, index, offset) => page(id === "first" ? firstBook : otherBook, index, offset));
  vi.mocked(api.health).mockResolvedValue({ status: "ok", answer_api_healthy: false, answer_provider_ready: false, chunk_index_exists: true, chunks_exist: true, embedding_model_exists: true, reranker_model_exists: true });
  vi.mocked(api.editorState).mockResolvedValue(state);
  vi.mocked(api.applyEditorOperation).mockResolvedValue({ ...state, dirty: true, undo_count: 1 });
  vi.mocked(api.saveEditor).mockResolvedValue(state);
  vi.mocked(api.listAnnotations).mockResolvedValue({ items: [], total: 0, offset: 0, limit: 50, next_offset: null });
  vi.mocked(api.listBookmarks).mockResolvedValue({ items: [], total: 0, offset: 0, limit: 50, next_offset: null });
  vi.mocked(api.searchBook).mockResolvedValue({ items: [], total: 0, offset: 0, limit: 50, next_offset: null });
  vi.mocked(api.deleteChapter).mockResolvedValue({ ...state, dirty: true });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function setup() {
  const view = render(<App />);
  await view.findByText("第一本书第 1 章正文");
  vi.useFakeTimers();
  return view;
}

function edit(view: ReturnType<typeof render>) {
  const block = view.container.querySelector<HTMLElement>(".reader-block")!;
  Object.defineProperty(block, "innerText", { configurable: true, get: () => block.textContent });
  block.textContent = "尚未同步的修改";
  fireEvent.input(view.container.querySelector(".reader-editable-content")!);
}

test.each(["chapter", "book"])("%s navigation flushes pending input before fetching the new document", async (destination) => {
  let finishEdit!: (value: EditorState) => void;
  vi.mocked(api.applyEditorOperation).mockReturnValue(new Promise((resolve) => { finishEdit = resolve; }));
  const view = await setup();
  edit(view);

  await act(async () => {
    fireEvent.click(destination === "chapter" ? view.getByRole("button", { name: "下一章" }) : view.getByText("另一本书"));
  });
  expect(api.applyEditorOperation).toHaveBeenCalledWith("first", "编辑正文", [{ edit_id: "first:0:0", text: "尚未同步的修改", deleted: false }]);
  expect(api.getChapter).toHaveBeenCalledTimes(1);
  expect(view.container.querySelector(".reader-editable-content")).toHaveAttribute("contenteditable", "false");

  await act(async () => { finishEdit({ ...state, dirty: true }); });
  expect(view.getByText(destination === "chapter" ? "第一本书第 2 章正文" : "另一本书第 1 章正文")).toBeInTheDocument();
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  expect(api.applyEditorOperation).toHaveBeenCalledTimes(1);
});

test("pagination waits for an in-flight edit and preserves it when appending paragraphs", async () => {
  vi.mocked(api.getChapter).mockImplementation(async (_id, index, offset = 0) => ({ ...page(firstBook, index, offset), next_offset: offset === 0 ? 1 : null }));
  let finishEdit!: (value: EditorState) => void;
  vi.mocked(api.applyEditorOperation).mockReturnValue(new Promise((resolve) => { finishEdit = resolve; }));
  const view = await setup();
  edit(view);
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  expect(api.applyEditorOperation).toHaveBeenCalledTimes(1);

  await act(async () => { observerCallbacks.at(-1)!(); });
  expect(api.getChapter).toHaveBeenCalledTimes(1);
  await act(async () => { finishEdit({ ...state, dirty: true }); });
  expect(api.getChapter).toHaveBeenLastCalledWith("first", 0, 1, 80);
  expect(view.getByText("尚未同步的修改")).toBeInTheDocument();
  expect(view.getByText("追加的正文")).toBeInTheDocument();
  expect(api.applyEditorOperation).toHaveBeenCalledTimes(1);
});

test("a failed flush keeps the draft visible and prevents navigation", async () => {
  vi.mocked(api.applyEditorOperation).mockRejectedValueOnce(new Error("模拟提交失败"));
  const view = await setup();
  edit(view);
  await act(async () => { fireEvent.click(view.getByRole("button", { name: "下一章" })); });

  expect(api.getChapter).toHaveBeenCalledTimes(1);
  expect(view.getByText("尚未同步的修改")).toBeInTheDocument();
  expect(view.getByText("模拟提交失败")).toBeInTheDocument();
  expect(view.container.querySelector(".reader-editable-content")).toHaveAttribute("contenteditable", "true");
});

test("saving reloads renumbered chapters and uses their new edit IDs", async () => {
  vi.mocked(api.listChapters).mockResolvedValueOnce([chapter(firstBook, 1), chapter(firstBook, 3)]).mockResolvedValue([chapter(firstBook, 0), chapter(firstBook, 1)]);
  vi.mocked(api.editorState).mockResolvedValue({ ...state, dirty: true });
  const view = render(<App />);
  await view.findByText("第一本书第 2 章正文");
  vi.useFakeTimers();
  const block = view.container.querySelector<HTMLElement>(".reader-block")!;
  Object.defineProperty(block, "innerText", { configurable: true, get: () => block.textContent });

  await act(async () => { fireEvent.click(view.getByRole("button", { name: "保存" })); });
  expect(api.saveEditor).toHaveBeenCalledTimes(1);
  expect(api.getChapter).toHaveBeenLastCalledWith("first", 0);
  expect(view.container.querySelector(".reader-block")).toHaveAttribute("data-edit-id", "first:0:0");

  edit(view);
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  expect(api.applyEditorOperation).toHaveBeenCalledWith("first", "编辑正文", [{ edit_id: "first:0:0", text: "尚未同步的修改", deleted: false }]);
});

test("saving migrates the reading location and cancels the old progress timer before a refresh", async () => {
  const savedBook = { ...firstBook, book_id: "first-saved" };
  let saved = false;
  vi.mocked(api.listBooks).mockImplementation(async () => [saved ? savedBook : firstBook]);
  vi.mocked(api.listChapters).mockImplementation(async () => saved
    ? [chapter(savedBook, 0), chapter(savedBook, 1)]
    : [chapter(firstBook, 2), chapter(firstBook, 5)]);
  vi.mocked(api.getChapter).mockImplementation(async (_id, index, offset) => page(saved ? savedBook : firstBook, index, offset));
  vi.mocked(api.editorState).mockResolvedValue({ ...state, dirty: true });
  vi.mocked(api.saveEditor).mockImplementation(async () => { saved = true; return state; });
  localStorage.setItem("reader-location:first", JSON.stringify({ chapter: 99, paragraph: 9, progress: 90 }));
  const view = render(<App />);
  await view.findByText("第一本书第 3 章正文");
  expect(api.getChapter).toHaveBeenCalledWith("first", 2, 0, 80, undefined);
  vi.useFakeTimers();
  const block = view.container.querySelector<HTMLElement>(".reader-block")!;
  Object.defineProperty(block, "innerText", { configurable: true, get: () => block.textContent });
  fireEvent.scroll(view.container.querySelector(".reader-scroll")!);

  await act(async () => { fireEvent.click(view.getByRole("button", { name: "保存" })); });
  const expected = { chapter: 0, paragraph: 0, progress: 0 };
  expect(JSON.parse(localStorage.getItem("reader-location:first-saved")!)).toEqual(expected);
  expect(localStorage.getItem("reader-location:first")).toBeNull();
  await act(async () => { await vi.advanceTimersByTimeAsync(250); });
  expect(localStorage.getItem("reader-location:first")).toBeNull();
  expect(JSON.parse(localStorage.getItem("reader-location:first-saved")!)).toEqual(expected);

  view.unmount();
  let refreshed!: ReturnType<typeof render>;
  await act(async () => { refreshed = render(<App />); });
  expect(api.getChapter).toHaveBeenLastCalledWith("first-saved", 0, 0, 80, 0);
  expect(refreshed.getByText("第一本书第 1 章正文")).toBeInTheDocument();
});

test("whole-book search waits for pending edits and re-resolves a hit before highlighting it", async () => {
  let finishEdit!: (value: EditorState) => void;
  vi.mocked(api.applyEditorOperation).mockReturnValue(new Promise((resolve) => { finishEdit = resolve; }));
  const target: ReaderLocation = { book_id: "first", chapter_index: 1, start_paragraph_index: 7, end_paragraph_index: 7 };
  vi.mocked(api.searchBook).mockResolvedValue({ items: [{ book_id: "first", edit_id: "result", chapter_index: 1, chapter_title: "远处的章节", paragraph_index: 5, excerpt: "远处的匹配文字", reader_location: { ...target, start_paragraph_index: 5 } }], total: 1, offset: 0, limit: 50, next_offset: null });
  vi.mocked(api.paragraphLocation).mockResolvedValue(target);
  const view = await setup();
  edit(view);
  fireEvent.click(view.getByRole("button", { name: "搜索全书" }));
  fireEvent.change(view.getByRole("textbox", { name: "搜索关键词" }), { target: { value: "匹配" } });
  await act(async () => { fireEvent.click(view.getByRole("button", { name: "查找" })); });
  expect(api.searchBook).not.toHaveBeenCalled();
  await act(async () => { finishEdit({ ...state, dirty: true }); });
  expect(api.searchBook).toHaveBeenCalledWith("first", "匹配", undefined, 0, 50, expect.any(AbortSignal));
  vi.mocked(api.getChapter).mockImplementation(async (_id, index, _offset, _limit, focus) => page(firstBook, index, focus));
  await act(async () => { fireEvent.click(view.getByRole("button", { name: /远处的章节/ })); });
  expect(api.paragraphLocation).toHaveBeenCalledWith("first", "result");
  expect(api.getChapter).toHaveBeenLastCalledWith("first", 1, 0, 80, 7);
  expect(view.container.querySelector('[data-paragraph="7"]')).toHaveClass("is-highlighted");
});

test("a delayed note location cannot replace a newer book navigation still loading", async () => {
  const target: ReaderLocation = { book_id: "first", chapter_index: 1, start_paragraph_index: 0, end_paragraph_index: 0 };
  vi.mocked(api.listAnnotations).mockResolvedValue({ items: [{
    annotation_id: "note", book_id: "first", chapter_index: 1, chapter_title: "旧书批注", start_edit_id: "note-start", end_edit_id: "note-start", start_offset: 0, end_offset: 2,
    selected_text: "正文", excerpt: "正文", quote: "正文", comment: "笔记", color: "yellow", created_at: "", updated_at: "", reader_location: target,
  }], total: 1, offset: 0, limit: 50, next_offset: null });
  let finishLocation!: (value: ReaderLocation) => void;
  vi.mocked(api.annotationLocation).mockReturnValue(new Promise((resolve) => { finishLocation = resolve; }));
  const view = await setup();
  const block = view.container.querySelector<HTMLElement>(".reader-block")!;
  Object.defineProperty(block, "innerText", { configurable: true, get: () => block.textContent });
  await act(async () => { fireEvent.click(view.getByRole("button", { name: "批注与书签" })); });
  await act(async () => { fireEvent.click(view.getByRole("button", { name: "定位原文" })); });
  let finishChapters!: (value: ReaderChapter[]) => void;
  vi.mocked(api.listChapters).mockReturnValueOnce(new Promise((resolve) => { finishChapters = resolve; }));
  await act(async () => { fireEvent.click(view.getByText("另一本书")); });
  await act(async () => { finishLocation(target); });
  expect(api.getChapter).toHaveBeenCalledTimes(1);
  await act(async () => { finishChapters([chapter(otherBook, 0)]); });
  expect(api.getChapter).toHaveBeenLastCalledWith("other", 0, 0, 80, undefined);
  expect(view.getByText("另一本书第 1 章正文")).toBeInTheDocument();
});

test.each([false, true])("deleting a chapter refreshes note lists even without a replacement navigation (last=%s)", async (last) => {
  vi.mocked(api.health).mockResolvedValue({ status: "ok", editor_capabilities: ["chapter_delete"], answer_api_healthy: false, answer_provider_ready: false, chunk_index_exists: true, chunks_exist: true, embedding_model_exists: true, reranker_model_exists: true });
  if (last) vi.mocked(api.listChapters).mockResolvedValueOnce([chapter(firstBook, 0)]).mockResolvedValue([]);
  const view = await setup();
  const block = view.container.querySelector<HTMLElement>(".reader-block")!;
  Object.defineProperty(block, "innerText", { configurable: true, get: () => block.textContent });
  await act(async () => { fireEvent.click(view.getByRole("button", { name: "批注与书签" })); });
  expect(api.listAnnotations).toHaveBeenCalledTimes(1);
  fireEvent.click(view.getByRole("button", { name: /^目录$/ }));
  await act(async () => { fireEvent.click(view.getByRole("button", { name: `删除章节：第 ${last ? 1 : 2} 章` })); });
  expect(api.deleteChapter).toHaveBeenCalledWith("first", last ? 0 : 1);
  expect(api.listAnnotations).toHaveBeenCalledTimes(2);
});
