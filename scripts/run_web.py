import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from app.config import get_settings
from app.services.aiclient2api import ensure_aiclient2api_running


def main() -> None:
    settings = get_settings()
    if not ensure_aiclient2api_running():
        raise SystemExit("AIClient2API 未就绪，请先完成登录或运行 python scripts/start_api.py。")
    print(f"阅读助理将在 http://{settings.WEB_HOST}:{settings.WEB_PORT} 启动")
    uvicorn.run("app.web.api:app", host=settings.WEB_HOST, port=settings.WEB_PORT, reload=False)


if __name__ == "__main__":
    main()
