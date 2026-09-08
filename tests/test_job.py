import time

from flash_device.backend.job import JobManager


def _fake_cmd(n=5):
    lines = [
        "print('[qfil] raw programming...')",
        "print('[qfil] programming /tmp/a.img to partition(0)...')",
    ]
    for i in range(n):
        pct = i * 100.0 / max(n - 1, 1)
        bar = "█" * i + "-" * (n - 1 - i)
        lines.append(
            f"print('Progress: |{bar}| {pct}% Write (Sector 0x{i} of 0x{n}) 20 MB/s', flush=True)"
        )
    lines.append("print('[qfil] raw programming ok.')")
    return ["python3", "-c", ";".join(lines)]


def test_job_runs_and_reports_progress():
    m = JobManager()
    job = m.start("SIM", _fake_cmd(), total_files=2)
    deadline = time.time() + 30
    while job.state != "done" and time.time() < deadline:
        time.sleep(0.1)
    assert job.state == "done"
    assert job.exit_code == 0
    snap = job.snapshot()
    assert snap["overall"] > 0
    assert snap["file"] == "/tmp/a.img"
    # pure progress lines must NOT flood the log ring
    assert not any(
        l.startswith("Progress:") or "Progress:" in l.split("] ", 1)[-1][:12]
        for l in snap["log_tail"]
    )
    assert any("[qfil]" in l for l in snap["log_tail"])


def test_job_subscribe_replay():
    m = JobManager()
    job = m.start("SIM", _fake_cmd(3), total_files=1)
    deadline = time.time() + 30
    while job.state != "done" and time.time() < deadline:
        time.sleep(0.1)
    q = m.subscribe(job.id)
    assert q is not None and not q.empty()
    m.unsubscribe(job.id, q)


def test_job_stop():
    m = JobManager()
    job = m.start("HANG", ["python3", "-c", "import time; time.sleep(60)"])
    time.sleep(0.5)
    assert job.stop()
    deadline = time.time() + 15
    while job.state != "done" and time.time() < deadline:
        time.sleep(0.1)
    assert job.state == "done"
