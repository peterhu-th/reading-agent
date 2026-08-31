import subprocess
import sys
from pathlib import Path


class DatabaseUpdater:
    def __init__(self) -> None:
        self.root_dir = Path(__file__).resolve().parents[1]
        self.scripts = [
            ("解析书籍并分块", "scripts/ingest_books.py"),
            ("重建阅读器书库", "scripts/build_reader_library.py"),
            ("重建分块向量索引", "scripts/rebuild_index.py"),
            ("生成书籍摘要", "scripts/build_summaries.py"),
            ("重建摘要向量索引", "scripts/rebuild_summary_index.py"),
        ]

    def run_script(self, name: str, script_path: str) -> None:
        print(f"[{name}] 正在执行 {script_path} ...")
        python_exe = sys.executable
        target_path = self.root_dir / script_path
        result = subprocess.run(
            [python_exe, str(target_path)],
            cwd=self.root_dir,
            capture_output=False,
            text=True
        )
        if result.returncode != 0:
            print(f"[{name}] 执行失败，退出码: {result.returncode}")
            sys.exit(result.returncode)
        print(f"[{name}] 执行完成。\n")

    def update_all(self) -> None:
        print("开始自动更新数据库...\n")
        for name, script_path in self.scripts:
            self.run_script(name, script_path) 
        print("所有数据库更新步骤均已成功完成！请记得重启 Web 服务以更新内存缓存。")


def main() -> None:
    updater = DatabaseUpdater()
    updater.update_all()


if __name__ == "__main__":
    main()
