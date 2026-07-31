import json
import re

import httpx
from langchain_openai import ChatOpenAI

from app.config import get_settings
from app.models.schemas import EvidenceAssessment, EvidenceRequirement, IntentAnalysis, PlannedQuery, RetrievedChunk
from app.retrieval.keyword_retriever import tokenize


CHECK_PROMPT = """你是中文书籍问答系统的证据检查器，不回答用户问题。
判断已有原文是否足以覆盖每项证据需求。返回严格 JSON：
{{"sufficient":true,"covered_requirement_ids":[],"missing_aspects":[],"supplemental_queries":[{{"query":"","metadata_filter":{{"title":""}},"purpose":"supplement"}}],"reason":""}}
补充查询最多 4 个，只针对缺失内容；指定书籍的问题必须保留 title 过滤。
用户问题：{question}
证据需求：{requirements}
已有证据：{evidence}
"""


def assess_evidence(question: str, intent: IntentAnalysis, chunks: list[RetrievedChunk]) -> EvidenceAssessment:
    rule_result = rule_assessment(intent, chunks)
    try:
        settings = get_settings()
        llm = ChatOpenAI(
            model=settings.EVIDENCE_CHECK_MODEL,
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_BASE_URL,
            temperature=0,
            http_client=httpx.Client(trust_env=False),
            http_socket_options=(),
        )
        response = llm.invoke(CHECK_PROMPT.format(
            question=question,
            requirements=json.dumps([item.model_dump() for item in intent.evidence_requirements], ensure_ascii=False),
            evidence=build_evidence_inventory(chunks, settings.EVIDENCE_CHECK_MAX_CHARS),
        ))
        llm_result = parse_assessment(str(response.content))
        return combine_assessments(rule_result, llm_result)
    except Exception:
        return rule_result


def rule_assessment(intent: IntentAnalysis, chunks: list[RetrievedChunk]) -> EvidenceAssessment:
    settings = get_settings()
    missing: list[str] = []
    covered: list[str] = []
    supplemental: list[PlannedQuery] = []

    for requirement in intent.evidence_requirements:
        matches = [item for item in chunks if requirement_matches(requirement, item)]
        if matches:
            covered.append(requirement.requirement_id)
        else:
            missing.append(requirement.description)
            metadata = {"title": requirement.target_books} if requirement.target_books else {}
            supplemental.append(PlannedQuery(query=requirement.query, metadata_filter=metadata, purpose="missing_requirement"))

    if len(chunks) < settings.EVIDENCE_MIN_CHUNKS:
        missing.append(f"有效原文不足 {settings.EVIDENCE_MIN_CHUNKS} 条")
        supplemental.append(PlannedQuery(query=f"{intent.question} 相关细节 原文", metadata_filter=book_filter(intent), purpose="evidence_count"))

    if "comparison" in intent.labels:
        for title in intent.book_titles:
            count = sum(1 for item in chunks if title in item.chunk.title)
            if count < settings.COMPARISON_MIN_CHUNKS_PER_BOOK:
                missing.append(f"《{title}》的证据不足")
                supplemental.append(PlannedQuery(query=f"{title} {' '.join(intent.topics)} 主题 观点 原文", metadata_filter={"title": title}, purpose="comparison_balance"))

    return EvidenceAssessment(
        sufficient=not missing,
        covered_requirement_ids=unique_strings(covered),
        missing_aspects=unique_strings(missing),
        supplemental_queries=dedupe_queries(supplemental)[:4],
        reason="规则检查通过" if not missing else "；".join(unique_strings(missing)),
        used_llm=False,
    )


def requirement_matches(requirement: EvidenceRequirement, item: RetrievedChunk) -> bool:
    if requirement.target_books and not any(title in item.chunk.title for title in requirement.target_books):
        return False
    query_tokens = set(tokenize(requirement.query))
    text_tokens = set(tokenize(f"{item.chunk.title} {item.chunk.chapter_title} {item.chunk.text}"))
    if not query_tokens:
        return True
    return len(query_tokens & text_tokens) / len(query_tokens) >= 0.08


def combine_assessments(rule_result: EvidenceAssessment, llm_result: EvidenceAssessment) -> EvidenceAssessment:
    missing = unique_strings(rule_result.missing_aspects + llm_result.missing_aspects)
    queries = dedupe_queries(rule_result.supplemental_queries + llm_result.supplemental_queries)[:4]
    return EvidenceAssessment(
        sufficient=rule_result.sufficient and llm_result.sufficient,
        covered_requirement_ids=unique_strings(rule_result.covered_requirement_ids + llm_result.covered_requirement_ids),
        missing_aspects=missing,
        supplemental_queries=queries,
        reason=llm_result.reason or rule_result.reason,
        used_llm=True,
    )


def build_evidence_inventory(chunks: list[RetrievedChunk], max_chars: int) -> str:
    blocks: list[str] = []
    total = 0
    for index, item in enumerate(chunks, start=1):
        block = f"[{index}] {item.chunk.title} | {item.chunk.chapter_title}\n{item.chunk.text}\n"
        if blocks and total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)
    return "\n".join(blocks)


def parse_assessment(content: str) -> EvidenceAssessment:
    text = content.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end >= start:
        text = text[start : end + 1]
    return EvidenceAssessment(**json.loads(text), used_llm=True)


def book_filter(intent: IntentAnalysis) -> dict[str, str | list[str]]:
    return {"title": intent.book_titles} if intent.book_titles else {}


def dedupe_queries(queries: list[PlannedQuery]) -> list[PlannedQuery]:
    result: list[PlannedQuery] = []
    seen: set[tuple[str, str]] = set()
    for item in queries:
        key = (item.query.strip(), str(sorted(item.metadata_filter.items())))
        if item.query.strip() and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def unique_strings(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
