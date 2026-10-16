"""Select both synchronized camera pairs before changing a comparison."""
from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QFileDialog, QGridLayout,
                            QGroupBox, QLabel, QLineEdit, QPushButton, QVBoxLayout)
from Src.paths import DB_DIR


def validate_comparison_paths(paths):
    """Return an actionable error, or an empty string for two distinct pairs."""
    if len(paths) != 4 or not all(paths):
        return "請為上、下兩組各選一支正面 CF 和一支側面 CS 影片，共四支。"
    files = [Path(path).resolve() for path in paths]
    if not all(path.is_file() for path in files):
        return "有影片不存在，請重新選擇檔案。"
    if len(set(files)) != 4:
        return "四個位置不能重複使用同一支影片；上下兩組請選不同次投球。"
    for index in (0, 2):
        front, side = files[index:index + 2]
        group = "上組 A" if index == 0 else "下組 B"
        if front.name.upper().startswith("CS") or side.name.upper().startswith("CF"):
            return f"{group}的視角放反了：正面請選 CF，側面請選 CS。"
        if front.parent != side.parent:
            return f"{group}請選同一投球資料夾中的正面、側面影片。"
        if (front.name.upper().startswith("CF") and side.name.upper().startswith("CS")
                and front.stem[2:].casefold() != side.stem[2:].casefold()):
            return f"{group}的 CF／CS 檔名不配對，請確認為同一次投球。"
    return ""


class CompareVideoDialog(QDialog):
    def __init__(self, is_processed=False, paths=None, parent=None):
        super().__init__(parent)
        self.setFont(QFont("Microsoft JhengHei", 10))
        self.setWindowTitle("載入兩組影片比較")
        self.resize(820, 410)
        layout = QVBoxLayout(self)
        hint = QLabel("上組 A、下組 B 請選不同次投球（可為不同投手）。\n"
                      "每組必須是同次投球、同步拍攝的一支正面 CF 與一支側面 CS；畫面左正面、右側面。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        location = QLabel(f"影片位置：{DB_DIR / 'Record'}\n"
                          "進入日期／投手與球次資料夾，選擇 CF_….mp4、CS_….mp4。")
        location.setWordWrap(True)
        location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(location)
        mode = "3D" if is_processed else "2D"
        note = QLabel(f"載入既有 {mode} 分析結果：請將對應的骨架、關鍵幀 JSON 保留在影片同一資料夾。\n"
                      "尚未分析的影片，請先到「2D」或「3D」分頁完成分析並儲存。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.fields = []
        paths = paths or [None] * 4
        for group_index, title in enumerate(("上組 A — 第一組投球", "下組 B — 另一組投球")):
            group = QGroupBox(title)
            grid = QGridLayout(group)
            for role_index, role in enumerate(("左：正面 CF", "右：側面 CS")):
                index = group_index * 2 + role_index
                field = QLineEdit(paths[index] or "")
                field.setPlaceholderText("選擇此組的正面影片 CF_….mp4" if role_index == 0
                                         else "選擇同一次投球的側面影片 CS_….mp4")
                field.setReadOnly(True)
                field.setToolTip(field.text())
                button = QPushButton("選擇影片…")
                button.clicked.connect(lambda checked=False, i=index: self.browse(i))
                grid.addWidget(QLabel(role), role_index, 0)
                grid.addWidget(field, role_index, 1)
                grid.addWidget(button, role_index, 2)
                self.fields.append(field)
            layout.addWidget(group)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #b42318;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("載入比較")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def paths(self):
        return tuple(field.text() for field in self.fields)

    def browse(self, index):
        group = "上組 A" if index < 2 else "下組 B"
        role = "正面 CF" if index % 2 == 0 else "側面 CS"
        current = self.fields[index].text() or self.fields[index ^ 1].text()
        directory = str(Path(current).parent) if current else str(DB_DIR / "Record")
        path, _ = QFileDialog.getOpenFileName(
            self, f"{group}：選擇{role}影片", directory,
            "影片 (*.mp4 *.avi *.mov *.mkv);;所有檔案 (*)")
        if not path:
            return
        self.fields[index].setText(path)
        self.fields[index].setToolTip(path)
        selected = Path(path)
        prefix = "CF" if index % 2 == 0 else "CS"
        # Update the partner even when replacing a previously selected pitch.
        # Never keep the other camera from the old pitch by accident.
        if selected.name.upper().startswith(prefix):
            self.fields[index ^ 1].clear()
            self.fields[index ^ 1].setToolTip("")
            partner = selected.with_name(("CS" if prefix == "CF" else "CF") + selected.name[2:])
            if partner.is_file():
                self.fields[index ^ 1].setText(str(partner))
                self.fields[index ^ 1].setToolTip(str(partner))
        self.error.clear()

    def accept(self):
        error = validate_comparison_paths(self.paths())
        if error:
            self.error.setText(error)
            return
        super().accept()
