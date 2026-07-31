import { useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpen,
  Check,
  ChevronLeft,
  CircleStop,
  FileText,
  Library,
  Menu,
  MessageSquarePlus,
  PanelRight,
  Search,
  Send,
  Settings2,
  Trash2,
  X,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api, streamChat } from "./api";
import { StatusStrip } from "./StatusStrip";
import type { Book, Citation, HealthStatus, RetrievalTrace, Session, SourceDetail, UiMessage } from "./types";

function messagesFromSession(session: Session): UiMessage[] {
  return session.turns.flatMap((turn) => [
    { role: "user" as const, text: turn.user_question },
    { role: "assistant" as const, text: turn.assistant_answer, citations: turn.citations },
  ]);
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [books, setBooks] = useState<Book[]>([]);
  const [selectedBooks, setSelectedBooks] = useState<string[]>([]);
  const [bookSearch, setBookSearch] = useState("");
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState({ stage: "", message: "" });
  const [trace, setTrace] = useState<RetrievalTrace | null>(null);
  const [debug, setDebug] = useState(false);
  const [source, setSource] = useState<SourceDetail | null>(null);
  const [error, setError] = useState("");
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [leftOpen, setLeftOpen] = useState(false);
  const [rightOpen, setRightOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const filteredBooks = useMemo(() => {
    const needle = bookSearch.trim().toLowerCase();
    return books.filter((book) => !needle || `${book.title} ${book.author}`.toLowerCase().includes(needle));
  }, [books, bookSearch]);

  useEffect(() => {
    void initialize();
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, status]);

  async function initialize() {
    try {
      const [bookList, sessionList, healthStatus] = await Promise.all([api.listBooks(), api.listSessions(), api.health()]);
      setBooks(bookList);
      setHealth(healthStatus);
      const remembered = sessionStorage.getItem("reading-active-session");
      let session = sessionList.find((item) => item.session_id === remembered) ?? sessionList[0];
      if (!session) session = await api.createSession();
      setSessions(sessionList.some((item) => item.session_id === session.session_id) ? sessionList : [session]);
      activateSession(session);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法连接后端服务");
    } finally {
      setLoading(false);
    }
  }

  function activateSession(session: Session) {
    setActiveSession(session);
    setMessages(messagesFromSession(session));
    setSelectedBooks(session.explicit_book_titles ?? []);
    setTrace(null);
    setSource(null);
    sessionStorage.setItem("reading-active-session", session.session_id);
  }

  async function newSession() {
    const session = await api.createSession();
    setSessions((current) => [session, ...current]);
    activateSession(session);
    setLeftOpen(false);
  }

  async function removeSession(id: string) {
    await api.deleteSession(id);
    const remaining = sessions.filter((item) => item.session_id !== id);
    if (activeSession?.session_id === id) {
      const next = remaining[0] ?? (await api.createSession());
      activateSession(next);
      setSessions(remaining.length ? remaining : [next]);
    } else {
      setSessions(remaining);
    }
  }

  function toggleBook(title: string) {
    setSelectedBooks((current) => current.includes(title) ? current.filter((item) => item !== title) : [...current, title]);
  }

  async function openCitation(citation: Citation) {
    try {
      setSource(await api.getSource(citation.source_id));
      setRightOpen(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取引用原文");
    }
  }

  async function submit() {
    const text = question.trim();
    if (!text || !activeSession || abortRef.current) return;
    setQuestion("");
    setError("");
    setTrace(null);
    setMessages((current) => [...current, { role: "user", text }, { role: "assistant", text: "", pending: true }]);
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamChat(
        { session_id: activeSession.session_id, question: text, selected_books: selectedBooks, debug },
        controller.signal,
        (event, data) => {
          if (event === "status") {
            setStatus({ stage: String(data.stage ?? ""), message: String(data.message ?? "") });
          } else if (event === "answer_delta") {
            const delta = String(data.text ?? "");
            setMessages((current) => current.map((item, index) => index === current.length - 1 ? { ...item, text: item.text + delta } : item));
          } else if (event === "citations") {
            const citations = data.items as Citation[];
            setMessages((current) => current.map((item, index) => index === current.length - 1 ? { ...item, citations } : item));
          } else if (event === "clarification") {
            const clarification = String(data.message ?? "请补充信息。 ");
            setMessages((current) => current.map((item, index) => index === current.length - 1 ? { ...item, text: clarification, pending: false } : item));
          } else if (event === "complete") {
            const session = data.session as unknown as Session;
            if (session) {
              setActiveSession(session);
              setSessions((current) => [session, ...current.filter((item) => item.session_id !== session.session_id)]);
            }
            if (data.trace) setTrace(data.trace as unknown as RetrievalTrace);
          } else if (event === "error") {
            const message = String(data.message ?? "生成失败");
            setError(message);
            setMessages((current) => current.map((item, index) => index === current.length - 1 ? { ...item, text: `未能生成回答：${message}`, pending: false } : item));
          }
        },
      );
    } catch (caught) {
      if (!controller.signal.aborted) {
        const message = caught instanceof Error ? caught.message : "生成失败";
        setError(message);
        setMessages((current) => current.map((item, index) => index === current.length - 1 ? { ...item, text: `未能生成回答：${message}`, pending: false } : item));
      }
    } finally {
      abortRef.current = null;
      setStatus({ stage: "", message: "" });
      setMessages((current) => current.map((item, index) => index === current.length - 1 ? { ...item, pending: false } : item));
    }
  }

  function stop() {
    abortRef.current?.abort();
    abortRef.current = null;
    setStatus({ stage: "", message: "" });
  }

  if (loading) return <div className="boot-screen"><BookOpen size={30} />正在载入书库…</div>;

  return (
    <div className="app-shell">
      <aside className={`left-panel ${leftOpen ? "is-open" : ""}`}>
        <div className="brand-row">
          <BookOpen size={22} />
          <div><strong>Reading Memory</strong><small>本地中文阅读助理</small></div>
          <button className="icon-button mobile-only" onClick={() => setLeftOpen(false)} title="关闭侧栏"><X /></button>
        </div>
        <button className="primary-command" onClick={() => void newSession()}><MessageSquarePlus size={17} />新对话</button>
        <section className="side-section session-section">
          <h2>会话</h2>
          <div className="session-list">
            {sessions.map((session) => (
              <div className={`session-row ${session.session_id === activeSession?.session_id ? "is-active" : ""}`} key={session.session_id}>
                <button onClick={() => activateSession(session)}>{session.title}</button>
                <button className="icon-button subtle" onClick={() => void removeSession(session.session_id)} title="删除会话"><Trash2 size={14} /></button>
              </div>
            ))}
          </div>
        </section>
        <section className="side-section library-section">
          <div className="section-heading"><h2>书库</h2><span>{books.length}</span></div>
          <label className="search-field"><Search size={15} /><input value={bookSearch} onChange={(event) => setBookSearch(event.target.value)} placeholder="搜索书名或作者" /></label>
          <div className="book-list">
            {filteredBooks.map((book) => (
              <label className="book-row" key={book.book_id}>
                <input type="checkbox" checked={selectedBooks.includes(book.title)} onChange={() => toggleBook(book.title)} />
                <span className="custom-check">{selectedBooks.includes(book.title) && <Check size={12} />}</span>
                <span><strong>{book.title}</strong><small>{book.author || "作者未知"} · {book.chapter_count} 章</small></span>
              </label>
            ))}
          </div>
        </section>
      </aside>

      <main className="chat-panel">
        <header className="topbar">
          <button className="icon-button mobile-only" onClick={() => setLeftOpen(true)} title="打开侧栏"><Menu /></button>
          <div><h1>{activeSession?.title ?? "新对话"}</h1><p>{selectedBooks.length ? `已限定 ${selectedBooks.length} 本书` : "检索全部书库"}</p></div>
          <div className="top-actions">
            <label className="debug-toggle" title="显示检索轮次和证据检查"><input type="checkbox" checked={debug} onChange={(event) => setDebug(event.target.checked)} /><Settings2 size={16} />调试</label>
            <button className="icon-button" onClick={() => setRightOpen(true)} title="打开证据面板"><PanelRight /></button>
          </div>
        </header>

        <div className="message-scroll">
          {!messages.length && (
            <div className="empty-state">
              <Library size={34} />
              <h2>从你的书库开始提问</h2>
              <div className="suggestions">
                {["《荒原狼》主要讲了什么？", "比较两本书对孤独的理解", "推荐能回应焦虑状态的书中片段"].map((item) => <button key={item} onClick={() => setQuestion(item)}>{item}</button>)}
              </div>
            </div>
          )}
          {messages.map((message, index) => (
            <article className={`message ${message.role}`} key={`${message.role}-${index}`}>
              <div className="message-label">{message.role === "user" ? "你" : "阅读助理"}</div>
              <div className="message-body">
                {message.text ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown> : message.pending ? <div className="typing"><i /><i /><i /></div> : <p>本轮没有生成内容。</p>}
              </div>
              {!!message.citations?.length && (
                <div className="citation-list">
                  {message.citations.map((citation) => <button key={citation.source_id} onClick={() => void openCitation(citation)}><FileText size={14} />[{citation.display_index}] {citation.title} · {citation.chapter_title}</button>)}
                </div>
              )}
            </article>
          ))}
          {status.stage && <StatusStrip stage={status.stage} message={status.message} />}
          {health && !health.answer_provider_ready && (
            <div className="service-warning">回答服务没有可用节点。请在 AIClient2API 中重新登录或启用健康节点；本地书库和检索索引仍然可用。</div>
          )}
          {error && <div className="error-banner">{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
          {debug && trace && <DebugTrace trace={trace} />}
          <div ref={bottomRef} />
        </div>

        <div className="composer-wrap">
          <div className="selected-books">
            {selectedBooks.map((title) => <button key={title} onClick={() => toggleBook(title)}>《{title}》<X size={12} /></button>)}
          </div>
          <div className="composer">
            <textarea value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder="询问情节、人物、主题，或比较多本书…" rows={2} />
            {abortRef.current ? <button className="send-button stop" onClick={stop} title="停止生成"><CircleStop /></button> : <button className="send-button" disabled={!question.trim()} onClick={() => void submit()} title="发送"><Send /></button>}
          </div>
          <small>回答仅依据当前本地书库，引用可展开核对原文。</small>
        </div>
      </main>

      <aside className={`right-panel ${rightOpen ? "is-open" : ""}`}>
        <div className="evidence-header">
          <button className="icon-button mobile-only" onClick={() => setRightOpen(false)} title="返回"><ChevronLeft /></button>
          <div><h2>原文证据</h2><p>点击回答下方引用查看</p></div>
          <button className="icon-button" onClick={() => setSource(null)} title="清除当前证据"><X /></button>
        </div>
        {source ? (
          <div className="source-view">
            <div className="source-meta"><span>{source.title}</span><strong>{source.chapter_title || `第 ${source.chapter_index} 章`}</strong><small>{source.author || "作者未知"} · 段落 {source.paragraph_range}</small></div>
            <p>{source.text}</p>
          </div>
        ) : (
          <div className="source-empty"><FileText size={30} /><p>选择一条引用后，这里会显示对应的完整 chunk 原文。</p></div>
        )}
      </aside>
      {(leftOpen || rightOpen) && <button className="mobile-scrim" onClick={() => { setLeftOpen(false); setRightOpen(false); }} aria-label="关闭面板" />}
    </div>
  );
}

function DebugTrace({ trace }: { trace: RetrievalTrace }) {
  return (
    <details className="debug-trace">
      <summary>检索调试 · {trace.rounds.length} 轮 · 证据{trace.evidence_sufficient ? "充分" : "仍有缺口"}</summary>
      {trace.rounds.map((round) => <p key={round.round_index}>第 {round.round_index} 轮：新增 {round.new_chunk_count} 条，{round.stop_reason || "继续检查"}</p>)}
      {trace.missing_aspects.map((item) => <p key={item}>缺口：{item}</p>)}
      {trace.debug_lines.map((line) => <code key={line}>{line}</code>)}
    </details>
  );
}
