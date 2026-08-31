import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.ingestion.reader_epub import load_epub_for_reader
from scripts.run_web import configure_console


def main() -> None:
    configure_console()
    settings = get_settings()
    epub_paths = sorted(Path(settings.RAW_EPUB_DIR).glob("*.epub"))
    output = Path(settings.READER_JSONL_PATH)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    total = 0
    with temporary.open("w", encoding="utf-8") as file:
        for index, epub_path in enumerate(epub_paths, start=1):
            records = load_epub_for_reader(epub_path)
            for record in records:
                file.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")
            total += len(records)
            chapters = len({record.chapter_index for record in records})
            print(f"[{index}/{len(epub_paths)}] {epub_path.name}: {chapters} 个目录项，{len(records)} 个文本块", flush=True)
    temporary.replace(output)
    print(f"阅读数据已生成：{len(epub_paths)} 本书，{total} 个文本块。", flush=True)


if __name__ == "__main__":
    main()
