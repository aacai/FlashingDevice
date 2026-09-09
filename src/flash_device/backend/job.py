"""Framework-free flash job engine: ONE source of truth for progress.

The PyQt GUI drives jobs through JobManager: one edl subprocess,
parsed ProgressState, ring buffer of events, one edl at a time.

A job = one edl subprocess + parsed ProgressState + ring buffer of events.
Events are plain dicts: {seq, t, overall, op, file, file_pct, line}.
"""

from __future__ import annotations

import datetime
import itertools
import os
import queue
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field

from flash_device.backend.progress import ProgressState, is_progress_line, parse_line

MAX_LOG_LINES = 800
MAX_EVENTS = 2000

# Lines worth pushing to live subscribers / log ring even without progress change.
MILESTONE_KEYS = (
    "[qfil]",
    "Device detected",
    "firehose",
    "sahara",
    "loader",
    "Trying to connect",
    "Waiting for",
    "bootable",
    "not found",
    "doesn't exist",
    "ERROR",
    "Error",
    "error",
    "Failed",
    "failed",
)


def _ts() -> str:
    return datetime.datetime.now().astimezone().strftime("%H:%M:%S")


@dataclass
class FlashJob:
    id: str
    label: str
    args: list[str]
    total_files: int = 0
    cwd: str | None = None
    state: str = "queued"  # queued | running | done
    exit_code: int | None = None
    started_at: str = ""
    ended_at: str = ""
    progress: ProgressState = field(default_factory=ProgressState)
    log: deque = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    events: deque = field(default_factory=lambda: deque(maxlen=MAX_EVENTS))
    subscribers: list = field(default_factory=list)  # queue.Queue objects for SSE/fan-out
    _seq: itertools.count = field(default_factory=lambda: itertools.count(1), repr=False)
    _proc: subprocess.Popen | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- internal ---------------------------------------------------------
    def _emit(self, line: str = "") -> None:
        ev = {
            "seq": next(self._seq),
            "t": _ts(),
            "overall": round(self.progress.overall_pct, 1),
            "op": self.progress.op,
            "file": self.progress.current_file,
            "file_pct": round(self.progress.pct_in_file, 1),
            "file_idx": self.progress.file_index,
            "file_total": self.progress.total_files,
            "state": self.state,
            "line": line[-500:] if line else "",
        }
        self.events.append(ev)
        for q in list(self.subscribers):
            try:
                q.put_nowait(ev)
            except queue.Full:
                pass

    def _push_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        before = (
            self.progress.overall_pct,
            self.progress.op,
            self.progress.current_file,
            self.progress.pct_in_file,
        )
        parse_line(line, self.progress)
        after = (
            self.progress.overall_pct,
            self.progress.op,
            self.progress.current_file,
            self.progress.pct_in_file,
        )
        prog_only = is_progress_line(line)
        kw = any(k in line for k in MILESTONE_KEYS)
        changed = after != before
        # 纯进度行只更新事件、不进日志环（否则长刷机会把里程碑冲掉）；进度条/SSE 照样实时。
        if kw or not prog_only:
            self.log.append(f"[{_ts()}] {line}"[-2000:])
        if changed or kw:
            self._emit(line if (kw or not prog_only) else "")

    def snapshot(self, log_tail: int = 120) -> dict:
        with self._lock:
            tail = list(self.log)[-log_tail:]
        return {
            "id": self.id,
            "label": self.label,
            "state": self.state,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "overall": round(self.progress.overall_pct, 1),
            "op": self.progress.op,
            "file": self.progress.current_file,
            "file_pct": round(self.progress.pct_in_file, 1),
            "file_idx": self.progress.file_index,
            "file_total": self.progress.total_files,
            "log_tail": tail,
        }

    def stop(self) -> bool:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
            return True
        return False


class JobManager:
    """Owns all jobs. Thread-safe. Exactly one edl job runs at a time."""

    def __init__(self) -> None:
        self._jobs: dict[str, FlashJob] = {}
        self._lock = threading.Lock()
        self._counter = itertools.count(1)
        self._run_lock = threading.Lock()

    def start(
        self, label: str, args: list[str], total_files: int = 0, cwd: str | None = None
    ) -> FlashJob:
        with self._lock:
            jid = f"job-{next(self._counter):03d}"
            job = FlashJob(id=jid, label=label, args=args, total_files=total_files, cwd=cwd)
            job.progress.total_files = total_files
            self._jobs[jid] = job
        th = threading.Thread(target=self._run, args=(job,), daemon=True, name=f"flash-{jid}")
        th.start()
        return job

    def get(self, jid: str) -> FlashJob | None:
        with self._lock:
            return self._jobs.get(jid)

    def all(self) -> list[FlashJob]:
        with self._lock:
            return list(self._jobs.values())

    def latest(self) -> FlashJob | None:
        jobs = self.all()
        return jobs[-1] if jobs else None

    # -- worker -----------------------------------------------------------
    def _run(self, job: FlashJob) -> None:
        # Serialize edl access: two writers on one USB device = brick risk.
        if not self._run_lock.acquire(blocking=False):
            job.state = "done"
            job.exit_code = -2
            job.log.append(f"[{_ts()}] [!] 已有一个刷机任务在跑，本次拒绝启动（一次只刷一台）。")
            job._emit()
            return
        try:
            job.state = "running"
            job.started_at = _ts()
            job.log.append(f"[{_ts()}] $ {' '.join(job.args)}")
            job._emit()
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env.setdefault("PYTHONIOENCODING", "utf-8")
            try:
                job._proc = subprocess.Popen(
                    job.args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=env,
                    cwd=job.cwd,
                    text=True,
                    bufsize=1,
                    errors="replace",
                )
            except Exception as e:  # spawn failure
                job.log.append(f"[{_ts()}] [!] 启动失败: {e}")
                job.state = "done"
                job.exit_code = -1
                job.ended_at = _ts()
                job._emit()
                return
            assert job._proc.stdout is not None
            leftover = ""
            while True:
                chunk = job._proc.stdout.read(65536)
                if not chunk:
                    break
                # EDL rewrites progress with '\r' and only '\n' at milestones:
                # normalize both into lines so EVERY update is parsed.
                text = (leftover + chunk).replace("\r", "\n")
                *lines, leftover = text.split("\n")
                for ln in lines:
                    job._push_line(ln)
            if leftover.strip():
                job._push_line(leftover)
            job._proc.wait()
            job.exit_code = job._proc.returncode
            if job.exit_code == 0:
                job.progress.pct_in_file = 100.0
                job.progress.update_overall()
            job.log.append(f"[{_ts()}] === 结束，退出码 {job.exit_code} ===")
        finally:
            job.state = "done"
            job.ended_at = _ts()
            job._emit()
            self._run_lock.release()

    # -- helpers ----------------------------------------------------------
    def subscribe(self, jid: str) -> queue.Queue | None:
        job = self.get(jid)
        if job is None:
            return None
        q: queue.Queue = queue.Queue(maxsize=500)
        job.subscribers.append(q)
        # Catch-up: replay recent events so SSE starts with context.
        for ev in list(job.events)[-20:]:
            try:
                q.put_nowait(ev)
            except queue.Full:
                break
        return q

    def unsubscribe(self, jid: str, q: queue.Queue) -> None:
        job = self.get(jid)
        if job is not None and q in job.subscribers:
            job.subscribers.remove(q)


MANAGER = JobManager()
