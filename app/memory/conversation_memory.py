from app.config import get_settings
from app.models.schemas import (
    AnswerWithCitations,
    ConversationSession,
    ConversationTurn,
    RetrievalResult,
)


def update_conversation(
    session: ConversationSession,
    question: str,
    retrieval: RetrievalResult,
    answer: AnswerWithCitations,
) -> ConversationSession:
    """Return an updated short-term CLI conversation session."""
    settings = get_settings()
    intent = retrieval.intent
    titles = unique_strings(intent.book_titles + [item.chunk.title for item in retrieval.chunks[:3]])
    authors = unique_strings(intent.authors + [item.chunk.author for item in retrieval.chunks[:3] if item.chunk.author])
    topics = unique_strings(intent.topics)

    turn = ConversationTurn(
        user_question=question,
        answer_summary=summarize_answer(answer.answer),
        book_titles=titles,
        authors=authors,
        topics=topics,
    )
    turns = (session.turns + [turn])[-settings.CONVERSATION_MAX_TURNS :]
    return ConversationSession(
        turns=turns,
        active_book_titles=titles or session.active_book_titles,
        active_authors=authors or session.active_authors,
        active_topics=topics or session.active_topics,
    )


def summarize_answer(answer: str, max_chars: int = 160) -> str:
    text = " ".join(answer.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def render_history(session: ConversationSession) -> str:
    if not session.turns:
        return "当前没有会话历史。"
    lines = []
    for index, turn in enumerate(session.turns, start=1):
        titles = "、".join(turn.book_titles) or "未限定"
        lines.append(f"{index}. 问题：{turn.user_question} | 书名：{titles}")
    return "\n".join(lines)


def unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
