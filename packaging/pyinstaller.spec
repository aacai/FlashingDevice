# PyInstaller spec for FlashingDevice (Win/Linux/macOS)
# Usage:
#   pip install pyinstaller
#   pyinstaller packaging/pyinstaller.spec
# No symlinks: edl core is at third_party/edl (submodule), bundled as data.
block_cipher = None

a = Analysis(
    ['../../src/flash_device/__main__.py'],
    pathex=['../../src'],
    binaries=[],
    datas=[
        ('../../src/flash_device/devices/profiles', 'flash_device/devices/profiles'),
        ('../../third_party/edl/edlclient', 'edlclient'),
    ],
    hiddenimports=['usb.backend.libusb1', 'serial', 'lxml.etree', 'yaml'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='flash-device',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=True, name='flash-device',
)
