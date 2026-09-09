# KDZ 支持（LG 官方包一键解包刷机）

GUI 固件目录行点 **「选择 KDZ…」**：预览机型/运营商/版本 → 选输出目录 →
解包跑在统一 job 引擎里（日志/进度与刷机同源）→ 自动生成 rawprogram →
填入固件目录并走标准包校验 → 直接整包刷入。

## 解包器 kdz-tool 从哪里来

- C++ 开源工具，MIT 协议（Pablo Enrique de Toledo Zúñiga），支持 KDZ V1/V2/V3。
- 本仓**不自带二进制**（各平台要分别编译），自备三选一：
  1. 放 `~/.flash-device/bin/kdz-tool`（`chmod +x`，macOS 上同目录还要配好 `libzstd.1.dylib`）；
  2. 放进 PATH；
  3. 设环境变量 `KDZ_TOOL=/path/to/kdz-tool`。
- 没装也不影响刷机：只是"选择 KDZ"按钮会提示补齐，照着装就行。

## 数据流

```text
xxx.kdz ──kdz-tool extract──▶ metadata.json + <LUN>.<分区>.img
        ──gen_rawprograms──▶ rawprogram{L}.xml
        ──validate_fwdir──▶ ✅/⚠️/⛔ ──edl qfil──▶ 手机
```

KDZ/解包产物永不进仓（`.gitignore` 已禁 `*.kdz/*.dz/*.img`，除测试小文件）。
