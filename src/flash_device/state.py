"""共享状态：GUI 与 CLI（含 HTTP 接口）看同一份刷机配置。

文件：~/.flash-device/state.json
  {loader, fwdir, memory, updated_at, updated_by, last_job:{...}}

规则简单可预测：last-writer-wins。
  - CLI 改了（--set-* 或 HTTP POST），GUI 下次启动/轮询读到就同步；
  - GUI 里改了，_save() 同时回写，CLI --show 即见最新。
QSettings 仍保留作本地兜底，但共享文件非空时优先。
"""

from __future__ import annotations

import datetime
import json
import os
import sys

FIELDS = ("loader", "fwdir", "memory")


def state_path() -> str:
    from flash_device.utils.platform import app_data_dir

    return os.path.join(app_data_dir(), "state.json")


def load() -> dict:
    state = {
        "loader": "",
        "fwdir": "",
        "memory": "ufs",
        "updated_at": "",
        "updated_by": "",
        "last_job": {},
    }
    try:
        with open(state_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return state
    if isinstance(data, dict):
        for k in (*FIELDS, "updated_at", "updated_by"):
            if isinstance(data.get(k), str):
                state[k] = data[k]
        if isinstance(data.get("last_job"), dict):
            state["last_job"] = data["last_job"]
    return state


def save(partial: dict, by: str = "cli") -> dict:
    """Merge non-empty-string fields, stamp, write back. Returns full state."""
    state = load()
    for k in FIELDS:
        v = partial.get(k)
        if isinstance(v, str) and v:
            state[k] = v
    if isinstance(partial.get("last_job"), dict):
        state["last_job"] = partial["last_job"]
    state["updated_at"] = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    state["updated_by"] = by
    tmp = state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, state_path())
    return state


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="flash-device-state", description="查看/修改 GUI 与 CLI 共享的刷机配置"
    )
    ap.add_argument("--set-loader", default="")
    ap.add_argument("--set-fwdir", default="")
    ap.add_argument("--set-memory", default="")
    ap.add_argument("--show", action="store_true", help="打印当前共享状态")
    ns = ap.parse_args(argv)
    if ns.set_loader or ns.set_fwdir or ns.set_memory:
        state = save(
            {"loader": ns.set_loader, "fwdir": ns.set_fwdir, "memory": ns.set_memory}, by="cli"
        )
        print(f"已写入共享状态 ({state['updated_at']})")
    state = load()
    print(f"loader : {state['loader'] or '(空)'}")
    print(f"fwdir  : {state['fwdir'] or '(空)'}")
    print(f"memory : {state['memory']}")
    print(f"更新于 : {state['updated_at']} by {state['updated_by'] or '-'}")
    if state["last_job"]:
        print(f"上次任务: {state['last_job']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
