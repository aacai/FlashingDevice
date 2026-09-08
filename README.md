# FlashingDevice

Open-source Qualcomm 9008 / EDL flashing tool with **safety guards, mandatory backups and real progress**.

> [!WARNING]
> Flashing can brick devices and wipe data. This tool forces backups and double-confirmation,
> but you are responsible for using a correct Firehose loader + firmware for your exact model.

## Features

- PyQt6 GUI: USB hot-poll, 9008/fastboot/adb state, loader + firmware picker
- Real progress: parses EDL `Progress: |██| 45.5% Write ...` + `[qfil] programming X` into total + per-file bars
- Safety: 9008-only gate, loader validation, critical-partition denylist, dry-run XML check, double confirm
- Backups: auto `printgpt` + `gpt` + `rl --skip=userdata,metadata` with `MANIFEST.json` + `sha256sums.txt`
- Cross-platform: Linux (Tier1), macOS (Tier1), Windows (Tier2 experimental, UsbDk/Zadig needed)
- File logging: every run writes `~/.flash-device/logs/flash-device-<ts>.log`, openable from the GUI
- Env preflight: `flash-device --check-env` / GUI “环境自检” tells you exactly what to install
- No symlinks, no `brew install` in scripts/docs/CI. EDL core via `third_party/edl` submodule (GPLv3).

Upstream: [bkerler/edl](https://github.com/bkerler/edl) (GPLv3). This repo is GPLv3-only accordingly.

## Repo layout (why a new folder?)

`FlashingDevice/` is a **brand-new clean git repo**, separate from your local `shuaji/` work area
that holds 25G+ of LG/Xiaomi firmware. Only source code lives here; the huge blobs stay
outside and are gitignored, so history stays small and nothing proprietary leaks.
Your old `edl_gui.py` was refactored into `src/flash_device/app.py` (not copied twice).

## Quick start (dev)

```bash
git clone --recurse-submodules https://github.com/aacai/FlashingDevice
cd FlashingDevice
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
flash-device --check-env   # missing adb/libusb/udev? it tells you how to fix
flash-device
# or: python -m flash_device
```

## Submodule (third_party/edl) — init & update

The EDL engine is **not copied** into this repo; it is a git submodule pointing at
`bkerler/edl`. You only need four commands:

```bash
# 1. Fresh clone WITH the engine (recommended):
git clone --recurse-submodules https://github.com/aacai/FlashingDevice

# 2. You cloned without it and third_party/edl is empty:
git submodule update --init --recursive

# 3. Update the engine to its latest pinned upstream later:
git submodule update --remote --merge third_party/edl
git add third_party/edl && git commit -m "chore: bump edl submodule"

# 4. Check status:
git submodule status
```

Notes: `Loaders/` inside the submodule stays empty by default (proprietary loaders are
never committed) — bring your own loader per device. CI checks out with
`submodules: recursive`, so Actions always get the engine automatically.

Linux USB rules:

```bash
sudo cp tools/udev/51-edl.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
# disable ModemManager: sudo systemctl disable --now ModemManager
```

See `docs/SAFETY.md`, `docs/DEVICES.md`, `firmware/README.md`.

## Firmware policy

**Never commit firmware/loaders.** Only `*.yaml` profiles + docs + checksums belong in git.
Put your files locally under `firmware/` (gitignored) or `~/.flash-device/`.

## Packaging

- Linux: AppImage + `.deb` (nfpm), Windows: portable exe (PyInstaller, experimental), macOS: dmg/zip
- GitHub Actions: `ci.yml` (lint+test), `build.yml` (artifacts), `release.yml` (tag `v*` → Release)

## License

GPL-3.0-only. See `LICENSE`.
