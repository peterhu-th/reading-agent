import json
import re

import httpx
from langchain_openai import ChatOpenAI

from app.agent.intent_analyzer import BOOK_TITLE_PATTERN, is_follow_up
from app.config import get_settings
from app.models.schemas import ConversationSession, ResolvedQuestion


RESOLVER_PROMPT = """你负责把中文多轮对话中的当前问题改写成可独立检索的问题，不要回答问题。
返回严格 JSON，字段为 standalone_question、inherited_book_titles、resolved_entities、confidence、clarification_needed、clarification_message。
只有上下文明确时才能解析“他、她、他们、这本书、后来”等指代。无法唯一确定时 clarification_needed=true。
当前会话摘要：{rolling_summary}
当前书籍：{active_books}
当前人物或实体：{active_entities}
最近对话：{recent_turns}
用户问题：{question}
"""


def resolve_question(
    question: str,
    session: ConversationSession,
    selected_books: list[str] | None = None,
) -> ResolvedQuestion:
    question = question.strip()
    explicit_books = unique_strings(selected_books or [])
    local_titles = BOOK_TITLE_PATTERN.findall(question)
    if explicit_books:
        return ResolvedQuestion(
            original_question=question,
            standalone_question=apply_book_constraint(question, explicit_books),
            inherited_book_titles=explicit_books,
            confidence=1.0,
        )
    if local_titles or not is_follow_up(question) or not session.turns:
        return ResolvedQuestion(
            original_question=question,
            standalone_question=question,
            inherited_book_titles=local_titles,
            confidence=1.0,
        )

    try:
        settings = get_settings()
        llm = ChatOpenAI(
            model=settings.INTENT_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            temperature=0,
            http_client=httpx.Client(trust_env=False),
            http_socket_options=(),
        )
        recent = format_recent_turns(session, settings.CONVERSATION_RECENT_TURNS)
        response = llm.invoke(
            RESOLVER_PROMPT.format(
                rolling_summary=session.rolling_summary or "无",
                active_books="、".join(session.active_book_titles) or "无",
                active_entities="、".join(session.active_entities) or "无",
                recent_turns=recent,
                question=question,
            )
        )
        data = parse_json(str(response.content))
        result = ResolvedQuestion(original_question=question, **data)
        if result.confidence < 0.45:
            return result.model_copy(
                update={
                    "clarification_needed": True,
                    "clarification_message": result.clarification_message or "请说明你指的是哪本书或哪个人物。",
                }
            )
        return result
    except Exception:
        return fallback_resolve(question, session)


def fallback_resolve(question: str, session: ConversationSession) -> ResolvedQuestion:
    books = session.explicit_book_titles or session.active_book_titles
    if len(books) > 1:
        return ResolvedQuestion(
            original_question=question,
            standalone_question=question,
            inherited_book_titles=books,
            confidence=0.3,
            clarification_needed=True,
            clarification_message=f"请说明你指的是哪本书：{'、'.join(books)}。",
        )
    if not books and not session.active_entities:
        return ResolvedQuestion(
            original_question=question,
            standalone_question=question,
            confidence=0.3,
            clarification_needed=True,
            clarification_message="请补充书名或人物名称，我才能准确理解这次追问。",
        )

    context = []
    if books:
        context.append(f"书籍《{books[0]}》")
    if session.active_entities:
        context.append(f"人物或概念{'、'.join(session.active_entities[:3])}")
    standalone = f"关于{'，'.join(context)}：{question}"
    return ResolvedQuestion(
        original_question=question,
        standalone_question=standalone,
        inherited_book_titles=books,
        resolved_entities=session.active_entities[:],
        confidence=0.7,
    )


def apply_book_constraint(question: str, books: list[str]) -> str:
    titles = "、".join(f"《{title}》" for title in books)
    return f"仅根据{titles}回答：{question}"


def format_recent_turns(session: ConversationSession, limit: int) -> str:
    lines = []
    for turn in session.turns[-limit:]:
        lines.append(f"用户：{turn.user_question}")
        if turn.answer_summary:
            lines.append(f"回答摘要：{turn.answer_summary}")
    return "\n".join(lines) or "无"


def parse_json(content: str) -> dict:
    text = content.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    return json.loads(text)


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
