"""Single source of truth for USB VID/PID classification.

Both `safety.guards` (policy) and `utils.usb` (scanning) import from here,
so the 9008/fastboot/adb sets can never drift apart.
Values come from bkerler/edl + the local detect.py experience.
"""

from __future__ import annotations

# Qualcomm 9008 / EDL PIDs
EDL_PIDS: frozenset[int] = frozenset(
    {
        0x9008,
        0x900E,
        0x901D,
        0x901F,
        0x9025,
        0x9026,
        0x9006,
        0x9007,
        0x900A,
        0x900B,
        0x9012,
        0x9013,
    }
)

FASTBOOT_PIDS: frozenset[int] = frozenset({0x4EE0, 0xD00D, 0x0D00, 0x900E})

ADB_PIDS: frozenset[int] = frozenset({0x4EE7, 0x4EE1, 0x4EE2, 0x4EE6, 0x9017, 0x9018, 0x2D01})

# Qualcomm-related VIDs worth highlighting in logs
QUALCOMM_VIDS: frozenset[int] = frozenset(
    {0x05C6, 0x2C7C, 0x1D6B, 0x18D1, 0x0B05, 0x2717, 0x2A70, 0x0489}
)


def classify_pid(pid: int) -> str:
    """Return 'edl' | 'fastboot' | 'adb' | 'unknown'."""
    if pid in EDL_PIDS:
        return "edl"
    if pid in FASTBOOT_PIDS:
        return "fastboot"
    if pid in ADB_PIDS:
        return "adb"
    return "unknown"
