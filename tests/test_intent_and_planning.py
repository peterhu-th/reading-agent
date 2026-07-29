from app.agent.intent_analyzer import fallback_intent, parse_intent_json
from app.config import Settings
from app.retrieval.query_planner import plan_retrieval


def make_settings() -> Settings:
    return Settings(
        OPENAI_API_KEY="test",
        OPENAI_BASE_URL="http://127.0.0.1:3000/v1",
        VECTOR_INITIAL_K=7,
        KEYWORD_INITIAL_K=8,
        RERANK_CANDIDATE_K=11,
        RERANK_TOP_K=9,
        FINAL_TOP_K=10,
        CONTEXT_MAX_CHARS=2000,
    )


def test_parse_intent_json_adds_question():
    intent = parse_intent_json(
        '{"labels":["specified_book"],"book_titles":["荒原狼"],"confidence":0.9}',
        "《荒原狼》讲了什么？",
    )

    assert intent.question == "《荒原狼》讲了什么？"
    assert intent.book_titles == ["荒原狼"]


def test_fallback_intent_extracts_title_and_summary():
    intent = fallback_intent("《荒原狼》主要内容是什么？")

    assert "specified_book" in intent.labels
    assert "summary" in intent.labels
    assert intent.book_titles == ["荒原狼"]


def test_query_planner_builds_filters_for_specified_book():
    intent = fallback_intent("只根据《百年孤独》回答布恩迪亚家族的循环")
    plan = plan_retrieval(intent, make_settings())

    assert plan.queries
    assert plan.queries[0].metadata_filter["title"] == ["百年孤独"]
    assert plan.vector_initial_k == 7
    assert plan.rerank_candidate_k == 11
