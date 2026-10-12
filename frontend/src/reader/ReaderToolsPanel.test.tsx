import { act, fireEvent, render, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";

import { api } from "../api";
import type { EditorState, ReaderAnnotationItem, ReaderLocation, ReaderResultPage } from "../types";
import { downloadFile } from "./download";
import { ReaderToolsPanel } from "./ReaderToolsPanel";

vi.mock("../api", () => ({ api: {
  listAnnotations: vi.fn(), listBookmarks: vi.fn(), searchBook: vi.fn(), annotationLocation: vi.fn(), bookmarkLocation: vi.fn(), paragraphLocation: vi.fn(),
  addBookmark: vi.fn(), deleteBookmark: vi.fn(), deleteAnnotation: vi.fn(), updateAnnotation: vi.fn(), exportNotes: vi.fn(),
} }));
vi.mock("./download", () => ({ downloadFile: vi.fn() }));

const editorState: EditorState = { dirty: true, undo_count: 1, last_operation: "", database_updating: false, database_status: "idle", database_message: "" };
const location: ReaderLocation = { book_id: "book", chapter_index: 1, start_paragraph_index: 7, end_paragraph_index: 8 };
const annotation: ReaderAnnotationItem = {
  annotation_id: "note", book_id: "book", chapter_index: 0, chapter_title: "第一章", start_edit_id: "first", end_edit_id: "last",
  start_offset: 0, end_offset: 2, selected_text: "旧选文", quote: "当前选文", excerpt: "当前选文", color: "yellow", comment: "我的理解",
  created_at: "2026-10-12", updated_at: "2026-10-12", reader_location: { ...location, chapter_index: 0 },
};
function page<T>(items: T[], next: number | null = null, total = items.length): ReaderResultPage<T> {
  return { items, total, offset: 0, limit: 50, next_offset: next };
}
function props(overrides: Partial<ComponentProps<typeof ReaderToolsPanel>> = {}): ComponentProps<typeof ReaderToolsPanel> {
  return {
    book: { book_id: "book", title: "测试书", author: "作者", book_type: "fiction", chapter_count: 2, paragraph_count: 10 },
    chapterIndex: 0, tab: "annotations", revision: 0, disabled: false, dirty: true,
    onTab: vi.fn(), onClose: vi.fn(), onFlush: vi.fn().mockResolvedValue(4), onLocate: vi.fn().mockResolvedValue(undefined),
    onMutation: vi.fn(async (action) => { await action({ chapter_index: 0, edit_id: "visible-paragraph" }); }),
    ...overrides,
  };
}
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listAnnotations).mockResolvedValue(page([annotation]));
  vi.mocked(api.listBookmarks).mockResolvedValue(page([]));
  vi.mocked(api.searchBook).mockResolvedValue(page([]));
  vi.mocked(api.annotationLocation).mockResolvedValue(location);
  vi.mocked(api.bookmarkLocation).mockResolvedValue(location);
  vi.mocked(api.paragraphLocation).mockResolvedValue(location);
  vi.mocked(api.addBookmark).mockResolvedValue({ bookmark: { bookmark_id: "mark", book_id: "book", chapter_index: 0, edit_id: "visible-paragraph", label: "下次阅读", created_at: "2026-10-12" }, state: editorState });
  vi.mocked(api.deleteAnnotation).mockResolvedValue(editorState);
  vi.mocked(api.deleteBookmark).mockResolvedValue(editorState);
  vi.mocked(api.updateAnnotation).mockResolvedValue({ annotation, state: editorState });
  vi.mocked(api.exportNotes).mockResolvedValue({ blob: new Blob(["笔记"]), filename: "测试书.md" });
});

test("annotation pages append, resolve their current location, and can be edited or deleted", async () => {
  vi.mocked(api.listAnnotations).mockResolvedValueOnce(page([annotation], 50, 2)).mockResolvedValueOnce(page([{ ...annotation, annotation_id: "next", comment: "下一页批注" }], null, 2));
  const callbacks = props();
  const view = render(<ReaderToolsPanel {...callbacks} />);
  await view.findByText("我的理解");
  expect(view.getByText("当前选文")).toBeInTheDocument();
  fireEvent.click(view.getByRole("button", { name: "加载更多" }));
  await view.findByText("下一页批注");
  expect(api.listAnnotations).toHaveBeenLastCalledWith("book", 50, 50, expect.any(AbortSignal));
  const first = view.getByText("我的理解").closest("li")!;
  fireEvent.click(within(first).getByRole("button", { name: "定位原文" }));
  await waitFor(() => expect(callbacks.onLocate).toHaveBeenCalledWith(location, 4));
  expect(api.annotationLocation).toHaveBeenCalledWith("book", "note");
  fireEvent.click(within(first).getByRole("button", { name: "编辑" }));
  fireEvent.change(view.getByRole("textbox", { name: "修改批注" }), { target: { value: "补充解释" } });
  fireEvent.change(view.getByLabelText("批注颜色"), { target: { value: "blue" } });
  fireEvent.click(view.getByRole("button", { name: "应用修改" }));
  await waitFor(() => expect(api.updateAnnotation).toHaveBeenCalledWith("book", "note", { comment: "补充解释", color: "blue" }));
  await waitFor(() => expect(view.queryByRole("textbox", { name: "修改批注" })).toBeNull());
  fireEvent.click(within(first).getByRole("button", { name: "删除批注：我的理解" }));
  await waitFor(() => expect(api.deleteAnnotation).toHaveBeenCalledWith("book", "note"));
});

test("list errors can retry and an old book response cannot populate the new book", async () => {
  let oldResponse!: (value: ReaderResultPage<ReaderAnnotationItem>) => void;
  vi.mocked(api.listAnnotations).mockReturnValueOnce(new Promise((resolve) => { oldResponse = resolve; })).mockRejectedValueOnce(new Error("列表暂不可用")).mockResolvedValueOnce(page([]));
  const initial = props();
  const view = render(<ReaderToolsPanel key="book" {...initial} />);
  await waitFor(() => expect(api.listAnnotations).toHaveBeenCalledTimes(1));
  view.rerender(<ReaderToolsPanel key="other" {...initial} book={{ ...initial.book, book_id: "other", title: "另一本书" }} />);
  await view.findByText("列表暂不可用");
  fireEvent.click(view.getByRole("button", { name: "重试" }));
  await view.findByText("还没有批注。选中正文即可添加高亮和笔记。");
  await act(async () => { oldResponse(page([annotation])); });
  expect(view.queryByText("我的理解")).toBeNull();
  expect(view.getByText("另一本书")).toBeInTheDocument();
});

test("search defaults to the whole book, ignores old queries, and supports chapter scope", async () => {
  let oldResponse!: (value: ReaderResultPage<never>) => void;
  vi.mocked(api.searchBook).mockReturnValueOnce(new Promise((resolve) => { oldResponse = resolve; })).mockResolvedValue(page([{ book_id: "book", chapter_index: 1, chapter_title: "第二章", paragraph_index: 7, edit_id: "match", excerpt: "找到<关键>文本", reader_location: location }]));
  const callbacks = props({ tab: "search" });
  const view = render(<ReaderToolsPanel {...callbacks} />);
  fireEvent.change(view.getByRole("textbox", { name: "搜索关键词" }), { target: { value: "旧查询" } });
  fireEvent.click(view.getByRole("button", { name: "查找" }));
  await waitFor(() => expect(api.searchBook).toHaveBeenCalledWith("book", "旧查询", undefined, 0, 50, expect.any(AbortSignal)));
  fireEvent.change(view.getByRole("textbox", { name: "搜索关键词" }), { target: { value: "<关键>" } });
  fireEvent.click(view.getByRole("button", { name: "查找" }));
  await view.findByText("<关键>");
  await act(async () => { oldResponse(page([])); });
  expect(view.container.querySelector("mark")).toHaveTextContent("<关键>");
  fireEvent.change(view.getByLabelText("搜索范围"), { target: { value: "chapter" } });
  await waitFor(() => expect(api.searchBook).toHaveBeenLastCalledWith("book", "<关键>", 0, 0, 50, expect.any(AbortSignal)));
  fireEvent.click(await view.findByRole("button", { name: /第二章 · 段落 8/ }));
  await waitFor(() => expect(callbacks.onLocate).toHaveBeenCalledWith(location, 4));
  expect(api.paragraphLocation).toHaveBeenCalledWith("book", "match");
});

test("bookmarks use the current edit ID and exports flush edits before downloading both formats", async () => {
  const callbacks = props({ tab: "bookmarks" });
  const view = render(<ReaderToolsPanel {...callbacks} />);
  await view.findByText("还没有书签。标记当前位置，方便下次继续阅读。");
  fireEvent.change(view.getByRole("textbox", { name: "书签名称" }), { target: { value: "下次阅读" } });
  fireEvent.click(view.getByRole("button", { name: "添加当前位置" }));
  await waitFor(() => expect(api.addBookmark).toHaveBeenCalledWith("book", { chapter_index: 0, edit_id: "visible-paragraph", label: "下次阅读" }));
  await waitFor(() => expect(view.getByRole("textbox", { name: "书签名称" })).toHaveValue(""));
  for (const format of ["markdown", "json"] as const) {
    vi.mocked(callbacks.onFlush).mockClear();
    vi.mocked(api.exportNotes).mockImplementationOnce(async () => {
      expect(callbacks.onFlush).toHaveBeenCalledTimes(1);
      return { blob: new Blob(["笔记"]), filename: `测试书.${format === "markdown" ? "md" : "json"}` };
    });
    fireEvent.click(view.getByRole("button", { name: format === "markdown" ? "Markdown" : "JSON" }));
    await waitFor(() => expect(api.exportNotes).toHaveBeenLastCalledWith("book", format));
    await waitFor(() => expect(downloadFile).toHaveBeenLastCalledWith(expect.any(Blob), `测试书.${format === "markdown" ? "md" : "json"}`));
  }
});

test("a failed editor flush prevents a search from using stale text", async () => {
  const view = render(<ReaderToolsPanel {...props({ tab: "search", onFlush: vi.fn().mockRejectedValue(new Error("正文提交失败")) })} />);
  fireEvent.change(view.getByRole("textbox", { name: "搜索关键词" }), { target: { value: "正文" } });
  fireEvent.click(view.getByRole("button", { name: "查找" }));
  await view.findByText("正文提交失败");
  expect(api.searchBook).not.toHaveBeenCalled();
});

test("a search hit deleted by pending edits reports a stale location without jumping to a neighbour", async () => {
  vi.mocked(api.searchBook).mockResolvedValue(page([{ book_id: "book", chapter_index: 0, chapter_title: "第一章", paragraph_index: 0, edit_id: "deleted", excerpt: "将被删除的正文", reader_location: location }]));
  vi.mocked(api.paragraphLocation).mockRejectedValue(new Error("原文位置已失效"));
  const callbacks = props({ tab: "search" });
  const view = render(<ReaderToolsPanel {...callbacks} />);
  fireEvent.change(view.getByRole("textbox", { name: "搜索关键词" }), { target: { value: "正文" } });
  fireEvent.click(view.getByRole("button", { name: "查找" }));
  fireEvent.click(await view.findByRole("button", { name: /第一章 · 段落 1/ }));
  await view.findByText("原文位置已失效");
  expect(api.paragraphLocation).toHaveBeenCalledWith("book", "deleted");
  expect(callbacks.onLocate).not.toHaveBeenCalled();
});

test("saved bookmarks resolve the latest location and delete within their book", async () => {
  vi.mocked(api.listBookmarks).mockResolvedValue(page([{ bookmark_id: "bookmark", book_id: "book", chapter_index: 0, edit_id: "marked", label: "读到这里", created_at: "2026-10-12", chapter_title: "第一章", excerpt: "书签附近的文字", quote: "书签附近的文字", reader_location: { ...location, chapter_index: 0 } }]));
  const callbacks = props({ tab: "bookmarks" });
  const view = render(<ReaderToolsPanel {...callbacks} />);
  await view.findByText("读到这里");
  fireEvent.click(view.getByRole("button", { name: "回到这里" }));
  await waitFor(() => expect(callbacks.onLocate).toHaveBeenCalledWith(location, 4));
  expect(api.bookmarkLocation).toHaveBeenCalledWith("book", "bookmark");
  fireEvent.click(view.getByRole("button", { name: "删除书签：读到这里" }));
  await waitFor(() => expect(api.deleteBookmark).toHaveBeenCalledWith("book", "bookmark"));
});
