"""QFIL argument builder shared by the HTTP server and the PyQt GUI.

ONE place decides how a firmware dir becomes an edl command line:
all rawprogram*.xml comma-joined (multi-LUN!), placeholder patch token
when the dump ships none (LG KDZ dumps have no patch files; a missing
patch file only warns, while an empty token would crash edl).
"""

from __future__ import annotations

import os

from flash_device.backend.progress import count_program_entries
from flash_device.safety import guards
from flash_device.utils import platform as pf

PLACEHOLDER_PATCH = "patch_dummy.xml"


def edl_cmd() -> list[str]:
    edl_py = pf.resolve_edl_bin()
    if edl_py.endswith(".py"):
        return [pf.python_for_edl(), edl_py]
    return [edl_py]


def build_qfil_argv(fwdir: str, loader: str, memory: str) -> tuple[list[str], int, str]:
    """Returns (argv, total_files, error). Empty error = ok to launch."""
    raws, pats, msg = guards.scan_firmware_dir(fwdir)
    if not raws:
        return [], 0, msg
    gl = guards.validate_loader(loader)
    if not gl.ok:
        return [], 0, gl.message
    total = 0
    for rp in raws:
        try:
            with open(rp, encoding="utf-8", errors="replace") as f:
                total += count_program_entries(f.read())
        except OSError:
            pass
    args = edl_cmd() + ["qfil", ",".join(raws)]
    args.append(",".join(pats) if pats else PLACEHOLDER_PATCH)
    args += [fwdir, f"--loader={loader.strip()}", f"--memory={memory}"]
    return args, total, ""


def qfil_summary(fwdir: str, loader: str, n_raw: int, n_pat: int) -> str:
    return (
        f"整包刷入：{os.path.basename(fwdir)}  ·  rawprogram×{n_raw}"
        + (f"  ·  补丁×{n_pat}" if n_pat else "  ·  无补丁文件（跳过patch）")
        + f"  ·  编程器 {os.path.basename(loader.strip())}"
    )
