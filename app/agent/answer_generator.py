from pathlib import Path

import httpx
from langchain_openai import ChatOpenAI

from app.agent.answer_strategy import choose_answer_strategy
from app.agent.citation_builder import build_citations, build_context
from app.config import get_settings
from app.models.schemas import (
    AnswerStrategy,
    AnswerWithCitations,
    ConversationSession,
    IntentAnalysis,
    RetrievedChunk,
)


PROMPT_BY_STRATEGY = {
    "summarize": Path("app/prompts/answer_summary.md"),
    "compare": Path("app/prompts/answer_compare.md"),
    "detail": Path("app/prompts/answer_detail.md"),
    "recommend": Path("app/prompts/answer_recommend.md"),
    "explain": Path("app/prompts/answer_with_citations.md"),
}


def load_prompt(strategy_name: str) -> str:
    path = PROMPT_BY_STRATEGY.get(strategy_name, PROMPT_BY_STRATEGY["explain"])
    return path.read_text(encoding="utf-8")


def generate_answer(
    question: str,
    retrieved: list[RetrievedChunk],
    intent: IntentAnalysis | None = None,
    conversation: ConversationSession | None = None,
    strategy: AnswerStrategy | None = None,
) -> AnswerWithCitations:
    if not retrieved:
        return AnswerWithCitations(answer="当前书库证据不足。", citations=[])

    settings = get_settings()
    strategy = strategy or (choose_answer_strategy(intent) if intent else AnswerStrategy())
    context = build_context(retrieved)
    citations = build_citations(retrieved)
    prompt = load_prompt(strategy.name).format(
        question=question,
        context=context,
        conversation_context=format_conversation_context(conversation),
        min_chars=strategy.min_chars,
        max_chars=strategy.max_chars,
        strategy_instruction=strategy.instruction,
    )

    llm = ChatOpenAI(
        model=settings.ANSWER_MODEL,
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
        http_client=httpx.Client(trust_env=False),
        http_socket_options=(),
    )
    try:
        response = llm.invoke(prompt)
    except Exception as exc:
        if "token_invalidated" in str(exc) or "401 Unauthorized" in str(exc):
            raise RuntimeError(
                "AIClient2API ChatGPT/Codex OAuth token is invalidated. "
                "Please sign in again in AIClient2API, then rerun RAG."
            ) from exc
        raise
    return AnswerWithCitations(answer=str(response.content), citations=citations)


def format_conversation_context(conversation: ConversationSession | None) -> str:
    if not conversation or not conversation.turns:
        return "无"
    titles = "、".join(conversation.active_book_titles) or "无"
    topics = "、".join(conversation.active_topics[:5]) or "无"
    recent = []
    for turn in conversation.turns[-3:]:
        if turn.answer_summary:
            recent.append(f"用户问：{turn.user_question}；上一答摘要：{turn.answer_summary}")
        else:
            recent.append(f"用户问：{turn.user_question}")
    return f"当前书名：{titles}\n当前主题：{topics}\n最近对话：{' | '.join(recent)}"
