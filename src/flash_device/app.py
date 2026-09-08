#!/usr/bin/env python3
"""FlashingDevice: open-source Qualcomm 9008 / EDL GUI (PyQt6).

- USB poll, 9008-only operation gate
- Loader + firmware pickers (no hardcoded user paths)
- Mandatory backup flow before destructive writes
- Real progress: total + per-file bars parsed from EDL stdout
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime

from PyQt6.QtCore import QSettings, Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
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
from flash_device.safety import guards
from flash_device.safety.backup import default_backup_root, new_backup_dir
from flash_device.utils import platform as pf
from flash_device.utils.envcheck import format_checks, run_all_checks
from flash_device.utils.logging_setup import get_logger
from flash_device.utils.usb import rank_device, scan_devices

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
            color:#c9d1d9; font-family:Menlo,Consolas,monospace; font-size:12px; }
QComboBox { background:#0d1117; border:1px solid #30363d; border-radius:8px; padding:7px 9px; }
QListWidget { background:#010409; border:1px solid #30363d; border-radius:8px; }
QProgressBar { background:#0d1117; border:1px solid #30363d; border-radius:11px;
               height:22px; text-align:center; color:#e6edf3; font-weight:700; }
QProgressBar::chunk { background:qlineargradient(x1:0,y1:0,x2:1,y2:0,
               stop:0 #1f6feb, stop:1 #39c5cf); border-radius:11px; }
QScrollBar:vertical { background:#0d1117; width:12px; }
QScrollBar::handle:vertical { background:#30363d; border-radius:6px; min-height:30px; }
"""


class MainWindow(QMainWindow):
    OP_BUTTONS = ("b_print", "b_gpt", "b_rl", "b_r", "b_qfil", "b_w", "b_reset")

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
        self.setStyleSheet(STYLE)
        self.settings = QSettings(ORG, APP_NAME)
        self.worker: JobWorker | None = None
        self.seen_9008 = False
        self.has_edl = False
        self.raws: list[str] = []
        self.pats: list[str] = []
        self._build_ui()
        self._restore()
        self._active = None
        self._rp_seen: list[str] = []
        self._rp_cur: str | None = None
        self._render_history()
        self._last_prog_ts: float | None = None
        self._server = None
        self._server_thread = None
        self._server_port = 8899
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(1000)
        self.beat = QTimer(self)
        self.beat.timeout.connect(self._heartbeat)
        self.beat.start(1000)
        self._poll()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        # 整个界面包进滚动区：窗口高度受限不超屏，内容再长也能滚动查看。
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        container = QWidget()
        v = QVBoxLayout(container)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        gb = QGroupBox("设备状态")
        gl = QVBoxLayout(gb)
        top = QHBoxLayout()
        self.dot = QLabel("●")
        self.dot.setFont(QFont("", 26))
        self.status = QLabel("正在检测…")
        self.status.setFont(QFont("", 15, QFont.Weight.Bold))
        top.addWidget(self.dot)
        top.addWidget(self.status, 1)
        self.mem = QComboBox()
        self.mem.addItems(["ufs", "emmc", "nand", "spinor"])
        top.addWidget(QLabel("存储类型:"))
        top.addWidget(self.mem)
        gl.addLayout(top)
        self.detail = QLabel("—")
        self.detail.setStyleSheet("color:#8b8b9a; font-family:Menlo,monospace; font-size:12px;")
        gl.addWidget(self.detail)
        self.hint = QLabel("—")
        self.hint.setStyleSheet("color:#9a9aac; font-size:12px; padding-top:2px;")
        self.hint.setWordWrap(True)
        gl.addWidget(self.hint)
        v.addWidget(gb)

        gb2 = QGroupBox("Firehose 编程器 (Loader，自备，不进仓)")
        g2 = QHBoxLayout(gb2)
        self.loader = QLineEdit()
        self.loader.setPlaceholderText("prog_firehose_*.elf / *.mbn")
        b2 = QPushButton("浏览…")
        b2.clicked.connect(self._pick_loader)
        g2.addWidget(self.loader, 1)
        g2.addWidget(b2)
        v.addWidget(gb2)

        gb3 = QGroupBox("固件目录（本地，不进仓）")
        g3 = QVBoxLayout(gb3)
        r = QHBoxLayout()
        self.fwdir = QLineEdit()
        self.fwdir.setPlaceholderText("包含 rawprogram*.xml / patch*.xml / *.img 的目录")
        b3 = QPushButton("浏览…")
        b3.clicked.connect(self._pick_fw)
        r.addWidget(self.fwdir, 1)
        r.addWidget(b3)
        g3.addLayout(r)
        self.fwinfos = QLabel("—")
        self.fwinfos.setStyleSheet("color:#8b8b9a; font-size:12px;")
        self.fwinfos.setWordWrap(True)
        self.fwinfos.setTextInteractionFlags(
            self.fwinfos.textInteractionFlags() | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        g3.addWidget(self.fwinfos)
        v.addWidget(gb3)

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
        self.b_reset = QPushButton("重启设备")
        self.b_stop = QPushButton("停止")
        self.b_stop.setEnabled(False)
        row2.addWidget(self.b_qfil)
        row2.addWidget(self.b_w)
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
        self.b_reset.clicked.connect(self._do_reset)
        self.b_stop.clicked.connect(self._stop)
        v.addWidget(gb4)

        gb_h = QGroupBox("刷机历史（点“恢复选中项”可一键回填当时选择，免得重选）")
        gh = QVBoxLayout(gb_h)
        hr = QHBoxLayout()
        self.hist = QListWidget()
        self.hist.setStyleSheet(
            "background:#131316; border:1px solid #30303a; border-radius:6px;"
            "color:#c8c8d0; font-size:12px;"
        )
        self.hist.setMaximumHeight(120)
        hr.addWidget(self.hist, 1)
        hbtn = QVBoxLayout()
        self.b_hist_apply = QPushButton("恢复选中项")
        self.b_hist_clear = QPushButton("清空历史")
        hbtn.addWidget(self.b_hist_apply)
        hbtn.addWidget(self.b_hist_clear)
        hbtn.addStretch(1)
        hr.addLayout(hbtn)
        gh.addLayout(hr)
        self.b_hist_apply.clicked.connect(self._apply_history)
        self.b_hist_clear.clicked.connect(self._clear_history)
        v.addWidget(gb_h)

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
        self.b_server = QPushButton("启动网页控制台")
        self.b_server.clicked.connect(self._toggle_server)
        self.heartbeat = QLabel("空闲")
        self.heartbeat.setStyleSheet("color:#8b949e; font-size:12px;")
        logrow.addWidget(self.logpath_label, 1)
        logrow.addWidget(self.heartbeat)
        logrow.addWidget(b_env)
        logrow.addWidget(self.b_server)
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
        mem = self.settings.value("mem", "ufs")
        idx = self.mem.findText(mem)
        if idx >= 0:
            self.mem.setCurrentIndex(idx)
        if self.fwdir.text().strip():
            self._scan_fw()

    def _save(self) -> None:
        self.settings.setValue("loader", self.loader.text())
        self.settings.setValue("fwdir", self.fwdir.text())
        self.settings.setValue("mem", self.mem.currentText())

    # ---------------- flash history ----------------
    def _history_path(self) -> str:
        from flash_device.utils.logging_setup import get_log_path

        p = get_log_path() or os.path.expanduser("~/flash_device.log")
        return os.path.join(os.path.dirname(p), "flash_history.json")

    def _load_history(self) -> list[dict]:
        try:
            with open(self._history_path(), encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def _add_history(self, entry: dict) -> None:
        hist = self._load_history()
        hist.insert(0, entry)
        hist = hist[:50]
        try:
            os.makedirs(os.path.dirname(self._history_path()), exist_ok=True)
            with open(self._history_path(), "w", encoding="utf-8") as f:
                json.dump(hist, f, indent=2, ensure_ascii=False)
        except Exception:
            pass
        self._render_history()

    def _render_history(self) -> None:
        self.hist.clear()
        for e in self._load_history():
            op = e.get("op", "?")
            fw = os.path.basename(e.get("fwdir", "") or e.get("target", "") or "?")
            res = e.get("result", "")
            ts = e.get("ts", "")
            label = f"{ts}  ·  {op}  ·  {fw}  ·  {res}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, e)
            self.hist.addItem(item)

    def _apply_history(self) -> None:
        it = self.hist.currentItem()
        if not it:
            return
        e = it.data(Qt.ItemDataRole.UserRole)
        if not e:
            return
        fw = e.get("fwdir", "")
        if fw:
            self.fwdir.setText(fw)
            self._scan_fw()
        if e.get("loader"):
            self.loader.setText(e["loader"])
        mem = e.get("mem")
        if mem:
            idx = self.mem.findText(mem)
            if idx >= 0:
                self.mem.setCurrentIndex(idx)
        self._save()

    def _clear_history(self) -> None:
        try:
            os.remove(self._history_path())
        except OSError:
            pass
        self._render_history()

    # ---------------- pickers ----------------
    def _pick_loader(self) -> None:
        p, _ = QFileDialog.getOpenFileName(
            self,
            "选择 Firehose 编程器",
            os.path.expanduser("~"),
            "Firehose (*.elf *.mbn *.bin);;所有文件 (*)",
        )
        if p:
            self.loader.setText(p)
            self._save()

    def _pick_fw(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "选择固件目录", os.path.expanduser("~"))
        if p:
            self.fwdir.setText(p)
            self._scan_fw()
            self._save()

    def _scan_fw(self) -> None:
        d = self.fwdir.text().strip()
        raws, pats, msg = guards.scan_firmware_dir(d)
        self.raws, self.pats = raws, pats
        extra = ""
        if raws and not self.loader.text().strip():
            cands = glob.glob(
                os.path.join(d, "**", "prog_firehose*.elf"), recursive=True
            ) + glob.glob(os.path.join(d, "**", "prog_firehose*.mbn"), recursive=True)
            if cands:
                self.loader.setText(cands[0])
                extra = f"\n（已自动选中编程器: {os.path.basename(cands[0])}）"
        # 把「选到的东西」详细列在界面上
        info = [
            f"目录: {d}",
            f"编程器: {self.loader.text().strip() or '（未选，需先选 Firehose）'}",
        ]
        if raws:
            info.append(f"分区表 rawprogram（{len(raws)} 个，整包刷入将全部覆盖）:")
            for x in raws:
                info.append(f"  • {os.path.basename(x)}")
        else:
            info.append(msg)
        if pats:
            info.append(f"补丁 patch（{len(pats)} 个）:")
            for x in pats:
                info.append(f"  • {os.path.basename(x)}")
        self.fwinfos.setText("\n".join(info) + extra)

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

    def _poll(self) -> None:
        devices = scan_devices()
        ordered = sorted(devices, key=rank_device)
        edl = [d for d in devices if d[3] in guards.EDL_PIDS]
        self.has_edl = bool(edl)
        self._update_buttons()
        if edl:
            b, p, vid, pid, nm = edl[0]
            self._set("#3ddc84", "✅  9008 已连接 —— 可以操作")
            self.detail.setText(f"{vid:04x}:{pid:04x}    Bus {b}  Port {p}    {nm}")
            self.hint.setText("已强制要求： destructive 操作前先备份，整包/单写需二次确认。")
            if not self.seen_9008:
                self.seen_9008 = True
                pf.notify("9008 已连接", f"检测到 {vid:04x}:{pid:04x}")
                self._log(f">>> 检测到 9008 设备 {vid:04x}:{pid:04x}")
            return
        self.seen_9008 = False
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
        edl_py = pf.resolve_edl_bin()
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
        gl = guards.validate_loader(self.loader.text())
        if not gl.ok:
            QMessageBox.warning(self, "编程器问题", gl.message)
            return False
        return True

    def _run(
        self,
        args: list[str],
        cwd: str | None = None,
        total_files: int = 0,
        status_text: str = "",
    ) -> None:
        logger.info("run: %s (cwd=%s)", " ".join(args), cwd)
        self._log("$ " + " ".join(args))
        self.bar_total.setValue(0)
        self.bar_file.setValue(0)
        self._rp_seen = []
        self._rp_cur = None
        self._update_rp_chips()
        self.op_label.setText(status_text or "")
        self.worker = JobWorker(status_text or "刷机任务", args, total_files=total_files, cwd=cwd)
        self.worker.line.connect(self._log)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._finished)
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
            f"正在刷：{what}    总进度 {overall:.1f}%    当前文件 {file_pct:.0f}%"
        )

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
        else:
            self.heartbeat.setText("空闲")
            self.heartbeat.setStyleSheet("color:#8b949e; font-size:12px;")

    def _toggle_server(self) -> None:
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
            self._server = None
            self.b_server.setText("启动网页控制台")
            self._log(">>> 网页控制台已停止")
            return
        try:
            from http.server import ThreadingHTTPServer

            from flash_device.server import Handler

            self._server = ThreadingHTTPServer(("127.0.0.1", self._server_port), Handler)
            self._server_thread = threading.Thread(target=self._server.serve_forever, daemon=True)
            self._server_thread.start()
        except OSError as e:
            QMessageBox.warning(self, "启动失败", f"端口 {self._server_port} 被占用：{e}")
            self._server = None
            return
        url = f"http://127.0.0.1:{self._server_port}"
        self.b_server.setText("停止网页控制台")
        self._log(f">>> 网页控制台已启动：{url}（桌面 GUI 与网页看的是同一个任务）")
        QDesktopServices.openUrl(QUrl(url))

    def _stop(self) -> None:
        if self.worker:
            self.worker.kill()
            self._log(">>> 已中断")

    def _finished(self, code: int) -> None:
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
        self._log(f"=== 结束，退出码 {code} ===")
        pf.notify("操作完成", f"退出码 {code}")
        if self._active:
            self._active["result"] = result
            self._active["ts"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
            self._add_history(self._active)
            self._active = None

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save()
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
        self._active = {
            "op": "打印分区表",
            "fwdir": self.fwdir.text().strip(),
            "loader": self.loader.text().strip(),
            "mem": self.mem.currentText(),
        }
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
        self._active = {
            "op": "备份分区表",
            "fwdir": self.fwdir.text().strip(),
            "loader": self.loader.text().strip(),
            "mem": self.mem.currentText(),
            "target": d,
        }
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
        self._active = {
            "op": "备份全部分区",
            "fwdir": self.fwdir.text().strip(),
            "loader": self.loader.text().strip(),
            "mem": self.mem.currentText(),
            "target": d,
        }
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
        self._active = {
            "op": f"读取分区 {p}",
            "fwdir": self.fwdir.text().strip(),
            "loader": self.loader.text().strip(),
            "mem": self.mem.currentText(),
            "target": f,
        }
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
        # 完整重刷：把目录下全部 rawprogram 一次性交给 edl（逗号分隔，覆盖 LUN0~6 所有分区）。
        # 按用户要求不做备份，故省略备份确认，仅保留最终危险操作确认。
        r = QMessageBox.critical(
            self,
            "危险操作：整包刷入",
            guards.qfil_safety_summary(self.loader.text().strip(), d, raws[0])
            + "\n\n将刷入以下分区表（全部 rawprogram，覆盖全部分区）：\n"
            + "\n".join(os.path.basename(x) for x in raws),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        # 命令行组装与 HTTP 服务端共用同一份逻辑（backend/qfil.py）， multi-LUN 全量刷入。
        args, total, err = build_qfil_argv(d, self.loader.text(), self.mem.currentText())
        if err:
            QMessageBox.warning(self, "无法启动", err)
            return
        summary = qfil_summary(d, self.loader.text(), len(raws), len(pats))
        self._log(">>> " + summary)
        self._active = {
            "op": "整包刷入(QFIL)",
            "fwdir": d,
            "loader": self.loader.text().strip(),
            "mem": self.mem.currentText(),
            "rawprogram": len(raws),
        }
        self._run(args, total_files=total, status_text=summary)

    def _do_w(self) -> None:
        if not self._ready():
            return
        p, ok = QInputDialog.getText(self, "刷入分区", "目标分区名（如 boot_a）：")
        if not ok or not p:
            return
        f, _ = QFileDialog.getOpenFileName(
            self,
            "选择镜像文件",
            self.fwdir.text().strip() or os.path.expanduser("~"),
            "镜像 (*.img *.elf *.mbn *.bin);;所有文件 (*)",
        )
        if not f:
            return
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
            self._active = {
                "op": f"刷入单分区 {p}",
                "fwdir": self.fwdir.text().strip(),
                "loader": self.loader.text().strip(),
                "mem": self.mem.currentText(),
                "target": f,
            }
            self._run(
                self._edl_cmd()
                + ["w", p, f, f"--loader={self.loader.text().strip()}"]
                + self._mem()
            )

    def _do_reset(self) -> None:
        if not self._ready():
            return
        self._active = {
            "op": "重启设备",
            "fwdir": self.fwdir.text().strip(),
            "loader": self.loader.text().strip(),
            "mem": self.mem.currentText(),
        }
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
    ap.add_argument("--log-level", default="INFO", help="日志级别：DEBUG/INFO/WARNING")
    ns = ap.parse_args(argv)

    log_path = setup_logging(ns.log_level)
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
