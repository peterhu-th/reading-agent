from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from threading import Lock, Thread


class DatabaseUpdateBusyError(RuntimeError):
    pass


class DatabaseUpdateService:
    """Run the existing database pipeline independently from editor saves."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._lock = Lock()
        self._running = False
        self._status = "idle"
        self._message = ""
        self._return_code: int | None = None

    def start(self, unsaved_changes: bool) -> dict:
        with self._lock:
            if self._running:
                raise DatabaseUpdateBusyError("数据库正在更新。")
            self._running = True
            self._status = "running"
            self._message = "正在启动数据库更新；未保存的编辑不会包含在本次更新中。" if unsaved_changes else "正在启动数据库更新。"
            self._return_code = None
        Thread(target=self._run, name="update-reading-database", daemon=True).start()
        return self.state()

    def state(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "status": self._status,
                "message": self._message,
                "return_code": self._return_code,
            }

    def _run(self) -> None:
        target = self.root / "scripts" / "update_database.py"
        try:
            process = subprocess.Popen(
                [sys.executable, str(target)],
                cwd=self.root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            assert process.stdout is not None
            for line in process.stdout:
                message = line.strip()
                if message:
                    with self._lock:
                        self._message = message
            return_code = process.wait()
            with self._lock:
                self._return_code = return_code
                self._status = "complete" if return_code == 0 else "failed"
                if return_code == 0:
                    self._message = "数据库更新完成；重新启动服务后将从新数据库载入书库。"
                elif not self._message:
                    self._message = f"数据库更新失败，退出码 {return_code}。"
        except Exception as exc:
            with self._lock:
                self._status = "failed"
                self._message = f"数据库更新失败：{exc}"
                self._return_code = -1
        finally:
            with self._lock:
                self._running = False
