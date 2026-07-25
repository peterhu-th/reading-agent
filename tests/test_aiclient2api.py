from app.services.aiclient2api import health_url_from_base_url


def test_health_url_from_provider_base_url():
    assert (
        health_url_from_base_url("http://127.0.0.1:3000/openai-codex-oauth/v1")
        == "http://127.0.0.1:3000/health"
    )


def test_health_url_from_plain_openai_base_url():
    assert health_url_from_base_url("http://127.0.0.1:3000/v1") == "http://127.0.0.1:3000/health"
