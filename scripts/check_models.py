import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings


REQUIRED_FILES = ("config.json",)


def main() -> None:
    settings = get_settings()
    checks = [
        ("embedding", settings.EMBEDDING_MODEL),
        ("reranker", settings.RERANK_MODEL),
    ]

    ok = True
    for name, model_path in checks:
        path = Path(model_path)
        if path.exists() and path.is_dir() and any((path / item).exists() for item in REQUIRED_FILES):
            print(f"[ok] {name}: {path}")
        else:
            ok = False
            print(f"[missing] {name}: {path}")

    if not ok:
        raise SystemExit(
            "Local model files are incomplete. Put models under ./models or update .env paths."
        )


if __name__ == "__main__":
    main()
