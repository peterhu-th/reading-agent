import os
from functools import cached_property

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from langchain_core.embeddings import Embeddings
from sentence_transformers import SentenceTransformer
from transformers.utils import logging as transformers_logging


transformers_logging.disable_progress_bar()


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
    def model(self) -> SentenceTransformer:
        return SentenceTransformer(
            self.model_name,
            device=self.device,
            local_files_only=self.local_files_only,
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
