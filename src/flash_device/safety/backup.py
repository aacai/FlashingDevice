"""Mandatory backup helpers: manifest + sha256, no device I/O here (runner does that)."""

from __future__ import annotations

import datetime
import hashlib
import json
import os


def timestamp() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def default_backup_root() -> str:
    return os.path.join(os.path.expanduser("~"), ".flash-device", "backups")


def new_backup_dir(device_hint: str = "qcom") -> str:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in device_hint)[:40] or "qcom"
    d = os.path.join(default_backup_root(), f"{safe}_{timestamp()}")
    os.makedirs(d, exist_ok=True)
    return d


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def write_manifest(backup_dir: str, entries: list[dict]) -> str:
    """Write MANIFEST.json + sha256sums.txt for files already in backup_dir."""
    os.makedirs(backup_dir, exist_ok=True)
    manifest = {
        "tool": "FlashingDevice",
        "created": timestamp(),
        "entries": entries,
    }
    mp = os.path.join(backup_dir, "MANIFEST.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    # sha256sums over all files except the sums file itself
    sp = os.path.join(backup_dir, "sha256sums.txt")
    lines = []
    for name in sorted(os.listdir(backup_dir)):
        if name in ("sha256sums.txt",):
            continue
        p = os.path.join(backup_dir, name)
        if os.path.isfile(p):
            try:
                lines.append(f"{sha256_file(p)}  {name}")
            except OSError:
                continue
    with open(sp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))
    return mp
