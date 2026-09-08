#!/bin/bash
# Build Linux AppImage from PyInstaller output. Requires: appimagetool, python3.
# Usage: packaging/appimage/build.sh <version>
set -euo pipefail
VER="${1:-0.1.0}"
DIST="dist/flash-device"
APPDIR="appdir"
rm -rf "$APPDIR" FlashingDevice-"$VER"-x86_64.AppImage
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/share/applications" "$APPDIR/usr/share/icons"
cp -r "$DIST"/* "$APPDIR/usr/bin/"
cat > "$APPDIR/usr/share/applications/flash-device.desktop" <<'EOF'
[Desktop Entry]
Name=FlashingDevice
Comment=Qualcomm 9008 flashing tool
Exec=flash-device
Icon=flash-device
Type=Application
Categories=System;
EOF
cat > "$APPDIR/AppRun" <<'EOF'
#!/bin/bash
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/flash-device" "$@"
EOF
chmod +x "$APPDIR/AppRun"
# icon placeholder (packager can replace)
touch "$APPDIR/usr/share/icons/flash-device.png" "$APPDIR/flash-device.png"
appimagetool "$APPDIR" FlashingDevice-"$VER"-x86_64.AppImage
echo "Built FlashingDevice-$VER-x86_64.AppImage"
