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
from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout

# vispy 必須在 PySpin 等第三方 DLL 被載入前鎖定 Qt 後端，
# 否則 PySpin 附帶的 DLL 會與 Qt 的 QtOpenGL 衝突，導致載入失敗。
import vispy
vispy.use('pyqt5')

from camera_widget import PoseCameraTabControl
from video_widget_2 import PoseVideoTabControl
from pitch_widget import PosePitchTabControl
from video_widget_compare import PoseVideoCompareTabControl
from main_window import Ui_MainWindow
from utils.model import Model

class Main(QMainWindow):
    def __init__(self):
        super(Main, self).__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        self.model = Model()
        self.init_tabs()

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

