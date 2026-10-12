import { act, fireEvent, render } from "@testing-library/react";
import { createRef, type ComponentProps } from "react";

import { ReaderView, type ReaderViewHandle } from "./ReaderView";
import type { ReaderBook, ReaderPage, ReaderParagraph } from "../types";

const book: ReaderBook = {
  book_id: "book",
  title: "测试书",
  author: "作者",
  book_type: "fiction",
  chapter_count: 1,
  paragraph_count: 1,
};

const paragraph: ReaderParagraph = {
  paragraph_index: 0,
  edit_id: "paragraph-0",
  text: "原始正文",
  kind: "paragraph",
  annotations: [],
};

const page: ReaderPage = {
  book,
  chapter: { book_id: "book", chapter_index: 0, chapter_title: "第一章", depth: 0, paragraph_count: 1, preview: "" },
  paragraphs: [paragraph],
  offset: 0,
  next_offset: null,
  previous_chapter_index: null,
  next_chapter_index: null,
};

function reader(paragraphs: ReaderParagraph[], contentRevision = 1, overrides: Partial<ComponentProps<typeof ReaderView>> = {}) {
  return <ReaderView
    book={book}
    page={{ ...page, paragraphs }}
    paragraphs={paragraphs}
    preferences={{ fontSize: 18, lineHeight: 1.9, width: "medium", theme: "light" }}
    loading={false}
    contentRevision={contentRevision}
    highlightParagraph={null}
    onChapter={() => undefined}
    onLoadMore={() => undefined}
    onProgress={() => undefined}
    onAskSelection={() => undefined}
    onEdit={async () => undefined}
    onAnnotate={async () => undefined}
    onUndo={async () => undefined}
    {...overrides}
  />;
}

test("preserves the browser selection during ordinary edit state updates", () => {
  const view = render(reader([paragraph]));
  const editable = view.container.querySelector<HTMLElement>(".reader-editable-content");
  const block = editable?.querySelector<HTMLElement>(".reader-block");
  if (!block) throw new Error("missing editable paragraph");
  block.textContent = "连续删除中的正文";
  const textNode = block.firstChild;
  if (!textNode) throw new Error("missing text node");
  const range = document.createRange();
  range.setStart(textNode, 4);
  range.collapse(true);
  window.getSelection()?.removeAllRanges();
  window.getSelection()?.addRange(range);

  view.rerender(reader([{ ...paragraph, text: "连续删除中的正文" }], 1));

  expect(view.container.querySelector(".reader-block")).toBe(block);
  expect(window.getSelection()?.anchorNode).toBe(textNode);
  expect(window.getSelection()?.anchorOffset).toBe(4);

  view.rerender(reader([{ ...paragraph, text: "撤销后的正文" }], 2));
  expect(view.container.querySelector(".reader-block")?.textContent).toBe("撤销后的正文");
});

afterEach(() => vi.useRealTimers());

test("emoji selections send Unicode code-point offsets and render the same annotated text", async () => {
  const onAnnotate = vi.fn().mockResolvedValue(undefined);
  const item = { ...paragraph, text: "甲😀乙尾" };
  const view = render(reader([item], 1, { onAnnotate }));
  const block = view.container.querySelector<HTMLElement>(".reader-block")!;
  Object.defineProperty(block, "innerText", { configurable: true, get: () => block.textContent });
  const range = document.createRange();
  range.setStart(block.firstChild!, 1);
  range.setEnd(block.firstChild!, 4);
  range.getBoundingClientRect = () => ({ left: 100, top: 200, width: 100 }) as DOMRect;
  window.getSelection()?.removeAllRanges();
  window.getSelection()?.addRange(range);
  fireEvent.mouseUp(view.container.querySelector(".reader-scroll")!);
  fireEvent.click(view.getByRole("button", { name: /^批注$/ }));
  await act(async () => undefined);
  expect(onAnnotate).toHaveBeenCalledWith(expect.objectContaining({ text: "😀乙", startOffset: 1, endOffset: 3 }), "yellow", "");
  view.rerender(reader([{ ...item, annotations: [{
    annotation_id: "emoji", book_id: "book", chapter_index: 0, start_edit_id: item.edit_id, end_edit_id: item.edit_id,
    start_offset: 1, end_offset: 3, selected_text: "😀乙", color: "yellow", comment: "", created_at: "", updated_at: "",
  }] }], 2));
  expect(view.container.querySelector("mark")).toHaveTextContent("😀乙");
  expect(view.container.querySelector(".reader-block")).toHaveTextContent("甲😀乙尾");
});

test("the bookmark target is the first visible paragraph rather than the first loaded paragraph", () => {
  const ref = createRef<ReaderViewHandle>();
  const next = { ...paragraph, edit_id: "visible", paragraph_index: 1, text: "当前屏幕中的正文" };
  const view = render(reader([paragraph, next], 1, { ref }));
  const blocks = view.container.querySelectorAll<HTMLElement>(".reader-block");
  blocks[0].getBoundingClientRect = () => ({ bottom: 10 }) as DOMRect;
  blocks[1].getBoundingClientRect = () => ({ bottom: 200 }) as DOMRect;
  expect(ref.current?.currentParagraph()).toEqual(next);
});

function edit(view: ReturnType<typeof render>, text: string) {
  const block = view.container.querySelector<HTMLElement>(".reader-block")!;
  Object.defineProperty(block, "innerText", { configurable: true, get: () => block.textContent });
  block.textContent = text;
  fireEvent.input(view.container.querySelector(".reader-editable-content")!);
}

test("cancels a scheduled commit when its chapter DOM is replaced", async () => {
  vi.useFakeTimers();
  const onEdit = vi.fn().mockResolvedValue(undefined);
  const view = render(reader([paragraph], 1, { onEdit }));
  edit(view, "旧章尚未提交的输入");

  const next = { ...paragraph, edit_id: "chapter-1-paragraph-0", text: "下一章" };
  view.rerender(reader([next], 2, { onEdit, page: { ...page, chapter: { ...page.chapter, chapter_index: 1 }, paragraphs: [next] } }));
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });

  expect(onEdit).not.toHaveBeenCalled();
  expect(view.container.querySelector(".reader-block")?.textContent).toBe("下一章");
});

test("flush waits for an in-flight edit and submits newer input in order to the same chapter", async () => {
  vi.useFakeTimers();
  let finishFirst!: () => void;
  const first = new Promise<void>((resolve) => { finishFirst = resolve; });
  const onEdit = vi.fn().mockImplementationOnce(() => first).mockResolvedValue(undefined);
  const ref = createRef<ReaderViewHandle>();
  const view = render(reader([paragraph], 1, { ref, onEdit }));
  edit(view, "第一次修改");
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  edit(view, "请求完成前的第二次修改");
  let flushed = false;
  const pending = ref.current!.flush().then(() => { flushed = true; });
  expect(flushed).toBe(false);
  expect(onEdit).toHaveBeenCalledTimes(1);

  await act(async () => { finishFirst(); await pending; });
  expect(flushed).toBe(true);
  expect(onEdit).toHaveBeenNthCalledWith(1, { bookId: "book", chapterIndex: 0 }, [{ edit_id: paragraph.edit_id, text: "第一次修改", deleted: false }], "编辑正文");
  expect(onEdit).toHaveBeenNthCalledWith(2, { bookId: "book", chapterIndex: 0 }, [{ edit_id: paragraph.edit_id, text: "请求完成前的第二次修改", deleted: false }], "编辑正文");
  await act(async () => { await vi.advanceTimersByTimeAsync(650); });
  expect(onEdit).toHaveBeenCalledTimes(2);
});

test("failed edits retain their draft and can be retried by flush", async () => {
  vi.useFakeTimers();
  const onEdit = vi.fn().mockRejectedValueOnce(new Error("connection failed")).mockResolvedValue(undefined);
  const ref = createRef<ReaderViewHandle>();
  const view = render(reader([paragraph], 1, { ref, onEdit }));
  edit(view, "需要重试的正文");
  await expect(ref.current!.flush()).rejects.toThrow("connection failed");
  expect(view.container.querySelector(".reader-block")?.textContent).toBe("需要重试的正文");
  await ref.current!.flush();
  expect(onEdit).toHaveBeenCalledTimes(2);
  expect(onEdit.mock.calls[1][1]).toEqual([{ edit_id: paragraph.edit_id, text: "需要重试的正文", deleted: false }]);
});
