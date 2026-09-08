"""Platform helpers: resolve edl binary + app dirs without hardcoded user paths."""

from __future__ import annotations

import os
import platform
import shutil
import sys


def app_data_dir() -> str:
    base = os.path.expanduser("~/.flash-device")
    os.makedirs(base, exist_ok=True)
    for sub in ("backups", "logs", "firmware"):
        os.makedirs(os.path.join(base, sub), exist_ok=True)
    return base


def log_dir() -> str:
    d = os.path.join(app_data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return d


def is_frozen() -> bool:
    """True when running inside a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> str:
    """Directory holding bundled data (sys._MEIPASS when frozen)."""
    if is_frozen():
        base = getattr(sys, "_MEIPASS", "")
        if base:
            return str(base)
    return ""


def bundled_edl_py() -> str:
    """Path of the EDL engine shipped INSIDE the bundle ('' when absent)."""
    root = bundle_root()
    if not root:
        return ""
    cand = os.path.join(root, "third_party", "edl", "edl.py")
    return cand if os.path.isfile(cand) else ""


def resolve_edl_bin(explicit: str = "") -> str:
    """Priority: explicit > bundled (frozen) / repo third_party (dev) > PATH edl."""
    if explicit and os.path.exists(explicit):
        return explicit
    if is_frozen():
        bundled = bundled_edl_py()
        if bundled:
            return bundled
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        for cand in (
            os.path.join(here, "..", "..", "..", "third_party", "edl", "edl.py"),
            os.path.join(os.getcwd(), "third_party", "edl", "edl.py"),
        ):
            if os.path.isfile(os.path.normpath(cand)):
                return os.path.normpath(cand)
    found = shutil.which("edl")
    if found:
        return found
    return "edl"


def python_for_edl() -> str:
    """Interpreter used to run edl.py.

    Frozen bundles are not interpreters, so they shell out to system python3
    (envcheck verifies it); dev mode reuses the current interpreter.
    """
    if is_frozen():
        return shutil.which("python3") or "python3"
    return sys.executable or "python3"


def system_info() -> str:
    return f"{platform.system()} {platform.release()} {platform.machine()} py{platform.python_version()}"


def notify(title: str, msg: str) -> None:
    try:
        if sys.platform == "darwin":
            import subprocess

            subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{msg}" with title "{title}" sound name "Ping"',
                ],
                capture_output=True,
                timeout=6,
                check=False,
            )
        elif sys.platform.startswith("linux"):
            import subprocess

            subprocess.run(["notify-send", title, msg], capture_output=True, timeout=6, check=False)
    except Exception:
        pass
