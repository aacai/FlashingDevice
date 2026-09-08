# firmware/ — 本地固件目录（不进仓）

本目录是占位符。**不要提交任何固件/loader。**

正确做法：

```text
firmware/
  <device>/
    rawprogram*.xml
    patch*.xml
    *.img
    prog_firehose_*.elf  # 你自己的 loader
```

- 推荐位置其实是 `~/.flash-device/firmware/`，避免误删。
- 校验：`sha256sum * > sha256sums.txt`，刷前核对 `docs/DEVICES.md`。
- `.gitignore` 已忽略本目录所有真实文件，仅保留本 README + `.gitkeep`。

为什么？固件/loader 多为原厂版权文件，开源仓只保留 `src/flash_device/devices/profiles/*.yaml` 模板。
