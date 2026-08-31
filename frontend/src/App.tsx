import { Bot, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";

import { api } from "./api";
import { ChatDrawer } from "./chat/ChatDrawer";
import { LibrarySidebar } from "./library/LibrarySidebar";
import { ReaderToolbar } from "./reader/ReaderToolbar";
import { ReaderView, type ReaderViewHandle, type TextSelection } from "./reader/ReaderView";
import type { AnnotationColor, EditorState, HealthStatus, ReaderBook, ReaderChapter, ReaderLocation, ReaderMutation, ReaderPage, ReaderParagraph, ReaderPreferences, ReaderSearchHit } from "./types";

const DEFAULT_PREFERENCES: ReaderPreferences = { fontSize: 18, lineHeight: 1.9, width: "medium", theme: "light" };
const DEFAULT_EDITOR_STATE: EditorState = { dirty: false, undo_count: 0, last_operation: "", database_updating: false, database_status: "idle", database_message: "" };

interface SavedLocation {
  chapter: number;
  paragraph: number;
  progress: number;
}

export default function App() {
  const [books, setBooks] = useState<ReaderBook[]>([]);
  const [chapters, setChapters] = useState<ReaderChapter[]>([]);
  const [book, setBook] = useState<ReaderBook | null>(null);
  const [page, setPage] = useState<ReaderPage | null>(null);
  const [paragraphs, setParagraphs] = useState<ReaderParagraph[]>([]);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [libraryOpen, setLibraryOpen] = useState(() => {
    const saved = localStorage.getItem("reader-library-open");
    if (window.innerWidth > 1180) return true;
    return saved === "true";
  });
  const [chatOpen, setChatOpen] = useState(() => localStorage.getItem("reader-chat-open") === "true");
  const [chatWidth, setChatWidth] = useState(() => Number(localStorage.getItem("reader-chat-width")) || 400);
  const [preferences, setPreferences] = useState<ReaderPreferences>(() => readJson("reader-preferences", DEFAULT_PREFERENCES));
  const [progress, setProgress] = useState(0);
  const [highlightParagraph, setHighlightParagraph] = useState<number | null>(null);
  const [selection, setSelection] = useState<TextSelection | null>(null);
  const [suggestedQuestion, setSuggestedQuestion] = useState("");
  const [searchResults, setSearchResults] = useState<ReaderSearchHit[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [editorState, setEditorState] = useState<EditorState>(DEFAULT_EDITOR_STATE);
  const [saving, setSaving] = useState(false);
  const [contentRevision, setContentRevision] = useState(0);
  const saveTimer = useRef<number | null>(null);
  const readerViewRef = useRef<ReaderViewHandle>(null);

  const activeChapter = useMemo(
    () => chapters.find((item) => item.chapter_index === page?.chapter.chapter_index) ?? page?.chapter ?? null,
    [chapters, page],
  );

  useEffect(() => { void initialize(); }, []);
  useEffect(() => {
    const timer = window.setInterval(() => { void api.health().then(setHealth).catch(() => undefined); }, 10_000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    localStorage.setItem("reader-library-open", String(libraryOpen));
    localStorage.setItem("reader-chat-open", String(chatOpen));
    localStorage.setItem("reader-chat-width", String(chatWidth));
    localStorage.setItem("reader-preferences", JSON.stringify(preferences));
  }, [libraryOpen, chatOpen, chatWidth, preferences]);
  useEffect(() => {
    const handleKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (searchResults.length) setSearchResults([]);
      else if (chatOpen) setChatOpen(false);
      else setLibraryOpen(false);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [chatOpen, searchResults]);
  useEffect(() => {
    if (!editorState.database_updating) return;
    const timer = window.setInterval(() => {
      void api.databaseUpdateState().then((state) => {
        setEditorState((current) => ({ ...current, database_updating: state.running, database_status: state.status, database_message: state.message }));
      }).catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [editorState.database_updating]);

  async function initialize() {
    try {
      const [library, currentHealth, currentEditorState] = await Promise.all([api.listBooks(), api.health(), api.editorState()]);
      setBooks(library);
      setHealth(currentHealth);
      setEditorState(currentEditorState);
      const savedBookId = localStorage.getItem("reader-active-book");
      const initial = library.find((item) => item.book_id === savedBookId) ?? library[0];
      if (initial) await openBook(initial);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法载入本地书库");
    } finally {
      setLoading(false);
    }
  }

  async function openBook(nextBook: ReaderBook, chapterOverride?: number, paragraphOverride?: number) {
    setLoading(true);
    setError("");
    try {
      const chapterList = await api.listChapters(nextBook.book_id);
      setBook(nextBook);
      setChapters(chapterList);
      localStorage.setItem("reader-active-book", nextBook.book_id);
      const saved = readJson<SavedLocation | null>(`reader-location:${nextBook.book_id}`, null);
      setProgress(saved?.progress ?? 0);
      const targetChapter = chapterOverride ?? saved?.chapter ?? chapterList[0]?.chapter_index;
      const targetParagraph = paragraphOverride ?? saved?.paragraph;
      if (targetChapter !== undefined) await loadChapter(nextBook, targetChapter, targetParagraph);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法打开书籍");
    } finally {
      setLoading(false);
    }
  }

  async function loadChapter(targetBook: ReaderBook, chapterIndex: number, focusParagraph?: number) {
    setLoading(true);
    setSearchResults([]);
    try {
      const result = await api.getChapter(targetBook.book_id, chapterIndex, 0, 80, focusParagraph);
      setPage(result);
      setParagraphs(result.paragraphs);
      setContentRevision((current) => current + 1);
      setHighlightParagraph(focusParagraph ?? null);
      if (focusParagraph !== undefined) window.setTimeout(() => setHighlightParagraph(null), 2600);
    } finally {
      setLoading(false);
    }
  }

  async function selectChapter(chapterIndex: number) {
    if (!book) return;
    await loadChapter(book, chapterIndex);
    if (window.innerWidth <= 1180) setLibraryOpen(false);
  }

  async function loadMore() {
    if (!book || !page || page.next_offset === null || loading) return;
    setLoading(true);
    try {
      const next = await api.getChapter(book.book_id, page.chapter.chapter_index, page.next_offset, 80);
      setParagraphs((current) => [...current, ...next.paragraphs]);
      setPage({ ...page, next_offset: next.next_offset });
      setContentRevision((current) => current + 1);
    } finally {
      setLoading(false);
    }
  }

  function saveProgress(paragraph: number, nextProgress: number) {
    setProgress(nextProgress);
    if (!book || !page) return;
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      localStorage.setItem(`reader-location:${book.book_id}`, JSON.stringify({ chapter: page.chapter.chapter_index, paragraph, progress: nextProgress }));
    }, 250);
  }

  async function searchChapter(query: string) {
    if (!book || !page || !query.trim()) return;
    setSearchQuery(query);
    setSearchResults(await api.searchBook(book.book_id, query.trim(), page.chapter.chapter_index));
  }

  async function jumpToSearch(hit: ReaderSearchHit) {
    if (!book) return;
    await loadChapter(book, hit.chapter_index, hit.paragraph_index);
    setSearchResults([]);
  }

  async function jumpToCitation(location: ReaderLocation) {
    const targetBook = books.find((item) => item.book_id === location.book_id);
    if (!targetBook) return;
    if (book?.book_id !== targetBook.book_id) await openBook(targetBook, location.chapter_index, location.start_paragraph_index);
    else await loadChapter(targetBook, location.chapter_index, location.start_paragraph_index);
  }

  function askSelection(nextSelection: TextSelection, prompt: string) {
    setSelection(nextSelection);
    setSuggestedQuestion(prompt);
    setChatOpen(true);
  }

  async function applyEdits(mutations: ReaderMutation[], label: string) {
    if (!book) return;
    const byId = new Map(mutations.map((item) => [item.edit_id, item]));
    setParagraphs((current) => current.flatMap((paragraph) => {
      const mutation = byId.get(paragraph.edit_id);
      if (!mutation) return [paragraph];
      return mutation.deleted ? [] : [{ ...paragraph, text: mutation.text }];
    }));
    try {
      setEditorState(await api.applyEditorOperation(book.book_id, label, mutations));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "正文修改失败");
      if (page) await loadChapter(book, page.chapter.chapter_index);
    }
  }

  async function deleteChapter(chapter: ReaderChapter) {
    if (!book) return;
    setError("");
    if (!health?.editor_capabilities?.includes("chapter_delete")) {
      setError("当前 Web 后端仍是旧版本，尚未加载章节删除接口。请停止服务后重新运行 python scripts/run_web.py。");
      return;
    }
    try {
      await readerViewRef.current?.flush();
      setEditorState(await api.deleteChapter(book.book_id, chapter.chapter_index));
      const [nextBooks, nextChapters] = await Promise.all([api.listBooks(), api.listChapters(book.book_id)]);
      const nextBook = nextBooks.find((item) => item.book_id === book.book_id) ?? book;
      setBooks(nextBooks);
      setBook(nextBook);
      setChapters(nextChapters);
      if (page?.chapter.chapter_index !== chapter.chapter_index) return;
      const replacement = nextChapters.find((item) => item.chapter_index > chapter.chapter_index) ?? nextChapters.at(-1);
      if (replacement) await loadChapter(nextBook, replacement.chapter_index);
      else {
        setPage(null);
        setParagraphs([]);
        setContentRevision((current) => current + 1);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除章节失败");
    }
  }

  async function renameChapter(chapter: ReaderChapter, title: string) {
    if (!book) return;
    setError("");
    if (!health?.editor_capabilities?.includes("chapter_rename")) {
      setError("当前 Web 后端仍是旧版本，尚未加载章节改名接口。请停止服务后重新运行 python scripts/run_web.py。");
      return;
    }
    try {
      await readerViewRef.current?.flush();
      setEditorState(await api.renameChapter(book.book_id, chapter.chapter_index, title));
      const nextChapters = await api.listChapters(book.book_id);
      setChapters(nextChapters);
      const renamed = nextChapters.find((item) => item.chapter_index === chapter.chapter_index);
      if (renamed && page?.chapter.chapter_index === chapter.chapter_index) {
        setPage((current) => current ? { ...current, chapter: renamed } : current);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "修改章节名称失败");
    }
  }

  async function addAnnotation(nextSelection: TextSelection, color: AnnotationColor, comment: string) {
    if (!book || !page) return;
    try {
      const result = await api.addAnnotation({
        book_id: book.book_id,
        chapter_index: page.chapter.chapter_index,
        start_edit_id: nextSelection.startEditId,
        end_edit_id: nextSelection.endEditId,
        start_offset: nextSelection.startOffset,
        end_offset: nextSelection.endOffset,
        selected_text: nextSelection.text,
        color,
        comment,
      });
      setEditorState(result.state);
      setParagraphs((current) => current.map((paragraph) => paragraph.paragraph_index >= nextSelection.paragraphs[0] && paragraph.paragraph_index <= nextSelection.paragraphs[1] ? { ...paragraph, annotations: [...paragraph.annotations, result.annotation] } : paragraph));
      setContentRevision((current) => current + 1);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "批注保存失败");
    }
  }

  async function undoEditor() {
    if (!book) return;
    try {
      await readerViewRef.current?.flush();
      setEditorState(await api.undoEditor());
      const [nextBooks, nextChapters] = await Promise.all([api.listBooks(), api.listChapters(book.book_id)]);
      const nextBook = nextBooks.find((item) => item.book_id === book.book_id) ?? book;
      setBooks(nextBooks);
      setBook(nextBook);
      setChapters(nextChapters);
      const currentChapterIndex = page?.chapter.chapter_index;
      const chapterIndex = currentChapterIndex !== undefined && nextChapters.some((item) => item.chapter_index === currentChapterIndex)
        ? currentChapterIndex
        : nextChapters[0]?.chapter_index;
      if (chapterIndex !== undefined) await loadChapter(nextBook, chapterIndex);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "撤销失败");
    }
  }

  async function saveEditor() {
    setSaving(true);
    try {
      await readerViewRef.current?.flush();
      setEditorState(await api.saveEditor());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function updateDatabase() {
    try {
      await readerViewRef.current?.flush();
      const state = await api.startDatabaseUpdate();
      setEditorState((current) => ({ ...current, database_updating: state.running, database_status: state.status, database_message: state.message }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法启动数据库更新");
    }
  }

  return (
    <div className={`reader-app theme-${preferences.theme} ${libraryOpen ? "library-visible" : ""} ${chatOpen ? "chat-visible" : ""}`} style={{ "--chat-width": `${chatWidth}px` } as CSSProperties}>
      <LibrarySidebar open={libraryOpen} books={books} chapters={chapters} activeBookId={book?.book_id ?? ""} activeChapter={page?.chapter.chapter_index ?? null} onClose={() => setLibraryOpen(false)} onBook={(value) => void openBook(value)} onChapter={(value) => void selectChapter(value)} onRenameChapter={(chapter, title) => void renameChapter(chapter, title)} onDeleteChapter={(value) => void deleteChapter(value)} />
      <main className="reader-main">
        <ReaderToolbar title={book?.title ?? ""} chapterTitle={activeChapter?.chapter_title ?? ""} progress={progress} preferences={preferences} chatOpen={chatOpen} editorState={editorState} saving={saving} onLibrary={() => setLibraryOpen((value) => !value)} onChat={() => setChatOpen((value) => !value)} onPreferences={setPreferences} onSearch={(query) => void searchChapter(query)} onUndo={() => void undoEditor()} onSave={() => void saveEditor()} onUpdateDatabase={() => void updateDatabase()} />
        <ReaderView ref={readerViewRef} book={book} page={page} paragraphs={paragraphs} preferences={preferences} loading={loading} contentRevision={contentRevision} highlightParagraph={highlightParagraph} onChapter={(value) => void selectChapter(value)} onLoadMore={() => void loadMore()} onProgress={saveProgress} onAskSelection={askSelection} onEdit={applyEdits} onAnnotate={addAnnotation} onUndo={undoEditor} />
        {error && <div className="global-error">{error}<button onClick={() => setError("")}><X /></button></div>}
        {editorState.database_message && editorState.database_status !== "idle" && <div className={`database-status status-${editorState.database_status}`}>{editorState.database_message}</div>}
        {!chatOpen && <button className="floating-chat" onClick={() => setChatOpen(true)} title="打开阅读助理"><Bot /></button>}
      </main>
      <ChatDrawer open={chatOpen} width={chatWidth} health={health} book={book} chapter={activeChapter} selection={selection} suggestedQuestion={suggestedQuestion} onClose={() => setChatOpen(false)} onWidth={setChatWidth} onCitation={(location) => void jumpToCitation(location)} onSelectionConsumed={() => { setSelection(null); setSuggestedQuestion(""); }} />
      {searchResults.length > 0 && <div className="search-results"><header><Search /><strong>“{searchQuery}”的结果</strong><button onClick={() => setSearchResults([])}><X /></button></header>{searchResults.map((hit) => <button key={`${hit.chapter_index}:${hit.paragraph_index}`} onClick={() => void jumpToSearch(hit)}><span>{hit.chapter_title} · 段落 {hit.paragraph_index}</span><p>{hit.excerpt}</p></button>)}</div>}
      {(libraryOpen || chatOpen) && <button className="mobile-scrim" onClick={() => { setLibraryOpen(false); setChatOpen(false); }} aria-label="关闭面板" />}
    </div>
  );
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const value = localStorage.getItem(key);
    return value ? JSON.parse(value) as T : fallback;
  } catch {
    return fallback;
  }
}
