import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.run_web import configure_console, main as run_web
from app.services.aiclient2api import ensure_aiclient2api_running


def main() -> None:
    configure_console()
    if ensure_aiclient2api_running():
        print("模型服务已就绪。", flush=True)
    else:
        print("模型服务未就绪，阅读器仍会启动，聊天暂不可用。", flush=True)
    run_web()


if __name__ == "__main__":
    main()
