"""状态同步服务：只暴露 JSON 接口，没有网页。

  python -m flash_device.server [--host 127.0.0.1] [--port 8899]

  GET  /api/state   → 共享刷机配置 {loader, fwdir, memory, updated_at, ...}
  POST /api/state   → {loader?, fwdir?, memory?} 合并写入，返回最新
  GET  /api/status  → {has_edl, devices, latest_job, state}

用途：CLI 改完配置（python -m flash_device.state --set-loader …，
或 curl POST），GUI 打开即见最新；GUI 里改了也会回写，CLI 照样看得到。
"""

from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from flash_device import state as shared
from flash_device.backend.job import MANAGER
from flash_device.safety import guards
from flash_device.utils import platform as pf
from flash_device.utils.envcheck import format_checks, run_all_checks
from flash_device.utils.logging_setup import get_logger, setup_logging
from flash_device.utils.usb import get_edl_serial, rank_device, scan_devices

logger = get_logger(__name__)


class Handler(BaseHTTPRequestHandler):
    server_version = "FlashingDevice/0.3"

    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0 or n > 1 << 20:
            return {}
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":
            self._json(shared.load())
            return
        if path == "/api/status":
            devs = sorted(scan_devices(), key=rank_device)
            edl = [d for d in devs if d[3] in guards.EDL_PIDS]
            latest = MANAGER.latest()
            self._json(
                {
                    "has_edl": bool(edl),
                    "edl_serial": get_edl_serial() if edl else "",
                    "devices": [
                        f"{v:04x}:{p:04x} bus={b} port={pt} {n}".strip()
                        for b, pt, v, p, n in devs[:8]
                    ],
                    "latest_job": latest.snapshot(log_tail=0) if latest else None,
                    "state": shared.load(),
                    "platform": pf.system_info(),
                }
            )
            return
        self._json({"error": "not found, try /api/state or /api/status"}, 404)

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/state":
            data = self._body()
            if not any(k in data for k in ("loader", "fwdir", "memory")):
                self._json({"error": "body needs at least one of: loader, fwdir, memory"}, 400)
                return
            self._json(shared.save(data, by="http"))
            return
        self._json({"error": "not found"}, 404)


def _looks_like_ours(host: str, port: int) -> bool:
    """端口被占时探一下：在跑的是否就是我们的状态服务（是就直接复用）。"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/state", timeout=2) as r:
            body = json.loads(r.read() or b"{}")
        return isinstance(body, dict) and "loader" in body and "memory" in body
    except Exception:
        return False


_started: dict[tuple[str, int], object] = {}


def ensure_started(host: str = "127.0.0.1", port: int = 8899):
    """幂等启动状态服务（daemon 线程）。返回 (ok, url_or_reason)。

    - 已在本进程起过 → 直接复用；
    - 端口上已是我们的服务（独立起的）→ 复用；
    - 端口被陌生进程占用 → (False, 原因)，调用方只记录不硬拦刷机。
    """
    import threading

    key = (host, port)
    if key in _started:
        return True, f"http://{host}:{port}"
    try:
        srv = ThreadingHTTPServer((host, port), Handler)
    except OSError:
        if _looks_like_ours(host, port):
            _started[key] = None
            logger.info("state server already running at http://%s:%s, reuse", host, port)
            return True, f"http://{host}:{port}"
        reason = f"端口 {port} 被其他程序占用，状态同步接口不可用"
        logger.warning(reason)
        return False, reason
    if port == 0:
        port = srv.server_address[1]
        key = (host, port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _started[key] = srv
    logger.info("state server started at http://%s:%s", host, port)
    return True, f"http://{host}:{port}"


def shutdown(host: str = "127.0.0.1", port: int = 8899) -> None:
    """停掉本进程内 ensure_started 拉起的服务（独立进程起的不碰）。"""
    srv = _started.pop((host, port), None)
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="flash-device-server")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8899)
    ns = ap.parse_args(argv)
    setup_logging("INFO")
    logger.info("env:\n%s", format_checks(run_all_checks()))
    srv = ThreadingHTTPServer((ns.host, ns.port), Handler)
    print(f">>> 状态同步服务 http://{ns.host}:{ns.port}  (Ctrl+C 停止)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
