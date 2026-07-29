import json
import re

import httpx
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.models.schemas import ConversationSession, IntentAnalysis
from app.retrieval.keyword_retriever import tokenize


BOOK_TITLE_PATTERN = re.compile(r"《([^《》]{1,80})》")
MOJIBAKE_MARKERS = ("\ufffd", "銆", "绗", "鐨", "瀛", "涓", "浠")

INTENT_EXAMPLES = [
    (
        "summary",
        "summarize",
        "这本书主要内容 情节梗概 讲了什么 大意 主题 概括",
    ),
    (
        "comparison",
        "compare",
        "比较 对比 两本书 共同点 不同点 差异 观念 理解",
    ),
    (
        "detail",
        "cite_detail",
        "人物关系 事件经过 细节 原文 体现在哪里 为什么 怎么发展",
    ),
    (
        "recommendation",
        "recommend",
        "推荐 适合 片段 回应 状态 读什么",
    ),
    (
        "emotion",
        "recommend",
        "焦虑 孤独 痛苦 迷茫 难过 悲伤 压力 害怕 情绪",
    ),
]


INTENT_PROMPT = """你是阅读记忆助手的意图分析器。只分析用户问题，不要回答问题。
请返回严格 JSON，不要 Markdown。

JSON 字段：
- labels: 字符串数组，可选值包括 emotion, scene, direct_question, specified_book, author_view, recommendation, comparison, summary, detail
- need_types: 字符串数组，例如 explain, summarize, compare, recommend, cite_detail
- book_titles: 用户明确提到的书名数组，不要猜测
- authors: 用户明确提到的作者数组，不要猜测
- topics: 中文主题关键词数组
- emotions: 情绪词数组
- complexity: simple, normal, complex
- confidence: 0 到 1 的数字

{conversation_context}
用户问题：{question}
"""


def analyze_intent(
    question: str,
    conversation: ConversationSession | None = None,
) -> IntentAnalysis:
    """Analyze user intent with LLM, falling back to local semantic prototypes."""
    question = repair_mojibake(question.strip())
    if not question:
        return fallback_intent(" ", conversation)

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
        prompt = INTENT_PROMPT.format(
            question=question,
            conversation_context=format_conversation_context(conversation),
        )
        response = llm.invoke(prompt)
        return repair_intent(parse_intent_json(str(response.content), question), question, conversation)
    except Exception:
        return fallback_intent(question, conversation)


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


def fallback_intent(
    question: str,
    conversation: ConversationSession | None = None,
) -> IntentAnalysis:
    """Local fallback based on Chinese character n-gram similarity to intent examples."""
    question = repair_mojibake(question)
    labels: list[str] = []
    need_types: list[str] = []
    book_titles = BOOK_TITLE_PATTERN.findall(question)
    authors: list[str] = []

    scored = score_examples(question)
    for label, need_type, score in scored:
        if score >= 0.12:
            labels.append(label)
            need_types.append(need_type)

    if book_titles:
        labels.append("specified_book")
    elif conversation and is_follow_up(question):
        book_titles = conversation.active_book_titles[:]
        authors = conversation.active_authors[:]

    if not labels:
        labels.append("direct_question")
        need_types.append("explain")

    topics = extract_topics(question, book_titles)
    if conversation and is_follow_up(question):
        topics = unique_strings(topics + conversation.active_topics[:3])

    complexity = "complex" if "comparison" in labels or len(book_titles) > 1 else "normal"
    return IntentAnalysis(
        labels=unique_strings(labels),
        need_types=unique_strings(need_types) or ["explain"],
        book_titles=book_titles,
        authors=authors,
        topics=topics[:8],
        emotions=extract_emotions(question),
        question=question,
        complexity=complexity,
        confidence=0.55 if scored else 0.4,
    )


def score_examples(question: str) -> list[tuple[str, str, float]]:
    query_tokens = set(tokenize(question))
    scored: list[tuple[str, str, float]] = []
    for label, need_type, prototype in INTENT_EXAMPLES:
        prototype_tokens = set(tokenize(prototype))
        if not query_tokens or not prototype_tokens:
            score = 0.0
        else:
            score = len(query_tokens & prototype_tokens) / len(query_tokens | prototype_tokens)
        scored.append((label, need_type, score))
    scored.sort(key=lambda item: item[2], reverse=True)
    return scored[:3]


def repair_intent(
    intent: IntentAnalysis,
    question: str,
    conversation: ConversationSession | None = None,
) -> IntentAnalysis:
    """Repair high-value fields with deterministic extraction from the raw question."""
    local_titles = BOOK_TITLE_PATTERN.findall(question)
    book_titles = [normalize_book_title(title) for title in intent.book_titles]
    if local_titles:
        book_titles = local_titles
    elif conversation and is_follow_up(question) and not book_titles:
        book_titles = conversation.active_book_titles[:]

    labels = unique_strings(intent.labels)
    if book_titles and "specified_book" not in labels:
        labels.append("specified_book")

    topics = unique_strings(intent.topics + extract_topics(question, book_titles))
    if conversation and is_follow_up(question):
        topics = unique_strings(topics + conversation.active_topics[:3])

    return intent.model_copy(
        update={
            "book_titles": [title for title in book_titles if title],
            "labels": labels,
            "topics": topics[:8],
            "question": question,
        }
    )


def format_conversation_context(conversation: ConversationSession | None) -> str:
    if not conversation or not conversation.turns:
        return ""
    titles = "、".join(conversation.active_book_titles) or "无"
    topics = "、".join(conversation.active_topics[:5]) or "无"
    recent = "；".join(turn.user_question for turn in conversation.turns[-3:])
    return f"对话上下文：当前书名={titles}；当前主题={topics}；最近问题={recent}\n"


def is_follow_up(question: str) -> bool:
    markers = ("那", "它", "他", "她", "这个", "这本", "继续", "后来", "上面", "刚才", "他们", "她们")
    return any(marker in question for marker in markers) and not BOOK_TITLE_PATTERN.findall(question)


def extract_topics(question: str, book_titles: list[str]) -> list[str]:
    cleaned = BOOK_TITLE_PATTERN.sub("", question)
    for title in book_titles:
        cleaned = cleaned.replace(title, "")
    stop_words = {
        "什么",
        "怎么",
        "为什么",
        "是否",
        "根据",
        "回答",
        "主要",
        "内容",
        "比较",
        "继续",
        "后来",
    }
    topics: list[str] = []
    for token in re.findall(r"[\u4e00-\u9fff]{2,}", cleaned):
        if token not in stop_words and token not in topics:
            topics.append(token)
    return topics


def extract_emotions(question: str) -> list[str]:
    emotions = ("焦虑", "孤独", "痛苦", "迷茫", "难过", "悲伤", "压力", "害怕")
    return [emotion for emotion in emotions if emotion in question]


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
    return repair_mojibake(title).strip("《》 \t\r\n")


def unique_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result
