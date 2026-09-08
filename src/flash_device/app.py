#!/usr/bin/env python3
"""FlashingDevice: open-source Qualcomm 9008 / EDL GUI (PyQt6).

- USB poll, 9008-only operation gate
- Loader + firmware pickers (no hardcoded user paths)
- Mandatory backup flow before destructive writes
- Real progress: total + per-file bars parsed from EDL stdout
"""

from __future__ import annotations

import glob
import logging
import os
import sys

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
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from flash_device.backend.edl_runner import CmdWorker
from flash_device.backend.progress import count_program_entries
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
QWidget { background:#1c1c20; color:#e8e8ee; font-size:13px; }
QMainWindow { background:#1c1c20; }
QGroupBox { border:1px solid #35353f; border-radius:8px; margin-top:16px;
            padding:14px 10px 10px 10px; font-weight:600; color:#a8a8bb; }
QGroupBox::title { subcontrol-origin:margin; left:12px; padding:0 6px; }
QLineEdit { background:#26262d; border:1px solid #35353f; border-radius:6px;
            padding:7px 9px; color:#e8e8ee; selection-background-color:#3a6ea5; }
QPushButton { background:#32323c; border:1px solid #44444f; border-radius:6px;
              padding:8px 14px; color:#e8e8ee; }
QPushButton:hover { background:#3d3d4a; border-color:#55555f; }
QPushButton:pressed { background:#2a2a33; }
QPushButton:disabled { background:#232329; color:#5c5c68; border-color:#2e2e36; }
QPushButton#danger { background:#7d2531; border-color:#9c303f; font-weight:600; }
QPushButton#danger:hover { background:#932c3a; }
QPushButton#primary { background:#25567d; border-color:#2f6a99; font-weight:600; }
QPushButton#primary:hover { background:#2c6494; }
QTextEdit { background:#131316; border:1px solid #30303a; border-radius:6px;
            color:#c8e6c8; font-family:Menlo,monospace; font-size:12px; }
QComboBox { background:#26262d; border:1px solid #35353f; border-radius:6px; padding:6px 8px; }
QProgressBar { background:#26262d; border:1px solid #35353f; border-radius:4px;
               height:14px; text-align:center; }
QProgressBar::chunk { background:#3a8fd0; border-radius:4px; }
"""


class MainWindow(QMainWindow):
    OP_BUTTONS = ("b_print", "b_gpt", "b_rl", "b_r", "b_qfil", "b_w", "b_reset")

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("FlashingDevice — Qualcomm 9008 / EDL")
        self.resize(1020, 780)
        self.setStyleSheet(STYLE)
        self.settings = QSettings(ORG, APP_NAME)
        self.worker: CmdWorker | None = None
        self.seen_9008 = False
        self.has_edl = False
        self.raws: list[str] = []
        self.pats: list[str] = []
        self._build_ui()
        self._restore()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(1000)
        self._poll()

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        v = QVBoxLayout(root)
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

        gb5 = QGroupBox("日志与进度（每次运行都写入文件，方便事后复盘）")
        g5 = QVBoxLayout(gb5)
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
        logrow.addWidget(self.logpath_label, 1)
        logrow.addWidget(b_env)
        logrow.addWidget(b_open_log)
        g5.addLayout(logrow)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
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
        v.addWidget(gb5, 1)

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
                extra = f"    已自动选中编程器: {os.path.basename(cands[0])}"
        self.fwinfos.setText(msg + extra)

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

    def _run(self, args: list[str], cwd: str | None = None, total_files: int = 0) -> None:
        logger.info("run: %s (cwd=%s)", " ".join(args), cwd)
        self._log("$ " + " ".join(args))
        self.bar_total.setValue(0)
        self.bar_file.setValue(0)
        self.op_label.setText("")
        self.worker = CmdWorker(args, cwd, total_files=total_files)
        self.worker.line.connect(self._log)
        self.worker.progress.connect(self._on_progress)
        self.worker.done.connect(self._finished)
        self._update_buttons()
        self.worker.start()

    def _on_progress(self, overall: float, op: str, cur: str) -> None:
        self.bar_total.setValue(int(max(0, min(100, overall))))
        # per-file pct is embedded in worker state; overall bar is primary,
        # file bar mirrors fractional part for qfil sessions
        self.op_label.setText(f"{op}  {cur}".strip())

    def _stop(self) -> None:
        if self.worker:
            self.worker.kill()
            self._log(">>> 已中断")

    def _finished(self, code: int) -> None:
        if code == 0:
            self.bar_total.setValue(100)
            self.bar_file.setValue(100)
        self._update_buttons()
        logger.info("done: exit=%s", code)
        self._log(f"=== 结束，退出码 {code} ===")
        pf.notify("操作完成", f"退出码 {code}")

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

    def _log(self, s: str) -> None:
        # File log first (post-mortem), panel second (live view).
        try:
            logging.getLogger("flash_device").info("%s", s)
        except Exception:
            pass
        # Per-file bar: try to mirror latest pct from worker state
        if self.worker is not None:
            try:
                self.bar_file.setValue(int(self.worker.state.pct_in_file))
            except Exception:
                pass
        self.log.append(s)
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
        if not self._confirm_backup_first("整包刷入"):
            return
        r = QMessageBox.critical(
            self,
            "危险操作",
            guards.qfil_safety_summary(self.loader.text().strip(), d, raws[0]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if r != QMessageBox.StandardButton.Yes:
            return
        total = 0
        try:
            with open(raws[0], encoding="utf-8", errors="replace") as f:
                total = count_program_entries(f.read())
        except OSError:
            total = 0
        args = self._edl_cmd() + ["qfil", raws[0]]
        if pats:
            args.append(pats[0])
        args += [d, f"--loader={self.loader.text().strip()}"] + self._mem()
        self._run(args, total_files=total)

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
            self._run(
                self._edl_cmd()
                + ["w", p, f, f"--loader={self.loader.text().strip()}"]
                + self._mem()
            )

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
