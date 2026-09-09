#!/usr/bin/env python3
"""FlashingDevice: open-source Qualcomm 9008 / EDL GUI (PyQt6).

- USB poll, 9008-only operation gate
- Loader + firmware pickers (no hardcoded user paths)
- Mandatory backup flow before destructive writes
- Real progress: total + per-file bars parsed from EDL stdout
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import time

from PyQt6.QtCore import QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFontDatabase
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from flash_device.backend.qfil import build_qfil_argv, qfil_summary
from flash_device.backend.qt_bridge import JobWorker
from flash_device.devices.firmware import validate_fwdir
from flash_device.devices.loaders import copy_into_library
from flash_device.devices.loaders import scan as scan_loaders
from flash_device.safety import guards
from flash_device.safety.backup import default_backup_root, new_backup_dir
from flash_device.utils import platform as pf
from flash_device.utils.envcheck import format_checks, run_all_checks
from flash_device.utils.logging_setup import get_logger
from flash_device.utils.usb import get_edl_serial, rank_device, scan_devices

logger = get_logger(__name__)

APP_NAME = "FlashingDevice"
ORG = "FlashingDevice"

STYLE = """
QWidget { background:#0d1117; color:#e6edf3; font-size:13px; }
QMainWindow { background:#0d1117; }
QGroupBox { border:1px solid #30363d; border-radius:12px; margin-top:18px;
            padding:16px 12px 12px 12px; font-weight:700; color:#9fb4d8;
            background:#161b22; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 8px; }
QLineEdit { background:#0d1117; border:1px solid #30363d; border-radius:8px;
            padding:8px 10px; color:#e6edf3; selection-background-color:#1f6feb; }
QLineEdit:focus { border-color:#2f81f7; }
QPushButton { background:#21262d; border:1px solid #3d444d; border-radius:8px;
              padding:9px 15px; color:#e6edf3; font-weight:600; }
QPushButton:hover { background:#30363d; border-color:#58a6ff; }
QPushButton:pressed { background:#1c2128; }
QPushButton:disabled { background:#161b22; color:#6e7681; border-color:#21262d; }
QPushButton#danger { background:#a40e26; border-color:#da3633; font-weight:700; }
QPushButton#danger:hover { background:#c21f36; }
QPushButton#primary { background:#1f6feb; border-color:#388bfd; font-weight:700; }
QPushButton#primary:hover { background:#388bfd; }
QTextEdit { background:#010409; border:1px solid #30363d; border-radius:10px;
            color:#c9d1d9; __MONO_CSS__ font-size:12px; }
QComboBox { background:#0d1117; border:1px solid #30363d; border-radius:8px;
            padding:7px 28px 7px 10px; min-width:90px; }
QComboBox::drop-down { border:0; width:24px; }
QComboBox QAbstractItemView { background:#161b22; border:1px solid #30363d;
            selection-background-color:#1f6feb; padding:4px; }
QListWidget { background:#010409; border:1px solid #30363d; border-radius:8px; }
QProgressBar { background:#0d1117; border:1px solid #30363d; border-radius:11px;
               height:22px; text-align:center; color:#e6edf3; font-weight:700; }
QProgressBar::chunk { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
               stop:0 #1f6feb, stop:1 #39c5cf); border-radius:11px; }
QScrollBar:vertical { background:#0d1117; width:12px; }
QScrollBar::handle:vertical { background:#30363d; border-radius:6px; min-height:30px; }
"""


class MainWindow(QMainWindow):
    OP_BUTTONS = ("b_print", "b_gpt", "b_rl", "b_r", "b_qfil", "b_w", "b_boot", "b_reset")

    @staticmethod
    def _mono_css() -> str:
        """挑一个本机真实存在的等宽字体。写死 'Menlo,Consolas,monospace' 会让 Qt
        对缺失字体族做别名填充（qt.qpa.fonts 警告，启动多花 100ms+）。"""
        try:
            installed = set(QFontDatabase.families())
        except Exception:
            return ""
        for name in ("Menlo", "Consolas", "DejaVu Sans Mono", "Courier New"):
            if name in installed:
                return f"font-family:{name};"
        return ""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FlashingDevice — Qualcomm 9008 / EDL")
        try:
            scr = QApplication.primaryScreen()
            avail = scr.availableGeometry().height() if scr else 800
        except Exception:
            avail = 800
        self.resize(1000, min(820, int(avail) - 30))
        self.setMinimumHeight(520)
        self._mono_css_ = self._mono_css()
        self.setStyleSheet(STYLE.replace("__MONO_CSS__", self._mono_css_))
        self.settings = QSettings(ORG, APP_NAME)
        self.worker: JobWorker | None = None
        self.seen_9008 = False
        self.has_edl = False
        self._edl_sn = ""
        # 真伪 9008 探测状态（只认 VID:PID 会谎报“已连接”，必须探协议）：
        self._probe_ok: bool | None = None
        self._probe_detail = ""
        self._probe_fails = 0
        self._probe_busy = False
        self._probe_next = 0.0
        self._probe_logged_silent = False
        self.raws: list[str] = []
        self.pats: list[str] = []
        self._build_ui()
        self._restore()
        self._rp_seen: list[str] = []
        self._rp_cur: str | None = None
        self._last_prog_ts: float | None = None
        self._kdz_probe: dict | None = None
        self._state_url = ""
        if self._prefs().get("server_autostart", True):
            self._ensure_state_server(startup=True)
        else:
            logger.info("state server autostart disabled in prefs")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(1000)
        self.beat = QTimer(self)
        self.beat.timeout.connect(self._heartbeat)
        self.beat.start(1000)
        self._poll()

    # ---------------- prefs ----------------
    def _prefs(self) -> dict:
        from flash_device import settings as S

        raw = {k: self.settings.value("prefs/" + k) for k in S.DEFAULTS}
        return S.load(raw)

    def _open_prefs(self) -> None:
        from flash_device.settings_dialog import PrefsDialog

        old = self._prefs()
        dlg = PrefsDialog(self, old)
        if dlg.exec() != dlg.DialogCode.Accepted:
            return
        new = dlg.values()
        for k, v in new.items():
            self.settings.setValue("prefs/" + k, v)
        self._log(
            ">>> 首选项已保存："
            + ", ".join(f"{k}={v}" for k, v in new.items() if new.get(k) != old.get(k))
            or "（无变化）"
        )
        # 端口/自启变了就地生效
        if new.get("server_port") != old.get("server_port") or new.get(
            "server_autostart"
        ) != old.get("server_autostart"):
            try:
                from flash_device.server import shutdown

                shutdown(*self._server_addr(old_port=old.get("server_port", 8899)))
            except Exception:
                pass
            if new.get("server_autostart", True):
                self._ensure_state_server()

    # ---------------- state sync ----------------
    def _server_addr(self, old_port: int | None = None) -> tuple[str, int]:
        if "FLASH_DEVICE_PORT" in os.environ:
            try:
                return "127.0.0.1", int(os.environ["FLASH_DEVICE_PORT"])
            except ValueError:
                pass
        if old_port is not None:
            return "127.0.0.1", old_port
        try:
            return "127.0.0.1", int(self._prefs().get("server_port", 8899))
        except (TypeError, ValueError):
            return "127.0.0.1", 8899

    def _ensure_state_server(self, startup: bool = False) -> bool:
        """保证状态同步接口开着：启动时拉起，刷机前再确认一次。"""
        from flash_device.server import ensure_started

        host, port = self._server_addr()
        ok, url_or_reason = ensure_started(host, port)
        if ok:
            self._state_url = url_or_reason
            self._log(f">>> 状态同步接口：{url_or_reason}" + ("（随 GUI 启动）" if startup else ""))
        else:
            self._log(f">>> [注意] {url_or_reason}，本次刷机继续但 CLI 看不到实时同步")
            logger.warning("state server unavailable: %s", url_or_reason)
        return ok

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        mb = self.menuBar()
        m_set = mb.addMenu("设置")
        act_prefs = m_set.addAction("首选项…")
        act_prefs.setShortcut("Ctrl+,")
        act_prefs.triggered.connect(self._open_prefs)
        # 整个界面包进滚动区：窗口高度受限不超屏，内容再长也能滚动查看。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        gb = QGroupBox("设备")
        gl = QVBoxLayout(gb)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.dot = QLabel("●")
        f = self.font()
        f.setPointSize(30)
        self.dot.setFont(f)
        top.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignTop)
        mid = QVBoxLayout()
        mid.setSpacing(2)
        self.status = QLabel("正在检测…")
        f2 = self.font()
        f2.setPointSize(16)
        f2.setBold(True)
        self.status.setFont(f2)
        mid.addWidget(self.status)
        self.detail = QLabel("—")
        self.detail.setStyleSheet(f"color:#8b949e; {self._mono_css_} font-size:12px;")
        mid.addWidget(self.detail)
        top.addLayout(mid, 1)
        right = QVBoxLayout()
        right.setSpacing(4)
        # 窗口内右上角设置入口：macOS 菜单在屏幕顶栏，窗口里没有按钮容易找不到。
        b_prefs = QPushButton("⚙ 设置")
        b_prefs.setFixedWidth(88)
        b_prefs.setToolTip("首选项（Ctrl+,）")
        b_prefs.clicked.connect(self._open_prefs)
        row_tr = QHBoxLayout()
        row_tr.addStretch(1)
        row_tr.addWidget(b_prefs)
        right.addLayout(row_tr)
        mem_label = QLabel("存储类型")
        mem_label.setStyleSheet("color:#9fb4d8; font-size:12px;")
        mem_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        right.addWidget(mem_label)
        self.mem = QComboBox()
        self.mem.addItems(["ufs", "emmc", "nand", "spinor"])
        self.mem.setMinimumWidth(120)
        self.mem.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        top.addWidget(self.mem)
        self.b_refresh = QPushButton("刷新")
        self.b_refresh.setToolTip("立即重新检测 USB 并重探 9008 是否真的活着")
        self.b_refresh.clicked.connect(self._manual_refresh)
        top.addWidget(self.b_refresh)
        gl.addLayout(top)
        self.hint = QLabel("—")
        self.hint.setStyleSheet("color:#8b949e; font-size:12px; padding-top:4px;")
        self.hint.setWordWrap(True)
        gl.addWidget(self.hint)
        v.addWidget(gb)

        gb_pkg = QGroupBox("刷机包")
        gpkg = QVBoxLayout(gb_pkg)
        form = QFormLayout()
        form.setSpacing(8)
        row_loader = QHBoxLayout()
        row_loader.setSpacing(8)
        self.loader = QLineEdit()
        self.loader.setPlaceholderText("prog_firehose_*.elf / *.mbn（点“扫描本机”自动找）")
        b2 = QPushButton("浏览…")
        b2.clicked.connect(self._pick_loader)
        bscan = QPushButton("扫描本机…")
        bscan.clicked.connect(self._scan_loaders)
        row_loader.addWidget(self.loader, 1)
        row_loader.addWidget(b2)
        row_loader.addWidget(bscan)
        form.addRow("Firehose 编程器:", row_loader)
        row_fw = QHBoxLayout()
        row_fw.setSpacing(8)
        self.fwdir = QLineEdit()
        self.fwdir.setPlaceholderText(
            "rawprogram*.xml + 镜像所在目录；只有 .kdz 就点右边的选择 KDZ"
        )
        b3 = QPushButton("浏览…")
        b3.clicked.connect(self._pick_fw)
        bkdz = QPushButton("选择 KDZ…")
        bkdz.clicked.connect(self._pick_kdz)
        row_fw.addWidget(self.fwdir, 1)
        row_fw.addWidget(b3)
        row_fw.addWidget(bkdz)
        form.addRow("固件目录:", row_fw)
        row_sub = QHBoxLayout()
        row_sub.setSpacing(8)
        self.bootsub = QLineEdit()
        self.bootsub.setPlaceholderText(
            "可选：整包刷入时用这个 boot 替换包内 boot_a/boot_b（如 Magisk 修补镜像）"
        )
        bsub = QPushButton("浏览…")
        bsub.clicked.connect(self._pick_bootsub)
        bsub_clr = QPushButton("清除")
        bsub_clr.clicked.connect(self.bootsub.clear)
        row_sub.addWidget(self.bootsub, 1)
        row_sub.addWidget(bsub)
        row_sub.addWidget(bsub_clr)
        form.addRow("替代 boot:", row_sub)
        gpkg.addLayout(form)
        self.fwinfos = QLabel("—")
        self.fwinfos.setStyleSheet("color:#8b949e; font-size:12px;")
        self.fwinfos.setWordWrap(True)
        self.fwinfos.setTextInteractionFlags(
            self.fwinfos.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        gpkg.addWidget(self.fwinfos)
        v.addWidget(gb_pkg)

        gb4 = QGroupBox("操作（9008 连接后解锁）")
        g4 = QVBoxLayout(gb4)
        row1 = QHBoxLayout()
        self.b_print = QPushButton("打印分区表")
        self.b_gpt = QPushButton("备份分区表")
        self.b_rl = QPushButton("备份全部分区")
        self.b_r = QPushButton("读取单分区…")
        row1.addWidget(self.b_print)
        row1.addWidget(self.b_gpt)
        row1.addWidget(self.b_rl)
        row1.addWidget(self.b_r)
        row2 = QHBoxLayout()
        self.b_qfil = QPushButton("⚠  整包刷入 (QFIL)")
        self.b_qfil.setObjectName("danger")
        self.b_w = QPushButton("刷入单分区…")
        self.b_boot = QPushButton("刷 boot…")
        self.b_reset = QPushButton("重启设备")
        self.b_stop = QPushButton("停止")
        self.b_stop.setEnabled(False)
        row2.addWidget(self.b_qfil)
        row2.addWidget(self.b_w)
        row2.addWidget(self.b_boot)
        row2.addWidget(self.b_reset)
        row2.addWidget(self.b_stop)
        g4.addLayout(row1)
        g4.addLayout(row2)
        self.b_print.clicked.connect(self._do_printgpt)
        self.b_gpt.clicked.connect(self._do_gpt)
        self.b_rl.clicked.connect(self._do_rl)
        self.b_r.clicked.connect(self._do_r)
        self.b_qfil.clicked.connect(self._do_qfil)
        self.b_w.clicked.connect(self._do_w)
        self.b_boot.clicked.connect(self._do_boot)
        self.b_reset.clicked.connect(self._do_reset)
        self.b_stop.clicked.connect(self._stop)
        v.addWidget(gb4)

        gb5 = QGroupBox("日志与进度（每次运行都写入文件，方便事后复盘）")
        g5 = QVBoxLayout(gb5)
        # rawprogram 进度小方块：随刷机推进点亮（灰=未动 / 蓝=进行中 / 绿=完成 / 红=出错）
        chrow = QHBoxLayout()
        self.chips = []
        for i in range(7):
            c = QLabel(f"rawprogram{i}")
            c.setFixedHeight(26)
            c.setAlignment(Qt.AlignmentFlag.AlignCenter)
            c.setStyleSheet(
                "QLabel{background:#21262d;border:1px solid #30363d;border-radius:6px;"
                "padding:2px 8px;color:#8b949e;font-size:12px;}"
            )
            self.chips.append(c)
            chrow.addWidget(c)
        g5.addLayout(chrow)
        logrow = QHBoxLayout()
        self.logpath_label = QLabel("日志: 初始化中…")
        self.logpath_label.setStyleSheet("color:#8b8b9a; font-size:12px;")
        self.logpath_label.setTextInteractionFlags(
            self.logpath_label.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        b_open_log = QPushButton("打开日志目录")
        b_open_log.clicked.connect(self._open_log_dir)
        b_env = QPushButton("环境自检")
        b_env.clicked.connect(self._show_env_dialog)
        self.heartbeat = QLabel("空闲")
        self.heartbeat.setStyleSheet("color:#8b949e; font-size:12px;")
        logrow.addWidget(self.logpath_label, 1)
        logrow.addWidget(self.heartbeat)
        logrow.addWidget(b_env)
        logrow.addWidget(b_open_log)
        g5.addLayout(logrow)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(220)
        g5.addWidget(self.log)
        self.op_label = QLabel("")
        self.op_label.setStyleSheet("color:#8b8b9a; font-size:12px;")
        g5.addWidget(self.op_label)
        self.bar_total = QProgressBar()
        self.bar_total.setRange(0, 100)
        self.bar_total.setValue(0)
        self.bar_total.setFormat("总进度 %p%")
        g5.addWidget(self.bar_total)
        self.bar_file = QProgressBar()
        self.bar_file.setRange(0, 100)
        self.bar_file.setValue(0)
        self.bar_file.setFormat("当前文件 %p%")
        g5.addWidget(self.bar_file)
        v.addWidget(gb5)

        scroll.setWidget(container)
        self.setCentralWidget(scroll)

    def _restore(self) -> None:
        self.loader.setText(self.settings.value("loader", ""))
        self.fwdir.setText(self.settings.value("fwdir", ""))
        self.bootsub.setText(self.settings.value("boot_sub", ""))
        mem = self.settings.value("mem", "ufs")
        # 共享状态（CLI/HTTP 写过）非空即覆盖本地：CLI 操作后开 GUI 即见最新。
        try:
            from flash_device import state as shared

            st = shared.load()
            synced = [k for k in ("loader", "fwdir", "memory") if st.get(k)]
            if synced:
                if st.get("loader"):
                    self.loader.setText(st["loader"])
                if st.get("fwdir"):
                    self.fwdir.setText(st["fwdir"])
                if st.get("memory"):
                    mem = st["memory"]
                logger.info(
                    "synced shared state (%s) updated_at=%s by %s",
                    ",".join(synced),
                    st.get("updated_at"),
                    st.get("updated_by"),
                )
        except Exception as e:
            logger.warning("shared state unreadable: %s", e)
        idx = self.mem.findText(mem)
        if idx >= 0:
            self.mem.setCurrentIndex(idx)
        if self.fwdir.text().strip():
            self._scan_fw()

    def _save(self) -> None:
        self.settings.setValue("loader", self.loader.text())
        self.settings.setValue("fwdir", self.fwdir.text())
        self.settings.setValue("mem", self.mem.currentText())
        self.settings.setValue("boot_sub", self.bootsub.text())
        try:
            from flash_device import state as shared

            shared.save(
                {
                    "loader": self.loader.text(),
                    "fwdir": self.fwdir.text(),
                    "memory": self.mem.currentText(),
                },
                by="gui",
            )
        except Exception as e:
            logger.warning("shared state unwritable: %s", e)

    # ---------------- pickers ----------------
    def _pick_loader(self) -> None:
        # 从当前已填路径所在目录开始选，方便微调；没填过才回 home。
        cur = self.loader.text().strip()
        start = os.path.dirname(cur) if cur else os.path.expanduser("~")
        p, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Firehose 编程器",
            start,
            "Firehose (*.elf *.mbn *.bin);;所有文件 (*)",
        )
        if p:
            self.loader.setText(p)
            self._save()

    def _scan_loaders(self) -> None:
        """扫描本机 loader（我的库→本仓子模块→当前固件目录），选中即用，可一键入库。"""
        from PyQt6.QtWidgets import QDialog

        found = scan_loaders([self.fwdir.text().strip()] if self.fwdir.text().strip() else [])
        if not found:
            QMessageBox.information(
                self,
                "扫描本机",
                "没找到任何 loader。\n\n去向：① 固件包自带（如小米包内 prog_*.elf）；"
                "② git submodule update --init --recursive 拉 bkerler/Loaders；"
                "③ 把手头 loader 放进 ~/.flash-device/loaders/。\n详见 docs/LOADERS.md。",
            )
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"本机 loader（{len(found)} 个）")
        dlg.resize(640, 380)
        lay = QVBoxLayout(dlg)
        lst = QListWidget()
        for info in found:
            QListWidgetItem(info.short, lst)
        lst.setCurrentRow(0)
        lay.addWidget(lst, 1)
        btns = QHBoxLayout()
        b_use = QPushButton("选用")
        b_save = QPushButton("存入我的库")
        b_close = QPushButton("关闭")
        btns.addWidget(b_use)
        btns.addWidget(b_save)
        btns.addStretch(1)
        btns.addWidget(b_close)
        lay.addLayout(btns)

        def use():
            row = lst.currentRow()
            if 0 <= row < len(found):
                self.loader.setText(found[row].path)
                self._save()
                self._log(f">>> 已选 loader：{found[row].short}\n    {found[row].path}")
                dlg.accept()

        def save():
            row = lst.currentRow()
            if 0 <= row < len(found):
                try:
                    dst = copy_into_library(found[row].path)
                    self._log(f">>> 已入库：{dst}")
                    QMessageBox.information(self, "入库", f"已存入：\n{dst}")
                except OSError as e:
                    QMessageBox.warning(self, "入库失败", str(e))

        b_use.clicked.connect(use)
        b_save.clicked.connect(save)
        b_close.clicked.connect(dlg.reject)
        lst.itemDoubleClicked.connect(lambda *_: use())
        dlg.exec()

    def _pick_fw(self) -> None:
        # 同理：从当前固件目录开始选，而不是每次都回 home。
        start = self.fwdir.text().strip() or os.path.expanduser("~")
        p = QFileDialog.getExistingDirectory(self, "选择固件目录", start)
        if p:
            self.fwdir.setText(p)
            self._scan_fw()
            self._save()

    def _pick_bootsub(self) -> None:
        """选替代 boot 镜像：整包刷入时替换包内 boot_a/boot_b（如 Magisk 修补后的 boot）。"""
        cur = self.bootsub.text().strip()
        start = (
            os.path.dirname(cur) if cur else (self.fwdir.text().strip() or os.path.expanduser("~"))
        )
        p, _ = QFileDialog.getOpenFileName(
            self,
            "选择替代 boot 镜像",
            start,
            "镜像 (*.img *.mbn *.bin);;所有文件 (*)",
        )
        if p:
            self.bootsub.setText(p)
            self._save()

    def _pick_kdz(self) -> None:
        """选 .kdz → 解包 → 生成 rawprogram → 自动填入固件目录并校验。"""
        from flash_device.devices import kdz as kdzmod

        # 先拦忙：解包和刷机共用单任务锁，忙时走完全流程才报"解包失败"是误导。
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(
                self, "忙", "有任务正在执行（看进度条），等它结束或点「停止」后再解包 KDZ。"
            )
            return
        start = self.settings.value("kdz_dir", "") or os.path.expanduser("~")
        p, _ = QFileDialog.getOpenFileName(
            self,
            "选择 LG KDZ 固件包",
            start,
            "LG KDZ (*.kdz);;所有文件 (*)",
        )
        if not p:
            return
        self.settings.setValue("kdz_dir", os.path.dirname(p))
        tool, hint = kdzmod.find_extractor(self._prefs().get("kdz_tool") or "")
        if not tool:
            QMessageBox.warning(self, "缺解包器", hint + "\n详见 docs/KDZ.md。")
            return
        info = kdzmod.parse_kdz_header(p)
        if not info.get("ok"):
            QMessageBox.warning(self, "不是 KDZ", info.get("error", ""))
            return
        # 输出目录：设置页里填了「KDZ 输出目录」就直接用，否则每次弹窗问。
        fixed_out = self._prefs().get("kdz_out_dir") or ""
        if fixed_out:
            dest = fixed_out
        else:
            dest = QFileDialog.getExistingDirectory(
                self,
                "选择解包输出目录（将新建子目录）",
                os.path.dirname(kdzmod.default_dest_for(p)),
            )
            if not dest:
                return
        out = os.path.join(dest, os.path.basename(kdzmod.default_dest_for(p)))
        r = QMessageBox.question(
            self,
            "解包 KDZ",
            f"机型：{info['model']}（{info['carrier']} {info['region']} v{info['version']}）\n"
            f"输出到：{out}\n\n"
            "7GB 包解包约需几分钟，开始？",
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        os.makedirs(out, exist_ok=True)
        # kdz-tool stdout 全缓冲（退出才 flush），GUI 靠轮询输出目录文件数/字节做实时进度；
        # 这里先跑一次 header-only（不写盘，秒回）预估分区文件数与总字节。
        n_parts, n_bytes = kdzmod.probe_extract_plan(p, tool)
        self._kdz_probe = {"out": out, "parts": n_parts, "total": n_bytes}
        summary = f"解包 KDZ：{os.path.basename(p)} → {out}"

        def after(code: int) -> None:
            if code == -2:
                QMessageBox.warning(
                    self,
                    "解包没启动",
                    "退出码 -2：有别的任务在跑，解包被单任务锁拒绝（不是 KDZ 的问题）。\n"
                    "等当前任务结束或点「停止」后再试。",
                )
                return
            if code != 0:
                QMessageBox.warning(
                    self, "解包失败", f"kdz-tool 退出码 {code}，详情看日志文件找原因。"
                )
                return
            try:
                made = kdzmod.gen_rawprograms(out)
            except (OSError, ValueError) as e:
                QMessageBox.warning(self, "生成分区表失败", str(e))
                return
            self.fwdir.setText(out)
            self._scan_fw()
            self._save()
            self._log(
                f">>> 解包完成，生成 {len(made)} 个 rawprogram，已填入固件目录并校验（见上）。"
            )

        self._run([tool, "extract", p, "-d", out], status_text=summary, on_done=after)

    def _scan_fw(self) -> None:
        d = self.fwdir.text().strip()
        raws, pats, _msg = guards.scan_firmware_dir(d)
        self.raws, self.pats = raws, pats
        extra = ""
        if raws and not self.loader.text().strip():
            # os.walk 而非 glob：目录名含 [ ] 时 glob 会当字符类，整个目录匹配为空。
            cands = []
            for root, _dirs, files in os.walk(d):
                for name in files:
                    low = name.lower()
                    if low.startswith("prog_firehose") and low.endswith((".elf", ".mbn")):
                        cands.append(os.path.join(root, name))
            if cands:
                self.loader.setText(cands[0])
                extra = f"\n（已自动选中编程器: {os.path.basename(cands[0])}）"
        # 目录状态只显示一行结论（详情去日志里看），界面不堆文字。
        rep = validate_fwdir(d, self.loader.text().strip(), self.mem.currentText())
        mark = {"ok": "✅", "warn": "⚠️", "error": "⛔"}[rep.level]
        line = f"{mark} {rep.summary()}"
        notes = (rep.errors + rep.warnings)[:2]
        if notes:
            line += "；" + "；".join(notes)[:160]
        if extra:
            line += extra
        self.fwinfos.setText(line)

    # ---------------- device poll ----------------
    def _set(self, color: str, text: str) -> None:
        self.dot.setStyleSheet(f"color:{color};")
        self.status.setText(text)
        self.status.setStyleSheet(f"color:{color};")

    def _update_buttons(self) -> None:
        busy = bool(self.worker and self.worker.isRunning())
        ok = self.has_edl and not busy
        for name in self.OP_BUTTONS:
            getattr(self, name).setEnabled(ok)
        self.b_stop.setEnabled(busy)

    def _edl_health(self) -> tuple[str, str]:
        """healthy | silent | busy | unknown（连续 2 次无响应才判死，防单次抖动误杀）。"""
        if self._probe_ok is True:
            return "healthy", self._probe_detail
        if self._probe_detail == "busy":
            return "busy", self._probe_detail
        if self._probe_fails >= 2:
            return "silent", self._probe_detail or "silent"
        return "unknown", self._probe_detail

    def _maybe_probe(self) -> None:
        """约 12s 探一次协议；刷机进行中绝不碰设备；结果下轮 tick 渲染（不跨线程碰 UI）。"""
        import threading
        import time

        if self._probe_busy:
            return
        if self.worker and self.worker.isRunning():
            return
        now = time.monotonic()
        if now < self._probe_next:
            return
        self._probe_busy = True
        self._probe_next = now + 12

        def go() -> None:
            try:
                from flash_device.utils.usb import probe_9008

                ok, detail = probe_9008()
            except Exception as e:
                ok, detail = False, f"error:{e}"
            self._probe_ok = ok
            self._probe_detail = detail
            if ok:
                self._probe_fails = 0
            else:
                self._probe_fails += 1
            self._probe_busy = False

        threading.Thread(target=go, daemon=True).start()

    def _manual_refresh(self) -> None:
        """刷新按钮：清掉上轮探测缓存，立即重查一遍（拔插后点它，不用干等）。"""
        self.seen_9008 = False
        self._probe_ok = None
        self._probe_detail = ""
        self._probe_fails = 0
        self._probe_next = 0.0
        self._probe_logged_silent = False
        self._log(">>> 手动刷新：重新检测 USB…")
        self._poll()

    def _poll(self) -> None:
        devices = scan_devices()
        ordered = sorted(devices, key=rank_device)
        edl = [d for d in devices if d[3] in guards.EDL_PIDS]
        self.has_edl = bool(edl)
        self._update_buttons()
        if edl:
            b, p, vid, pid, nm = edl[0]
            sn = get_edl_serial()
            self._edl_sn = sn
            extra = f"    SN:{sn}" if sn else ""
            self.detail.setText(f"{vid:04x}:{pid:04x}    Bus {b}  Port {p}    {nm}{extra}")
            health, hdetail = self._edl_health()
            if health == "healthy":
                self._set("#3ddc84", "✅  9008 已连接 —— 可以操作")
                self.hint.setText("已强制要求： destructive 操作前先备份，整包/单写需二次确认。")
                self._probe_logged_silent = False
            elif health == "silent":
                self._set("#ff5d5d", "⛔  9008 无响应（假死）——现在刷只会空转")
                self.hint.setText(
                    "手机 USB 口活着但协议已死（残留会话/电量过低/需断电重启）：\n"
                    "1) 按住电源键 20 秒强制断电；2) 充电；3) 换口直连；4) 音量+-插线重进 9008。"
                    f"（探针：{hdetail}）"
                )
                if not self._probe_logged_silent:
                    self._probe_logged_silent = True
                    self._log(f">>> [探针] 9008 无响应（{hdetail}），已亮红灯，开刷会被拦截。")
            elif health == "busy":
                self._set("#d8a534", "⚠ 9008 正被别的程序占用")
                self.hint.setText("可能有另一个刷机窗口/脚本正在用设备，同时刷会变砖，先停掉那边。")
            else:  # unknown：刚出现，探针还在路上，不谎报也不误拦
                self._set("#8b8b9a", "○ 9008 已出现，正在确认是否真的活着…")
                self.hint.setText("确认协议有回应后才会绿灯，稍等几秒。")
            self._maybe_probe()
            if not self.seen_9008:
                self.seen_9008 = True
                pf.notify("9008 已连接", f"检测到 {vid:04x}:{pid:04x}")
                self._log(f">>> 检测到 9008 设备 {vid:04x}:{pid:04x}" + (f" SN:{sn}" if sn else ""))
            return
        self.seen_9008 = False
        self._probe_ok = None
        self._probe_detail = ""
        self._probe_fails = 0
        self._probe_logged_silent = False
        if not ordered:
            self._set("#55555f", "○  没插任何 USB 设备")
            self.detail.setText("—")
            self.hint.setText("用数据线直连（别经过 Hub），关机状态进 9008 后再插线。")
            return
        b, p, vid, pid, nm = ordered[0]
        self.detail.setText(f"{vid:04x}:{pid:04x}    Bus {b}  Port {p}    {nm}")
        kind = guards.classify_pid(pid)
        if kind == "fastboot":
            self._set("#d8a534", "⚠  Fastboot 模式 —— 还不是 9008")
            self.hint.setText("fastboot 下本工具的 9008 功能不可用，请按机型方式进 9008。")
        elif kind == "adb":
            self._set("#4a9eda", "⚠  ADB 模式 —— 设备正常开机，不是 9008")
            self.hint.setText("开着机永远不会变成 9008，需关机后按机型方式进 9008。")
        else:
            self._set("#8b8b9a", "○  未识别到手机/平板")
            self.hint.setText(f"当前看到 {len(ordered)} 个 USB 设备，平板没插上或没进 9008。")

    # ---------------- run ----------------
    def _edl_cmd(self) -> list[str]:
        edl_py = pf.resolve_edl_bin(self._prefs().get("edl_bin") or "")
        if edl_py.endswith(".py"):
            return [pf.python_for_edl(), edl_py]
        return [edl_py]

    def _ready(self) -> bool:
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "忙", "有操作正在执行，请先停止。")
            return False
        g = guards.require_edl_present(self.has_edl)
        if not g.ok:
            QMessageBox.warning(self, "无 9008 设备", g.message)
            return False
        health, hdetail = self._edl_health()
        if health == "silent":
            QMessageBox.warning(
                self,
                "9008 无响应，禁止开刷",
                "探针确认手机 USB 口活着但协议已死（残留会话/电量过低/需断电重启），\n"
                "现在点刷只会空转，之前已经浪费过两次 11 分钟。\n\n"
                "请：1) 按住电源键 20 秒强制断电；2) 充电；3) 换口直连；\n"
                "4) 音量+-插线重进 9008，等本界面变绿灯再刷。\n"
                f"（探针：{hdetail}）",
            )
            return False
        if health == "busy":
            QMessageBox.warning(
                self,
                "设备正被占用",
                "有别的程序占着 9008（可能正在别处刷机），同时开刷会变砖，先停掉那边。",
            )
            return False
        gl = guards.validate_loader(self.loader.text())
        if not gl.ok:
            QMessageBox.warning(self, "编程器问题", gl.message)
            return False
        want = (self._prefs().get("expected_serial") or "").strip().upper()
        if want:
            sn = (self._edl_sn or "").strip().upper()
            if sn and sn != want:
                QMessageBox.warning(
                    self,
                    "设备不对",
                    f"当前 9008 序列号 {sn}，设置里期望的是 {want}，拒绝开刷（防刷错机）。\n"
                    "换对设备，或去 设置→首选项 清空期望序列号。",
                )
                return False
            if not sn:
                self._log(">>> [注意] 设置了期望序列号但读不到当前 SN，本次放行，请目测确认设备。")
        return True

    def _run(
        self,
        args: list[str],
        cwd: str | None = None,
        total_files: int = 0,
        status_text: str = "",
        on_done=None,
    ) -> None:
        logger.info("run: %s (cwd=%s)", " ".join(args), cwd)
        self._ensure_state_server()
        self._log("$ " + " ".join(args))
        self.bar_total.setValue(0)
        self.bar_file.setValue(0)
        self._rp_seen = []
        self._rp_cur = None
        self._update_rp_chips()
        self.op_label.setText(status_text or "")
        self._post_job = on_done
        self.worker = JobWorker(status_text or "刷机任务", args, total_files=total_files, cwd=cwd)
        self.worker.line.connect(self._log)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._finished)
        self._job_start = time.monotonic()
        self._no_upload_warned = False
        self._update_buttons()
        self.worker.start()

    def _on_progress(self, overall: float, op: str, cur: str, file_pct: float = 0.0) -> None:
        overall = max(0.0, min(100.0, overall))
        file_pct = max(0.0, min(100.0, file_pct))
        self._last_prog_ts = time.monotonic()
        self.bar_total.setValue(int(overall))
        self.bar_file.setValue(int(file_pct))
        what = cur if cur else (op or "处理中")
        self.op_label.setText(
            f"正在处理：{what}    总进度 {overall:.1f}%    当前文件 {file_pct:.0f}%"
        )

    # 残留会话超时：超过这么久还没上传 loader，基本就是连上旧会话，继续等=浪费时间。
    STALE_SESSION_TIMEOUT = 120

    def _heartbeat(self) -> None:
        """1s 心跳：即使信号通路异常，也直接从 job 快照刷新进度条；卡住肉眼可见。"""
        w = self.worker
        if w is not None and w.isRunning() and w.job is not None:
            try:
                snap = w.job.snapshot(log_tail=0)
                self.bar_total.setValue(int(snap["overall"]))
                self.bar_file.setValue(int(snap["file_pct"]))
                w.state.pct_in_file = snap["file_pct"]
            except Exception:
                pass
            if self._last_prog_ts is None:
                self.heartbeat.setText("● 等待第一包进度…")
            else:
                age = int(time.monotonic() - self._last_prog_ts)
                self.heartbeat.setText(f"● 进度{age}s前更新" if age > 1 else "● 进度实时同步中")
            self.heartbeat.setStyleSheet("color:#3fb950; font-size:12px;")
            # 看门狗只针对 edl 刷机；KDZ 解包开头要算 7GB 哈希，本来就该静默一两分钟。
            if not (w.args and str(w.args[0]).rstrip("/").endswith("kdz-tool")):
                self._watch_stale_session(w)
            # KDZ 解包：stdout 全缓冲不可靠，改用输出目录文件数/字节实时估算。
            if w.args and str(w.args[0]).rstrip("/").endswith("kdz-tool"):
                self._kdz_dir_progress()
        else:
            self.heartbeat.setText("空闲")
            self.heartbeat.setStyleSheet("color:#8b949e; font-size:12px;")

    def _kdz_dir_progress(self) -> None:
        """轮询解包输出目录：文件数算百分比（准确），字节数做辅助展示。"""
        probe = getattr(self, "_kdz_probe", None) or {}
        out = probe.get("out", "")
        if not out:
            return
        n_files, written = 0, 0
        for root, _dirs, files in os.walk(out):
            for name in files:
                n_files += 1
                try:
                    written += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        parts = int(probe.get("parts") or 0)
        mb = written / 1048576
        if parts > 0:
            # +4 ≈ 2 个 DLL + 2 个附加数据，metadata.json 最后写，封顶 99% 等退出。
            pct = max(0.0, min(99.0, n_files / (parts + 4) * 100.0))
            self.bar_total.setValue(int(pct))
            self.bar_file.setValue(int(pct))
            tail = "" if n_files else "（先校验哈希，开头无写入属正常）"
            self.op_label.setText(
                f"正在解包：{n_files}/{parts} 个分区    已写入 {mb:.0f} MB    总进度 {pct:.0f}%{tail}"
            )
        else:
            self.op_label.setText(f"正在解包：已写入 {mb:.0f} MB（实时字节数）")

    def _watch_stale_session(self, w) -> None:
        """残留会话提醒：超 120s 还没 Uploading loader → 只提醒，不自动杀任务。

        由用户决定继续等还是点「停止」——自动杀会打断正常操作（误杀比空等更招恨）。
        """
        if getattr(self, "_no_upload_warned", False):
            return
        start = getattr(self, "_job_start", None)
        if start is None or time.monotonic() - start < self.STALE_SESSION_TIMEOUT:
            return
        try:
            recent = " ".join(list(w.job.log)[-60:])
        except Exception:
            return
        if "Uploading loader" not in recent and w.job.state != "done":
            self._no_upload_warned = True
            msg = (
                "120 秒还没上传 loader：可能是残留会话或手机 EDL 引擎卡死（枚举成 9008 但不回话）。"
                "任务继续挂着不动它，确认没动静就点「停止」。处理：拔线 → 手机彻底断电"
                "（长按电源 10 秒）→ 重新进 9008 → 换一个 USB 口直连再试。"
            )
            self._log(">>> [提醒] " + msg)
            logger.warning(
                "no loader upload within %ss (advisory only)", self.STALE_SESSION_TIMEOUT
            )
            pf.notify("刷机提醒", "120 秒还没上传 loader，请确认设备状态")

    def _stop(self) -> None:
        if self.worker:
            self.worker.kill()
            self._log(">>> 已中断")

    def _finished(self, code: int) -> None:
        self._kdz_probe = None
        if code == 0:
            self.bar_total.setValue(100)
            self.bar_file.setValue(100)
            result = "成功"
        elif code is None:
            result = "已中断"
        else:
            result = f"失败(退出码 {code})"
        self._update_buttons()
        logger.info("done: exit=%s", code)
        self._log(f"=== 结束：{result} ===")
        # Sahara error：上次会话被打断，ROM 拒绝新会话——这必须拔线重进 9008，重试没用。
        job_log = ""
        try:
            if self.worker is not None and self.worker.job is not None:
                job_log = " ".join(list(self.worker.job.log))
        except Exception:
            job_log = ""
        if "Sahara error state" in job_log or "Mode detected: error" in job_log:
            msg = (
                "手机的 9008 处于 Sahara error 状态（上次会话被打断所致），EDL 拒绝新会话。\n\n"
                "拔线等 5 秒 → 手机彻底关机 → 重新进 9008 → 插线再操作。"
            )
            self._log(">>> [提示] " + msg.replace("\n", " "))
            pf.notify("9008 错误状态", "Sahara error：请重启手机重进 9008")
            QMessageBox.warning(self, "9008 处于错误状态", msg)
        pf.notify("操作完成", result)
        try:
            from flash_device import state as shared

            op = ""
            try:
                op = (self.op_label.text() or "")[:160]
            except Exception:
                pass
            shared.save({"last_job": {"op": op, "result": result, "exit_code": code}}, by="gui")
        except Exception as e:
            logger.warning("shared last_job unwritable: %s", e)
        post, self._post_job = getattr(self, "_post_job", None), None
        if callable(post):
            try:
                post(code)
            except Exception as e:
                self._log(f"[!] 后续步骤失败：{e}")

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save()
        try:
            from flash_device.server import shutdown

            shutdown(*self._server_addr())
        except Exception:
            pass
        super().closeEvent(event)

    def _open_log_dir(self) -> None:
        from flash_device.utils.logging_setup import get_log_path
        from flash_device.utils.platform import log_dir

        path = get_log_path() or log_dir()
        target = os.path.dirname(path) if os.path.isfile(path) else path
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def _show_env_dialog(self) -> None:
        checks = run_all_checks()
        text = format_checks(checks)
        logger.info("env check:\n%s", text)
        self._log(">>> 环境自检:\n" + text)
        bad = [c for c in checks if not c.ok]
        if bad:
            QMessageBox.warning(self, "环境自检：有缺失", text + "\n\n按每行“补齐”提示安装后重试。")
        else:
            QMessageBox.information(self, "环境自检", text)

    def _update_rp_chips(self) -> None:
        for i, c in enumerate(self.chips):
            x = str(i)
            if self._rp_cur == x:
                st = "background:#1f6feb33;color:#58a6ff;border:1px solid #1f6feb;"
            elif x in self._rp_seen:
                st = "background:#23863633;color:#3fb950;border:1px solid #238636;"
            else:
                st = "background:#21262d;color:#8b949e;border:1px solid #30363d;"
            c.setStyleSheet(f"QLabel{{{st}border-radius:6px;padding:2px 8px;font-size:12px;}}")

    def _log(self, s: str) -> None:
        # File log first (post-mortem), panel second (live view).
        try:
            logging.getLogger("flash_device").info("%s", s)
        except Exception:
            pass
        # rawprogram 进度方块跟踪：解析 "programming rawprogramN.xml" 点亮对应方块
        m = re.search(r"programming (rawprogram[0-9]+)\.xml", s)
        if m:
            n = m.group(1).replace("rawprogram", "")
            if n not in self._rp_seen:
                self._rp_seen.append(n)
            self._rp_cur = n
            self._update_rp_chips()
        if "raw programming ok" in s.lower() or "programming ok" in s.lower():
            self._rp_cur = None
            self._update_rp_chips()
        # 当前文件进度条（来自 worker 内部状态）
        if self.worker is not None:
            try:
                self.bar_file.setValue(int(self.worker.state.pct_in_file))
            except Exception:
                pass
        # 报错行标红，其余原样；统一转义避免 HTML 注入
        esc = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        is_err = (
            bool(re.search(r"error|fail|exception|失败|错误", s, re.IGNORECASE))
            and "no error" not in s.lower()
        )
        if is_err:
            self.log.append(f'<span style="color:#f85149">{esc}</span>')
        else:
            self.log.append(esc)
        self.log.moveCursor(self.log.textCursor().MoveOperation.End)

    def _mem(self) -> list[str]:
        return ["--memory=" + self.mem.currentText()]

    def _do_printgpt(self) -> None:
        if not self._ready():
            return

        self._run(
            self._edl_cmd() + ["printgpt", f"--loader={self.loader.text().strip()}"] + self._mem()
        )

    def _do_gpt(self) -> None:
        if not self._ready():
            return
        d = QFileDialog.getExistingDirectory(self, "选择分区表保存目录", default_backup_root())
        if not d:
            d = new_backup_dir("gpt")
            self._log(f">>> 未选择目录，已新建 {d}")

        self._run(
            self._edl_cmd() + ["gpt", d, f"--loader={self.loader.text().strip()}"] + self._mem()
        )

    def _do_rl(self) -> None:
        if not self._ready():
            return
        d = QFileDialog.getExistingDirectory(self, "选择备份保存目录", default_backup_root())
        if not d:
            d = new_backup_dir("full")
            self._log(f">>> 未选择目录，已新建 {d}")

        r = QMessageBox.question(
            self,
            "备份全部分区",
            "将读取全部分区到:\n" + d + "\n\n耗时较长（UFS 可能 20-60 分钟），确定？",
        )
        if r == QMessageBox.StandardButton.Yes:
            self._run(
                self._edl_cmd()
                + ["rl", d, f"--loader={self.loader.text().strip()}", "--skip=userdata,metadata"]
                + self._mem()
            )

    def _do_r(self) -> None:
        if not self._ready():
            return
        p, ok = QInputDialog.getText(self, "读取分区", "分区名（如 boot / persist）：")
        if not ok or not p:
            return
        f, _ = QFileDialog.getSaveFileName(
            self, "保存为", os.path.join(default_backup_root(), f"{p}.img")
        )
        if not f:
            return

        self._run(
            self._edl_cmd() + ["r", p, f, f"--loader={self.loader.text().strip()}"] + self._mem()
        )

    def _confirm_backup_first(self, action: str) -> bool:
        r = QMessageBox.warning(
            self,
            "先备份",
            f"{action}前必须备份。\n\n已经用本工具备份过 GPT + 关键分区了吗？\n"
            "点 Yes 继续，点 No 先去点「备份全部分区」。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return r == QMessageBox.StandardButton.Yes

    def _do_qfil(self) -> None:
        if not self._ready():
            return
        d = self.fwdir.text().strip()
        raws, pats, msg = guards.scan_firmware_dir(d)
        if not raws:
            QMessageBox.warning(self, "缺少分区表", msg)
            return
        self.raws, self.pats = raws, pats
        # 标准包校验：error 直接拦（刷不了），warn 二次确认。
        rep = validate_fwdir(d, self.loader.text(), self.mem.currentText())
        self._log(">>> 包校验：" + rep.summary())
        for w in rep.warnings:
            self._log(f"    ⚠ {w}")
        if rep.level == "error":
            QMessageBox.critical(
                self,
                "包校验不通过，禁止刷入",
                "这个目录不是标准刷机包，刷了大概率失败：\n\n"
                + "\n".join(f"• {e}" for e in rep.errors)
                + "\n\n请重选目录（QFIL 包=rawprogram*.xml+镜像齐全）。",
            )
            return
        if rep.warnings:
            r0 = QMessageBox.warning(
                self,
                "包校验有警告",
                "包基本可用，但有以下可疑点：\n\n"
                + "\n".join(f"• {w}" for w in rep.warnings)
                + "\n\n还要继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if r0 != QMessageBox.StandardButton.Yes:
                return
        # 替代 boot：把选中的镜像复制覆盖包内 boot_a/boot_b（原始 boot 随时可从 KDZ 重解恢复）。
        replaced: list[str] = []
        sub = self.bootsub.text().strip()
        if sub and not os.path.isfile(sub):
            QMessageBox.warning(
                self,
                "替代 boot 不可用",
                f"替代 boot 文件不存在：\n{sub}\n\n请重新选择或点「清除」。",
            )
            return
        if sub:
            for root, _dirs, files in os.walk(d):
                for name in files:
                    if re.sub(r"^\d+\.", "", name.lower()) in (
                        "boot_a.img",
                        "boot_b.img",
                        "boot.img",
                    ):
                        try:
                            shutil.copyfile(sub, os.path.join(root, name))
                            replaced.append(name)
                        except OSError as e:
                            QMessageBox.warning(self, "替代 boot 失败", f"覆盖 {name} 出错：{e}")
                            return
            if not replaced:
                QMessageBox.warning(
                    self,
                    "替代 boot 未生效",
                    "包里没找到 boot_a/boot_b 镜像，替代未应用。\n\n"
                    "确认这是完整刷机包，或清空「替代 boot」后再试。",
                )
                return
        # 完整重刷：把目录下全部 rawprogram 一次性交给 edl（逗号分隔，覆盖 LUN0~6 所有分区）。
        # 按用户要求不做备份，故省略备份确认，仅保留最终危险操作确认。
        sub_note = ""
        if replaced:
            sub_note = (
                f"\n\n⚠ 替代 boot 已生效：包内 {('、'.join(sorted(replaced)))} 已被替换为\n{sub}"
            )
        r = QMessageBox.critical(
            self,
            "危险操作：整包刷入",
            guards.qfil_safety_summary(self.loader.text().strip(), d, raws[0])
            + "\n\n将刷入以下分区表（全部 rawprogram，覆盖全部分区）：\n"
            + "\n".join(os.path.basename(x) for x in raws)
            + sub_note,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        # 命令行组装与 HTTP 服务端共用同一份逻辑（backend/qfil.py）， multi-LUN 全量刷入。
        args, total, err = build_qfil_argv(
            d,
            self.loader.text(),
            self.mem.currentText(),
            edl_bin=self._prefs().get("edl_bin") or "",
        )
        if err:
            QMessageBox.warning(self, "无法启动", err)
            return
        summary = qfil_summary(d, self.loader.text(), len(raws), len(pats))
        self._log(">>> " + summary)

        self._run(args, total_files=total, status_text=summary)

    def _write_image(self, p: str, f: str) -> None:
        """单分区写入的公共尾部：边界保护 → 备份确认 → 危险确认 → 执行。"""
        g = guards.validate_single_write(p, f)
        if not g.ok:
            QMessageBox.warning(self, "被边界保护拦截", g.message)
            return
        if not self._confirm_backup_first(f"写入 {p}"):
            return
        r = QMessageBox.warning(
            self,
            "确认",
            f"将把\n{f}\n写入分区 {p}\n\n确定？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r == QMessageBox.StandardButton.Yes:
            # 按分区名记住本次镜像，下次写同一分区直接预选。
            self.settings.setValue(f"last_img/{p.lower()}", f)
            self._run(
                self._edl_cmd()
                + ["w", p, f, f"--loader={self.loader.text().strip()}"]
                + self._mem()
            )

    def _last_img(self, part: str) -> str:
        prev = (self.settings.value(f"last_img/{(part or '').strip().lower()}", "") or "").strip()
        return prev if prev and os.path.isfile(prev) else ""

    def _pick_image_for(self, part: str, allow_boot_fallback: bool = False) -> str:
        """给分区 part 选镜像：记忆优先（问一次"直接用？"，Yes 连文件对话框都不弹），
        其次固件目录里按名匹配，最后手动选。返回路径，取消返回 ""。"""
        last = self._last_img(part)
        if last:
            r = QMessageBox.question(
                self,
                "写入 " + part,
                f"上次写入 {part} 用的镜像：\n{last}\n\n直接用这个吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if r == QMessageBox.StandardButton.Yes:
                return last
        pre = self._fw_image(part)
        if not pre and allow_boot_fallback:
            pre = self._fw_image("boot")
        start = pre or self.fwdir.text().strip() or os.path.expanduser("~")
        f, _ = QFileDialog.getOpenFileName(
            self,
            f"选择 {part} 镜像",
            start,
            "镜像 (*.img *.mbn *.bin);;所有文件 (*)",
        )
        return f or ""

    def _do_w(self) -> None:
        if not self._ready():
            return
        p, ok = QInputDialog.getText(self, "刷入分区", "目标分区名（如 boot_a）：")
        if not ok or not p:
            return
        f = self._pick_image_for(p.strip())
        if not f:
            return
        self._write_image(p.strip(), f)

    def _fw_image(self, part: str) -> str:
        """在固件目录按分区名找镜像；兼容 boot.img 与 KDZ 解包产物 <lun>.<part>.img。

        用 os.walk 而非 glob：目录名可能含 [ ] 等字符，glob 会当字符类吞掉。
        """
        d = self.fwdir.text().strip()
        if not d:
            return ""
        for root, _dirs, files in os.walk(d):
            for name in files:
                if not name.lower().endswith(".img"):
                    continue
                if re.sub(r"^\d+\.", "", name.lower()) == f"{part.lower()}.img":
                    return os.path.join(root, name)
        return ""

    def _do_boot(self) -> None:
        """专用刷 boot：分区名默认 boot_a；记忆的镜像优先（问一次直接用）。"""
        if not self._ready():
            return
        p, ok = QInputDialog.getText(
            self, "刷 boot", "分区名（A/B 槽任一，当前槽通常为 boot_a）：", text="boot_a"
        )
        if not ok or not p:
            return
        p = p.strip()
        f = self._pick_image_for(p, allow_boot_fallback=True)
        if not f:
            return
        self._write_image(p, f)

    def _do_reset(self) -> None:
        if not self._ready():
            return

        self._run(self._edl_cmd() + ["reset", f"--loader={self.loader.text().strip()}"])


def main(argv: list[str] | None = None) -> int:
    import argparse

    from flash_device.utils.logging_setup import get_log_path, setup_logging

    ap = argparse.ArgumentParser(
        prog="flash-device", description="Qualcomm 9008 / EDL flashing tool"
    )
    ap.add_argument("--check-env", action="store_true", help="只做环境自检并退出（缺什么补什么）")
    ap.add_argument(
        "--self-test",
        action="store_true",
        help="构建 MainWindow 后直接退出（CI/冻包冒烟用，需显示或 QT_QPA_PLATFORM=offscreen）",
    )
    ap.add_argument(
        "--log-level", default=None, help="日志级别：DEBUG/INFO/WARNING（不给则用设置页的值）"
    )
    ns = ap.parse_args(argv)

    level = (ns.log_level or "").strip().upper() or None
    if not level:
        try:
            from flash_device import settings as S

            qs = QSettings(ORG, APP_NAME)
            level = S.load({k: qs.value("prefs/" + k) for k in S.DEFAULTS}).get("log_level")
        except Exception:
            level = None
    log_path = setup_logging(level or "INFO")
    checks = run_all_checks()
    logger.info("platform: %s", pf.system_info())
    logger.info("env check:\n%s", format_checks(checks))
    if ns.check_env:
        print(format_checks(checks))
        print(f"\nlog: {log_path}")
        return 0 if all(c.ok for c in checks) else 2

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG)
    w = MainWindow()
    w.logpath_label.setText(f"日志: {get_log_path()}")
    if ns.self_test:
        # Headless-friendly: window built, event loop pumped once, then quit.
        w.show()
        app.processEvents()
        # 门控一致性：有 9008 才解锁刷机按钮（CI 无设备时保持锁定）。
        assert w.b_qfil.isEnabled() == w.has_edl
        # 首选项对话框必须能构建、可接受（冻包缺 Qt 资源会直接炸）。
        from flash_device.settings_dialog import PrefsDialog

        dlg = PrefsDialog(w, w._prefs())
        assert dlg.values().get("server_port", 0) >= 1024
        dlg.accept()
        print(f"self-test-ok buttons_locked={not w.b_qfil.isEnabled()} log={log_path}")
        return 0
    w._log(f">>> 日志文件: {get_log_path()}")
    missing = [c for c in checks if not c.ok]
    if missing:
        w._log(
            ">>> 环境自检发现缺失:\n"
            + format_checks(checks)
            + "\n>>> 点右上「环境自检」看补齐方法。"
        )
    w.show()
    w.raise_()
    w.activateWindow()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
