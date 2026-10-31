
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import sys
import os

# ``CONDA_PREFIX`` is not guaranteed to be set when this file is launched
# directly from VS Code or with an explicit Python executable.  ``sys.prefix``
# still points at the active conda environment in those cases.
conda_prefix = os.environ.get("CONDA_PREFIX", sys.prefix)
os.environ.setdefault("CONDA_PREFIX", conda_prefix)
qt_plugin_path = os.path.join(conda_prefix, "Library", "plugins", "platforms")
if os.path.isdir(qt_plugin_path):
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = qt_plugin_path
from PyQt5.QtWidgets import (QApplication, QMainWindow, QComboBox, QGroupBox,
    QFormLayout, QPushButton, QMessageBox)

# vispy 必須在 PySpin 等第三方 DLL 被載入前鎖定 Qt 後端，
# 否則 PySpin 附帶的 DLL 會與 Qt 的 QtOpenGL 衝突，導致載入失敗。
import vispy
vispy.use('pyqt5')

# Load torch/model DLLs before the camera SDK as well (Windows DLL search order).
from Src.pose.model import Model
from Src.UI_Control.widgets.camera_widget import PoseCameraTabControl
from Src.UI_Control.widgets.video_widget_2 import PoseVideoTabControl
from Src.UI_Control.widgets.pitch_widget import PosePitchTabControl
from Src.UI_Control.widgets.video_widget_compare import PoseVideoCompareTabControl
from Src.UI_Control.forms.main_window import Ui_MainWindow
from Src.cameras.pitch_profiles import profile_serials
from Src.UI_Control.widgets.pitch_camera_settings import (PitchCameraSettingsDialog,
                                                         PitchCameraAssignmentsPanel)
from Src.UI_Control.widgets.appearance import configure_high_dpi, style_main_window

class Main(QMainWindow):
    def __init__(self):
        super(Main, self).__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.model = Model()
        self.init_tabs()
        self.camera_tab.configure_settings(self.pitch_tab, self.camera_sources)
        self.configure_pitch_hand()
        style_main_window(self)

    def configure_pitch_hand(self):
        self.pitch_hand = "right" if self.pitch_tab.ui.pitchInput.isChecked() else "left"
        self._changing_pitch_hand = False
        group = QGroupBox("左右投相機")
        form = QFormLayout(group)
        self.pitch_hand_combo = QComboBox()
        self.pitch_hand_combo.addItem("右投", "right")
        self.pitch_hand_combo.addItem("左投", "left")
        self.pitch_hand_combo.setCurrentIndex(self.pitch_hand_combo.findData(self.pitch_hand))
        form.addRow("投球手別", self.pitch_hand_combo)
        self.pitch_camera_assignments = PitchCameraAssignmentsPanel(self.can_edit_pitch_profiles)
        self.pitch_camera_assignments.saved.connect(self._pitch_profiles_saved)
        form.addRow(self.pitch_camera_assignments)
        edit = QPushButton("左右投相機設定（含外參）")
        form.addRow(edit)
        self.camera_tab.ui.verticalLayout_7.insertWidget(1, group)
        self.pitch_tab.pitch_hand_handler = self.set_pitch_hand
        self.video_tab.pitch_hand_handler = self.set_pitch_hand
        self.pitch_hand_combo.currentIndexChanged.connect(
            lambda: self.set_pitch_hand(self.pitch_hand_combo.currentData()))
        edit.clicked.connect(self.edit_pitch_profiles)
        self._sync_pitch_hand_controls()

    def _sync_pitch_hand_controls(self):
        hand = self.pitch_hand
        self.pitch_camera_assignments.set_hand(hand)
        self.pitch_hand_combo.blockSignals(True)
        self.pitch_hand_combo.setCurrentIndex(self.pitch_hand_combo.findData(hand))
        self.pitch_hand_combo.blockSignals(False)
        for button, selected in ((self.pitch_tab.ui.pitchInput, hand == "right"),
                                 (self.pitch_tab.ui.pitchInput_L, hand == "left")):
            button.blockSignals(True)
            button.setChecked(selected)
            button.blockSignals(False)
        for _, camera in self.camera_sources():
            camera.pitch_hand = hand
        for estimator in (self.pitch_tab.pose_estimater, self.pitch_tab.pose_estimater_2):
            estimator.setPitchHandId(10 if hand == "right" else 9)
        self.pitch_tab.pose_estimater_2.setKptId(10 if hand == "right" else 9)
        self.pitch_tab.apply_pitch_hand(hand)
        self.video_tab.set_pitch_hand(hand)

    def _capture_busy(self):
        return (getattr(self.pitch_tab, "is_auto_recording", False)
                or any(camera.is_auto_recording or
                       (camera.video_writer is not None and camera.video_writer.is_writing)
                       for _, camera in self.camera_sources()))

    def set_pitch_hand(self, hand):
        if self._changing_pitch_hand or hand == self.pitch_hand:
            return True
        if self._capture_busy():
            self._sync_pitch_hand_controls()
            QMessageBox.warning(self, "暫時無法切換", "錄影中請先停止錄影，再切換左右投相機。")
            return False
        tabs = (self.camera_tab, self.pitch_tab)
        opened = [tab for tab in tabs if tab.camera.is_opened]
        try:
            # Offline playback still supports either hand before hardware is set.
            if opened:
                profile_serials(hand)
        except (ValueError, OSError) as exc:
            self._sync_pitch_hand_controls()
            QMessageBox.warning(self, "無法切換相機", str(exc))
            return False
        self._changing_pitch_hand = True
        try:
            for tab in opened:
                tab.ui.cameraCheckBox.setChecked(False)
            self.pitch_hand = hand
            self._sync_pitch_hand_controls()
            for tab in opened:
                tab.ui.cameraCheckBox.setChecked(True)
        finally:
            self._changing_pitch_hand = False
        return True

    def can_edit_pitch_profiles(self):
        if self._capture_busy() or any(camera.is_opened for _, camera in self.camera_sources()):
            QMessageBox.information(self, "請先關閉相機", "修改實體相機序號或外參檔前，請先關閉相機。")
            return False
        return True

    def _pitch_profiles_saved(self):
        self.video_tab.set_pitch_hand(self.pitch_hand, force=True)

    def edit_pitch_profiles(self):
        if not self.can_edit_pitch_profiles():
            return
        if PitchCameraSettingsDialog(self).exec_():
            self.pitch_camera_assignments.reload()
            self._pitch_profiles_saved()

    def camera_sources(self):
        return [("2D 相機", self.camera_tab.camera), ("2D", self.pitch_tab.camera)]

    def closeEvent(self, event):
        self.pitch_tab.single_video_scan_running = False
        self.camera_tab.settings_panel.timer.stop()
        self.camera_tab.preview_timer.stop()
        self.camera_tab.timer.stop()
        self.pitch_tab.timer.stop()
        self.video_tab._set_analysis_running(False)
        for tab in (self.pitch_tab, self.video_tab, self.video_compare_tab):
            tab.is_play = False
            if getattr(tab, '_play_timer', None) is not None:
                tab._play_timer.stop()
        for _, camera in self.camera_sources():
            if camera is not None:
                camera.close_camera()
        super().closeEvent(event)

    def init_tabs(self):
        self.camera_tab = PoseCameraTabControl()
        self.ui.Two_d_Tab.addTab(self.camera_tab, "2D 相機")
        self.pitch_tab = PosePitchTabControl(self.model, self)
        self.ui.Two_d_Tab.addTab(self.pitch_tab, "2D")
        self.video_tab = PoseVideoTabControl(self.model)
        self.ui.Two_d_Tab.addTab(self.video_tab, "3D")    
        self.video_compare_tab = PoseVideoCompareTabControl(self.model)
        self.ui.Two_d_Tab.addTab(self.video_compare_tab, "回放比較")    

if __name__ == '__main__':
    # Model configs, fonts and recordings use paths relative to this directory.
    # Support launching `python Src/UI_Control/main.py` from the project root.
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    configure_high_dpi()
    app = QApplication(sys.argv)
    main_window = Main()
    main_window.show()
    sys.exit(app.exec_())

