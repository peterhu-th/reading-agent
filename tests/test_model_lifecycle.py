import sys
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import ModuleType

import numpy as np
import pytest

from app.config import Settings
from app.models.schemas import PlannedQuery, RetrievedChunk, TextChunk
from app.retrieval import cross_encoder_reranker, embedding_factory, model_runtime, reranker
from app.retrieval.cross_encoder_reranker import CrossEncoderReranker
from app.retrieval.sentence_transformer_embeddings import SentenceTransformerEmbeddings


@pytest.fixture
def fake_models(monkeypatch):
    model_runtime._load_model.cache_clear()
    created = {"embedding": [], "reranker": []}

    class Model:
        def __init__(self, kind, model_name, device, local_files_only):
            self.configuration = (model_name, device, local_files_only)
            self.batch_sizes = []
            created[kind].append(self)

        def encode(self, texts, **kwargs):
            self.batch_sizes.append(kwargs["batch_size"])
            return np.ones((len(texts), 3), dtype=np.float32)

        def predict(self, pairs, **_kwargs):
            return [0.75] * len(pairs)

    library = ModuleType("sentence_transformers")
    monkeypatch.setattr(library, "SentenceTransformer", lambda model_name, **kwargs: Model("embedding", model_name, **kwargs), raising=False)
    monkeypatch.setattr(library, "CrossEncoder", lambda model_name, **kwargs: Model("reranker", model_name, **kwargs), raising=False)
    monkeypatch.setitem(sys.modules, "sentence_transformers", library)
    yield created, library
    model_runtime._load_model.cache_clear()


@pytest.fixture
def settings(monkeypatch):
    value = Settings(
        OPENAI_API_KEY="test",
        OPENAI_BASE_URL="http://127.0.0.1:1/v1",
        EMBEDDING_BACKEND="sentence-transformers",
        EMBEDDING_MODEL="embedding-a",
        EMBEDDING_DEVICE="cpu",
        EMBEDDING_BATCH_SIZE=8,
        RERANK_BACKEND="cross_encoder",
        RERANK_MODEL="reranker-a",
        RERANK_DEVICE="cpu",
    )
    for module in (embedding_factory, cross_encoder_reranker, reranker):
        monkeypatch.setattr(module, "get_settings", lambda: value)
    return value


def test_body_and_summary_retrievers_lazily_share_embedding_weights(fake_models, settings, monkeypatch):
    from app.retrieval import summary_retriever, vector_retriever
    from app.retrieval.hybrid_retriever import EnhancedRetriever

    class VectorStore:
        def __init__(self, *, embedding_function, **_kwargs):
            self.embeddings = embedding_function

    monkeypatch.setattr(vector_retriever, "Chroma", VectorStore)
    monkeypatch.setattr(summary_retriever, "Chroma", VectorStore)
    created, _ = fake_models
    retriever = EnhancedRetriever()
    body_embeddings = retriever.vector.vectorstore.embeddings
    summary_embeddings = retriever.summary.vectorstore.embeddings

    assert created["embedding"] == []
    assert body_embeddings.embed_query("body question") == [1.0, 1.0, 1.0]
    assert summary_embeddings.embed_query("summary question") == [1.0, 1.0, 1.0]
    assert body_embeddings.model is summary_embeddings.model
    assert len(created["embedding"]) == 1


def test_embedding_batch_size_stays_per_instance_without_reloading_weights(fake_models, settings):
    created, _ = fake_models
    first = embedding_factory.make_embeddings()
    settings.EMBEDDING_BATCH_SIZE = 32
    second = embedding_factory.make_embeddings()

    first.embed_documents(["first batch"])
    second.embed_documents(["second batch"])

    assert first.model is second.model
    assert len(created["embedding"]) == 1
    assert first.model.batch_sizes == [8, 32]


def test_successive_reranking_rounds_reuse_model(fake_models, settings):
    created, _ = fake_models
    candidate = RetrievedChunk(chunk=TextChunk(
        chunk_id="book:0:0", book_id="book", title="Book", chapter_index=0,
        chunk_index=0, start_paragraph_index=0, end_paragraph_index=0,
        text="Evidence for a question.",
    ))
    assert created["reranker"] == []

    for _ in range(3):
        ranked, debug = reranker.rerank_chunks_with_debug([candidate], [PlannedQuery(query="question")], 1)
        assert ranked[0].cross_encoder_score == 0.75
        assert debug.used_cross_encoder

    assert len(created["reranker"]) == 1


@pytest.mark.parametrize("kind", ["embedding", "reranker"])
def test_concurrent_first_access_loads_one_model(fake_models, settings, monkeypatch, kind):
    created, library = fake_models
    constructor_name = "SentenceTransformer" if kind == "embedding" else "CrossEncoder"
    constructor = getattr(library, constructor_name)
    loading_started = Event()
    release_load = Event()
    callers_ready = Barrier(9)

    def slow_constructor(*args, **kwargs):
        loading_started.set()
        assert release_load.wait(timeout=5)
        return constructor(*args, **kwargs)

    monkeypatch.setattr(library, constructor_name, slow_constructor)

    def access_model():
        instance = embedding_factory.make_embeddings() if kind == "embedding" else CrossEncoderReranker()
        callers_ready.wait(timeout=5)
        return instance.model

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(access_model) for _ in range(8)]
        callers_ready.wait(timeout=5)
        try:
            assert loading_started.wait(timeout=5)
            # Let the other requests reach the same cold cache while construction is blocked.
            time.sleep(0.05)
        finally:
            release_load.set()
        models = [future.result(timeout=5) for future in futures]

    assert len(created[kind]) == 1
    assert all(model is models[0] for model in models)


@pytest.mark.parametrize("kind", ["embedding", "reranker"])
def test_model_name_device_and_offline_configuration_are_isolated(fake_models, kind):
    created, _ = fake_models
    getter = model_runtime.get_embedding_model if kind == "embedding" else model_runtime.get_reranker_model
    original = getter("model-a", "cpu", True)
    configurations = [("model-b", "cpu", True), ("model-a", "cuda", True), ("model-a", "cpu", False)]

    models = [getter(*configuration) for configuration in configurations]

    assert all(model is not original for model in models)
    assert [model.configuration for model in models] == configurations
    assert getter("model-a", "cpu", True) is original
    assert len(created[kind]) == 4


def test_model_types_never_share_the_same_cache_entry(fake_models):
    embedding = model_runtime.get_embedding_model("same-name", "cpu", True)
    cross_encoder = model_runtime.get_reranker_model("same-name", "cpu", True)

    assert embedding is not cross_encoder


def test_failed_load_can_be_retried(fake_models, monkeypatch):
    created, library = fake_models
    constructor = library.SentenceTransformer
    attempts = []

    def fail_once(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            raise OSError("Model directory is temporarily unavailable")
        return constructor(*args, **kwargs)

    monkeypatch.setattr(library, "SentenceTransformer", fail_once)
    embeddings = SentenceTransformerEmbeddings("retry-model", device="cpu")
    with pytest.raises(OSError, match="temporarily unavailable"):
        embeddings.embed_query("first")

    assert embeddings.embed_query("retry") == [1.0, 1.0, 1.0]
    assert len(attempts) == 2
    assert len(created["embedding"]) == 1
