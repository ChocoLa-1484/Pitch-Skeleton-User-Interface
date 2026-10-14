
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
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QAction

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
from Src.UI_Control.widgets.camera_settings import CameraSettingsDialog

class Main(QMainWindow):
    def __init__(self):
        super(Main, self).__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.model = Model()
        self.init_tabs()
        self.settings_dialog = None
        action = QAction("攝影機設定（曝光／焦距／顏色）", self)
        action.triggered.connect(self.open_camera_settings)
        self.menuBar().addMenu("攝影機").addAction(action)
        self.addToolBar("攝影機").addAction(action)

    def camera_sources(self):
        return [("2D 相機", self.camera_tab.camera), ("2D", self.pitch_tab.camera)]

    def open_camera_settings(self):
        if self.settings_dialog is not None:
            self.settings_dialog.close()
            self.settings_dialog.deleteLater()
        self.settings_dialog = CameraSettingsDialog(self.camera_sources, self)
        self.settings_dialog.settings_changed.connect(self.pitch_tab.updateCameraSliders)
        # Modeless so the user can see the live preview while adjusting.
        self.settings_dialog.show()
        self.settings_dialog.raise_()

    def closeEvent(self, event):
        if self.settings_dialog is not None:
            self.settings_dialog.close()
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
    app = QApplication(sys.argv)
    main_window = Main()
    main_window.show()
    sys.exit(app.exec_())

