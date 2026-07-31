import httpx
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.models.schemas import (
    AnswerWithCitations,
    ConversationSession,
    ConversationTurn,
    IterativeRetrievalResult,
    RetrievalResult,
    utc_now_iso,
)


def update_conversation(
    session: ConversationSession,
    question: str,
    retrieval: RetrievalResult | IterativeRetrievalResult,
    answer: AnswerWithCitations,
    resolved_question: str = "",
    entities: list[str] | None = None,
) -> ConversationSession:
    """Update deterministic session state without trusting incidental retrieval hits."""

    settings = get_settings()
    intent = retrieval.intent
    titles = unique_strings(
        session.explicit_book_titles
        or intent.book_titles
        or session.active_book_titles
    )
    authors = unique_strings(intent.authors or session.active_authors)
    topics = unique_strings(intent.topics or session.active_topics)
    active_entities = unique_strings((entities or []) + session.active_entities)
    missing = retrieval.assessment.missing_aspects if isinstance(retrieval, IterativeRetrievalResult) else []

    turn = ConversationTurn(
        user_question=question,
        resolved_question=resolved_question or question,
        assistant_answer=answer.answer,
        answer_summary=summarize_answer(answer.answer),
        citations=answer.citations,
        book_titles=titles,
        authors=authors,
        entities=active_entities,
        topics=topics,
        missing_aspects=missing,
    )
    turns = (session.turns + [turn])[-settings.CONVERSATION_MAX_TURNS :]
    title = session.title
    if title == "新对话":
        title = summarize_answer(question, 28)
    return session.model_copy(
        update={
            "title": title,
            "turns": turns,
            "active_book_titles": titles,
            "active_authors": authors,
            "active_entities": active_entities[:12],
            "active_topics": topics[:12],
            "last_resolved_question": resolved_question or question,
            "unresolved_references": missing[:6],
            "updated_at": utc_now_iso(),
        }
    )


def maybe_compact_conversation(session: ConversationSession) -> ConversationSession:
    settings = get_settings()
    if not session.turns or len(session.turns) % settings.CONVERSATION_SUMMARY_INTERVAL:
        return session
    transcript = "\n".join(
        f"用户：{turn.user_question}\n回答摘要：{turn.answer_summary}"
        for turn in session.turns
    )
    prompt = (
        "把下面的阅读对话压缩成中文会话记忆，只保留已讨论书籍、人物、主题、明确结论和未解决问题。"
        f"不超过 {settings.CONVERSATION_SUMMARY_MAX_CHARS} 个中文字符，不要添加原对话没有的信息。\n{transcript}"
    )
    try:
        llm = ChatOpenAI(
            model=settings.INTENT_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            temperature=0,
            http_client=httpx.Client(trust_env=False),
            http_socket_options=(),
        )
        summary = str(llm.invoke(prompt).content).strip()
    except Exception:
        summary = "；".join(turn.answer_summary for turn in session.turns[-3:])
    summary = summary[: settings.CONVERSATION_SUMMARY_MAX_CHARS]
    return session.model_copy(update={"rolling_summary": summary, "updated_at": utc_now_iso()})


def summarize_answer(answer: str, max_chars: int = 200) -> str:
    text = " ".join(answer.split())
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"


def render_history(session: ConversationSession) -> str:
    if not session.turns:
        return "当前没有会话历史。"
    lines = []
    for index, turn in enumerate(session.turns, start=1):
        titles = "、".join(turn.book_titles) or "未限定"
        lines.append(f"{index}. 问题：{turn.user_question} | 书名：{titles}")
    return "\n".join(lines)


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
