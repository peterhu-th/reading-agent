import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.aiclient2api import ensure_aiclient2api_running, health_url_from_base_url


def main() -> None:
    settings = get_settings()
    health_url = health_url_from_base_url(settings.OPENAI_BASE_URL)
    print(f"Checking AIClient2API: {health_url}")

    if ensure_aiclient2api_running():
        print("AIClient2API is healthy.")
        return

    raise SystemExit(
        "AIClient2API did not become healthy. "
        f"Check logs under {settings.AICLIENT2API_DIR}/logs."
    )


if __name__ == "__main__":
    main()
