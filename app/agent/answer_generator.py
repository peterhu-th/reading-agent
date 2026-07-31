from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from langchain_openai import ChatOpenAI

from app.agent.answer_strategy import choose_answer_strategy
from app.agent.citation_builder import build_citations, build_context
from app.config import get_settings
from app.models.schemas import AnswerStrategy, AnswerWithCitations, ConversationSession, IntentAnalysis, RetrievedChunk


PROMPT_BY_STRATEGY = {
    "summarize": Path("app/prompts/answer_summary.md"),
    "compare": Path("app/prompts/answer_compare.md"),
    "detail": Path("app/prompts/answer_detail.md"),
    "recommend": Path("app/prompts/answer_recommend.md"),
    "explain": Path("app/prompts/answer_with_citations.md"),
}


def load_prompt(strategy_name: str) -> str:
    return PROMPT_BY_STRATEGY.get(strategy_name, PROMPT_BY_STRATEGY["explain"]).read_text(encoding="utf-8")


def build_answer_prompt(question: str, retrieved: list[RetrievedChunk], intent: IntentAnalysis | None = None, conversation: ConversationSession | None = None, strategy: AnswerStrategy | None = None) -> str:
    strategy = strategy or (choose_answer_strategy(intent) if intent else AnswerStrategy())
    return load_prompt(strategy.name).format(
        question=question,
        context=build_context(retrieved),
        conversation_context=format_conversation_context(conversation),
        min_chars=strategy.min_chars,
        max_chars=strategy.max_chars,
        strategy_instruction=strategy.instruction,
    )


def llm_kwargs() -> dict:
    settings = get_settings()
    return {
        "model": settings.ANSWER_MODEL,
        "api_key": settings.OPENAI_API_KEY,
        "base_url": settings.OPENAI_BASE_URL,
        "temperature": 0.2,
        "http_socket_options": (),
    }


def make_llm() -> ChatOpenAI:
    return ChatOpenAI(**llm_kwargs(), http_client=httpx.Client(trust_env=False))


def generate_answer(question: str, retrieved: list[RetrievedChunk], intent: IntentAnalysis | None = None, conversation: ConversationSession | None = None, strategy: AnswerStrategy | None = None) -> AnswerWithCitations:
    if not retrieved:
        return AnswerWithCitations(answer="当前书库证据不足，无法可靠回答。", citations=[])
    prompt = build_answer_prompt(question, retrieved, intent, conversation, strategy)
    try:
        response = make_llm().invoke(prompt)
    except Exception as exc:
        raise translate_llm_error(exc) from exc
    return AnswerWithCitations(answer=str(response.content), citations=build_citations(retrieved))


async def stream_answer(question: str, retrieved: list[RetrievedChunk], intent: IntentAnalysis | None = None, conversation: ConversationSession | None = None, strategy: AnswerStrategy | None = None) -> AsyncIterator[str]:
    if not retrieved:
        yield "当前书库证据不足，无法可靠回答。"
        return
    prompt = build_answer_prompt(question, retrieved, intent, conversation, strategy)
    async_client = httpx.AsyncClient(trust_env=False)
    llm = ChatOpenAI(**llm_kwargs(), http_async_client=async_client)
    try:
        async for chunk in llm.astream(prompt):
            content = chunk.content
            if isinstance(content, str) and content:
                yield content
    except Exception as exc:
        raise translate_llm_error(exc) from exc
    finally:
        await async_client.aclose()


def translate_llm_error(exc: Exception) -> RuntimeError:
    message = str(exc)
    if "No healthy provider found" in message:
        return RuntimeError(
            "AIClient2API 服务已启动，但当前没有可用的 ChatGPT OAuth 节点。"
            "请在 AIClient2API 中重新登录或启用健康节点后再试。"
        )
    if "token_invalidated" in message or "401 Unauthorized" in message:
        return RuntimeError("AIClient2API 登录状态已失效，请重新登录后再试。")
    return RuntimeError(f"回答模型调用失败：{message[:240]}")


def format_conversation_context(conversation: ConversationSession | None) -> str:
    if not conversation or not conversation.turns:
        return "无"
    settings = get_settings()
    titles = "、".join(conversation.active_book_titles) or "无"
    topics = "、".join(conversation.active_topics[:5]) or "无"
    recent = []
    for turn in conversation.turns[-settings.CONVERSATION_RECENT_TURNS :]:
        recent.append(f"用户问：{turn.user_question}；回答摘要：{turn.answer_summary}")
    return f"滚动摘要：{conversation.rolling_summary or '无'}\n当前书名：{titles}\n当前主题：{topics}\n最近对话：{' | '.join(recent)}"
