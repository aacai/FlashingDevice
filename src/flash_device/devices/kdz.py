"""LG KDZ 支持：选 .kdz → 解包 → 生成 rawprogram → 走现有校验+刷机流水线。

分工：
  重活（解包大文件）交给外部 kdz-tool 二进制（MIT，见 docs/KDZ.md 自备）；
  本模块只做：KDZ 头速览（机型/版本，不解包就能看）、rawprogram 生成（已审查逻辑）、
  找二进制、组装 job 参数。解包跑在共享 job 引擎里，进度/日志与刷机同源。
固件/KDZ 永不进仓（.gitignore 已禁）。
"""

from __future__ import annotations

import os
import re
import shutil

SECT = 4096
GPT_LABELS = ("PrimaryGPT", "BackupGPT")

# KDZ 头内嵌信息形如：$8"%V500N30c_0_user-signed-ARB0_LGU_KR_OP_1021.dz
_HEADER_RE = re.compile(
    rb"\$8\"%([A-Za-z0-9]+)_0_user-signed-ARB\d+_([A-Z]+)_([A-Z]+)_OP_([0-9]+)\.dz"
)


def parse_kdz_header(path: str) -> dict:
    """只读前 64KB 速览 KDZ。返回 {model, carrier, region, version, ok, error}。"""
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
    except OSError as e:
        return {"ok": False, "error": f"打不开文件：{e}"}
    if len(head) < 64:
        return {"ok": False, "error": "文件太小，不是 KDZ"}
    m = _HEADER_RE.search(head)
    if not m:
        return {"ok": False, "error": "头里找不到机型信息（不是 LG KDZ？）"}
    model, carrier, region, ver = (g.decode("ascii", "replace") for g in m.groups())
    return {
        "ok": True,
        "model": model,  # 如 V500N30c
        "carrier": carrier,  # 如 LGU / KT / SKT / OPEN
        "region": region,  # 如 KR
        "version": ver,
        "error": "",
    }


def find_extractor(explicit: str = "") -> tuple[str, str]:
    """找 kdz-tool 二进制。返回 (path, fix_hint)，找不到 path=''。

    优先级：显式路径（设置页） > KDZ_TOOL 环境变量 > ~/.flash-device/bin > PATH。
    """
    explicit = (explicit or "").strip()
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return explicit, ""
        return "", f"设置页指定的 kdz-tool 不可用：{explicit}（清空则为自动查找）"
    env = os.environ.get("KDZ_TOOL", "").strip()
    if env and os.path.isfile(env) and os.access(env, os.X_OK):
        return env, ""
    home_bin = os.path.join(os.path.expanduser("~"), ".flash-device", "bin", "kdz-tool")
    if os.path.isfile(home_bin):
        return home_bin, ""
    which = shutil.which("kdz-tool")
    if which:
        return which, ""
    return "", (
        "没找到 kdz-tool（LG KDZ 解包器，MIT）。补齐三选一：\n"
        "1) 放到 ~/.flash-device/bin/kdz-tool 并 chmod +x；\n"
        "2) 装到 PATH 里能找到的位置；\n"
        "3) 设环境变量 KDZ_TOOL=/path/to/kdz-tool。"
    )


def default_dest_for(kdz_path: str) -> str:
    base = os.path.splitext(os.path.basename(kdz_path))[0]
    safe = "".join(c if (c.isalnum() or c in "-_.[]") else "_" for c in base)[:80]
    return os.path.join(os.path.expanduser("~"), ".flash-device", "firmware", safe)


def gen_rawprograms(extracted_dir: str) -> list[str]:
    """metadata.json → rawprogram{L}.xml（kdz_to_rawprogram 逻辑，已审查，逐字移植）。

    kdz-tool 已把每分区解成"完整原始镜像"（大小==跨度*4096），
    LUN/起始扇区/扇区数直接取 chunk 信息，自洽。
    """
    import json

    meta = os.path.join(extracted_dir, "metadata.json")
    if not os.path.exists(meta):
        raise FileNotFoundError(f"找不到 {meta}，请先解包")
    with open(meta, encoding="utf-8") as f:
        m = json.load(f)
    try:
        parts_by_lun = m["dz"]["parts"]
    except (KeyError, TypeError):
        raise ValueError("metadata.json 里没有 dz.parts，解包不完整？")
    written = []
    for lun in sorted(parts_by_lun.keys(), key=lambda x: int(x)):
        parts = parts_by_lun[lun]
        lines = ['<?xml version="1.0" ?>', "<data>"]
        for part, chunks in parts.items():
            starts = [c["start_sector"] for c in chunks]
            ends = [c["start_sector"] + c["sector_count"] for c in chunks]
            lo, hi = min(starts), max(ends)
            span = hi - lo
            fname = f"{lun}.{part}.img"
            partof = "true" if part in GPT_LABELS else "false"
            lines.append(
                f'\t<program SECTOR_SIZE_IN_BYTES="{SECT}" file_sector_offset="0" '
                f'filename="{fname}" label="{part}" num_partition_sectors="{span}" '
                f'partofsingleimage="{partof}" physical_partition_number="{lun}" '
                f'readbackverify="false" size_in_KB="{(span * SECT / 1024):.1f}" '
                f'sparse="false" start_byte_hex="{hex(lo * SECT)}" start_sector="{lo}"/>'
            )
        lines.append("</data>")
        fn = os.path.join(extracted_dir, f"rawprogram{lun}.xml")
        with open(fn, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        written.append(fn)
    return written
