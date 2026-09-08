# macOS notes (Tier1). No brew allowed in this repo.
#
# 1. Install Python from https://www.python.org/downloads/ (official pkg, 3.10+).
# 2. git clone --recurse-submodules https://github.com/aacai/FlashingDevice
# 3. python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
# 4. pip install -e . && flash-device
#
# USB tips:
# - Use a data cable, direct to USB-C port, no hub.
# - Apple Silicon: Thunderbolt/USB-C direct works best.
# - If pyusb can't see device: check System Settings > Privacy > Input/USB accessories.
# - libusb on macOS comes via `pip install libusb-package` fallback if needed;
#   do NOT document brew here per project policy.
