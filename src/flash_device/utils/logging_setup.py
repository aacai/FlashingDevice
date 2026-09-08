"""File logging: every run writes a timestamped log for post-mortem debugging.

Layout: ~/.flash-device/logs/flash-device-<ts>.log  (+ `latest.log` pointer)
Console (GUI panel) stays human-readable; the file gets full detail incl.
exact EDL argv, env summary and every stdout line.
"""

from __future__ import annotations

import datetime
import logging
import os
from logging.handlers import RotatingFileHandler

from flash_device.utils.platform import log_dir

_LOGGER_NAME = "flash_device"
_current_log_path = ""


def _ts() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")


def get_log_path() -> str:
    return _current_log_path


def setup_logging(level: str = "INFO") -> str:
    """Configure root `flash_device` logger. Returns the log file path."""
    global _current_log_path
    d = log_dir()
    _current_log_path = os.path.join(d, f"flash-device-{_ts()}.log")
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    fh = RotatingFileHandler(_current_log_path, maxBytes=5 << 20, backupCount=5, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(fh)
    # Keep a stable pointer for "open latest log"
    try:
        latest = os.path.join(d, "latest.log")
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        with open(latest, "w", encoding="utf-8") as f:
            f.write(_current_log_path + "\n")
    except OSError:
        pass
    logger.info("log file: %s", _current_log_path)
    return _current_log_path


def get_logger(name: str = _LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)
