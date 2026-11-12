"""Launch with python -m Src.UI_Control.main_flir_preview."""
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

plugin_path = Path(sys.prefix) / "Library/plugins/platforms"
if plugin_path.is_dir():
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(plugin_path))

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout,
                             QCheckBox, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget)
from Src.cameras.flir_preview import PreviewService


class CameraTile(QFrame):
    acquisition_changed = pyqtSignal(bool)
    preview_changed = pyqtSignal(bool)

    def __init__(self, serial):
        super().__init__()
        self.setObjectName("tile")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("FLIR  ·  SN %s" % serial))
        switches = QHBoxLayout()
        self.acquisition = QCheckBox("接收資料")
        self.preview = QCheckBox("顯示畫面")
        for control in (self.acquisition, self.preview):
            control.setChecked(True)
            switches.addWidget(control)
        self.acquisition.setToolTip("停止該台相機取像；重新啟用會重新計算幀數與 FPS")
        self.preview.setToolTip("只關閉預覽轉色與繪圖，接收及 FPS 統計繼續")
        self.acquisition.toggled.connect(self.acquisition_changed.emit)
        self.preview.toggled.connect(self.preview_changed.emit)
        layout.addLayout(switches)
        self.image = QLabel("連線中…")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(160, 90)
        from PyQt5.QtWidgets import QSizePolicy
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        layout.addWidget(self.image, 1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setMinimumHeight(110)
        layout.addWidget(self.info)
        self._shown = None  # (frame object, label size) last rendered, to skip redundant rescales

    def display(self, frame, fps, status, stats=None):
        stats = stats or {}
        receiving = stats.get("acquisition_enabled", True)
        showing = stats.get("preview_enabled", True)
        for control, checked in ((self.acquisition, receiving), (self.preview, showing)):
            control.blockSignals(True)
            control.setChecked(checked)
            control.blockSignals(False)
        self.info.setToolTip(status + "\n上次中斷：" + (stats.get("last_disconnect") or "無"))
        configured = stats.get("configured_fps")
        rate = "未知" if configured is None else "%.1f" % configured
        self.info.setText(
            "接收 %.1f FPS / 目標 180  ·  平均 %.1f FPS\n"
            "完整 %d 幀  ·  不完整 %d  ·  跳號 %d\n"
            "取像錯誤 %d  ·  已測 %.0f 秒  ·  設定 %s FPS\n重連 %d 次 · %s" %
            (fps, stats.get("average", 0), stats.get("received", 0),
             stats.get("incomplete", 0), stats.get("gaps", 0),
             stats.get("errors", 0), stats.get("elapsed", 0), rate,
             stats.get("reconnects", 0), status[:90]))
        if frame is None or not receiving or not showing:
            self.image.clear()
            self._shown = None
            self.image.setText("接收已關閉" if not receiving else
                               "畫面已關閉 · 接收繼續" if not showing else "等待攝影機影像")
            return
        key = (frame, self.image.size())
        if self._shown is not None and self._shown[0] is key[0] and self._shown[1] == key[1]:
            return  # Same frame at same size: nothing to redraw.
        self._shown = key
        height, width, _ = frame.shape
        image = QImage(frame.data, width, height, frame.strides[0], QImage.Format_RGB888)
        self.image.setPixmap(QPixmap.fromImage(image).scaled(
            self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.image.setToolTip("%d × %d · 預覽上限 30 FPS" % (width, height))


class PreviewWindow(QMainWindow):
    def __init__(self, service=None):
        super().__init__()
        self.setWindowTitle("FLIR 多相機即時預覽")
        self.resize(1280, 800)
        self.tiles = {}
        self.closing = False
        self.service = service or PreviewService()
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        header = QHBoxLayout()
        title = QLabel("FLIR 多相機預覽")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()
        self.scan = QPushButton("重新偵測")
        self.scan.clicked.connect(self.service.wake.set)
        header.addWidget(self.scan)
        layout.addLayout(header)
        layout.addWidget(QLabel("1–5 台 · 1920×1080 · 全速接收統計 · 預覽上限 30 FPS · 未同步"))
        layout.addWidget(QLabel("接收 FPS 為最近 2 秒完整影格；設定值不代表實收。跳號可能來自傳輸或主機緩衝，重連會重計。"))
        self.empty = QLabel("等待 FLIR 攝影機\n接上 USB 或網路後，畫面會自動出現")
        self.empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty, 1)
        self.grid = QGridLayout()
        self.grid.setSpacing(12)
        layout.addLayout(self.grid, 1)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #101c2b; color: #e4edf5; font-family: 'Microsoft JhengHei', 'Segoe UI'; font-size: 14px; }
            QFrame#tile { background: #1b2d42; border: 1px solid #37506a; border-radius: 8px; }
            QLabel { background: transparent; }
            QPushButton { background: #167d8d; padding: 10px 18px; border-radius: 5px; }
            QPushButton:hover { background: #219cad; }
        """)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.start(34)
        self.service.start()

    def refresh(self):
        if self.closing:
            if not self.service.is_alive():
                self.close()
            return
        snapshots, message = self.service.snapshot()
        serials = sorted(snapshots)
        if serials != sorted(self.tiles):
            for tile in self.tiles.values():
                self.grid.removeWidget(tile)
                tile.deleteLater()
            self.tiles = {}
            columns = 1 if len(serials) <= 1 else 2 if len(serials) <= 4 else 3
            for index in range(3):
                self.grid.setColumnStretch(index, int(index < columns and bool(serials)))
                self.grid.setRowStretch(index, int(index < (len(serials) + columns - 1) // columns))
            for index, serial in enumerate(serials):
                tile = self.tiles[serial] = CameraTile(serial)
                tile.acquisition_changed.connect(
                    lambda enabled, sn=serial: self.service.set_enabled(sn, acquisition=enabled))
                tile.preview_changed.connect(
                    lambda enabled, sn=serial: self.service.set_enabled(sn, preview=enabled))
                self.grid.addWidget(tile, index // columns, index % columns)
        self.empty.setVisible(not serials)
        self.status.setText(message)
        for serial, snapshot in snapshots.items():
            self.tiles[serial].display(*snapshot)

    def closeEvent(self, event):
        if self.service.is_alive():
            self.closing = True
            self.scan.setEnabled(False)
            self.status.setText("正在停止擷取並釋放攝影機…")
            self.service.stop()
            event.ignore()
        else:
            self.timer.stop()
            event.accept()


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    window = PreviewWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
