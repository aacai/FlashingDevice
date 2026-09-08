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


def resolve_edl_bin(explicit: str = "") -> str:
    """Priority: explicit > sibling third_party/edl/edl.py via current python > PATH edl."""
    if explicit and os.path.exists(explicit):
        return explicit
    # Packaged PyInstaller: edl.py is bundled next to executable
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
