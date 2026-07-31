from __future__ import annotations

import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import get_settings


def health_url_from_base_url(base_url: str) -> str:
    """Build AIClient2API's /health URL from an OpenAI-compatible base URL."""
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid OPENAI_BASE_URL: {base_url}")
    return f"{parsed.scheme}://{parsed.netloc}/health"


def is_api_healthy(health_url: str | None = None, timeout: float = 2.0) -> bool:
    settings = get_settings()
    url = health_url or health_url_from_base_url(settings.OPENAI_BASE_URL)

    try:
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()
            return data.get("status") == "healthy"
    except Exception:
        return False


def is_provider_ready(timeout: float = 4.0) -> bool:
    """Check whether AIClient2API has a healthy provider, not just a live process."""

    settings = get_settings()
    url = f"{settings.OPENAI_BASE_URL.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {settings.OPENAI_API_KEY}"}
    try:
        with httpx.Client(trust_env=False, timeout=timeout) as client:
            response = client.get(url, headers=headers)
            response.raise_for_status()
            return True
    except Exception:
        return False


def start_aiclient2api() -> None:
    """Start AIClient2API in the background via npm start."""
    settings = get_settings()
    project_dir = Path(settings.AICLIENT2API_DIR)
    if not project_dir.exists():
        raise FileNotFoundError(f"AIClient2API directory does not exist: {project_dir}")

    logs_dir = project_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stdout = open(logs_dir / "reading-agent-api.stdout.log", "a", encoding="utf-8")
    stderr = open(logs_dir / "reading-agent-api.stderr.log", "a", encoding="utf-8")

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen(
        ["npm.cmd", "start"],
        cwd=str(project_dir),
        stdout=stdout,
        stderr=stderr,
        stdin=subprocess.DEVNULL,
        creationflags=creationflags,
    )


def ensure_aiclient2api_running() -> bool:
    """Start AIClient2API if configured and wait until /health is healthy."""
    settings = get_settings()
    health_url = health_url_from_base_url(settings.OPENAI_BASE_URL)

    if is_api_healthy(health_url):
        return True
    if not settings.AUTO_START_API:
        return False

    start_aiclient2api()
    deadline = time.monotonic() + settings.API_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if is_api_healthy(health_url):
            return True
        time.sleep(1)

    return False
