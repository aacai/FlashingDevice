"""Safety guards: device gate, loader validation, denylist, dry-run checks."""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass

# USB ID sets live in flash_device.ids (single source of truth).
from flash_device.ids import ADB_PIDS, EDL_PIDS, FASTBOOT_PIDS, classify_pid

__all__ = [
    "ADB_PIDS",
    "EDL_PIDS",
    "FASTBOOT_PIDS",
    "classify_pid",
]

# Partitions that must never be written without explicit typed confirmation.
# These carry IMEI / calibration / persist data.
CRITICAL_PARTITIONS = {
    "modemst1",
    "modemst2",
    "fsg",
    "fsc",
    "persist",
    "persistbak",
    "devinfo",
    "limits",
    "sns",
    "sid_a",
    "sid_b",
    "apdp",
    "storsec",
    "secdata",
    "keymaster_a",
    "keymaster_b",
    "cmnlib_a",
    "cmnlib_b",
    "cmnlib64_a",
    "cmnlib64_b",
    "gpt",
    "pgpt",
    "sgpt",
}

ALLOWED_LOADER_SUFFIX = (".elf", ".mbn", ".bin")

# AVB（ verified boot）链上的分区：改了之后能不能开机，看 BL 锁 + vbmeta 状态。
# BL 未解 + 校验开着 → 改过的 boot/vbmeta 基本拒绝开机（红字/卡 logo）。
# BL 已解（orange）→ 能开机，会弹黄字警告。
AVB_SENSITIVE = {
    "boot", "boot_a", "boot_b",
    "init_boot", "init_boot_a", "init_boot_b",
    "vendor_boot", "vendor_boot_a", "vendor_boot_b",
    "vbmeta", "vbmeta_a", "vbmeta_b", "vbmeta_system",
    "vbmeta_system_a", "vbmeta_system_b",
    "dtbo", "dtbo_a", "dtbo_b",
}


@dataclass
class GuardResult:
    ok: bool
    message: str = ""


def require_edl_present(has_edl: bool) -> GuardResult:
    if has_edl:
        return GuardResult(True)
    return GuardResult(
        False, "未检测到 9008 设备：请关机后按机型方式进 9008 再插线（不要经过 Hub）。"
    )


def validate_loader(path: str) -> GuardResult:
    p = (path or "").strip().strip('"')
    if not p:
        return GuardResult(False, "缺少 Firehose 编程器：请先指定 prog_firehose_*.elf / *.mbn。")
    if not os.path.isfile(p):
        return GuardResult(False, f"编程器不存在：{p}")
    if not p.lower().endswith(ALLOWED_LOADER_SUFFIX):
        return GuardResult(False, f"编程器后缀异常（应为 {ALLOWED_LOADER_SUFFIX}）：{p}")
    try:
        if os.path.getsize(p) == 0:
            return GuardResult(False, f"编程器为空文件：{p}")
    except OSError as e:
        return GuardResult(False, f"编程器不可读：{e}")
    return GuardResult(True)


def validate_single_write(partition: str, image: str) -> GuardResult:
    part = (partition or "").strip()
    if not part:
        return GuardResult(False, "分区名为空。")
    # Basic injection guard: partition names are [A-Za-z0-9_+-]
    if not all(c.isalnum() or c in "_+-" for c in part):
        return GuardResult(False, f"分区名含非法字符：{part}")
    if part.lower() in CRITICAL_PARTITIONS:
        return GuardResult(
            False,
            f"分区 {part} 在关键分区黑名单中：默认拒绝写入。如确需写入请先备份并在设置中显式解锁。",
        )
    if not image or not os.path.isfile(image):
        return GuardResult(False, f"镜像不存在：{image}")
    return GuardResult(True)


def scan_firmware_dir(fwdir: str) -> tuple[list[str], list[str], str]:
    """Return (rawprograms, patches, message)."""
    if not fwdir or not os.path.isdir(fwdir):
        return [], [], "固件目录不存在。"
    raws = sorted(glob.glob(os.path.join(fwdir, "**", "rawprogram*.xml"), recursive=True))
    pats = sorted(glob.glob(os.path.join(fwdir, "**", "patch*.xml"), recursive=True))
    if not raws:
        return [], pats, "目录下未找到 rawprogram*.xml，无法整包刷入。"
    return raws, pats, f"rawprogram: {len(raws)} 个, patch: {len(pats)} 个"


def qfil_safety_summary(loader: str, fwdir: str, raw: str) -> str:
    return (
        "即将整包刷入（会清空数据并覆盖全部分区）：\n"
        f"固件目录: {fwdir}\n分区表: {os.path.basename(raw)}\n"
        f"编程器: {os.path.basename(loader)}\n\n"
        "已确认备份完成且机型/loader 匹配吗？"
    )


def needs_avb_warning(partition: str) -> bool:
    """boot/vbmeta 这类分区：写入前必须提醒 AVB 后果。"""
    return (partition or "").strip().lower() in AVB_SENSITIVE


def avb_warning_text(partition: str, avb: dict) -> str:
    """按实测到的 AVB 状态组织提醒文案。avb 为空 dict 表示查不到（按最严的说）。"""
    vbs = (avb.get("verifiedbootstate") or "").strip().lower()
    dst = (avb.get("device_state") or "").strip().lower()
    head = (
        f"⚠ {partition} 在 AVB 校验链上：刷入改过的镜像后，能不能开机取决于验证状态，"
        "不是写成功就万事大吉。"
    )
    if vbs == "orange" or dst == "unlocked":
        tail = (
            "实测本机 verifiedbootstate=orange（BL 已解）：可以开机，开机时会有黄字警告，属正常。\n"
            "若之后卡 logo，用 9008 把原版镜像写回同一分区即可恢复。"
        )
    elif vbs == "green" or dst == "locked":
        tail = (
            "实测本机 verifiedbootstate=green 且 BL 未解：改过的镜像大概率被拒绝开机\n"
            "（红字/卡 logo/自动回滚），强烈建议先解锁 BL 或先确认有原版镜像可回滚，"
            "否则不要继续。"
        )
    else:
        tail = (
            "当前查不到手机的验证状态（没连 adb 或读不到，默认按最严处理）：\n"
            "BL 未解 + 校验开着的机器，改过的 boot/vbmeta 基本开不了机。\n"
            "继续前请确认：① BL 已解（开机有黄字）或 ② 原版镜像在手、可 9008 写回救砖。"
        )
    return head + "\n\n" + tail
