import { ArrowLeft, ArrowRight, Bot, BookOpen, Highlighter, LoaderCircle, MessageSquarePlus, MessageSquareText, Quote, Sparkles } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";

import type { AnnotationColor, ReaderAnnotation, ReaderBook, ReaderMutation, ReaderPage, ReaderParagraph, ReaderPreferences } from "../types";

export interface TextSelection {
  text: string;
  paragraphs: number[];
  startEditId: string;
  endEditId: string;
  startOffset: number;
  endOffset: number;
}

interface Props {
  book: ReaderBook | null;
  page: ReaderPage | null;
  paragraphs: ReaderParagraph[];
  preferences: ReaderPreferences;
  loading: boolean;
  contentRevision: number;
  highlightParagraph: number | null;
  onChapter: (chapter: number) => void;
  onLoadMore: () => void;
  onProgress: (paragraph: number, progress: number) => void;
  onAskSelection: (selection: TextSelection, prompt: string) => void;
  onEdit: (mutations: ReaderMutation[], label: string) => Promise<void>;
  onAnnotate: (selection: TextSelection, color: AnnotationColor, comment: string) => Promise<void>;
  onUndo: () => Promise<void>;
}

export interface ReaderViewHandle {
  flush: () => Promise<void>;
}

type FloatingSelection = TextSelection & { x: number; y: number };

export const ReaderView = forwardRef<ReaderViewHandle, Props>(function ReaderView({ book, page, paragraphs, preferences, loading, contentRevision, highlightParagraph, onChapter, onLoadMore, onProgress, onAskSelection, onEdit, onAnnotate, onUndo }, ref) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const loadMoreRef = useRef<HTMLDivElement>(null);
  const commitTimer = useRef<number | null>(null);
  const [selection, setSelection] = useState<FloatingSelection | null>(null);
  const [comment, setComment] = useState("");

  useLayoutEffect(() => {
    const container = contentRef.current;
    if (container) container.innerHTML = renderEditableHtml(paragraphs);
  }, [contentRevision]);

  useEffect(() => {
    const container = contentRef.current;
    if (!container) return;
    container.querySelectorAll(".reader-block.is-highlighted").forEach((element) => element.classList.remove("is-highlighted"));
    if (highlightParagraph !== null) container.querySelector(`[data-paragraph="${highlightParagraph}"]`)?.classList.add("is-highlighted");
  }, [highlightParagraph, contentRevision]);

  useEffect(() => {
    if (highlightParagraph === null) return;
    requestAnimationFrame(() => document.getElementById(`paragraph-${highlightParagraph}`)?.scrollIntoView({ block: "center" }));
  }, [highlightParagraph, paragraphs]);
  useEffect(() => {
    const target = loadMoreRef.current;
    const root = scrollRef.current;
    if (!target || !root || loading || page?.next_offset === null) return;
    const observer = new IntersectionObserver(
      (entries) => { if (entries[0]?.isIntersecting) onLoadMore(); },
      { root, rootMargin: "300px" },
    );
    observer.observe(target);
    return () => observer.disconnect();
  }, [loading, page?.next_offset, onLoadMore]);
  useEffect(() => () => {
    if (commitTimer.current) window.clearTimeout(commitTimer.current);
  }, []);
  useImperativeHandle(ref, () => ({
    flush: async () => {
      if (commitTimer.current) window.clearTimeout(commitTimer.current);
      await commitEditorChanges();
    },
  }));

  function captureSelection() {
    const nativeSelection = window.getSelection();
    const text = nativeSelection?.toString().trim() ?? "";
    if (!nativeSelection || !text || nativeSelection.rangeCount === 0 || text.length > 4000) {
      setSelection(null);
      return;
    }
    const range = nativeSelection.getRangeAt(0);
    const container = contentRef.current;
    if (!container || !container.contains(range.commonAncestorContainer)) return;
    const start = elementFromNode(range.startContainer)?.closest<HTMLElement>("[data-edit-id]");
    const end = elementFromNode(range.endContainer)?.closest<HTMLElement>("[data-edit-id]");
    if (!start || !end) return;
    const startIndex = Number(start.dataset.paragraph ?? 0);
    const endIndex = Number(end.dataset.paragraph ?? startIndex);
    const rect = range.getBoundingClientRect();
    setComment("");
    setSelection({
      text,
      paragraphs: [Math.min(startIndex, endIndex), Math.max(startIndex, endIndex)],
      startEditId: start.dataset.editId ?? "",
      endEditId: end.dataset.editId ?? start.dataset.editId ?? "",
      startOffset: offsetWithin(start, range.startContainer, range.startOffset),
      endOffset: offsetWithin(end, range.endContainer, range.endOffset),
      x: Math.min(window.innerWidth - 370, Math.max(12, rect.left + rect.width / 2 - 175)),
      y: Math.max(72, rect.top - 54),
    });
  }

  function scheduleEditorCommit() {
    setSelection(null);
    if (commitTimer.current) window.clearTimeout(commitTimer.current);
    commitTimer.current = window.setTimeout(() => { void commitEditorChanges(); }, 650);
  }

  async function commitEditorChanges() {
    commitTimer.current = null;
    const container = contentRef.current;
    if (!container) return;
    const elements = new Map(
      Array.from(container.querySelectorAll<HTMLElement>("[data-edit-id]")).map((element) => [element.dataset.editId ?? "", element]),
    );
    const mutations: ReaderMutation[] = [];
    for (const paragraph of paragraphs) {
      const element = elements.get(paragraph.edit_id);
      const text = cleanEditableText(element?.innerText ?? "");
      if (!element || !text) mutations.push({ edit_id: paragraph.edit_id, text: "", deleted: true });
      else if (text !== paragraph.text) mutations.push({ edit_id: paragraph.edit_id, text, deleted: false });
    }
    if (!mutations.length) return;
    const deletedCount = mutations.filter((item) => item.deleted).length;
    const label = deletedCount > 1 ? `删除 ${deletedCount} 个段落` : deletedCount === 1 ? "删除段落" : "编辑正文";
    await onEdit(mutations, label);
  }

  function trackProgress() {
    const container = scrollRef.current;
    if (!container || !page || !paragraphs.length) return;
    const candidates = Array.from(container.querySelectorAll<HTMLElement>("[data-paragraph]"));
    const visible = candidates.find((element) => element.getBoundingClientRect().bottom > 120) ?? candidates.at(-1);
    const paragraph = Number(visible?.dataset.paragraph ?? paragraphs[0].paragraph_index);
    const chapterBase = page.chapter.chapter_index / Math.max(1, page.book.chapter_count);
    const paragraphPart = paragraph / Math.max(1, page.chapter.paragraph_count) / Math.max(1, page.book.chapter_count);
    onProgress(paragraph, Math.min(100, (chapterBase + paragraphPart) * 100));
  }

  async function addAnnotation(color: AnnotationColor) {
    if (!selection) return;
    await commitEditorChanges();
    await onAnnotate(selection, color, comment.trim());
    setSelection(null);
    setComment("");
    window.getSelection()?.removeAllRanges();
  }

  async function handleEditorKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z") return;
    event.preventDefault();
    await onUndo();
  }

  if (!book) return <div className="reader-empty"><BookOpen /><h1>选择一本书开始阅读</h1><p>从左侧书库打开书籍，阅读位置会自动保存在当前浏览器。</p></div>;

  return (
    <div className="reader-scroll" ref={scrollRef} onMouseUp={captureSelection} onScroll={trackProgress}>
      <article className={`reader-document width-${preferences.width}`} style={{ fontSize: `${preferences.fontSize}px`, lineHeight: preferences.lineHeight }}>
        <header className="chapter-header" contentEditable={false}>
          <span>{book.title}</span><h1>{page?.chapter.chapter_title ?? "正在载入章节"}</h1><p>{book.author || "作者未知"}</p>
        </header>
        <div className="reader-editable-content" contentEditable suppressContentEditableWarning spellCheck={false} ref={contentRef} onInput={scheduleEditorCommit} onKeyDown={(event) => { void handleEditorKeyDown(event); }} onKeyUp={captureSelection} />
        {loading && <div className="reader-loading" contentEditable={false}><LoaderCircle />正在载入正文</div>}
        {!loading && page?.next_offset !== null && <div className="load-more-sentinel" ref={loadMoreRef} contentEditable={false}>继续载入本章</div>}
        {page && <nav className="chapter-navigation" contentEditable={false}><button disabled={page.previous_chapter_index === null} onClick={() => page.previous_chapter_index !== null && onChapter(page.previous_chapter_index)}><ArrowLeft />上一章</button><button disabled={page.next_chapter_index === null} onClick={() => page.next_chapter_index !== null && onChapter(page.next_chapter_index)}>下一章<ArrowRight /></button></nav>}
      </article>
      {selection && (
        <div className="selection-menu annotation-menu" style={{ left: selection.x, top: selection.y }} contentEditable={false} onMouseUp={(event) => event.stopPropagation()}>
          <div className="annotation-colors"><Highlighter />{(["yellow", "green", "blue", "red"] as AnnotationColor[]).map((color) => <button className={`annotation-color color-${color}`} key={color} onClick={() => void addAnnotation(color)} title={`${color} 高亮`} />)}</div>
          <div className="annotation-comment"><MessageSquarePlus /><input value={comment} onChange={(event) => setComment(event.target.value)} placeholder="输入批注（可选）" /><button onClick={() => void addAnnotation("yellow")}>批注</button></div>
          <div className="selection-ai-actions"><button onClick={() => onAskSelection(selection, "解释这段文字")}><Sparkles />解释</button><button onClick={() => onAskSelection(selection, "总结这段文字")}><Quote />总结</button><button onClick={() => onAskSelection(selection, "结合前后文分析这段文字")}><MessageSquareText />上下文</button><button onClick={() => onAskSelection(selection, "")}><Bot />提问</button></div>
        </div>
      )}
    </div>
  );
});

function renderEditableHtml(paragraphs: ReaderParagraph[]): string {
  return paragraphs.map((paragraph) => {
    return `<p class="reader-block kind-${paragraph.kind}" data-edit-id="${escapeHtml(paragraph.edit_id)}" data-paragraph="${paragraph.paragraph_index}" id="paragraph-${paragraph.paragraph_index}">${renderAnnotatedText(paragraph)}</p>`;
  }).join("");
}

function renderAnnotatedText(paragraph: ReaderParagraph): string {
  const ranges = paragraph.annotations.map((annotation) => annotationRange(paragraph, annotation)).filter((item): item is { start: number; end: number; annotation: ReaderAnnotation } => item !== null).sort((left, right) => left.start - right.start);
  if (!ranges.length) return escapeHtml(paragraph.text);
  const output: string[] = [];
  let cursor = 0;
  for (const range of ranges) {
    if (range.start < cursor) continue;
    output.push(escapeHtml(paragraph.text.slice(cursor, range.start)));
    output.push(`<mark class="reader-annotation color-${range.annotation.color}" title="${escapeHtml(range.annotation.comment || "高亮")}" data-annotation-id="${escapeHtml(range.annotation.annotation_id)}">${escapeHtml(paragraph.text.slice(range.start, range.end))}</mark>`);
    cursor = range.end;
  }
  output.push(escapeHtml(paragraph.text.slice(cursor)));
  return output.join("");
}

function annotationRange(paragraph: ReaderParagraph, annotation: ReaderAnnotation) {
  const start = paragraph.edit_id === annotation.start_edit_id ? annotation.start_offset : 0;
  const end = paragraph.edit_id === annotation.end_edit_id ? annotation.end_offset : paragraph.text.length;
  if (end <= start || start >= paragraph.text.length) return null;
  return { start: Math.max(0, start), end: Math.min(paragraph.text.length, end), annotation };
}

function offsetWithin(element: HTMLElement, node: Node, offset: number): number {
  const range = document.createRange();
  range.selectNodeContents(element);
  try { range.setEnd(node, offset); } catch { return 0; }
  return range.toString().length;
}

function cleanEditableText(text: string): string {
  return text.replace(/\u00a0/g, " ").replace(/\r\n/g, "\n").trim();
}

function elementFromNode(node: Node): Element | null {
  return node.nodeType === Node.ELEMENT_NODE ? node as Element : node.parentElement;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
