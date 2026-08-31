from pathlib import Path

from scripts import run_web


def test_run_web_does_not_start_model_gateway():
    source = Path("scripts/run_web.py").read_text(encoding="utf-8")
    assert "ensure_aiclient2api_running" not in source
    assert "aiclient2api" not in source.lower()


def test_open_browser_uses_loopback_url(monkeypatch):
    opened = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(run_web.socket, "create_connection", lambda *_args, **_kwargs: Connection())
    monkeypatch.setattr(run_web.webbrowser, "open", opened.append)

    run_web.open_browser_when_ready(8000)

    assert opened == ["http://127.0.0.1:8000"]
