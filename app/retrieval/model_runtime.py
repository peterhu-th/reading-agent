"""Lazily share local model weights across retrievers in one process."""

import os
from functools import lru_cache
from threading import Lock
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder, SentenceTransformer


os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
_load_lock = Lock()


@lru_cache(maxsize=None)
def _load_model(
    kind: Literal["embedding", "reranker"],
    model_name: str,
    device: str,
    local_files_only: bool,
):
    from sentence_transformers import CrossEncoder, SentenceTransformer
    from transformers.utils import logging as transformers_logging

    transformers_logging.disable_progress_bar()
    model_class = SentenceTransformer if kind == "embedding" else CrossEncoder
    return model_class(model_name, device=device, local_files_only=local_files_only)


def get_embedding_model(model_name: str, device: str, local_files_only: bool) -> "SentenceTransformer":
    # lru_cache alone can run its factory more than once on concurrent misses.
    with _load_lock:
        return cast("SentenceTransformer", _load_model("embedding", model_name, device, local_files_only))


def get_reranker_model(model_name: str, device: str, local_files_only: bool) -> "CrossEncoder":
    with _load_lock:
        return cast("CrossEncoder", _load_model("reranker", model_name, device, local_files_only))
