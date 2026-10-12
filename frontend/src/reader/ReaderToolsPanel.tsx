import { Bookmark, Download, Highlighter, MapPin, Pencil, Search, Trash2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { AnnotationColor, EditorState, NotesExportFormat, ReaderAnnotation, ReaderAnnotationItem, ReaderBook, ReaderBookmarkItem, ReaderLocation, ReaderResultPage, ReaderSearchHit } from "../types";
import { downloadFile } from "./download";
import { HighlightedText } from "./HighlightedText";

export type ReaderToolTab = "annotations" | "bookmarks" | "search";
export interface BookmarkTarget { chapter_index: number; edit_id: string }
export interface NoteMutationResult { state: EditorState; annotation?: ReaderAnnotation; deletedAnnotationId?: string }

interface Props {
  book: ReaderBook;
  chapterIndex: number | null;
  tab: ReaderToolTab;
  revision: number;
  disabled: boolean;
  dirty: boolean;
  onTab: (tab: ReaderToolTab) => void;
  onClose: () => void;
  onFlush: () => Promise<number>;
  onLocate: (location: ReaderLocation, navigationVersion: number) => Promise<void>;
  onMutation: (action: (current: BookmarkTarget | null) => Promise<NoteMutationResult>) => Promise<void>;
}

type ToolResults =
  | { kind: "annotations"; page: ReaderResultPage<ReaderAnnotationItem> }
  | { kind: "bookmarks"; page: ReaderResultPage<ReaderBookmarkItem> }
  | { kind: "search"; page: ReaderResultPage<ReaderSearchHit> };

const PAGE_SIZE = 50;

export function ReaderToolsPanel(props: Props) {
  const { book, chapterIndex, tab, revision, disabled, dirty, onTab, onClose } = props;
  const callbacks = useRef(props);
  callbacks.current = props;
  const [results, setResults] = useState<ToolResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [failedOffset, setFailedOffset] = useState(0);
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [scope, setScope] = useState<"book" | "chapter">("book");
  const [label, setLabel] = useState("");
  const [editing, setEditing] = useState<ReaderAnnotationItem | null>(null);
  const [comment, setComment] = useState("");
  const [color, setColor] = useState<AnnotationColor>("yellow");
  const requestNumber = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const actionPending = useRef(false);
  const loadRef = useRef(load);
  loadRef.current = load;
  const searchChapter = tab === "search" && scope === "chapter" ? chapterIndex : null;

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; controller.current?.abort(); requestNumber.current++; };
  }, []);

  useEffect(() => {
    setEditing(null);
    setActionError("");
    void loadRef.current(0);
    return () => { controller.current?.abort(); requestNumber.current++; };
  }, [tab, revision, submittedQuery, scope, searchChapter]);

  async function load(offset: number) {
    controller.current?.abort();
    const request = ++requestNumber.current;
    const abort = new AbortController();
    controller.current = abort;
    setError("");
    setFailedOffset(offset);
    if (offset === 0) setResults(null);
    if (tab === "search" && (!submittedQuery || (scope === "chapter" && chapterIndex === null))) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      await callbacks.current.onFlush();
      if (abort.signal.aborted) return;
      let next: ToolResults;
      if (tab === "annotations") next = { kind: tab, page: await api.listAnnotations(book.book_id, offset, PAGE_SIZE, abort.signal) };
      else if (tab === "bookmarks") next = { kind: tab, page: await api.listBookmarks(book.book_id, offset, PAGE_SIZE, abort.signal) };
      else next = { kind: tab, page: await api.searchBook(book.book_id, submittedQuery, scope === "chapter" ? chapterIndex ?? undefined : undefined, offset, PAGE_SIZE, abort.signal) };
      if (request !== requestNumber.current) return;
      setResults((previous) => {
        if (!offset || !previous || previous.kind !== next.kind) return next;
        // Each branch keeps the paginated collection tied to its own endpoint.
        if (next.kind === "annotations" && previous.kind === "annotations") return { ...next, page: { ...next.page, items: [...previous.page.items, ...next.page.items] } };
        if (next.kind === "bookmarks" && previous.kind === "bookmarks") return { ...next, page: { ...next.page, items: [...previous.page.items, ...next.page.items] } };
        if (next.kind === "search" && previous.kind === "search") return { ...next, page: { ...next.page, items: [...previous.page.items, ...next.page.items] } };
        return next;
      });
    } catch (caught) {
      if (request === requestNumber.current && !abort.signal.aborted) setError(messageOf(caught));
    } finally {
      if (request === requestNumber.current) setLoading(false);
    }
  }

  async function act(action: () => Promise<void>) {
    if (actionPending.current || disabled) return;
    actionPending.current = true;
    setBusy(true);
    setActionError("");
    try { await action(); }
    catch (caught) { if (alive.current) setActionError(messageOf(caught)); }
    finally { actionPending.current = false; if (alive.current) setBusy(false); }
  }

  async function locate(kind: "annotations" | "bookmarks", id: string) {
    await act(async () => {
      const navigationVersion = await callbacks.current.onFlush();
      const location = kind === "annotations" ? await api.annotationLocation(book.book_id, id) : await api.bookmarkLocation(book.book_id, id);
      if (alive.current) await callbacks.current.onLocate(location, navigationVersion);
    });
  }

  async function exportNotes(format: NotesExportFormat) {
    await act(async () => {
      await callbacks.current.onFlush();
      const file = await api.exportNotes(book.book_id, format);
      if (alive.current) downloadFile(file.blob, file.filename);
    });
  }

  const unavailable = disabled || busy;
  const current = results?.kind === tab ? results.page : null;

  return <aside className="reader-tools" aria-label="阅读工具">
    <header className="tools-header"><div><strong>阅读工具</strong><small>{book.title}</small></div><button className="icon-button" aria-label="关闭阅读工具" onClick={onClose}><X /></button></header>
    <div className="tools-tabs" role="tablist" aria-label="阅读工具分类">
      <button role="tab" aria-selected={tab === "annotations"} onClick={() => onTab("annotations")}><Highlighter />批注</button>
      <button role="tab" aria-selected={tab === "bookmarks"} onClick={() => onTab("bookmarks")}><Bookmark />书签</button>
      <button role="tab" aria-selected={tab === "search"} onClick={() => onTab("search")}><Search />搜索</button>
    </div>
    <div className="tools-body" role="tabpanel" aria-label={tab === "annotations" ? "批注列表" : tab === "bookmarks" ? "书签列表" : "关键词搜索"}>
      {tab === "search" && <form onSubmit={(event) => { event.preventDefault(); const value = query.trim(); if (!value) return; if (value === submittedQuery) void load(0); else setSubmittedQuery(value); }}>
        <input aria-label="搜索关键词" placeholder="输入书中的关键词" maxLength={200} value={query} onChange={(event) => setQuery(event.target.value)} />
        <select aria-label="搜索范围" value={scope} onChange={(event) => setScope(event.target.value as "book" | "chapter")}><option value="book">全书</option><option value="chapter" disabled={chapterIndex === null}>本章</option></select>
        <button type="submit" disabled={unavailable || !query.trim()}>查找</button>
      </form>}
      {tab === "bookmarks" && <><form onSubmit={(event) => {
        event.preventDefault();
        void act(async () => {
          await callbacks.current.onMutation(async (position) => {
            if (!position) throw new Error("当前没有可添加书签的正文。");
            return api.addBookmark(book.book_id, { ...position, label: label.trim() });
          });
          if (alive.current) setLabel("");
        });
      }}><input aria-label="书签名称" placeholder="书签名称（可选）" maxLength={200} value={label} onChange={(event) => setLabel(event.target.value)} /><button type="submit" disabled={unavailable || chapterIndex === null}>添加当前位置</button></form><p className="tools-hint">书签记录当前可见的正文位置。</p></>}
      {actionError && <div className="tools-error" role="alert">{actionError}</div>}
      {error && <div className="tools-error" role="alert">{error}<button disabled={loading || unavailable} onClick={() => void load(failedOffset)}>重试</button></div>}
      {current && <p className="tools-count">{tab === "search" ? `“${submittedQuery}” · ` : ""}共 {current.total} {tab === "search" ? "处匹配" : "条"}</p>}
      {results?.kind === "annotations" && tab === "annotations" && <ul className="tools-list">{results.page.items.map((note) => <li className="tool-note" key={note.annotation_id}>
        <div className="tool-note-heading"><i className={`note-color color-${note.color}`} /><span>{note.chapter_title || "未命名章节"}</span><time>{shortDate(note.created_at)}</time></div>
        <blockquote>{note.quote || note.selected_text || note.excerpt || "原文已不可用"}</blockquote>
        {editing?.annotation_id === note.annotation_id ? <form onSubmit={(event) => {
          event.preventDefault();
          void act(async () => {
            await callbacks.current.onMutation(() => api.updateAnnotation(book.book_id, note.annotation_id, { comment: comment.trim(), color }));
            if (alive.current) setEditing(null);
          });
        }}><textarea aria-label="修改批注" maxLength={10000} value={comment} onChange={(event) => setComment(event.target.value)} /><select aria-label="批注颜色" value={color} onChange={(event) => setColor(event.target.value as AnnotationColor)}><option value="yellow">黄色</option><option value="green">绿色</option><option value="blue">蓝色</option><option value="red">红色</option></select><button type="submit" disabled={unavailable}>应用修改</button><button type="button" onClick={() => setEditing(null)}>取消</button></form> : note.comment && <p>{note.comment}</p>}
        {!note.reader_location && <p className="tools-hint">原文位置已失效，仍可保留或导出笔记。</p>}
        <div className="tool-note-actions"><button disabled={unavailable || !note.reader_location} onClick={() => void locate("annotations", note.annotation_id)}><MapPin />定位原文</button><button disabled={unavailable} onClick={() => { setEditing(note); setComment(note.comment); setColor(note.color); }}><Pencil />编辑</button><button aria-label={`删除批注：${note.comment || note.selected_text}`} disabled={unavailable} onClick={() => void act(() => callbacks.current.onMutation(async () => ({ state: await api.deleteAnnotation(book.book_id, note.annotation_id), deletedAnnotationId: note.annotation_id })))}><Trash2 />删除</button></div>
      </li>)}</ul>}
      {results?.kind === "bookmarks" && tab === "bookmarks" && <ul className="tools-list">{results.page.items.map((bookmark) => <li className="tool-note" key={bookmark.bookmark_id}>
        <div className="tool-note-heading"><span>{bookmark.chapter_title || "未命名章节"}</span><time>{shortDate(bookmark.created_at)}</time></div>
        <strong>{bookmark.label || "阅读书签"}</strong><p>{bookmark.excerpt || "原文已不可用"}</p>
        <div className="tool-note-actions"><button disabled={unavailable || !bookmark.reader_location} onClick={() => void locate("bookmarks", bookmark.bookmark_id)}><MapPin />回到这里</button><button aria-label={`删除书签：${bookmark.label || "阅读书签"}`} disabled={unavailable} onClick={() => void act(() => callbacks.current.onMutation(async () => ({ state: await api.deleteBookmark(book.book_id, bookmark.bookmark_id) })))}><Trash2 />删除</button></div>
      </li>)}</ul>}
      {results?.kind === "search" && tab === "search" && <ul className="tools-list">{results.page.items.map((hit) => <li key={hit.edit_id}><button className="tool-search-hit" disabled={unavailable} onClick={() => void act(async () => {
        const navigationVersion = await callbacks.current.onFlush();
        const location = await api.paragraphLocation(book.book_id, hit.edit_id);
        if (alive.current) await callbacks.current.onLocate(location, navigationVersion);
      })}><span>{hit.chapter_title || "未命名章节"} · 段落 {hit.paragraph_index + 1}</span><p><HighlightedText text={hit.excerpt} query={submittedQuery} /></p></button></li>)}</ul>}
      {loading && <p className="tools-loading" role="status">正在加载…</p>}
      {!loading && !error && current?.total === 0 && <p className="tools-empty">{tab === "annotations" ? "还没有批注。选中正文即可添加高亮和笔记。" : tab === "bookmarks" ? "还没有书签。标记当前位置，方便下次继续阅读。" : "没有找到匹配的正文，请换一个关键词。"}</p>}
      {tab === "search" && !submittedQuery && <p className="tools-empty">输入关键词，查找这本书的全部正文。</p>}
      {current?.next_offset !== null && current?.next_offset !== undefined && !error && <button className="tools-more" disabled={loading || unavailable} onClick={() => void load(current.next_offset!)}>加载更多</button>}
    </div>
    <footer className="tools-footer"><span>导出本书笔记</span><button disabled={unavailable} onClick={() => void exportNotes("markdown")}><Download />Markdown</button><button disabled={unavailable} onClick={() => void exportNotes("json")}><Download />JSON</button><small>{dirty ? "有未保存修改。导出包含当前内容；点击顶部“保存”以持久保存批注和书签。" : "批注和书签支持顶部撤销；修改后请点击“保存”。"}</small></footer>
  </aside>;
}

function shortDate(value: string) { return value.slice(0, 10); }
function messageOf(error: unknown) { return error instanceof Error ? error.message : "操作失败，请重试。"; }
