import { render } from "@testing-library/react";

import { ReaderView } from "./ReaderView";
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

function reader(paragraphs: ReaderParagraph[], contentRevision = 1) {
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
