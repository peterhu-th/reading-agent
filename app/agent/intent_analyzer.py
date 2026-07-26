import json
import re

import httpx
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.models.schemas import IntentAnalysis


BOOK_TITLE_PATTERN = re.compile(r"《([^》]{1,80})》")
SUMMARY_TERMS = ("主要内容", "讲了什么", "概括", "总结", "情节", "梗概", "大意")
COMPARE_TERMS = ("比较", "对比", "共同点", "不同点", "差异")
EMOTION_TERMS = ("焦虑", "孤独", "痛苦", "迷茫", "难过", "悲伤", "压力", "害怕")
RECOMMEND_TERMS = ("推荐", "适合", "读什么", "片段")
MOJIBAKE_MARKERS = (
    "\ufffd",
    "\u9286\u3006",
    "\u7edb",
    "\u9a9e",
    "\u9427",
    "\u701b",
)


INTENT_PROMPT = """你是阅读记忆助手的意图分析器。
只分析用户问题，不要回答问题。返回严格 JSON，不要 Markdown。

JSON 字段：
- labels: 字符串数组，可选值包括 emotion, scene, direct_question, specified_book, author_view, recommendation, comparison, summary, detail
- need_types: 字符串数组，描述用户需要，例如 explain, summarize, compare, recommend, cite_detail
- book_titles: 用户明确提到的书名数组，不要猜测
- authors: 用户明确提到的作者数组，不要猜测
- topics: 主题关键词数组
- emotions: 情绪词数组
- complexity: simple, normal, complex
- confidence: 0 到 1 的数字

用户问题：
{question}
"""


def analyze_intent(question: str) -> IntentAnalysis:
    """Analyze user intent with LLM, falling back to local rules."""
    question = repair_mojibake(question.strip())
    if not question:
        return fallback_intent(" ")

    try:
        settings = get_settings()
        llm = ChatOpenAI(
            model=settings.CHAT_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            temperature=0,
            http_client=httpx.Client(trust_env=False),
            http_socket_options=(),
        )
        response = llm.invoke(INTENT_PROMPT.format(question=question))
        return repair_intent(parse_intent_json(str(response.content), question), question)
    except Exception:
        return fallback_intent(question)


def parse_intent_json(content: str, question: str) -> IntentAnalysis:
    """Parse strict or fenced JSON returned by the LLM."""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]

    data = json.loads(text)
    data["question"] = question
    return IntentAnalysis(**data)


def fallback_intent(question: str) -> IntentAnalysis:
    """Small deterministic intent analyzer used when LLM analysis is unavailable."""
    question = repair_mojibake(question)
    labels: list[str] = []
    need_types: list[str] = []
    topics: list[str] = []
    emotions = [term for term in EMOTION_TERMS if term in question]
    book_titles = BOOK_TITLE_PATTERN.findall(question)

    if book_titles:
        labels.append("specified_book")
    if any(term in question for term in SUMMARY_TERMS):
        labels.append("summary")
        need_types.append("summarize")
    if any(term in question for term in COMPARE_TERMS):
        labels.append("comparison")
        need_types.append("compare")
    if emotions:
        labels.append("emotion")
        need_types.append("recommend")
    if any(term in question for term in RECOMMEND_TERMS):
        labels.append("recommendation")
        need_types.append("recommend")
    if not labels:
        labels.append("direct_question")
        need_types.append("explain")

    cleaned = BOOK_TITLE_PATTERN.sub("", question)
    for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}", cleaned):
        if token not in topics and token not in emotions:
            topics.append(token)

    complexity = "complex" if "comparison" in labels or len(book_titles) > 1 else "normal"
    return IntentAnalysis(
        labels=labels,
        need_types=need_types or ["explain"],
        book_titles=book_titles,
        topics=topics[:8],
        emotions=emotions,
        question=question,
        complexity=complexity,
        confidence=0.45,
    )


def repair_intent(intent: IntentAnalysis, question: str) -> IntentAnalysis:
    """Repair high-value fields with deterministic extraction from the raw question."""
    local_titles = BOOK_TITLE_PATTERN.findall(question)
    book_titles = [normalize_book_title(title) for title in intent.book_titles]
    if local_titles and (not book_titles or any(looks_mojibake(title) for title in book_titles)):
        book_titles = local_titles
    book_titles = [title for title in (normalize_book_title(title) for title in book_titles) if title]

    labels = list(intent.labels)
    if book_titles and "specified_book" not in labels:
        labels.append("specified_book")

    return intent.model_copy(
        update={
            "book_titles": book_titles,
            "labels": labels,
            "question": question,
        }
    )


def looks_mojibake(text: str) -> bool:
    return any(marker in text for marker in MOJIBAKE_MARKERS)


def repair_mojibake(text: str) -> str:
    """Repair common UTF-8 text decoded as GBK/CP936 in Windows shells."""
    if not looks_mojibake(text):
        return text
    try:
        repaired = text.encode("gbk", errors="strict").decode("utf-8", errors="strict")
    except UnicodeError:
        return text
    return repaired if repaired else text


def normalize_book_title(title: str) -> str:
    title = repair_mojibake(title).strip()
    if title.startswith("《") and title.endswith("》"):
        title = title[1:-1]
    return title.strip()
