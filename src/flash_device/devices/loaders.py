"""Loader 库：解决 "ELF 藏太深找不到" 的问题。

Firehose loader 是高通/OEM 签名的专有二进制：
  - 不能进开源仓（版权 + 按机型匹配 + 体积），.gitignore 已全禁 *.elf/*.mbn/*.bin；
  - 但散落在各处：bkerler/Loaders 子模块、固件自带（如小米包内 prog_*.elf）、用户手头。
LoaderStore 把它们编成"我的库"（~/.flash-device/loaders）+ 按需扫描：
  - 文件名即身份证：<MSMID>_<PKHASH前16>_<名字>.bin，如 000a50e1_e746e34f…_fhprg_lg_g8x.bin
  - GUI"扫描本机"列出全部候选（名字/来源/大小/sha），一点即用，一键入库。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass

from flash_device.utils.platform import app_data_dir

LOADER_SUFFIX = (".elf", ".mbn", ".bin")
MAX_SCAN_FILES = 500

# 出厂通用名对照（按 sha256 精确匹配，文件名再怎么变也不会认错）：
# bkerler/Loaders 仓用 MSMID_PKHASH 长名（如 000a50e1_…_fhprg_lg_g8x.bin），
# 原厂通用名一般是 prog_ufs_firehose_smXXXX_ddr.elf 格式，两者可能是同一文件。
KNOWN_LOADERS = {
    # LG V50 (sm8150)，本机实测 9008 可用
    "caca45707b5dad61c2e6bd6de27a377a28ac1197cc3c809401684485a0991947": (
        "LG V50 (sm8150) Firehose【出厂通用名：prog_ufs_firehose_sm8150_ddr.elf】"
    ),
    # 小米平板6 pipa (sm8250)，包内自带，实测整包刷入成功
    "bd1da5b2a92f3731c7f3f5f7072587e0ade0b025593ea9f1cfd2d24ddaeaea94": (
        "小米平板6 pipa (sm8250) Firehose【包内原名：prog_ufs_firehose_sm8250_ddr_5.elf】"
    ),
}


def friendly_name(sha256: str, filename: str) -> str:
    return KNOWN_LOADERS.get(sha256, filename)


@dataclass
class LoaderInfo:
    name: str
    path: str
    size: int
    sha256: str
    source: str  # 库 / 随包 / 子模块 / 固件目录

    @property
    def short(self) -> str:
        show = friendly_name(self.sha256, self.name)
        return f"{show}  [{self.source} {self.size // 1024}KB {self.sha256[:12]}…]"


def library_dir() -> str:
    d = os.path.join(app_data_dir(), "loaders")
    os.makedirs(d, exist_ok=True)
    return d


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def repo_loaders_dir() -> str:
    """本仓 third_party/edl/Loaders（submodule，要先 git submodule update）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.normpath(os.path.join(here, "..", "..", "..", "third_party", "edl", "Loaders"))
    if os.path.isdir(cand):
        return cand
    cand2 = os.path.normpath(os.path.join(os.getcwd(), "third_party", "edl", "Loaders"))
    return cand2 if os.path.isdir(cand2) else ""


def _collect(directory: str, source: str, out: list[LoaderInfo]) -> None:
    if not directory or not os.path.isdir(directory):
        return
    n = 0
    for root, _, files in os.walk(directory):
        for fn in sorted(files):
            if not fn.lower().endswith(LOADER_SUFFIX):
                continue
            p = os.path.join(root, fn)
            try:
                size = os.path.getsize(p)
                if size == 0:
                    continue
                out.append(LoaderInfo(fn, p, size, sha256_of(p), source))
            except OSError:
                continue
            n += 1
            if n >= MAX_SCAN_FILES:
                return


def scan(extra_dirs: list[str] | None = None) -> list[LoaderInfo]:
    """扫描：我的库 -> 本仓子模块 -> 指定固件目录。去重（同 sha 只留第一个）。"""
    found: list[LoaderInfo] = []
    _collect(library_dir(), "库", found)
    _collect(repo_loaders_dir(), "子模块", found)
    for d in extra_dirs or []:
        _collect(d, "固件目录", found)
    seen: set[str] = set()
    uniq: list[LoaderInfo] = []
    for info in found:
        if info.sha256 in seen:
            continue
        seen.add(info.sha256)
        uniq.append(info)
    return uniq


def copy_into_library(src: str) -> str:
    """把任意 loader 存入我的库（同名同内容跳过），返回库内路径。"""
    dst = os.path.join(library_dir(), os.path.basename(src))
    if os.path.abspath(src) == os.path.abspath(dst):
        return dst
    if os.path.isfile(dst):
        try:
            if sha256_of(src) == sha256_of(dst):
                return dst
        except OSError:
            pass
        base, ext = os.path.splitext(os.path.basename(src))
        dst = os.path.join(library_dir(), f"{base}_{sha256_of(src)[:8]}{ext}")
    shutil.copy2(src, dst)
    return dst
