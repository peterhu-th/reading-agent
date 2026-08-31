import { BookOpen, Check, ChevronLeft, Library, List, Pencil, Search, Trash2, X } from "lucide-react";
import { useMemo, useState } from "react";

import type { ReaderBook, ReaderChapter } from "../types";

interface Props {
  open: boolean;
  books: ReaderBook[];
  chapters: ReaderChapter[];
  activeBookId: string;
  activeChapter: number | null;
  onClose: () => void;
  onBook: (book: ReaderBook) => void;
  onChapter: (chapter: number) => void;
  onRenameChapter: (chapter: ReaderChapter, title: string) => void;
  onDeleteChapter: (chapter: ReaderChapter) => void;
}

export function LibrarySidebar({ open, books, chapters, activeBookId, activeChapter, onClose, onBook, onChapter, onRenameChapter, onDeleteChapter }: Props) {
  const [tab, setTab] = useState<"library" | "toc">("library");
  const [query, setQuery] = useState("");
  const [editingChapter, setEditingChapter] = useState<number | null>(null);
  const [chapterTitle, setChapterTitle] = useState("");
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return books.filter((book) => !needle || `${book.title} ${book.author}`.toLowerCase().includes(needle));
  }, [books, query]);

  function startRename(chapter: ReaderChapter) {
    setEditingChapter(chapter.chapter_index);
    setChapterTitle(chapter.chapter_title);
  }

  function finishRename(chapter: ReaderChapter) {
    const title = chapterTitle.trim();
    if (title && title !== chapter.chapter_title) onRenameChapter(chapter, title);
    setEditingChapter(null);
    setChapterTitle("");
  }

  return (
    <aside className={`library-sidebar ${open ? "is-open" : ""}`}>
      <header className="library-brand">
        <BookOpen size={21} />
        <div><strong>Reading Memory</strong><small>本地中文阅读器</small></div>
        <button className="icon-button sidebar-close" onClick={onClose} title="隐藏书库"><ChevronLeft /></button>
      </header>
      <div className="sidebar-tabs" role="tablist">
        <button className={tab === "library" ? "is-active" : ""} onClick={() => setTab("library")}><Library />书库</button>
        <button className={tab === "toc" ? "is-active" : ""} onClick={() => setTab("toc")} disabled={!activeBookId}><List />目录</button>
      </div>
      {tab === "library" ? (
        <>
          <label className="sidebar-search"><Search /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索书名或作者" />{query && <button onClick={() => setQuery("")} title="清除"><X /></button>}</label>
          <div className="book-list">
            {filtered.map((book) => (
              <button className={`book-item ${book.book_id === activeBookId ? "is-active" : ""}`} key={book.book_id} onClick={() => { onBook(book); setTab("toc"); }}>
                <strong>{book.title}</strong>
                <span>{book.author || "作者未知"}</span>
                <small>{book.chapter_count} 章 · {book.paragraph_count.toLocaleString()} 段</small>
              </button>
            ))}
          </div>
        </>
      ) : (
        <div className="chapter-list">
          {chapters.map((chapter) => (
            <div className={`chapter-item ${chapter.chapter_index === activeChapter ? "is-active" : ""}`} key={chapter.chapter_index}>
              {editingChapter === chapter.chapter_index ? (
                <input className="chapter-rename-input" autoFocus value={chapterTitle} onChange={(event) => setChapterTitle(event.target.value)} onKeyDown={(event) => {
                  if (event.key === "Enter") finishRename(chapter);
                  if (event.key === "Escape") setEditingChapter(null);
                }} aria-label={`修改章节名称：${chapter.chapter_title}`} />
              ) : (
                <button className="chapter-open" style={{ paddingLeft: `${10 + Math.min(chapter.depth, 3) * 12}px` }} onClick={() => onChapter(chapter.chapter_index)} onDoubleClick={() => startRename(chapter)}>
                  <span>{chapter.chapter_title}</span><small>{chapter.preview}</small>
                </button>
              )}
              <div className="chapter-actions">
                {editingChapter === chapter.chapter_index ? (
                  <><button className="chapter-action chapter-rename-save" aria-label="保存章节名称" title="保存名称" onClick={() => finishRename(chapter)}><Check /></button><button className="chapter-action" aria-label="取消修改章节名称" title="取消" onClick={() => setEditingChapter(null)}><X /></button></>
                ) : (
                  <><button className="chapter-action" aria-label={`修改章节名称：${chapter.chapter_title}`} title="修改章节名称" onClick={() => startRename(chapter)}><Pencil /></button><button className="chapter-action chapter-delete" aria-label={`删除章节：${chapter.chapter_title}`} title="删除章节（可撤销）" onClick={() => onDeleteChapter(chapter)}><Trash2 /></button></>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}
