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

interface ChatMessage extends UiMessage {
  id: string;
}

interface ChatRequest {
  sessionId: string;
  messageId: string;
  controller: AbortController;
}

function sessionMessages(session: Session): ChatMessage[] {
  return session.turns.flatMap((turn, index) => [
    { id: `${session.session_id}:${index}:user`, role: "user" as const, text: turn.user_question },
    { id: `${session.session_id}:${index}:assistant`, role: "assistant" as const, text: turn.assistant_answer, citations: turn.citations },
  ]);
}

export function ChatDrawer({ open, width, health, book, chapter, selection, suggestedQuestion, onClose, onWidth, onCitation, onSelectionConsumed }: Props) {
  const [session, setSession] = useState<Session | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [question, setQuestion] = useState("");
  const [scope, setScope] = useState<ChatScope>("book");
  const [status, setStatus] = useState({ stage: "", message: "" });
  const [trace, setTrace] = useState<RetrievalTrace | null>(null);
  const [debug, setDebug] = useState(false);
  const [error, setError] = useState("");
  const [generating, setGenerating] = useState(false);
  const [sessionLoading, setSessionLoading] = useState(true);
  const activeRequest = useRef<ChatRequest | null>(null);
  const sessionVersion = useRef(0);
  const requestSequence = useRef(0);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void initializeSession();
    return () => {
      sessionVersion.current++;
      activeRequest.current?.controller.abort();
      activeRequest.current = null;
    };
  }, []);
  useEffect(() => { if (suggestedQuestion) setQuestion(suggestedQuestion); }, [suggestedQuestion]);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, status]);

  async function initializeSession() {
    const version = ++sessionVersion.current;
    try {
      const sessions = await api.listSessions();
      if (version !== sessionVersion.current) return;
      const remembered = sessionStorage.getItem("reading-chat-session");
      const current = sessions.find((item) => item.session_id === remembered) ?? sessions[0] ?? await api.createSession();
      if (version !== sessionVersion.current) return;
      setSession(current);
      setMessages(sessionMessages(current));
      sessionStorage.setItem("reading-chat-session", current.session_id);
    } catch (caught) {
      if (version === sessionVersion.current) setError(caught instanceof Error ? caught.message : "无法载入对话");
    } finally {
      if (version === sessionVersion.current) setSessionLoading(false);
    }
  }

  async function newSession() {
    const version = ++sessionVersion.current;
    stopGeneration();
    setSessionLoading(true);
    setError("");
    try {
      const created = await api.createSession();
      if (version !== sessionVersion.current) return;
      setSession(created);
      setMessages([]);
      setTrace(null);
      sessionStorage.setItem("reading-chat-session", created.session_id);
    } catch (caught) {
      if (version === sessionVersion.current) setError(caught instanceof Error ? caught.message : "无法新建对话");
    } finally {
      if (version === sessionVersion.current) setSessionLoading(false);
    }
  }

  async function submit() {
    const text = question.trim();
    if (!text || !session || sessionLoading || activeRequest.current) return;
    const messagePrefix = `request-${++requestSequence.current}`;
    const request: ChatRequest = { sessionId: session.session_id, messageId: `${messagePrefix}:assistant`, controller: new AbortController() };
    const version = sessionVersion.current;
    const isCurrent = () => activeRequest.current === request && sessionVersion.current === version && !request.controller.signal.aborted;
    activeRequest.current = request;
    setGenerating(true);
    setQuestion("");
    setError("");
    setMessages((current) => [...current, { id: `${messagePrefix}:user`, role: "user", text }, { id: request.messageId, role: "assistant", text: "", pending: true }]);
    try {
      await streamChat({
        session_id: request.sessionId,
        question: text,
        selected_books: scope === "library" || !book ? [] : [book.title],
        scope,
        book_id: book?.book_id ?? "",
        book_title: book?.title ?? "",
        chapter_index: scope === "chapter" || selection ? chapter?.chapter_index ?? null : null,
        selected_text: selection?.text ?? "",
        selected_paragraphs: selection?.paragraphs ?? [],
        debug,
      }, request.controller.signal, (event, data) => {
        if (!isCurrent()) return;
        if (event === "status") setStatus({ stage: String(data.stage ?? ""), message: String(data.message ?? "") });
        if (event === "answer_delta") appendAnswer(request.messageId, String(data.text ?? ""));
        if (event === "citations") updateMessage(request.messageId, (message) => ({ ...message, citations: data.items as Citation[] }));
        if (event === "clarification") appendAnswer(request.messageId, String(data.message ?? "请补充信息。"), true);
        if (event === "complete") {
          const completed = data.session as Session | undefined;
          if (completed?.session_id === request.sessionId) setSession(completed);
          if (data.trace) setTrace(data.trace as unknown as RetrievalTrace);
        }
        if (event === "error") failAnswer(request.messageId, String(data.message ?? "生成失败"));
      });
      if (isCurrent()) onSelectionConsumed();
    } catch (caught) {
      if (isCurrent()) failAnswer(request.messageId, caught instanceof Error ? caught.message : "生成失败");
    } finally {
      if (activeRequest.current === request) {
        activeRequest.current = null;
        setGenerating(false);
        setStatus({ stage: "", message: "" });
        updateMessage(request.messageId, (message) => ({ ...message, pending: false }));
      }
    }
  }

  function updateMessage(id: string, update: (message: ChatMessage) => ChatMessage) {
    setMessages((current) => current.map((message) => message.id === id ? update(message) : message));
  }
  function appendAnswer(id: string, text: string, replace = false) {
    updateMessage(id, (message) => ({ ...message, text: replace ? text : message.text + text }));
  }
  function failAnswer(id: string, message: string) {
    setError(message);
    appendAnswer(id, `未能生成回答：${message}`, true);
  }
  function stopGeneration() {
    const request = activeRequest.current;
    activeRequest.current = null;
    request?.controller.abort();
    setGenerating(false);
    setStatus({ stage: "", message: "" });
    if (request) updateMessage(request.messageId, (message) => ({ ...message, pending: false }));
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
        {messages.map((message) => <article className={`chat-message ${message.role}`} key={message.id}><span>{message.role === "user" ? "你" : "AI"}</span><div>{message.text ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown> : message.pending ? <i className="typing-dot" /> : null}{message.citations?.map((citation) => <button className="inline-citation" key={citation.source_id} disabled={!citation.reader_location} onClick={() => citation.reader_location && onCitation(citation.reader_location)}>[{citation.display_index}] {citation.title} · {citation.chapter_title}</button>)}</div></article>)}
        {status.stage && <StatusStrip stage={status.stage} message={status.message} />}
        {debug && trace && <details className="chat-debug"><summary>检索过程 · {trace.rounds.length} 轮</summary>{trace.rounds.map((round) => <p key={round.round_index}>第 {round.round_index} 轮新增 {round.new_chunk_count} 条 · {round.stop_reason || "继续"}</p>)}</details>}
        {error && <p className="chat-error">{error}</p>}
        <div ref={bottomRef} />
      </div>
      <footer className="chat-composer"><textarea rows={2} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder="直接询问这本书…" />{generating ? <button onClick={stopGeneration} title="停止"><CircleStop /></button> : <button onClick={() => void submit()} disabled={!question.trim() || !session || sessionLoading} title="发送"><Send /></button>}</footer>
    </aside>
  );
}
