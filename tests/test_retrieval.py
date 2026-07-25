def test_vector_retriever_importable():
    from app.retrieval.vector_retriever import VectorRetriever

    assert VectorRetriever is not None


def test_local_hash_embeddings_are_deterministic():
    from app.retrieval.local_embeddings import LocalHashEmbeddings

    embeddings = LocalHashEmbeddings(dimensions=32)
    assert embeddings.embed_query("孤独") == embeddings.embed_query("孤独")
    assert len(embeddings.embed_query("孤独")) == 32


def test_embedding_factory_supports_local_hash(monkeypatch):
    from app.config import get_settings
    from app.retrieval.embedding_factory import make_embeddings
    from app.retrieval.local_embeddings import LocalHashEmbeddings

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:3000/v1")
    monkeypatch.setenv("EMBEDDING_BACKEND", "local-hash")
    monkeypatch.setenv("EMBEDDING_MODEL", "local-hash")
    monkeypatch.setenv("EMBEDDING_LOCAL_FILES_ONLY", "true")
    get_settings.cache_clear()

    assert isinstance(make_embeddings(), LocalHashEmbeddings)

    get_settings.cache_clear()
