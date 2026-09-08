# FlashingDevice 使用说明（中文）

开源 Qualcomm 9008 / EDL 刷机工具：**强制备份 + 边界保护 + 真实进度**。

> [!WARNING]
> 刷机有变砖/丢数据风险。本工具强制备份+二次确认，但你必须为自己的 loader+固件负责，
> 机型不对一定不要刷。

## 安装（开发模式）

```bash
git clone --recurse-submodules https://github.com/aacai/FlashingDevice
cd FlashingDevice
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
flash-device --check-env   # 缺 adb/libusb/udev 会直接告诉你补什么
flash-device
```

## 目录说明（为什么是新文件夹？）

`FlashingDevice/` 是一个**全新的干净 git 仓**，和你本地 `shuaji/` 里几十 G 的 LG/小米固件完全隔离：
固件进不了 git 历史，也不会误传。老 `edl_gui.py` 已重构成 `src/flash_device/app.py`，
没有两份重复代码，旧文件留在本地不动、不进仓。

## 子模块 submodule（third_party/edl）—— 初始化和更新

底层 EDL 引擎**没有拷贝**进本仓，是指向 `bkerler/edl` 的子模块，只记四个命令：

```bash
# 1. 首次克隆直接带上引擎（推荐）：
git clone --recurse-submodules https://github.com/aacai/FlashingDevice

# 2. 已经克隆了但 third_party/edl 是空的：
git submodule update --init --recursive

# 3. 以后想把引擎升到上游新版：
git submodule update --remote --merge third_party/edl
git add third_party/edl && git commit -m "chore: bump edl submodule"

# 4. 看状态：
git submodule status
```

注意：子模块里的 `Loaders/` 默认是空的（版权 loader 永不进仓），按机型自备。
CI 里已配 `submodules: recursive`，Actions 自动会拉到引擎。

## 日志

- 每次启动+每次刷机都写文件：`~/.flash-device/logs/flash-device-<时间>.log`（最新指针 `latest.log`）。
- GUI 日志卡片右上有点「打开日志目录」「环境自检」；刷机命令原文、每行输出、退出码全在文件里，
  变砖复盘直接把这个文件发出来看。
- 想看更细：`flash-device --log-level DEBUG`。

Linux：

```bash
sudo cp tools/udev/51-edl.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo systemctl disable --now ModemManager
```

macOS：用 python.org 官方 pkg 装 Python（不要用 brew），直连 USB-C，不要经过 hub。
Windows：实验性支持，需要 UsbDk + Zadig，详见 `tools/windows/`。

## 固件政策

**LG/小米等固件、loader 一律不进仓。** 仓里只有 `devices/profiles/*.yaml` 模板+文档。
本地放到 `firmware/`（已 gitignore）或 `~/.flash-device/`。

## 安全流程

1. 只认 9008（`05c6:9008` 等），fastboot/adb 会锁按钮。
2. loader 校验存在+后缀+非空。
3. 写前强制备份 `gpt + 关键分区`，生成 `MANIFEST.json + sha256sums.txt`。
4. 整包/单写都要二次确认，支持 `--dry-run` 先解析 `rawprogram/patch`。
5. 日志在 `~/.flash-device/logs/`。

详见 `docs/SAFETY.md`。
