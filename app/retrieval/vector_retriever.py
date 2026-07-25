from langchain_chroma import Chroma

from app.config import get_settings
from app.models.schemas import RetrievedChunk, TextChunk
from app.retrieval.embedding_factory import make_embeddings


class VectorRetriever:
    """Retrieve chunks from the persisted Chroma index."""

    def __init__(self) -> None:
        settings = get_settings()
        self.vectorstore = Chroma(
            collection_name=settings.CHROMA_COLLECTION,
            embedding_function=make_embeddings(),
            persist_directory=settings.VECTOR_DB_PATH,
        )

    def search(self, query: str, top_k: int = 5) -> list[RetrievedChunk]:
        query = query.strip()
        if not query:
            return []

        results = self.vectorstore.similarity_search_with_score(query, k=top_k)
        retrieved: list[RetrievedChunk] = []
        for document, score in results:
            data = dict(document.metadata)
            data["text"] = document.page_content
            retrieved.append(RetrievedChunk(chunk=TextChunk(**data), score=score))
        return retrieved
