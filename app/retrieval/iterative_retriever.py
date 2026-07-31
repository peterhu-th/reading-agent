from collections.abc import Callable

from app.config import get_settings
from app.models.schemas import ConversationSession, EvidenceAssessment, IterativeRetrievalResult, RetrievalDebugInfo, RetrievalPlan, RetrievalRound, RetrievedChunk
from app.retrieval.evidence_checker import assess_evidence
from app.retrieval.hybrid_retriever import EnhancedRetriever, balance_comparison_results
from app.retrieval.reranker import rerank_chunks_with_debug


StatusCallback = Callable[[str, int, str], None]


class IterativeRetriever:
    """Run retrieve-check-supplement retrieval with a hard round limit."""

    def __init__(self, retriever: EnhancedRetriever | None = None) -> None:
        self.retriever = retriever or EnhancedRetriever()
        self.settings = get_settings()

    def search(self, question: str, conversation: ConversationSession | None = None, debug: bool = False, status_callback: StatusCallback | None = None) -> IterativeRetrievalResult:
        notify(status_callback, "retrieving", 0, "正在检索原文")
        initial = self.retriever.search(question, debug=debug, conversation=conversation)
        all_chunks = list(initial.chunks)
        all_queries = list(initial.plan.queries)
        rounds: list[RetrievalRound] = []

        notify(status_callback, "checking", 0, "正在检查证据覆盖")
        assessment = assess_evidence(question, initial.intent, all_chunks)
        rounds.append(RetrievalRound(round_index=0, queries=initial.plan.queries, candidate_count=initial.debug.candidate_count, new_chunk_count=len(initial.chunks), assessment=assessment))

        for round_index in range(1, self.settings.RETRIEVAL_MAX_SUPPLEMENT_ROUNDS + 1):
            if assessment.sufficient:
                rounds[-1].stop_reason = "evidence_sufficient"
                break
            if not assessment.supplemental_queries:
                rounds[-1].stop_reason = "no_supplemental_query"
                break

            notify(status_callback, "supplementing", round_index, f"正在进行第 {round_index} 轮补充检索")
            executed_queries = assessment.supplemental_queries
            supplement_plan = initial.plan.model_copy(update={"queries": executed_queries, "use_summary_index": initial.plan.use_summary_index})
            supplement = self.retriever.search_with_plan(initial.intent, supplement_plan, debug=debug)
            before = len({item.chunk.chunk_id for item in all_chunks})
            all_chunks = merge_chunks(all_chunks, supplement.chunks)
            new_count = len(all_chunks) - before
            all_queries.extend(executed_queries)
            all_chunks = self.rerank_final(all_chunks, all_queries, initial.plan, initial.intent)

            notify(status_callback, "checking", round_index, f"正在检查第 {round_index} 轮新增证据")
            assessment = assess_evidence(question, initial.intent, all_chunks)
            rounds.append(RetrievalRound(round_index=round_index, queries=executed_queries, candidate_count=supplement.debug.candidate_count, new_chunk_count=new_count, assessment=assessment))
            if new_count == 0:
                rounds[-1].stop_reason = "no_new_chunks"
                break
            if round_index == self.settings.RETRIEVAL_MAX_SUPPLEMENT_ROUNDS:
                rounds[-1].stop_reason = "max_rounds"

        debug_info = initial.debug.model_copy(update={"final_count": len(all_chunks)})
        if debug:
            debug_info.lines.extend(build_round_debug(rounds))
        return IterativeRetrievalResult(chunks=all_chunks, intent=initial.intent, plan=initial.plan, assessment=assessment, rounds=rounds, debug=debug_info)

    def rerank_final(self, chunks: list[RetrievedChunk], queries, plan: RetrievalPlan, intent) -> list[RetrievedChunk]:
        reranked, _ = rerank_chunks_with_debug(chunks, queries, max(plan.rerank_top_k, plan.final_top_k))
        balanced = balance_comparison_results(reranked, intent, max(plan.rerank_top_k, plan.final_top_k))
        expanded = self.retriever.expand_neighbors(balanced, plan.neighbor_window)
        return self.retriever.apply_context_budget(expanded, plan)


def merge_chunks(left: list[RetrievedChunk], right: list[RetrievedChunk]) -> list[RetrievedChunk]:
    merged = {item.chunk.chunk_id: item for item in left}
    for item in right:
        existing = merged.get(item.chunk.chunk_id)
        if existing is None or (item.rerank_score or item.score or 0.0) > (existing.rerank_score or existing.score or 0.0):
            merged[item.chunk.chunk_id] = item
    return list(merged.values())


def notify(callback: StatusCallback | None, stage: str, round_index: int, message: str) -> None:
    if callback:
        callback(stage, round_index, message)


def build_round_debug(rounds: list[RetrievalRound]) -> list[str]:
    return [f"第 {item.round_index} 轮：新增 {item.new_chunk_count} 条；证据{'充分' if item.assessment and item.assessment.sufficient else '不足'}；停止原因={item.stop_reason or '继续'}" for item in rounds]
