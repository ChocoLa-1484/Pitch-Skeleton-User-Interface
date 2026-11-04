"""Launch with python -m Src.UI_Control.main_flir_preview."""
import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

plugin_path = Path(sys.prefix) / "Library/plugins/platforms"
if plugin_path.is_dir():
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(plugin_path))

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget)
from Src.cameras.flir_preview import PreviewService


class CameraTile(QFrame):
    def __init__(self, serial):
        super().__init__()
        self.setObjectName("tile")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("FLIR  ·  SN %s" % serial))
        self.image = QLabel("連線中…")
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(160, 90)
        from PyQt5.QtWidgets import QSizePolicy
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        layout.addWidget(self.image, 1)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setMaximumHeight(52)
        layout.addWidget(self.info)

    def display(self, frame, fps, status):
        self.info.setToolTip(status)
        if frame is None:
            self.image.clear()
            self.image.setText("等待攝影機影像")
            self.info.setText(status[:180])
            return
        height, width, _ = frame.shape
        image = QImage(frame.data, width, height, frame.strides[0], QImage.Format_RGB888)
        self.image.setPixmap(QPixmap.fromImage(image).scaled(
            self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.info.setText("● 即時畫面    %.1f FPS    %d × %d" % (fps, width, height))


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
        layout.addWidget(QLabel("接上即顯示 · 1–5 台 · 獨立預覽（未同步）"))
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
        self.timer.start(50)
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
