"""Parse EDL stdout into structured progress events.

Upstream bkerler/edl prints progress via Library/utils.py::print_progress:
    \\rProgress: |████------|  45.5% Write (Sector 0x10 of 0xFF, 00m:10s left) 2.10 MB/s
    \\rDone |██| 100.0% ...
and firehose_client prints:
    [qfil] programming foo.img to partition(3) ...
    [qfil] raw programming ok.
    [qfil] patching ...

This module is deliberately dependency-free so tests can run without PyQt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# e.g. "Progress: |███---|  45.5% Write (Sector 0x10 of 0x20, ...) 1.23 MB/s"
_PROGRESS_RE = re.compile(
    r"(?P<prefix>Progress|Done)\s*:\s*\|[█\-]*\|\s*(?P<pct>\d+(?:\.\d+)?)\s*%\s*(?P<rest>.*)?"
)
# e.g. "[qfil] programming boot_a.img to partition(4) ..."
_QFIL_PROG_RE = re.compile(
    r"\[qfil\]\s+programming\s+(?P<file>\S+)\s+to\s+partition\((?P<part>[^)]+)\)", re.IGNORECASE
)
_QFIL_OK_RE = re.compile(r"\[qfil\]\s+(raw programming ok|patching ok)", re.IGNORECASE)
# Generic "programming <file>" fallback
_GENERIC_PROG_RE = re.compile(r"programming\s+(?P<file>\S+\.(img|bin|elf|mbn))", re.IGNORECASE)

# kdz-tool extract 输出（实测格式，见 docs/KDZ.md 采样）：
#   parts = 176                                    ← DZ 头里总 chunk 数，解包开始前打印
#     extracting part vendor_a...
#       extracting chunk vendor_0.img_91784 (1060864 bytes)...
#     done. extracted size = 1782579200 bytes
_KDZ_TOTAL_RE = re.compile(r"^\s*parts\s*=\s*(\d+)\s*$")
_KDZ_PART_RE = re.compile(r"extracting part\s+(.+?)\.\.\.")
_KDZ_CHUNK_RE = re.compile(r"extracting (?:chunk\s+)?(?P<file>\S+) \((?P<size>\d+) bytes\)")
_KDZ_CHUNK_DONE = "done. extracted size"


@dataclass
class ProgressState:
    """Running progress estimate across a whole qfil/write session."""

    total_files: int = 0
    file_index: int = 0  # 1-based when known
    current_file: str = ""
    op: str = ""  # Write/Read/Erase/qfil/...
    pct_in_file: float = 0.0
    overall_pct: float = 0.0
    log_tail: list = field(default_factory=list)

    def update_overall(self) -> None:
        if self.total_files > 0 and self.file_index > 0:
            base = (self.file_index - 1) / self.total_files * 100.0
            self.overall_pct = base + (self.pct_in_file / 100.0) * (100.0 / self.total_files)
        else:
            # No file enumeration: overall tracks current file pct.
            self.overall_pct = self.pct_in_file
        self.overall_pct = max(0.0, min(100.0, self.overall_pct))


def parse_line(line: str, state: ProgressState) -> ProgressState:
    """Update state in place from one stdout line. Returns state for chaining."""
    # Strip \r progress rewrites and ANSI/padding.
    clean = line.replace("\r", "\n").split("\n")[-1].strip()
    if not clean:
        return state
    state.log_tail.append(clean[-300:])
    if len(state.log_tail) > 5:
        state.log_tail.pop(0)

    # KDZ 解包：先收到 "parts = N"（总 chunk 数），随后每个 chunk 开始/完成各一行。
    # chunk 大小差异大（24KB~1.8GB），按条数估算略不均匀，但远好于 0% 干等。
    if _KDZ_TOTAL_RE.match(clean):
        state.total_files = int(_KDZ_TOTAL_RE.match(clean).group(1))
        state.op = "kdz-extract"
        state.file_index = 0
        state.current_file = ""
        state.pct_in_file = 0.0
        state.update_overall()
        return state

    mp = _KDZ_PART_RE.search(clean)
    if mp and "chunk" not in clean:
        state.op = "kdz-extract"
        state.current_file = mp.group(1).strip()[:120]
        return state

    mc = _KDZ_CHUNK_RE.search(clean)
    if mc:
        state.op = "kdz-extract"
        state.file_index += 1
        state.current_file = mc.group("file")[:120]
        state.pct_in_file = 0.0
        state.update_overall()
        return state

    if _KDZ_CHUNK_DONE in clean:
        state.pct_in_file = 100.0
        state.update_overall()
        return state

    m = _QFIL_PROG_RE.search(clean)
    if m:
        state.current_file = m.group("file").strip()[:120]
        state.op = "qfil-write"
        # file_index increments when a new file starts; total_files may be set by caller.
        if state.total_files and state.file_index < state.total_files or not state.total_files:
            state.file_index += 1
        state.pct_in_file = 0.0
        state.update_overall()
        return state

    m2 = _GENERIC_PROG_RE.search(clean)
    if m2 and "qfil" not in clean.lower():
        state.current_file = m2.group("file").strip()[:120]
        state.update_overall()
        return state

    if _QFIL_OK_RE.search(clean):
        state.pct_in_file = 100.0
        state.update_overall()
        return state

    pm = _PROGRESS_RE.search(clean)
    if pm:
        try:
            pct = float(pm.group("pct"))
        except ValueError:
            return state
        rest = (pm.group("rest") or "").strip()
        # rest like "Write (Sector 0x10 of 0x20, ...) 1.2 MB/s"
        op = rest.split("(")[0].split()[0] if rest else ""
        if op:
            state.op = op[:20]
        state.pct_in_file = max(0.0, min(100.0, pct))
        state.update_overall()
        return state
    return state


def is_progress_line(line: str) -> bool:
    """True for pure EDL progress rewrites (Progress: |██| 50% ...)."""
    clean = line.replace("\r", "\n").split("\n")[-1].strip()
    if not clean:
        return False
    return _PROGRESS_RE.search(clean) is not None


def count_program_entries(rawprogram_xml_text: str) -> int:
    """Count <program .../> entries for total_files estimate. 0 if unparseable."""
    try:
        import xml.etree.ElementTree as ET

        root = ET.fromstring(rawprogram_xml_text)
        # rawprogram files use <data><program .../></data>
        n = 0
        for el in root.iter():
            if el.tag.lower() == "program":
                # skip empty filename entries
                fn = (el.get("filename") or "").strip()
                if fn:
                    n += 1
        return n
    except Exception:
        return 0
