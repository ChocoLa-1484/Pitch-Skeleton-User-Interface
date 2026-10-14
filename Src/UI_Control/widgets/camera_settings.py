"""Live settings for the cameras already owned by the main window's tabs."""
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QFormLayout,
    QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget)

LABELS = {"exposure": "曝光時間", "gain": "增益", "red": "紅色白平衡",
          "blue": "藍色白平衡", "focus": "對焦位置", "focal_length": "光學焦距"}


class CameraSettingsDialog(QDialog):
    settings_changed = pyqtSignal()
    def __init__(self, sources, parent=None):
        super().__init__(parent)
        self.sources = sources
        self._cameras = []
        self._identity = None
        self._states = {}
        self.setWindowTitle("攝影機設定")
        self.resize(720, 510)
        layout = QVBoxLayout(self)
        self.camera_combo = QComboBox()
        layout.addWidget(self.camera_combo)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        form = QFormLayout()
        self.rows = {}
        for key, label in LABELS.items():
            row = QWidget()
            box = QVBoxLayout(row)
            box.setContentsMargins(0, 0, 0, 0)
            controls = QHBoxLayout()
            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 1000)
            spin = QDoubleSpinBox()
            spin.setMinimumWidth(150)
            spin.setKeyboardTracking(False)
            apply = QPushButton("套用")
            note = QLabel()
            note.setWordWrap(True)
            controls.addWidget(slider, 1)
            controls.addWidget(spin)
            controls.addWidget(apply)
            box.addLayout(controls)
            box.addWidget(note)
            form.addRow(label, row)
            self.rows[key] = (slider, spin, apply, note)
            slider.valueChanged.connect(lambda value, k=key: self._from_slider(k, value))
            spin.valueChanged.connect(lambda value, k=key: self._from_spin(k, value))
            apply.clicked.connect(lambda checked=False, k=key: self.apply(k))
        layout.addLayout(form)
        note = QLabel("調整數值後按該列「套用」，會立即更新畫面並儲存。\n"
                      "焦距與對焦需鏡頭支援電子控制；手動鏡頭請轉動鏡頭環。"
                      "更動焦距或對焦後，請重新校正內參。")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QHBoxLayout()
        refresh = QPushButton("重新讀取")
        refresh.clicked.connect(self.refresh)
        close = QPushButton("關閉")
        close.clicked.connect(self.close)
        buttons.addWidget(refresh)
        buttons.addStretch()
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.camera_combo.currentIndexChanged.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.sync_sources)
        self.timer.start(500)
        self.sync_sources()

    def _available(self):
        result = []
        for name, controller in self.sources():
            thread = getattr(controller, "video_thread", None)
            if not getattr(controller, "is_opened", False) or thread is None or not thread.running:
                continue
            for attr, role in (("camera1", "正面 CF"), ("camera2", "側面 CS")):
                camera = getattr(thread, attr, None)
                if camera is not None and not camera._released:
                    result.append((f"{name} / {role} / SN{camera.serial_number}", camera, controller))
        return result

    @staticmethod
    def _recording(controller):
        writer = getattr(controller, "video_writer", None)
        return bool(controller.is_auto_recording or (writer is not None and writer.is_writing))

    def sync_sources(self):
        available = self._available()
        identity = tuple((label, id(cam), self._recording(owner)) for label, cam, owner in available)
        if identity == self._identity:
            return
        selected = self.camera_combo.currentText()
        self._identity, self._cameras = identity, available
        self.camera_combo.blockSignals(True)
        self.camera_combo.clear()
        self.camera_combo.addItems([label for label, _, _ in available])
        index = self.camera_combo.findText(selected)
        self.camera_combo.setCurrentIndex(max(0, index))
        self.camera_combo.blockSignals(False)
        self.refresh()

    def refresh(self):
        self._states = {}
        index = self.camera_combo.currentIndex()
        message = "請先在「2D 相機」或「2D」分頁開啟攝影機，再開啟此設定視窗。"
        recording = False
        if 0 <= index < len(self._cameras):
            _, camera, owner = self._cameras[index]
            recording = self._recording(owner)
            try:
                self._states = camera.settings.describe()
                message = "錄影中暫停調整，停止錄影後即可使用。" if recording else "已讀取目前相機數值。"
            except Exception as exc:
                message = f"無法讀取攝影機：{exc}"
        self.status.setText(message)
        for key, (slider, spin, apply, note) in self.rows.items():
            state = self._states.get(key)
            enabled = state is not None and state.writable and not recording
            slider.setEnabled(enabled)
            spin.setEnabled(enabled)
            apply.setEnabled(enabled)
            spin.blockSignals(True)
            slider.blockSignals(True)
            if state is not None:
                spin.setDecimals(0 if state.integer else (1 if key == "exposure" else 3))
                spin.setRange(min(state.minimum, state.value), max(state.maximum, state.value))
                spin.setSingleStep(state.step)
                spin.setSuffix(" " + state.unit if state.unit else "")
                spin.setValue(state.value)
                span = state.maximum - state.minimum
                slider.setValue(round(1000 * (state.value - state.minimum) / span) if span > 0 else 0)
                note.setText(state.reason or f"範圍 {state.minimum:g}–{state.maximum:g} {state.unit}")
            else:
                spin.setValue(0)
                slider.setValue(0)
                note.setText("未連線")
            spin.blockSignals(False)
            slider.blockSignals(False)

    def _from_slider(self, key, value):
        state = self._states.get(key)
        if state is None: return
        target = state.minimum + (state.maximum - state.minimum) * value / 1000
        if state.integer:
            target = state.minimum + round((target - state.minimum) / state.step) * state.step
        spin = self.rows[key][1]
        spin.blockSignals(True)
        spin.setValue(min(state.maximum, target))
        spin.blockSignals(False)

    def _from_spin(self, key, value):
        state = self._states.get(key)
        if state is None or state.maximum <= state.minimum: return
        slider = self.rows[key][0]
        slider.blockSignals(True)
        slider.setValue(round(1000 * (value - state.minimum) / (state.maximum - state.minimum)))
        slider.blockSignals(False)

    def apply(self, key):
        # Recheck ownership and recording at click time, including disconnects
        # between timer ticks. Never redirect an edit to a different camera.
        index = self.camera_combo.currentIndex()
        if not 0 <= index < len(self._cameras): return
        _, camera, owner = self._cameras[index]
        if not any(cam is camera for _, cam, _ in self._available()):
            self.sync_sources()
            self.status.setText("攝影機已關閉，設定未套用。")
            return
        if self._recording(owner):
            self.refresh()
            return
        value = self.rows[key][1].value()
        try:
            actual = camera.settings.apply(key, value)
            self.settings_changed.emit()
            message = f"{LABELS[key]}已套用並儲存：{actual:g}"
        except Exception as exc:
            message = f"套用失敗：{exc}"
        self.refresh()
        self.status.setText(message)

    def closeEvent(self, event):
        self.timer.stop()
        super().closeEvent(event)
