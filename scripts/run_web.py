import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn

from app.config import get_settings


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8")


def open_browser_when_ready(port: int, timeout: float = 30.0) -> None:
    """Open the local reader after Uvicorn starts accepting connections."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                webbrowser.open(f"http://127.0.0.1:{port}")
                return
        except OSError:
            time.sleep(0.1)


def main() -> None:
    configure_console()
    settings = get_settings()
    browser_url = f"http://127.0.0.1:{settings.WEB_PORT}"
    print(f"阅读器将在 {browser_url} 启动", flush=True)
    print("模型服务不会由此脚本启动；聊天不可用时请另行运行 python scripts/start_api.py。", flush=True)
    threading.Thread(
        target=open_browser_when_ready,
        args=(settings.WEB_PORT,),
        name="open-reader-browser",
        daemon=True,
    ).start()
    uvicorn.run("app.web.api:app", host=settings.WEB_HOST, port=settings.WEB_PORT, reload=False)


if __name__ == "__main__":
    main()
