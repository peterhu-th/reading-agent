import json
import re

import httpx
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.models.schemas import ConversationSession, EvidenceRequirement, IntentAnalysis
from app.retrieval.keyword_retriever import tokenize


BOOK_TITLE_PATTERN = re.compile(r"《([^《》]{1,80})》")
MOJIBAKE_MARKERS = ("\ufffd", "銆", "绗", "鐨", "瀛", "涓", "浠")

INTENT_EXAMPLES = [
    ("summary", "summarize", "这本书主要内容 情节梗概 讲了什么 大意 主题 概括"),
    ("comparison", "compare", "比较 对比 两本书 共同点 不同点 差异 观念 理解"),
    ("detail", "cite_detail", "人物关系 事件经过 细节 原文 体现在哪里 为什么 怎么发展"),
    ("recommendation", "recommend", "推荐 适合 片段 回应 状态 读什么"),
    ("emotion", "recommend", "焦虑 孤独 痛苦 迷茫 难过 悲伤 压力 害怕 情绪"),
]

INTENT_PROMPT = """你是中文阅读助理的意图分析器，只分析问题，不回答问题。
返回严格 JSON，不要使用 Markdown。字段如下：
- labels: 可多选 emotion, scene, direct_question, specified_book, author_view, recommendation, comparison, summary, detail
- need_types: 可多选 explain, summarize, compare, recommend, cite_detail
- book_titles: 只填写问题明确提到或上下文明确继承的书名
- authors: 作者名
- topics: 中文主题词
- emotions: 情绪词
- complexity: simple, normal, complex
- confidence: 0 到 1
- evidence_requirements: 1 到 5 项，每项包含 description、query、target_books、purpose
证据需求应把复杂问题拆成可以分别检索的事实或观点。比较问题必须为每本书分别建立需求。
{conversation_context}
用户问题：{question}
"""


def analyze_intent(
    question: str,
    conversation: ConversationSession | None = None,
) -> IntentAnalysis:
    """Analyze intent with an LLM, falling back to local semantic prototypes."""

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
        response = llm.invoke(
            INTENT_PROMPT.format(
                question=question,
                conversation_context=format_conversation_context(conversation),
            )
        )
        parsed = parse_intent_json(str(response.content), question)
        return repair_intent(parsed, question, conversation)
    except Exception:
        return fallback_intent(question, conversation)


def parse_intent_json(content: str, question: str) -> IntentAnalysis:
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

    inherited = []
    if conversation:
        inherited = conversation.explicit_book_titles or conversation.active_book_titles
    if book_titles:
        labels.append("specified_book")
    elif conversation and is_follow_up(question):
        book_titles = inherited[:]
        authors = conversation.active_authors[:]

    if not labels:
        labels.append("direct_question")
        need_types.append("explain")

    topics = extract_topics(question, book_titles)
    if conversation and is_follow_up(question):
        topics = unique_strings(topics + conversation.active_topics[:3])

    complexity = "complex" if "comparison" in labels or len(book_titles) > 1 else "normal"
    intent = IntentAnalysis(
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
    return intent.model_copy(update={"evidence_requirements": build_evidence_requirements(intent)})


def build_evidence_requirements(intent: IntentAnalysis) -> list[EvidenceRequirement]:
    base = " ".join(unique_strings(intent.topics + intent.emotions)).strip() or intent.question
    requirements: list[EvidenceRequirement] = []
    if "comparison" in intent.labels and intent.book_titles:
        for title in intent.book_titles:
            requirements.append(
                EvidenceRequirement(
                    description=f"确认《{title}》对相关主题的表达及原文依据",
                    query=f"{title} {base} 主题 观点 原文",
                    target_books=[title],
                    purpose="comparison_target",
                )
            )
        requirements.append(
            EvidenceRequirement(
                description="找到可用于比较共同点与差异的证据",
                query=f"{base} 共同点 差异",
                target_books=intent.book_titles,
                purpose="comparison_synthesis",
            )
        )
    elif "summary" in intent.labels:
        requirements.extend(
            [
                EvidenceRequirement(
                    description="覆盖故事、论述或内容的主要发展阶段",
                    query=f"{base} 主要内容 发展 转折",
                    target_books=intent.book_titles,
                    purpose="summary_structure",
                ),
                EvidenceRequirement(
                    description="覆盖核心人物、观点或主题",
                    query=f"{base} 核心人物 主题 结局",
                    target_books=intent.book_titles,
                    purpose="summary_theme",
                ),
            ]
        )
    else:
        requirements.append(
            EvidenceRequirement(
                description="找到能够直接回答用户问题的原文证据",
                query=intent.question,
                target_books=intent.book_titles,
                purpose="direct_evidence",
            )
        )
    return requirements[:5]


def score_examples(question: str) -> list[tuple[str, str, float]]:
    query_tokens = set(tokenize(question))
    scored: list[tuple[str, str, float]] = []
    for label, need_type, prototype in INTENT_EXAMPLES:
        prototype_tokens = set(tokenize(prototype))
        score = (
            len(query_tokens & prototype_tokens) / len(query_tokens | prototype_tokens)
            if query_tokens and prototype_tokens
            else 0.0
        )
        scored.append((label, need_type, score))
    scored.sort(key=lambda item: item[2], reverse=True)
    return scored[:3]


def repair_intent(
    intent: IntentAnalysis,
    question: str,
    conversation: ConversationSession | None = None,
) -> IntentAnalysis:
    local_titles = BOOK_TITLE_PATTERN.findall(question)
    book_titles = [normalize_book_title(title) for title in intent.book_titles]
    inherited = []
    if conversation:
        inherited = conversation.explicit_book_titles or conversation.active_book_titles
    if local_titles:
        book_titles = local_titles
    elif conversation and is_follow_up(question) and not book_titles:
        book_titles = inherited[:]

    labels = unique_strings(intent.labels)
    if book_titles and "specified_book" not in labels:
        labels.append("specified_book")
    topics = unique_strings(intent.topics + extract_topics(question, book_titles))
    if conversation and is_follow_up(question):
        topics = unique_strings(topics + conversation.active_topics[:3])

    updated = intent.model_copy(
        update={
            "book_titles": [title for title in book_titles if title],
            "labels": labels,
            "topics": topics[:8],
            "question": question,
        }
    )
    requirements = updated.evidence_requirements or build_evidence_requirements(updated)
    return updated.model_copy(update={"evidence_requirements": requirements[:5]})


def format_conversation_context(conversation: ConversationSession | None) -> str:
    if not conversation or not conversation.turns:
        return ""
    titles = "、".join(conversation.explicit_book_titles or conversation.active_book_titles) or "无"
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
    stop_words = {"什么", "怎么", "为什么", "是否", "根据", "回答", "主要", "内容", "比较", "继续", "后来"}
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
    if not looks_mojibake(text):
        return text
    try:
        repaired = text.encode("gbk", errors="strict").decode("utf-8", errors="strict")
    except UnicodeError:
        return text
    return repaired or text


def normalize_book_title(title: str) -> str:
    return repair_mojibake(title).strip("《》 \t\r\n")


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
