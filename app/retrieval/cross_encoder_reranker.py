from functools import cached_property

from app.config import get_settings
from app.models.schemas import PlannedQuery, RetrievedChunk, RerankDebugInfo


class CrossEncoderReranker:
    """Optional local cross-encoder reranker with graceful fallback."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.debug = RerankDebugInfo(backend=self.settings.RERANK_BACKEND)

    @cached_property
    def model(self):
        from sentence_transformers import CrossEncoder

        return CrossEncoder(
            self.settings.RERANK_MODEL,
            device=self.settings.RERANK_DEVICE,
            local_files_only=self.settings.EMBEDDING_LOCAL_FILES_ONLY,
        )

    def rerank(
        self,
        candidates: list[RetrievedChunk],
        planned_queries: list[PlannedQuery],
    ) -> tuple[list[RetrievedChunk], RerankDebugInfo]:
        if not candidates:
            return [], self.debug

        query = build_rerank_query(planned_queries)
        pairs = [(query, item.chunk.text[:1200]) for item in candidates]
        try:
            scores = self.model.predict(pairs, show_progress_bar=False)
        except Exception as exc:
            return candidates, RerankDebugInfo(
                backend=self.settings.RERANK_BACKEND,
                used_cross_encoder=False,
                fallback_reason=str(exc),
            )

        reranked: list[RetrievedChunk] = []
        for item, raw_score in zip(candidates, scores, strict=False):
            score = float(raw_score)
            reranked.append(
                item.model_copy(
                    update={
                        "cross_encoder_score": score,
                        "rerank_score": score,
                        "score": score,
                        "debug_reason": f"cross_encoder={score:.3f}",
                    }
                )
            )
        reranked.sort(key=lambda item: item.cross_encoder_score or 0.0, reverse=True)
        return reranked, RerankDebugInfo(
            backend=self.settings.RERANK_BACKEND,
            used_cross_encoder=True,
        )


def build_rerank_query(planned_queries: list[PlannedQuery]) -> str:
    if not planned_queries:
        return ""
    original = next((query.query for query in planned_queries if query.purpose == "original"), "")
    if original:
        return original
    return planned_queries[0].query
