"""应用首选项：纯数据层，不依赖 Qt，方便单元测试。

存 QSettings `prefs/` 下；读取时做类型纠偏（ini 后端全是字符串）。
键：
  server_autostart  bool  状态接口是否随 GUI 启动（默认开）
  server_port       int   状态接口端口（默认 8899，环境变量 FLASH_DEVICE_PORT 优先）
  edl_bin           str   EDL 引擎显式路径（空=自动：冻包内 bundled → 仓库 submodule → PATH）
  kdz_tool          str   KDZ 解包器显式路径（空=自动：KDZ_TOOL 环境变量 → ~/.flash-device/bin → PATH）
  expected_serial   str   期望的 9008 序列号（空=不限制；填了就对不上不让刷，防刷错机）
  log_level         str   DEBUG/INFO/WARNING，下次启动生效
"""

from __future__ import annotations

DEFAULTS: dict = {
    "server_autostart": True,
    "server_port": 8899,
    "edl_bin": "",
    "kdz_tool": "",
    "expected_serial": "",
    "log_level": "INFO",
}

LOG_LEVELS = ("DEBUG", "INFO", "WARNING")
MIN_PORT, MAX_PORT = 1024, 65535


def _to_bool(v, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _to_int(v, default: int) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def load(raw: dict | None) -> dict:
    """raw 缺键/字符串都行，返回纠偏后的完整 dict。"""
    raw = raw or {}
    port = _to_int(raw.get("server_port"), DEFAULTS["server_port"])
    port = min(max(port, MIN_PORT), MAX_PORT)
    level = str(raw.get("log_level", DEFAULTS["log_level"])).strip().upper()
    return {
        "server_autostart": _to_bool(raw.get("server_autostart"), True),
        "server_port": port,
        "edl_bin": str(raw.get("edl_bin") or "").strip(),
        "kdz_tool": str(raw.get("kdz_tool") or "").strip(),
        "expected_serial": str(raw.get("expected_serial") or "").strip().upper(),
        "log_level": level if level in LOG_LEVELS else "INFO",
    }


def dump(values: dict) -> dict:
    """存盘前规范化（与 load 同规则，保证读出来一致）。"""
    return load(values)
