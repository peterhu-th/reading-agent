import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";

import { LibrarySidebar } from "./LibrarySidebar";

test("renames a chapter inline from the table of contents", () => {
  const onRenameChapter = vi.fn();
  const chapter = { book_id: "book", chapter_index: 0, chapter_title: "第一章", depth: 0, paragraph_count: 2, preview: "正文" };
  render(<LibrarySidebar
    open
    books={[{ book_id: "book", title: "测试书", author: "作者", book_type: "fiction", chapter_count: 1, paragraph_count: 2 }]}
    chapters={[chapter]}
    activeBookId="book"
    activeChapter={0}
    onClose={() => undefined}
    onBook={() => undefined}
    onChapter={() => undefined}
    onRenameChapter={onRenameChapter}
    onDeleteChapter={() => undefined}
  />);

  fireEvent.click(screen.getByRole("button", { name: "目录" }));
  fireEvent.click(screen.getByRole("button", { name: "修改章节名称：第一章" }));
  const input = screen.getByRole("textbox", { name: "修改章节名称：第一章" });
  fireEvent.change(input, { target: { value: "新的第一章" } });
  fireEvent.keyDown(input, { key: "Enter" });

  expect(onRenameChapter).toHaveBeenCalledWith(chapter, "新的第一章");
});
