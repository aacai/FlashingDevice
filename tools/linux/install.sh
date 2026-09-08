#!/bin/bash
# Linux setup: python.org/distro python + udev + ModemManager off. No brew (Linux has no brew path anyway).
set -euo pipefail
echo "[1/4] installing system deps (needs sudo)..."
if command -v apt >/dev/null 2>&1; then
  sudo apt update
  sudo apt install -y python3 python3-venv python3-pip libusb-1.0-0 adb fastboot
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y python3 python3-pip libusbx android-tools
elif command -v pacman >/dev/null 2>&1; then
  sudo pacman -Sy --noconfirm python python-pip libusb android-tools
fi
echo "[2/4] udev rules..."
sudo cp "$(dirname "$0")/../udev/51-edl.rules" /etc/udev/rules.d/51-edl.rules
sudo udevadm control --reload-rules && sudo udevadm trigger || true
echo "[3/4] disable ModemManager (it steals 9008 serial)..."
sudo systemctl disable --now ModemManager 2>/dev/null || true
echo "[4/4] venv..."
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
echo "OK. Run: source .venv/bin/activate && flash-device"
