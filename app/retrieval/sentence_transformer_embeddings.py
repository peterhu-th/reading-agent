from functools import cached_property
from typing import TYPE_CHECKING

from langchain_core.embeddings import Embeddings

from app.retrieval.model_runtime import get_embedding_model

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class SentenceTransformerEmbeddings(Embeddings):
    """LangChain-compatible embeddings backed by sentence-transformers."""

    def __init__(
        self,
        model_name: str,
        device: str = "cuda",
        batch_size: int = 64,
        local_files_only: bool = True,
    ) -> None:
        if not model_name:
            raise ValueError("model_name must not be empty")
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than 0")

        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self.local_files_only = local_files_only

    @cached_property
    def model(self) -> "SentenceTransformer":
        return get_embedding_model(
            self.model_name,
            self.device,
            self.local_files_only,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._encode([text])[0]

    def _encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        embeddings = self.model.encode(
            texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.astype("float32").tolist()
