"""adb 快查：手机连着时实测 AVB/槽位状态，供刷 boot/vbmeta 前提醒用。

从不抛异常、从不长等：没 adb / 没设备 / 超时，一律返回 {}，
调用方按"查不到=从严提醒"处理。
"""
from __future__ import annotations

import shutil
import subprocess


def _run(args: list[str], timeout: int = 8) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return (r.stdout or "").strip() if r.returncode == 0 else ""


def first_device() -> str:
    adb = shutil.which("adb")
    if not adb:
        return ""
    for line in _run([adb, "devices"]).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return ""


def read_avb_state(serial: str = "") -> dict:
    """返回 {verifiedbootstate, device_state, slot}，拿不到就是 {}。"""
    adb = shutil.which("adb")
    if not adb:
        return {}
    serial = serial or first_device()
    if not serial:
        return {}
    base = [adb, "-s", serial, "shell", "getprop"]
    out = {}
    for prop, key in (("ro.boot.verifiedbootstate", "verifiedbootstate"),
                      ("ro.boot.vbmeta.device_state", "device_state"),
                      ("ro.boot.slot_suffix", "slot")):
        v = _run(base + [prop]).splitlines()
        v = v[0].strip() if v else ""
        if v:
            out[key] = v
    return out
