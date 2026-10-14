from PyQt5.QtWidgets import QDialog, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QSlider
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt, pyqtSignal

class FullscreenVideoDialog(QDialog):
    control_signal = pyqtSignal(str)  # 用於與主窗口通信的信號
    slider_signal = pyqtSignal(int)  # 新增：用於通知主程式slider移動

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setScaledContents(True)

        # 添加控制按鈕
        self.play_pause_btn = QPushButton("暫停")
        self.prev_frame_btn = QPushButton("上一幀")
        self.next_frame_btn = QPushButton("下一幀")

        # 新增slider
        self.frame_slider = QSlider(Qt.Horizontal)
        self.frame_slider.setMinimum(0)
        self.frame_slider.setMaximum(100)  # 預設，請在外部根據影片長度設定
        self.frame_slider.setValue(0)
        self.frame_slider.setTickPosition(QSlider.TicksBelow)
        self.frame_slider.setTickInterval(1)
        self.frame_slider.sliderReleased.connect(self.onSliderReleased)

        # 設置按鈕的點擊事件
        self.play_pause_btn.clicked.connect(self.togglePlayPause)
        self.prev_frame_btn.clicked.connect(lambda: self.control_signal.emit("prev"))
        self.next_frame_btn.clicked.connect(lambda: self.control_signal.emit("next"))

        # 佈局
        layout = QVBoxLayout()
        layout.addWidget(self.label)
        layout.addWidget(self.frame_slider)  # 新增slider到畫面

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.prev_frame_btn)
        button_layout.addWidget(self.play_pause_btn)
        button_layout.addWidget(self.next_frame_btn)

        layout.addLayout(button_layout)
        self.setLayout(layout)

        self.showMaximized()
        self.is_playing = True  # 播放狀態

    def set_frame(self, frame):
        """Set the frame to be displayed in the fullscreen dialog."""
        pixmap = QPixmap.fromImage(frame)
        scaled_pixmap = pixmap.scaled(
            self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.label.setPixmap(scaled_pixmap)

    def set_slider_range(self, min_value, max_value):
        self.frame_slider.setMinimum(min_value)
        self.frame_slider.setMaximum(max_value)

    def set_slider_value(self, value):
        self.frame_slider.setValue(value)

    def onSliderReleased(self):
        value = self.frame_slider.value()
        self.slider_signal.emit(value)  # 通知主程式slider移動

    def resizeEvent(self, event):
        """Handle window resize to adjust the frame scaling."""
        if self.label.pixmap():
            # 獲取當前 pixmap 並重新縮放
            scaled_pixmap = self.label.pixmap().scaled(
                self.label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.label.setPixmap(scaled_pixmap)
        super().resizeEvent(event)

    def togglePlayPause(self):
        """切換播放/暫停狀態"""
        self.is_playing = not self.is_playing
        self.play_pause_btn.setText("播放" if not self.is_playing else "暫停")
        self.control_signal.emit("pause" if not self.is_playing else "play")