# Loader（Firehose ELF）从哪里来？

新手最懵的就是这个文件：刷机命令里 `--loader=xxx.elf`，但全仓找不到。
本页讲清三个来源 + 本工具怎么帮你找。

## 1. 它是什么

Firehose loader 是高通/OEM 签名的**专有二进制**（如 `prog_ufs_firehose_sm8250_ddr_5.elf`），
9008 模式下先由它接管手机才能读写分区。文件名就是身份证：

```text
000a50e1 _ e746e34f737403f4 _ fhprg_lg_g8x.bin
 MSM_ID      PK_HASH前16位      备注
```

- **MSM_ID** 必须和手机 SoC 一致（LG V50=sm8150=`000a50e1`，小米平板6=sm8250=`000c30e1`）；
  不一致会被 PBL 直接拒收（我们实测过：拿 LG 的 loader 去碰小米，`Uploading loader` 后 I/O 中断，**一个字节都写不进去**，虚惊一场）。
- 同 SoC 不同 OEM 还要对 PK_HASH，错了同样拒收——这是好事，天然防砖。

## 2. 三个来源（按推荐顺序）

| # | 来源 | 说明 |
|---|------|------|
| 1 | **固件包自带** | 小米线刷包 `images/` 里经常自带（如 pipa 包的 `prog_ufs_firehose_sm8250_ddr_5.elf`），**最优先用它**，版本一定对 |
| 2 | **bkerler/Loaders 子模块** | `git submodule update --init --recursive` 后在 `third_party/edl/Loaders/<厂商>/`，按 MSM_ID+PKHASH 找 |
| 3 | **我的库** | 手头已有的 loader，GUI 里"存入我的库"收到 `~/.flash-device/loaders/`，以后一点即用 |

## 3. 为什么不开源进仓

1. **版权**：签名 blob 是厂商财产，分发有风险；
2. **匹配**：错一个字符就拒收，进仓反而误导；
3. **体积**：几百 KB～几 MB 一堆，仓会臃肿。
`.gitignore` 已禁 `*.elf/*.mbn/*.bin`，CI 也会拦。放心：GUI 的"扫描本机"对话框会把三个来源全列出来（名字/来源/大小/sha256），**找得到、用得上、进不了仓**。

## 4. 本机实例（作者环境）

```text
FlashingDevice/firmware/loaders/prog_ufs_firehose_sm8150_ddr.elf   ← LG V50 出厂通用名
└─ 原名 000a50e100310000_e746e34f737403f4_fhprg_lg_g8x.bin（bkerler 仓规范），sha 相同
~/.flash-device/loaders/                                            ← 我的库（GUI 一键入库）
~/.flash-device/loaders-archive/Loaders/                            ← 旧子模块全量归档（备用）
firmware/pipa_images_*/images/prog_ufs_firehose_sm8250_ddr_5.elf
└─ 小米包自带，刷 pipa 就用它
```
