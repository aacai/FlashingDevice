"""FlashingDevice HTTP server (stdlib only): progress for ANY frontend.

  python -m flash_device.server [--port 8899]

Endpoints:
  GET  /                        tiny live dashboard (bars + log + start/stop)
  GET  /api/status              {has_edl, devices, latest_job}
  GET  /api/devices             usb scan
  POST /api/qfil                {fwdir, loader, memory} -> {job_id} (all rawprograms!)
  GET  /api/jobs                [snapshots...]
  GET  /api/jobs/<id>           snapshot + log_tail
  GET  /api/jobs/<id>/events    SSE stream of progress events
  POST /api/jobs/<id>/stop      kill job
"""

from __future__ import annotations

import json
import os
import queue
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from flash_device.backend.job import MANAGER
from flash_device.backend.qfil import build_qfil_argv
from flash_device.safety import guards
from flash_device.utils.envcheck import format_checks, run_all_checks
from flash_device.utils.logging_setup import get_logger, setup_logging
from flash_device.utils.usb import scan_devices

logger = get_logger(__name__)

DASHBOARD = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FlashingDevice · 刷机控制台</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{background:#0f1115;color:#e8ebf1;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0}
.wrap{max-width:860px;margin:0 auto;padding:20px 14px 60px}
.card{background:#171b22;border:1px solid #2a3140;border-radius:14px;padding:18px;margin-bottom:16px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:0 0 10px;color:#9fb4d8}
.dot{font-size:22px;vertical-align:middle}.ok{color:#3ddc84}.bad{color:#ff5d5d}.warn{color:#ffb020}
.bar{height:22px;background:#262c38;border-radius:11px;overflow:hidden;margin:8px 0}
.bar>i{display:block;height:100%;width:0%;border-radius:11px;background:linear-gradient(90deg,#2f6fed,#39c2ff);transition:width .3s}
.bar.file>i{background:linear-gradient(90deg,#7a5cff,#c86bff)}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
button{background:#2b6cb0;border:0;color:#fff;border-radius:9px;padding:10px 16px;font-size:14px;cursor:pointer}
button.danger{background:#a02a37}button.ghost{background:#2a3140}button:disabled{opacity:.4}
input,select{background:#0f141b;border:1px solid #2a3140;color:#e8ebf1;border-radius:8px;padding:9px 10px;font-size:13px;width:100%}
label{font-size:12px;color:#9aa3b2;display:block;margin:8px 0 3px}
#log{background:#0b0e12;border:1px solid #2a3140;border-radius:10px;padding:12px;height:300px;overflow-y:auto;
font-family:Menlo,Consolas,monospace;font-size:12px;color:#b9f0c9;white-space:pre-wrap}
.mut{color:#8b93a5;font-size:12px}.mono{font-family:Menlo,monospace;font-size:12px}
</style></head><body><div class="wrap">
<h1>🔥 FlashingDevice 刷机控制台</h1>
<div class="mut">HTTP + SSE 实时进度 · 与桌面 GUI 同源（同一 job 引擎）</div>

<div class="card"><h2>设备状态 <span id="upd" class="mut"></span></h2>
<div id="dev">检测中…</div></div>

<div class="card"><h2>整包刷入（QFIL · 全部 rawprogram）</h2>
<label>固件目录</label><input id="fwdir" placeholder="/…/lg_v50/extract/KT">
<div class="row">
<button data-fw="KT">KT</button><button data-fw="LGU">LGU</button><button data-fw="SKT">SKT</button>
</div>
<label>Firehose loader</label><input id="loader" placeholder="…/prog_firehose_*.elf">
<label>存储类型</label><select id="mem"><option>ufs</option><option>emmc</option><option>nand</option></select>
<div class="row"><button id="go" class="danger">⚠ 开始整包刷入</button><button id="stop" class="ghost">停止当前任务</button></div>
<div class="mut">快捷按钮自动填入本机 LG V50 路径；loader 需自备（版权原因不进仓）。</div>
</div>

<div class="card"><h2>进度 <span id="op" class="mut"></span></h2>
<div class="mut">总进度 <b id="pt">0%</b></div><div class="bar"><i id="bt"></i></div>
<div class="mut">当前文件 <b id="pf">0%</b> <span id="fn" class="mono"></span></div><div class="bar file"><i id="bf"></i></div>
</div>

<div class="card"><h2>实时日志</h2><div id="log"></div></div>
</div>
<script>
const $=id=>document.getElementById(id);
const BASE="/Users/zhiqiu/AndroidStudioProjects/shuaji/lg_v50/extract/";
document.querySelectorAll("[data-fw]").forEach(b=>b.onclick=()=>{
  $("fwdir").value=BASE+b.dataset.fw;
  $("loader").value="/Users/zhiqiu/AndroidStudioProjects/shuaji/FlashingDevice/firmware/loaders/prog_ufs_firehose_sm8150_ddr.elf";
});
let jobId=null,es=null,lastUpd=0;
async function api(p,o){const r=await fetch(p,o);return r.json();}
async function status(){
  const s=await api("/api/status");
  $("dev").innerHTML=s.has_edl?'<span class="dot ok">●</span> <b class="ok">9008 已连接</b> <span class="mono">'+s.devices[0]+'</span>'
    :'<span class="dot bad">●</span> 没检测到 9008（关机进 9008 后直连 USB-C）';
  if(s.latest_job&&s.latest_job.id!==jobId){jobId=s.latest_job.id;watch(jobId);}
}
function paint(j){
  $("bt").style.width=j.overall+"%";$("pt").textContent=j.overall+"%";
  $("bf").style.width=j.file_pct+"%";$("pf").textContent=j.file_pct+"%";
  $("fn").textContent=(j.file||"")+" "+(j.op||"");
  $("op").textContent="["+j.state+"] "+(j.label||"");
  lastUpd=Date.now();
}
function addLog(lines){const el=$("log");let atBottom=el.scrollHeight-el.scrollTop-el.clientHeight<40;
  el.textContent+=(el.textContent?"\\n":"")+lines.join("\\n");
  const rows=el.textContent.split("\\n");if(rows.length>600)el.textContent=rows.slice(-600).join("\\n");
  if(atBottom)el.scrollTop=el.scrollHeight;}
function watch(id){
  if(es)es.close();
  es=new EventSource("/api/jobs/"+id+"/events");
  es.onmessage=e=>{const j=JSON.parse(e.data);paint(j);if(j.line)addLog([j.line]);};
}
$("go").onclick=async()=>{
  if(!confirm("整包刷入会清空全部分区，确定？"))return;
  const r=await api("/api/qfil",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({fwdir:$("fwdir").value,loader:$("loader").value,memory:$("mem").value})});
  if(r.error){alert(r.error);return;}
  jobId=r.job_id;$("log").textContent="";watch(jobId);
};
$("stop").onclick=async()=>{if(jobId)await api("/api/jobs/"+jobId+"/stop",{method:"POST"});};
setInterval(status,2000);status();
setInterval(()=>{if(lastUpd)$("upd").textContent="· 进度 "+Math.round((Date.now()-lastUpd)/1000)+"s 前更新";},1000);
</script></body></html>"""


def build_qfil_args(fwdir: str, loader: str, memory: str) -> tuple[list[str], int, str]:
    """Back-compat alias (logic lives in backend.qfil, shared with the GUI)."""
    return build_qfil_argv(fwdir, loader, memory)


class Handler(BaseHTTPRequestHandler):
    server_version = "FlashingDevice/0.2"

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
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def log_message(self, *a) -> None:  # keep console clean; we file-log instead
        pass

    # -- GET ----------------------------------------------------------
    def do_GET(self) -> None:
        url = urllib.parse.urlparse(self.path)
        path = url.path
        if path == "/":
            body = DASHBOARD.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/status":
            devs = scan_devices()
            edl = [d for d in devs if d[3] in guards.EDL_PIDS]
            latest = MANAGER.latest()
            self._json(
                {
                    "has_edl": bool(edl),
                    "devices": [
                        f"{v:04x}:{p:04x} bus={b} port={pt} {n}".strip()
                        for b, pt, v, p, n in devs[:8]
                    ],
                    "latest_job": latest.snapshot(log_tail=0) if latest else None,
                }
            )
            return
        if path == "/api/devices":
            devs = scan_devices()
            self._json(
                {
                    "devices": [
                        {"bus": b, "port": pt, "vid": f"{v:04x}", "pid": f"{p:04x}", "name": n}
                        for b, pt, v, p, n in devs
                    ]
                }
            )
            return
        if path == "/api/jobs":
            self._json({"jobs": [j.snapshot(log_tail=0) for j in MANAGER.all()]})
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "jobs":
            job = MANAGER.get(parts[2])
            if job is None:
                self._json({"error": "no such job"}, 404)
                return
            self._json(job.snapshot())
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "jobs" and parts[3] == "events":
            self._sse(parts[2])
            return
        self._json({"error": "not found"}, 404)

    def _sse(self, jid: str) -> None:
        q = MANAGER.subscribe(jid)
        if q is None:
            self._json({"error": "no such job"}, 404)
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=15)
                    payload = ("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode()
                    self.wfile.write(payload)
                    self.wfile.flush()
                    if ev.get("state") == "done":
                        # Drain a beat so the client sees 100%, then close.
                        time.sleep(0.5)
                        break
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            MANAGER.unsubscribe(jid, q)

    # -- POST ---------------------------------------------------------
    def do_POST(self) -> None:
        url = urllib.parse.urlparse(self.path)
        path = url.path
        if path == "/api/qfil":
            data = self._body()
            fwdir = (data.get("fwdir") or "").strip()
            loader = (data.get("loader") or "").strip()
            memory = (data.get("memory") or "ufs").strip()
            expect_serial = (data.get("expect_serial") or "").strip()
            g = guards.require_edl_present(
                bool([d for d in scan_devices() if d[3] in guards.EDL_PIDS])
            )
            if not g.ok:
                self._json({"error": g.message}, 409)
                return
            if expect_serial:
                from flash_device.utils.usb import get_edl_serial

                sn = get_edl_serial()
                if sn != expect_serial:
                    self._json(
                        {
                            "error": f"9008 序列号是 {sn or '未知'}，不是期望的 {expect_serial}，拒绝开刷（防刷错机）"
                        },
                        409,
                    )
                    return
            args, total, err = build_qfil_args(fwdir, loader, memory)
            if err:
                self._json({"error": err}, 400)
                return
            from flash_device.devices.firmware import validate_fwdir

            rep = validate_fwdir(fwdir, loader, memory)
            if rep.level == "error":
                self._json({"error": "包校验不通过：" + "；".join(rep.errors)}, 400)
                return
            job = MANAGER.start(f"整包刷入 {os.path.basename(fwdir)}", args, total_files=total)
            logger.info("qfil job %s: %s", job.id, " ".join(args))
            self._json({"job_id": job.id, "warnings": rep.warnings, "summary": rep.summary()})
            return
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "jobs" and parts[3] == "stop":
            job = MANAGER.get(parts[2])
            if job is None:
                self._json({"error": "no such job"}, 404)
                return
            self._json({"stopped": job.stop()})
            return
        self._json({"error": "not found"}, 404)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="flash-device-server")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--host", default="127.0.0.1")
    ns = ap.parse_args(argv)
    setup_logging("INFO")
    logger.info("env:\n%s", format_checks(run_all_checks()))
    srv = ThreadingHTTPServer((ns.host, ns.port), Handler)
    print(f">>> FlashingDevice server on http://{ns.host}:{ns.port}  (Ctrl+C 停止)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
