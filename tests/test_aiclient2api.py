from app.services.aiclient2api import health_url_from_base_url
from app.agent.answer_generator import translate_llm_error


def test_health_url_from_openai_compatible_base_url():
    assert (
        health_url_from_base_url("http://127.0.0.1:3000/openai-codex-oauth/v1")
        == "http://127.0.0.1:3000/health"
    )


def test_health_url_from_plain_v1_base_url():
    assert health_url_from_base_url("http://localhost:3000/v1") == "http://localhost:3000/health"


def test_provider_pool_error_has_actionable_message():
    error = translate_llm_error(RuntimeError("No healthy provider found in pool"))

    assert "重新登录" in str(error)
    assert "健康节点" in str(error)
