"""Qt bridge: run a MANAGER job inside a QThread and re-emit Qt signals.

This is what makes the desktop GUI show EXACTLY the same progress as the
web dashboard: both read from the same FlashJob (backend/job.py).
Polls the job snapshot; never blocks the Qt event loop.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QThread, pyqtSignal

from flash_device.backend.job import MANAGER, FlashJob


class JobWorker(QThread):
    line = pyqtSignal(str)
    # overall_pct, op, current_file, file_pct
    progress = pyqtSignal(float, str, str, float)
    done = pyqtSignal(int)

    def __init__(self, label: str, args: list[str], total_files: int = 0, cwd: str | None = None):
        super().__init__()
        self.label = label
        self.args = args
        self.cwd = cwd
        self.total_files = total_files
        self.job: FlashJob | None = None
        # Compat mirror so GUI log code can read worker.state.pct_in_file.
        self.state = _StateMirror()
        self._stop_asked = False

    def run(self) -> None:
        self.job = MANAGER.start(self.label, self.args, total_files=self.total_files, cwd=self.cwd)
        last_log = 0
        last_ev = (None, None, None, None)
        try:
            while not self._stop_asked:
                with self.job._lock:
                    new_lines = list(self.job.log)[last_log:]
                    last_log = len(self.job.log)
                    snap = (
                        round(self.job.progress.overall_pct, 1),
                        self.job.progress.op,
                        self.job.progress.current_file,
                        round(self.job.progress.pct_in_file, 1),
                    )
                    finished = self.job.state == "done"
                    code = self.job.exit_code
                for ln in new_lines[-200:]:
                    self.line.emit(ln)
                self.state.pct_in_file = snap[3]
                if snap != last_ev:
                    last_ev = snap
                    self.progress.emit(*snap)
                if finished:
                    break
                time.sleep(0.15)
        finally:
            if self.job and self.job.exit_code is not None:
                code = self.job.exit_code
            else:
                code = -1
            self.done.emit(code)

    def kill(self) -> None:
        self._stop_asked = True
        if self.job is not None:
            self.job.stop()


class _StateMirror:
    pct_in_file: float = 0.0
