"""USB helpers: VID/PID classification without hard dependencies at import time."""

from __future__ import annotations

# Single source of truth -> flash_device.ids (re-exported for compatibility).
from flash_device.ids import ADB_PIDS, EDL_PIDS, FASTBOOT_PIDS, classify_pid

__all__ = [
    "ADB_PIDS",
    "EDL_PIDS",
    "FASTBOOT_PIDS",
    "classify_pid",
    "probe_9008",
    "rank_device",
    "scan_devices",
]


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
    """Return the serial of the first 9008/900E device, '' if none.

    序列号是认设备的唯一靠谱办法（LG V50=6A738FEE，小米平板6=169621F5），
    自动脚本开刷前必须核对，杜绝刷错机。
    注意：9008 的 USB 序列号描述符经常为空，SN 藏在 product 字符串里
    （如 "QUSB_BULK_CID:0404_SN:6A738FEE"），两处都试。
    """
    import re

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
                if d.serial_number:
                    return d.serial_number
            except Exception:
                pass
            try:
                m = re.search(r"SN:([0-9A-Fa-f]+)", d.product or "")
                if m:
                    return m.group(1).upper()
            except Exception:
                pass
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


def probe_9008(sniff_ms: int = 800, nop_ms: int = 2000) -> tuple[bool, str]:
    """真伪 9008 探测：枚举到了不算数，协议有回应才算活着。

    Returns (ok, detail), detail in:
      no-device      没插 9008
      sahara          Now Sahara hello，自检后已复位恢复（后来的 edl 不受影响）
      sahara-unrestored  嗅到 hello 但复位失败（活着，但下一次 edl 握手可能受影响）
      firehose       firehose nop 有回包
      silent         枚举正常但零字节回应（假死：残留会话/掉电前兆/需断电重启）
      busy           接口被别的进程占着（可能正在别处刷机，别抢）
      no-bulk-endpoints / error:...  其他异常

    安全规则：
      - 只读 + 一个无害 nop，不写任何固件数据；
      - 嗅到 Sahara hello（会消耗掉它）后必做 USB 复位，让 PBL 重发，
        不影响后续 edl 连接；
      - 任何异常都不抛，只返回 silent/error。
    """
    try:
        import usb.core  # type: ignore
        import usb.util  # type: ignore
    except Exception as e:
        return False, f"error:{e}"

    TimeoutErr = getattr(usb.core, "USBTimeoutError", usb.core.USBError)

    dev = None
    for pid in sorted(EDL_PIDS):
        try:
            dev = usb.core.find(idVendor=0x05C6, idProduct=pid)
        except Exception:
            continue
        if dev is not None:
            break
    if dev is None:
        return False, "no-device"

    try:
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]
        ep_in = ep_out = None
        for e in intf:
            if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN:
                ep_in = e.bEndpointAddress
            else:
                ep_out = e.bEndpointAddress
        if ep_in is None or ep_out is None:
            return False, "no-bulk-endpoints"
    except Exception as e:
        return False, f"error:{e}"

    detached = False
    claimed = False
    try:
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
                detached = True
        except Exception:
            pass
        try:
            usb.util.claim_interface(dev, 0)
        except Exception as e:
            msg = str(e).lower()
            if "busy" in msg or "access" in msg or "denied" in msg:
                return False, "busy"
            return False, f"error:{e}"
        claimed = True

        # 1) 嗅自发包：Sahara 上电会自己发 hello（0x01 开头）
        try:
            r = dev.read(ep_in, 4096, timeout=sniff_ms)
            if len(r) > 0:
                if r[0] == 0x01:
                    # 消耗了唯一的 hello：先放接口再复位，让 PBL 重发，
                    # 后来的 edl 才能正常握手。
                    try:
                        usb.util.release_interface(dev, 0)
                        claimed = False
                    except Exception:
                        pass
                    restored = _reset_quietly(dev)
                    return True, "sahara" if restored else "sahara-unrestored"
                if b"<?xml" in bytes(r):
                    return True, "firehose"
                return True, f"alive-0x{r[0]:02x}"
        except TimeoutErr:
            pass
        except Exception as e:
            return False, f"error:{e}"

        # 2) firehose nop 试探（Sahara 会忽略，firehose 会回 XML）
        try:
            dev.write(ep_out, b'<?xml version="1.0" ?><data><nop /></data>', timeout=2000)
            r = dev.read(ep_in, 4096, timeout=nop_ms)
            if b"<?xml" in bytes(r):
                return True, "firehose"
            return True, "alive-nop"
        except TimeoutErr:
            return False, "silent"
        except Exception as e:
            return False, f"error:{e}"
    finally:
        try:
            if claimed:
                usb.util.release_interface(dev, 0)
        except Exception:
            pass
        try:
            if detached:
                dev.attach_kernel_driver(0)
        except Exception:
            pass


def _reset_quietly(dev) -> bool:
    """USB 复位让 PBL 重发 hello。失败也不抛，调用方如实上报。"""
    try:
        dev.reset()
        return True
    except Exception:
        return False
