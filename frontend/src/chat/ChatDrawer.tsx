import { Bot, CircleStop, Maximize2, MessageSquarePlus, Send, Settings2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api, streamChat } from "../api";
import { StatusStrip } from "../StatusStrip";
import type { ChatScope, Citation, HealthStatus, ReaderBook, ReaderChapter, ReaderLocation, RetrievalTrace, Session, UiMessage } from "../types";
import type { TextSelection } from "../reader/ReaderView";

interface Props {
  open: boolean;
  width: number;
  health: HealthStatus | null;
  book: ReaderBook | null;
  chapter: ReaderChapter | null;
  selection: TextSelection | null;
  suggestedQuestion: string;
  onClose: () => void;
  onWidth: (width: number) => void;
  onCitation: (location: ReaderLocation) => void;
  onSelectionConsumed: () => void;
}

function sessionMessages(session: Session): UiMessage[] {
  return session.turns.flatMap((turn) => [
    { role: "user" as const, text: turn.user_question },
    { role: "assistant" as const, text: turn.assistant_answer, citations: turn.citations },
  ]);
}

export function ChatDrawer({ open, width, health, book, chapter, selection, suggestedQuestion, onClose, onWidth, onCitation, onSelectionConsumed }: Props) {
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState<ChatScope>("book");
  const [status, setStatus] = useState({ stage: "", message: "" });
  const [trace, setTrace] = useState<RetrievalTrace | null>(null);
  const [debug, setDebug] = useState(false);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => { void initializeSession(); }, []);
  useEffect(() => { if (suggestedQuestion) setQuestion(suggestedQuestion); }, [suggestedQuestion]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, status]);

  async function initializeSession() {
    const sessions = await api.listSessions();
    const remembered = sessionStorage.getItem("reading-chat-session");
    const current = sessions.find((item) => item.session_id === remembered) ?? sessions[0] ?? await api.createSession();
    setSession(current);
    setMessages(sessionMessages(current));
    sessionStorage.setItem("reading-chat-session", current.session_id);
  }

  async function newSession() {
    const created = await api.createSession();
    setSession(created);
    setMessages([]);
    setTrace(null);
    sessionStorage.setItem("reading-chat-session", created.session_id);
  }

  async function submit() {
    const text = question.trim();
    if (!text || !session || abortRef.current) return;
    setQuestion("");
    setError("");
    setMessages((current) => [...current, { role: "user", text }, { role: "assistant", text: "", pending: true }]);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamChat({
        session_id: session.session_id,
        question: text,
        selected_books: scope === "library" || !book ? [] : [book.title],
        scope,
        book_id: book?.book_id ?? "",
        book_title: book?.title ?? "",
        chapter_index: scope === "chapter" ? chapter?.chapter_index ?? null : null,
        selected_text: selection?.text ?? "",
        selected_paragraphs: selection?.paragraphs ?? [],
        debug,
      }, controller.signal, (event, data) => {
        if (event === "status") setStatus({ stage: String(data.stage ?? ""), message: String(data.message ?? "") });
        if (event === "answer_delta") appendAnswer(String(data.text ?? ""));
        if (event === "citations") setCitations(data.items as Citation[]);
        if (event === "clarification") appendAnswer(String(data.message ?? "请补充信息。"), true);
        if (event === "complete") {
          if (data.session) setSession(data.session as unknown as Session);
          if (data.trace) setTrace(data.trace as unknown as RetrievalTrace);
        }
        if (event === "error") failAnswer(String(data.message ?? "生成失败"));
      });
      onSelectionConsumed();
    } catch (caught) {
      if (!controller.signal.aborted) failAnswer(caught instanceof Error ? caught.message : "生成失败");
    } finally {
      abortRef.current = null;
      setStatus({ stage: "", message: "" });
      setMessages((current) => current.map((item, index) => index === current.length - 1 ? { ...item, pending: false } : item));
    }
  }

  function appendAnswer(text: string, replace = false) {
    setMessages((current) => current.map((item, index) => index === current.length - 1 ? { ...item, text: replace ? text : item.text + text } : item));
  }
  function setCitations(citations: Citation[]) {
    setMessages((current) => current.map((item, index) => index === current.length - 1 ? { ...item, citations } : item));
  }
  function failAnswer(message: string) {
    setError(message);
    appendAnswer(`未能生成回答：${message}`, true);
  }
  function startResize(event: React.PointerEvent) {
    event.currentTarget.setPointerCapture(event.pointerId);
    const startX = event.clientX;
    const startWidth = width;
    const move = (moveEvent: PointerEvent) => onWidth(Math.max(320, Math.min(560, startWidth + startX - moveEvent.clientX)));
    const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  return (
    <aside className={`chat-drawer ${open ? "is-open" : ""}`}>
      <button className="chat-resizer" onPointerDown={startResize} title="调整聊天宽度"><Maximize2 /></button>
      <header className="chat-header"><Bot /><div><strong>阅读助理</strong><small>{book ? `正在阅读《${book.title}》` : "尚未选择书籍"}</small></div><button className="icon-button" onClick={() => void newSession()} title="新对话"><MessageSquarePlus /></button><button className="icon-button" onClick={onClose} title="隐藏聊天"><X /></button></header>
      <div className="scope-control">
        {(["book", "chapter", "library"] as ChatScope[]).map((item) => <button className={scope === item ? "is-active" : ""} disabled={item !== "library" && !book} key={item} onClick={() => setScope(item)}>{item === "book" ? "当前书" : item === "chapter" ? "当前章" : "全书库"}</button>)}
        <label title="显示检索调试"><input type="checkbox" checked={debug} onChange={(event) => setDebug(event.target.checked)} /><Settings2 /></label>
      </div>
      {selection && <div className="selection-context"><span>已引用选文</span><p>{selection.text.slice(0, 160)}{selection.text.length > 160 ? "…" : ""}</p><button onClick={onSelectionConsumed}>取消</button></div>}
      {!health?.answer_provider_ready && <div className="provider-warning">模型节点当前不可用。阅读功能不受影响；请单独运行 <code>python scripts/start_api.py</code> 并完成登录。</div>}
      <div className="chat-messages">
        {!messages.length && <div className="chat-empty"><Bot /><p>针对当前书、当前章节或选中文字提问。</p></div>}
        {messages.map((message, index) => <article className={`chat-message ${message.role}`} key={`${message.role}-${index}`}><span>{message.role === "user" ? "你" : "AI"}</span><div>{message.text ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown> : <i className="typing-dot" />}{message.citations?.map((citation) => <button className="inline-citation" key={citation.source_id} disabled={!citation.reader_location} onClick={() => citation.reader_location && onCitation(citation.reader_location)}>[{citation.display_index}] {citation.title} · {citation.chapter_title}</button>)}</div></article>)}
        {status.stage && <StatusStrip stage={status.stage} message={status.message} />}
        {debug && trace && <details className="chat-debug"><summary>检索过程 · {trace.rounds.length} 轮</summary>{trace.rounds.map((round) => <p key={round.round_index}>第 {round.round_index} 轮新增 {round.new_chunk_count} 条 · {round.stop_reason || "继续"}</p>)}</details>}
        {error && <p className="chat-error">{error}</p>}
        <div ref={bottomRef} />
      </div>
      <footer className="chat-composer"><textarea rows={2} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder="直接询问这本书…" />{abortRef.current ? <button onClick={() => abortRef.current?.abort()} title="停止"><CircleStop /></button> : <button onClick={() => void submit()} disabled={!question.trim()} title="发送"><Send /></button>}</footer>
    </aside>
  );
}
