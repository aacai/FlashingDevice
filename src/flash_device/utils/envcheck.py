"""Environment preflight: tell the user exactly what to install.

Checks are read-only and never crash: each returns ok + fix hint.
GUI shows them in a dialog; CLI via `python -m flash_device --check-env`.
Python itself is assumed good (we bundle/venv it); adb/fastboot/libusb/edl
submodule are the usual missing pieces.
"""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass


@dataclass
class EnvCheck:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def check_python() -> EnvCheck:
    ok = sys.version_info >= (3, 10)
    return EnvCheck(
        "Python >= 3.10",
        ok,
        f"{sys.version.split()[0]} ({sys.executable})",
        "" if ok else "从 https://www.python.org/downloads/ 装官方 Python 3.10+（本仓不用 brew）。",
    )


def check_py_packages() -> EnvCheck:
    missing: list[str] = []
    for mod, pkg in (
        ("PyQt6", "PyQt6"),
        ("usb", "pyusb"),
        ("serial", "pyserial"),
        ("lxml", "lxml"),
        ("yaml", "pyyaml"),
    ):
        try:
            __import__(mod)
        except Exception:
            missing.append(pkg)
    if missing:
        return EnvCheck(
            "Python 依赖",
            False,
            "缺失: " + ", ".join(missing),
            "pip install -r requirements.txt",
        )
    return EnvCheck("Python 依赖", True, "PyQt6/pyusb/pyserial/lxml/pyyaml 就绪")


def check_adb() -> EnvCheck:
    adb, fb = _have("adb"), _have("fastboot")
    if adb and fb:
        return EnvCheck("adb + fastboot", True, "就绪（用于重启到 edl / 快照核对，非 9008 主通道）")
    return EnvCheck(
        "adb + fastboot",
        False,
        "adb 或 fastboot 不在 PATH",
        "Linux: sudo apt install adb fastboot；macOS: 装 Android platform-tools 并加 PATH；"
        "Windows: 同上。缺它不影响 9008，但建议补齐。",
    )


def check_libusb() -> EnvCheck:
    try:
        import usb.backend.libusb1 as b  # type: ignore
        import usb.core  # type: ignore

        backend = b.get_backend()
        if backend is None:
            return EnvCheck(
                "libusb 后端",
                False,
                "pyusb 正常但找不到 libusb 动态库",
                "Linux: sudo apt install libusb-1.0-0；macOS: 重装官方 Python 或 pip install libusb-package；"
                "Windows: 先跑 tools/windows/install_edl.ps1 装 UsbDk。",
            )
        n = len(list(usb.core.find(find_all=True) or []))
        return EnvCheck("libusb 后端", True, f"后端就绪，当前可见 {n} 个 USB 设备")
    except Exception as e:
        return EnvCheck(
            "libusb 后端", False, f"pyusb 不可用: {e}", "pip install -r requirements.txt 后重试。"
        )


def check_edl_submodule() -> EnvCheck:
    here = os.path.dirname(os.path.abspath(__file__))
    edl_py = os.path.normpath(os.path.join(here, "..", "..", "..", "third_party", "edl", "edl.py"))
    if os.path.isfile(edl_py):
        return EnvCheck("EDL 子模块", True, edl_py)
    return EnvCheck(
        "EDL 子模块",
        False,
        "third_party/edl 为空",
        "git submodule update --init --recursive（见 README 的 submodule 小节）。",
    )


def check_platform_extras() -> EnvCheck:
    if sys.platform.startswith("linux"):
        ok = os.path.exists("/etc/udev/rules.d/51-edl.rules")
        return EnvCheck(
            "udev 规则",
            ok,
            "已安装" if ok else "/etc/udev/rules.d/51-edl.rules 缺失（无 root 会认不出 9008）",
            ""
            if ok
            else "sudo cp tools/udev/51-edl.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger",
        )
    if sys.platform == "darwin":
        return EnvCheck("macOS", True, "用官方 Python + 直连 USB-C 即可，无需额外驱动")
    if sys.platform.startswith("win"):
        return EnvCheck(
            "Windows 驱动",
            False,
            "实验性支持：需 UsbDk + Zadig，无法自动确认",
            "以管理员跑 tools/windows/install_edl.ps1，有感叹号再用 Zadig 绑 WinUSB。",
        )
    return EnvCheck("平台", True, sys.platform)


def run_all_checks() -> list[EnvCheck]:
    return [
        check_python(),
        check_py_packages(),
        check_libusb(),
        check_edl_submodule(),
        check_adb(),
        check_platform_extras(),
    ]


def format_checks(checks: list[EnvCheck]) -> str:
    lines = []
    for c in checks:
        mark = "✅" if c.ok else "❌"
        lines.append(f"{mark} {c.name}: {c.detail}")
        if not c.ok and c.fix:
            lines.append(f"   → 补齐: {c.fix}")
    return "\n".join(lines)
