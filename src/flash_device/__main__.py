"""python -m flash_device entry.

GUI:            python -m flash_device
Env preflight:  python -m flash_device --check-env   (works without PyQt6)
GUI smoke:      QT_QPA_PLATFORM=offscreen python -m flash_device --self-test
"""

import sys


def _preparse():
    import argparse

    ap = argparse.ArgumentParser(
        prog="flash-device", description="Qualcomm 9008 / EDL flashing tool"
    )
    ap.add_argument("--check-env", action="store_true", help="只做环境自检并退出（缺什么补什么）")
    ap.add_argument("--log-level", default="INFO", help="日志级别：DEBUG/INFO/WARNING")
    ns, rest = ap.parse_known_args(sys.argv[1:])
    return ns, rest


def main() -> int:
    ns, rest = _preparse()
    if ns.check_env:
        # No PyQt6 import here: preflight must run on a bare machine.
        from flash_device.utils import envcheck
        from flash_device.utils.logging_setup import setup_logging

        log_path = setup_logging(ns.log_level)
        checks = envcheck.run_all_checks()
        print(envcheck.format_checks(checks))
        print(f"\nlog: {log_path}")
        return 0 if all(c.ok for c in checks) else 2

    from flash_device.app import main as gui_main

    return gui_main(rest)


if __name__ == "__main__":
    raise SystemExit(main())
