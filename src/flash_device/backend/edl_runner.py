"""QThread worker that streams EDL output and emits structured progress."""

from __future__ import annotations

import os
import subprocess

from PyQt6.QtCore import QThread, pyqtSignal

from flash_device.backend.progress import ProgressState, parse_line


class CmdWorker(QThread):
    line = pyqtSignal(str)
    # overall_pct, op, current_file
    progress = pyqtSignal(float, str, str)
    done = pyqtSignal(int)

    def __init__(self, args: list[str], cwd: str | None = None, total_files: int = 0):
        super().__init__()
        self.args = args
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self.state = ProgressState(total_files=total_files)

    def run(self) -> None:
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        # Force UTF-8 so EDL block chars don't crash on Windows cp936
        env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            self.proc = subprocess.Popen(
                self.args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                cwd=self.cwd,
                text=True,
                bufsize=1,
                errors="replace",
            )
            assert self.proc.stdout is not None
            for ln in self.proc.stdout:
                text = ln.replace("\r", "\n").strip("\n")
                # EDL rewrites one \r line many times; split to catch last update
                for part in text.split("\n"):
                    part = part.strip()
                    if not part:
                        continue
                    self.line.emit(part[-2000:])
                    before = (self.state.overall_pct, self.state.op, self.state.current_file)
                    parse_line(part, self.state)
                    after = (self.state.overall_pct, self.state.op, self.state.current_file)
                    if after != before:
                        self.progress.emit(
                            self.state.overall_pct, self.state.op, self.state.current_file
                        )
            self.proc.wait()
            self.done.emit(self.proc.returncode)
        except Exception as e:
            self.line.emit(f"[!] 启动失败: {e}")
            self.done.emit(-1)

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
