# PyInstaller spec for FlashingDevice (Win/Linux/macOS).
# ALWAYS run from the REPO ROOT:  pyinstaller packaging/pyinstaller.spec
# Paths below are anchored to this spec file, so CWD never matters.
#
# What goes inside:
#   - our GUI + safety/progress/logging code (frozen)
#   - third_party/edl/edl.py + edlclient/ as DATA (NOT Loaders/: proprietary
#     OEM loaders must never be distributed; users bring their own)
# Frozen runtime executes the engine via SYSTEM python3:
#   python3 <bundle>/third_party/edl/edl.py ...  (see utils/platform.py)
import os

# Anchor to THIS spec file (SPECPATH is just CWD) so the spec works from any CWD.
_anchor = globals().get("__file__", "")
if _anchor:
    SPEC_DIR = os.path.dirname(os.path.abspath(_anchor))
else:  # fallback: assume invoked from the repo root
    SPEC_DIR = os.path.join(os.getcwd(), "packaging")
ROOT = os.path.dirname(SPEC_DIR)
SRC = os.path.join(ROOT, "src")
EDL = os.path.join(ROOT, "third_party", "edl")

block_cipher = None

a = Analysis(
    [os.path.join(SRC, "flash_device", "__main__.py")],
    pathex=[SRC],
    binaries=[],
    datas=[
        (os.path.join(SRC, "flash_device", "devices", "profiles"), "flash_device/devices/profiles"),
        (os.path.join(EDL, "edl.py"), os.path.join("third_party", "edl")),
        (os.path.join(EDL, "edlclient"), os.path.join("third_party", "edl", "edlclient")),
    ],
    # 显式声明所有自家子模块：部分是函数内懒加载 import，
    # 静态分析看不见，不写就会在冻包里缺失（server/state/settings/kdz 都是）。
    hiddenimports=[
        "usb.backend.libusb1",
        "serial",
        "lxml.etree",
        "yaml",
        "flash_device.app",
        "flash_device.state",
        "flash_device.server",
        "flash_device.settings",
        "flash_device.settings_dialog",
        "flash_device.backend.job",
        "flash_device.backend.progress",
        "flash_device.backend.qfil",
        "flash_device.backend.qt_bridge",
        "flash_device.devices.firmware",
        "flash_device.devices.kdz",
        "flash_device.devices.loaders",
        "flash_device.safety.guards",
        "flash_device.safety.backup",
        "flash_device.utils.usb",
        "flash_device.utils.platform",
        "flash_device.utils.envcheck",
        "flash_device.utils.logging_setup",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="flash-device",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Technician tool: keep the console so --check-env/--self-test/logs are visible.
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name="flash-device",
)
