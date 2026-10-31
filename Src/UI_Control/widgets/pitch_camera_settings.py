"""User-supplied physical camera assignments for each pitching hand."""
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
    QStackedWidget, QSizePolicy)

from Src.cameras.pitch_profiles import (get_profile, calibration_path, save_profile,
                                       save_camera_assignments)


class PitchCameraAssignmentsPanel(QWidget):
    """Switch camera pairs inside two fixed form rows."""
    saved = pyqtSignal()

    def __init__(self, can_save, parent=None):
        super().__init__(parent)
        self.can_save = can_save
        form = QFormLayout(self)
        self.form = form
        form.setContentsMargins(0, 0, 0, 0)
        self.fields = {}
        self.stacks = {}
        for role, view in (("front", "正面 CF"), ("side", "側面 CS")):
            stack = QStackedWidget()
            stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for hand in ("left", "right"):
                edit = QLineEdit()
                edit.setObjectName(f"{hand}_{role}_serial")
                edit.setPlaceholderText("實體相機序號")
                edit.textChanged.connect(self._mark_unsaved)
                self.fields[hand, role] = edit
                stack.addWidget(edit)
            form.addRow(view, stack)
            self.stacks[role] = stack
        note = QLabel("尚未設定的手別可留空；同一手別的正面與側面序號須不同。\n"
                      "儲存前請先關閉相機，下次開啟時套用。")
        note.setWordWrap(True)
        form.addRow(note)
        self.save_button = QPushButton("儲存相機配置")
        self.save_button.clicked.connect(self.save)
        form.addRow(self.save_button)
        self.status = QLabel()
        self.status.setWordWrap(True)
        form.addRow(self.status)
        self.reload()
        self.set_hand("right")

    def set_hand(self, hand):
        """Keep edits for both hands, showing only the selected CF/CS pair."""
        for role, stack in self.stacks.items():
            stack.setCurrentWidget(self.fields[hand, role])

    def _mark_unsaved(self):
        self.status.setText("尚未儲存")

    def reload(self):
        try:
            profiles = {hand: get_profile(hand) or {} for hand in ("left", "right")}
        except (ValueError, OSError) as exc:
            self.status.setText(f"無法讀取相機配置：{exc}")
            return
        for (hand, role), edit in self.fields.items():
            edit.setText(str(profiles[hand].get(role, "")))
        self.status.clear()

    def save(self):
        if not self.can_save():
            return
        assignments = {
            hand: (self.fields[hand, "front"].text(), self.fields[hand, "side"].text())
            for hand in ("left", "right")
        }
        try:
            save_camera_assignments(assignments)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "設定未儲存", str(exc))
            return
        self.reload()
        self.status.setText("相機配置已儲存，下次開啟相機時套用。")
        self.saved.emit()


class PitchCameraSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("左右投相機設定")
        self.resize(680, 400)
        layout = QVBoxLayout(self)
        note = QLabel("依現場實體序號分別指定正面 CF、側面 CS。左右投可共用同一台正面相機。\n"
                      "每組相機都要各自計算外參；選擇該組的 stereo.json 或 fundamental.json。")
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        self.fields = {}
        for hand, label in (("right", "右投"), ("left", "左投")):
            page = QWidget()
            form = QFormLayout(page)
            profile = get_profile(hand) or {}
            front, side = QLineEdit(profile.get("front", "")), QLineEdit(profile.get("side", ""))
            front.setPlaceholderText("正面相機實際序號")
            side.setPlaceholderText("側面相機實際序號")
            form.addRow("正面 CF 序號", front)
            form.addRow("側面 CS 序號", side)
            paths = {}
            for method, title in (("pnp", "PnP／木框外參"), ("8point", "8Points／雙桿外參"), ("legacy", "F 矩陣外參")):
                row = QHBoxLayout()
                path = QLineEdit(str(calibration_path(hand, method)))
                browse = QPushButton("選擇")
                browse.clicked.connect(lambda checked=False, edit=path: self.browse(edit))
                row.addWidget(path, 1)
                row.addWidget(browse)
                form.addRow(title, row)
                paths[method] = path
            self.fields[hand] = (front, side, paths)
            tabs.addTab(page, label)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def browse(self, edit):
        path, _ = QFileDialog.getOpenFileName(self, "選擇校正檔", edit.text(), "JSON (*.json)")
        if path:
            edit.setText(path)

    def save(self):
        entries = []
        for hand, (front, side, paths) in self.fields.items():
            a, b = front.text().strip(), side.text().strip()
            if not a and not b:
                continue
            if not a or not b or a == b:
                QMessageBox.warning(self, "設定未儲存", "每組都要填入不同的正面 CF／側面 CS 序號；尚未使用的一組可留空。")
                return
            entries.append((hand, a, b, {k: v.text().strip() for k, v in paths.items()}))
        try:
            for entry in entries:
                save_profile(*entry)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "設定未儲存", str(exc))
            return
        self.accept()
