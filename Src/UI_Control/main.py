
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
from PyQt5.QtWidgets import QApplication, QMainWindow

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

class Main(QMainWindow):
    def __init__(self):
        super(Main, self).__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.model = Model()
        self.init_tabs()
        self.camera_tab.configure_settings(self.pitch_tab, self.camera_sources)

    def camera_sources(self):
        return [("2D 相機", self.camera_tab.camera), ("2D", self.pitch_tab.camera)]

    def closeEvent(self, event):
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
    app = QApplication(sys.argv)
    main_window = Main()
    main_window.show()
    sys.exit(app.exec_())

