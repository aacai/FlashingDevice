"""USB helpers: VID/PID classification without hard dependencies at import time."""

from __future__ import annotations

# Single source of truth -> flash_device.ids (re-exported for compatibility).
from flash_device.ids import ADB_PIDS, EDL_PIDS, FASTBOOT_PIDS, classify_pid

__all__ = ["ADB_PIDS", "EDL_PIDS", "FASTBOOT_PIDS", "classify_pid", "rank_device", "scan_devices"]


def scan_devices() -> list[tuple]:
    """Return [(bus, port, vid, pid, product)]. Empty list on error (no crash)."""
    out: list[tuple] = []
    try:
        import usb.core  # type: ignore

        for d in usb.core.find(find_all=True):
            try:
                port = ".".join(str(p) for p in d.port_numbers)
            except Exception:
                port = "?"
            try:
                bus = d.bus
            except Exception:
                bus = "?"
            try:
                name = d.product or ""
            except Exception:
                name = ""
            try:
                out.append((bus, port, d.idVendor, d.idProduct, name))
            except Exception:
                continue
    except Exception:
        pass
    return out


def get_edl_serial() -> str:
    """Return the USB serial of the first 9008/900E device, '' if none.

    序列号是认设备的唯一靠谱办法（LG V50=6A738FEE，小米平板6=169621F5），
    自动脚本开刷前必须核对，杜绝刷错机。
    """
    try:
        import usb.core  # type: ignore

        for pid in sorted(EDL_PIDS):
            try:
                d = usb.core.find(idVendor=0x05C6, idProduct=pid)
            except Exception:
                continue
            if d is None:
                continue
            try:
                return d.serial_number or ""
            except Exception:
                return "?"
    except Exception:
        pass
    return ""


def rank_device(d: tuple) -> int:
    pid = d[3]
    if pid in EDL_PIDS:
        return 0
    if pid in FASTBOOT_PIDS:
        return 1
    if pid in ADB_PIDS:
        return 2
    return 3
