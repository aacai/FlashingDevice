#Requires -RunAsAdministrator
# Windows setup (Tier2 experimental): UsbDk + Zadig + Python.
# Run in PowerShell as Admin: .\tools\windows\install_edl.ps1
$ErrorActionPreference = "Stop"
Write-Host "[1/4] Checking Python..."
python --version
Write-Host "[2/4] Installing UsbDk (required for 9008 bulk)..."
# Download from https://github.com/daynix/UsbDk/releases/ manually if this URL rots:
$usbdk = "$env:TEMP\UsbDk_64.msi"
if (!(Test-Path $usbdk)) {
  Invoke-WebRequest -Uri "https://github.com/daynix/UsbDk/releases/latest/download/UsbDk_64.msi" -OutFile $usbdk
}
Start-Process msiexec.exe -ArgumentList "/i `"$usbdk`" /qn" -Wait
Write-Host "[3/4] Python deps..."
pip install -r requirements.txt
Write-Host "[4/4] Done. If 9008 shows exclamation in Device Manager, run Zadig (Drivers/Windows/zadig) to bind WinUSB."
Write-Host "Then: python -m flash_device"
