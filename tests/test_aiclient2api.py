from app.services.aiclient2api import health_url_from_base_url


def test_health_url_from_openai_compatible_base_url():
    assert (
        health_url_from_base_url("http://127.0.0.1:3000/openai-codex-oauth/v1")
        == "http://127.0.0.1:3000/health"
    )


def test_health_url_from_plain_v1_base_url():
    assert health_url_from_base_url("http://localhost:3000/v1") == "http://localhost:3000/health"
