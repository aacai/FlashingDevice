"""首选项对话框（Qt 层很薄：只负责展示/收集，读写规则全在 settings.py）。"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from flash_device import settings as S


class PrefsDialog(QDialog):
    def __init__(self, parent, current: dict):
        super().__init__(parent)
        self.setWindowTitle("首选项")
        self.setMinimumWidth(520)
        v = QVBoxLayout(self)
        form = QFormLayout()
        v.addLayout(form)

        self.autostart = QCheckBox("GUI 启动时自动拉起状态同步接口")
        self.autostart.setChecked(bool(current.get("server_autostart", True)))
        form.addRow("状态接口", self.autostart)

        self.port = QSpinBox()
        self.port.setRange(S.MIN_PORT, S.MAX_PORT)
        self.port.setValue(int(current.get("server_port", 8899)))
        form.addRow("接口端口", self.port)

        self.edl_bin = QLineEdit(str(current.get("edl_bin") or ""))
        self.edl_bin.setPlaceholderText("空=自动（冻包内置 → 仓库 submodule → PATH）")
        b_edl = QPushButton("浏览…")
        b_edl.clicked.connect(lambda: self._pick(self.edl_bin, "选择 EDL 引擎"))
        row_edl = QHBoxLayout()
        row_edl.addWidget(self.edl_bin, 1)
        row_edl.addWidget(b_edl)
        form.addRow("EDL 引擎", row_edl)

        self.kdz_tool = QLineEdit(str(current.get("kdz_tool") or ""))
        self.kdz_tool.setPlaceholderText("空=自动（环境变量 → ~/.flash-device/bin → PATH）")
        b_kdz = QPushButton("浏览…")
        b_kdz.clicked.connect(lambda: self._pick(self.kdz_tool, "选择 kdz-tool"))
        row_kdz = QHBoxLayout()
        row_kdz.addWidget(self.kdz_tool, 1)
        row_kdz.addWidget(b_kdz)
        form.addRow("KDZ 解包器", row_kdz)

        self.serial = QLineEdit(str(current.get("expected_serial") or ""))
        self.serial.setPlaceholderText("空=不限制；填了就对不上序列号不让刷")
        form.addRow("期望序列号", self.serial)

        self.level = QComboBox()
        self.level.addItems(list(S.LOG_LEVELS))
        idx = self.level.findText(str(current.get("log_level") or "INFO"))
        self.level.setCurrentIndex(max(idx, 0))
        form.addRow("日志级别", self.level)

        note = QLabel("日志级别下次启动生效；其余保存即生效。")
        note.setStyleSheet("color:#8b949e; font-size:12px;")
        v.addWidget(note)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.b_default = QPushButton("恢复默认")
        self.b_default.clicked.connect(self._restore_defaults)
        btns.addButton(self.b_default, QDialogButtonBox.ButtonRole.ResetRole)
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _pick(self, edit: QLineEdit, title: str) -> None:
        p, _ = QFileDialog.getOpenFileName(self, title, edit.text().strip() or "~", "所有文件 (*)")
        if p:
            edit.setText(p)

    def _restore_defaults(self) -> None:
        d = dict(S.DEFAULTS)
        self.autostart.setChecked(d["server_autostart"])
        self.port.setValue(d["server_port"])
        self.edl_bin.setText("")
        self.kdz_tool.setText("")
        self.serial.setText("")
        self.level.setCurrentIndex(self.level.findText(d["log_level"]))

    def _on_accept(self) -> None:
        import os

        for label, path in (
            ("EDL 引擎", self.edl_bin.text().strip()),
            ("KDZ 解包器", self.kdz_tool.text().strip()),
        ):
            if path and not os.path.exists(path):
                QMessageBox.warning(self, "路径不存在", f"{label}：\n{path}\n\n清空则为自动。")
                return
        self.accept()

    def values(self) -> dict:
        return S.dump(
            {
                "server_autostart": self.autostart.isChecked(),
                "server_port": self.port.value(),
                "edl_bin": self.edl_bin.text(),
                "kdz_tool": self.kdz_tool.text(),
                "expected_serial": self.serial.text(),
                "log_level": self.level.currentText(),
            }
        )
