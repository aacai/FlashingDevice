# DEVICES — 机型配置规范（通用模板）

仓内只放 `src/flash_device/devices/profiles/*.yaml`，不放固件/loader。

## 新增机型

1. 复制 `generic.yaml` → `<soc>_<model>.yaml`（如 `sm8150_lg-v50.yaml`）。
2. 填写：存储类型、loader sha256、跳过分区、备注（含进 9008 方式）。
3. 本地验证：`printgpt` 成功 + `gpt` 备份成功，再提 PR。
4. PR 只含 yaml + 文档，不含任何 `.bin/.elf/.img/.xml` 固件。

## LG / 小米说明

- LG V50、Xiaomi pipa 等私有固件**暂不提交**，本地放 `firmware/` 或 `~/.flash-device/firmware/`。
- 需要分享时只分享：机型名、MSM_ID、loader 文件名+sha256、rawprogram 列表、刷机日志脱敏版。

## 进 9008 速查（通用）

- 关机 → 按住音量组合/短接测试点 → 插线，`lsusb` 见 `05c6:9008` 即成功。
- `adb reboot edl` 仅在已 root/工程机有效，不要依赖。
