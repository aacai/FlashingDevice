"""固件包标准校验：选目录即验明正身，不标准不让刷。

两种标准样式：
  QFIL 包（EDL 直刷）：rawprogram*.xml + 引用的镜像全在 + 条目合法；
      patch*.xml 可无（LG KDZ 解包就没有，无则警告）；loader 另给。
  fastboot 包（小米 flash_all.sh 那种）：有 flash_all.sh + images/，
      本工具走 EDL，如无 rawprogram 则判 error 并指路。

级别：
  error → 直接拦截，刷不了（缺 rawprogram / 引用文件缺失 / 目录不对）；
  warn  → 二次确认（无 patch / 无 GPT 条目 / loader 没着落）；
  ok    → 放行，附带包摘要（几个 rawprogram、几个文件、patch、loader 来源）。
"""

from __future__ import annotations

import glob
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from flash_device.devices.loaders import LOADER_SUFFIX


@dataclass
class FirmwareReport:
    kind: str = "unknown"  # qfil | fastboot | unknown
    level: str = "error"  # ok | warn | error
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rawprograms: list[str] = field(default_factory=list)
    patches: list[str] = field(default_factory=list)
    file_count: int = 0
    loader_hint: str = ""

    @property
    def ok(self) -> bool:
        return self.level != "error"

    def summary(self) -> str:
        if self.kind == "qfil":
            s = f"标准 QFIL 包：{len(self.rawprograms)} 个 rawprogram，{self.file_count} 个镜像"
            s += f"，{len(self.patches)} 个补丁" if self.patches else "，无补丁文件"
            if self.loader_hint:
                s += f"，编程器：{self.loader_hint}"
            return s
        if self.kind == "fastboot":
            return "这是 fastboot 线刷包（flash_all.sh），不是 EDL 包"
        return "不是可识别的刷机包"


def _program_entries(rp_path: str) -> tuple[list[dict], str]:
    try:
        root = ET.parse(rp_path).getroot()
    except ET.ParseError as e:
        return [], f"XML 解析失败：{e}"
    entries = []
    for el in root.iter():
        if el.tag.lower() != "program":
            continue
        fn = (el.get("filename") or "").strip()
        if not fn:
            continue
        try:
            sectors = int(float(el.get("num_partition_sectors") or "0"))
        except ValueError:
            sectors = -1
        try:
            size_kb = float(el.get("size_in_KB") or "0")
        except ValueError:
            size_kb = 0
        # sparse 包（如小米 userdata）：扇区数为 0，大小以文件为准，edl 实测可刷
        sparse = (el.get("sparse") or "").strip().lower() == "true"
        entries.append({"file": fn, "sectors": sectors, "size_kb": size_kb, "sparse": sparse})
    return entries, ""


def _looks_like_loader(filename: str) -> bool:
    """包内自动识别 loader：prog_*/firehose/fhprg 命名（裸 .bin 太多 modem，不能全算）。"""
    n = filename.lower()
    if not n.endswith(LOADER_SUFFIX):
        return False
    if n.endswith((".elf", ".mbn")) and (n.startswith("prog_") or "firehose" in n or "fhprg" in n):
        return True
    return n.startswith("prog_") or "firehose" in n or "fhprg" in n


def validate_fwdir(fwdir: str, loader: str = "", memory: str = "ufs") -> FirmwareReport:
    rep = FirmwareReport()
    d = (fwdir or "").strip()
    if not d or not os.path.isdir(d):
        rep.errors.append("固件目录不存在")
        return rep

    # fastboot 包识别（小米 flash_all.sh 样式）
    if os.path.isfile(os.path.join(d, "flash_all.sh")) or (
        os.path.isdir(os.path.join(d, "images"))
        and os.path.isfile(os.path.join(d, "images", "super.img"))
    ):
        rep.kind = "fastboot"
        raws_in_images = sorted(
            glob.glob(os.path.join(d, "images", "rawprogram*.xml"))
            + glob.glob(os.path.join(d, "rawprogram*.xml"))
        )
        if not raws_in_images:
            rep.errors.append("fastboot 包无 rawprogram，EDL 刷不了——请进 fastboot 跑 flash_all.sh")
            return rep
        d = os.path.join(d, "images") if os.path.isdir(os.path.join(d, "images")) else d

    raws = sorted(glob.glob(os.path.join(d, "**", "rawprogram*.xml"), recursive=True))
    if not raws:
        rep.errors.append("目录下没有 rawprogram*.xml，不是标准 QFIL 包")
        return rep
    # 多 LUN 包要求 rawprogram 连续（0..N），缺号多半解包不全
    rep.kind = "qfil"
    rep.rawprograms = raws
    rep.patches = sorted(glob.glob(os.path.join(d, "**", "patch*.xml"), recursive=True))

    total = 0
    has_gpt = False
    for rp in raws:
        entries, err = _program_entries(rp)
        if err:
            rep.errors.append(f"{os.path.basename(rp)}：{err}")
            continue
        if not entries:
            rep.warnings.append(f"{os.path.basename(rp)} 里没有有效 program 条目")
            continue
        for e in entries:
            total += 1
            if e["sectors"] <= 0 and not e["sparse"] and e["size_kb"] <= 0:
                rep.errors.append(f"{os.path.basename(rp)}：{e['file']} 扇区数异常")
            if not os.path.isfile(os.path.join(os.path.dirname(rp), e["file"])):
                rep.errors.append(f"缺文件：{e['file']}（{os.path.basename(rp)} 引用了它）")
            if "gpt" in e["file"].lower():
                has_gpt = True
    rep.file_count = total
    if total == 0 and not rep.errors:
        rep.errors.append("所有 rawprogram 加起来 0 个有效文件")
    if not has_gpt:
        rep.warnings.append("没看到 GPT 相关镜像，包结构可疑（正常包都有 PrimaryGPT）")
    if not rep.patches:
        rep.warnings.append("无 patch*.xml（LG KDZ 解包常见，可跳过；小米包一般自带）")

    # loader 着落检查
    lp = (loader or "").strip()
    if lp and os.path.isfile(lp) and lp.lower().endswith(LOADER_SUFFIX):
        rep.loader_hint = os.path.basename(lp) + "（已指定）"
    else:
        in_dir = []
        for root, _, files in os.walk(d):
            in_dir += [f for f in files if _looks_like_loader(f)]
            if in_dir:
                break
        if in_dir:
            # 优先推荐 memory 专用的（如 prog_ufs_*），而不是通用 lite 版
            in_dir.sort(key=lambda f: (0 if "ufs" in f.lower() or "emmc" in f.lower() else 1, f))
            rep.loader_hint = in_dir[0] + "（包内自带，可直接用）"
        elif not lp:
            rep.warnings.append("没指定 loader：小米包一般自带 prog_*.elf，LG 需从库/子模块选")
        else:
            rep.errors.append(f"指定的 loader 不存在或后缀不对：{loader}")

    if rep.errors:
        rep.level = "error"
    elif rep.warnings:
        rep.level = "warn"
    else:
        rep.level = "ok"
    return rep
