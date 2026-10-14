
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

from PyQt5.QtWidgets import *
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtCore import Qt, QTimer
import numpy as np
import sys
import cv2
import os
from Src.paths import DB_DIR
from Src.UI_Control.forms.camera_ui import Ui_camera_ui
from datetime import datetime
from Src.cameras.video_control import Camera

class PoseCameraTabControl(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_camera_ui()
        self.ui.setupUi(self)
        # self.model = model
        # self.init_pose_estimater()
        self.initVar()
        self.bindUI()

    def initVar(self):
        """Initialize variables and timer."""
        self.camera = Camera()
        self.timer = QTimer()
        self.timer.timeout.connect(self.analyzeFrame)
        self.camera_scene = QGraphicsScene()
        self.camera_scene_2 = QGraphicsScene()
        self.last_frame_cf = None
        self.last_frame_cs = None

    def bindUI(self):
        """Bind UI elements to their corresponding functions."""
        self.ui.cameraCheckBox.stateChanged.connect(self.toggleCamera)
        self.ui.recordCheckBox.stateChanged.connect(self.toggleRecord)
        self.ui.CameraIdInput.valueChanged.connect(self.changeCamera)
        self.ui.pictureButton.clicked.connect(self.savePicture)

    def toggleCamera(self, state:int):
        """Toggle the camera on/off based on checkbox state."""
        if state == 2:
            # frame_width, frame_height, fps = 
            self.camera.toggleCamera(True)
            # self.ui.ResolutionLabel.setText(f"(0, 0) - ({frame_width} x {frame_height}), FPS: {fps}")
            self.timer.start(1)
        else:
            self.camera.toggleCamera(False)
            self.timer.stop()

    def toggleRecord(self, state:int):
        """Start or stop video recording."""
        if state == 2:
            self.startRecording()
            self.ui.showSkeletonCheckBox.setChecked(False)
        else:
            self.camera.stop_recording()

    def startRecording(self):
        """Start recording the video."""
        current_time = datetime.now().strftime("%Y%m%d_%H%M")
        output_dir = f'{DB_DIR.as_posix()}/Record/C{self.ui.CameraIdInput.value()}_Fps120_{current_time}'
        os.makedirs(output_dir, exist_ok=True)
        video_filename = os.path.join(output_dir, f'CF_{current_time}.mp4')
        video_filename_2 = os.path.join(output_dir, f'CS_{current_time}.mp4')
        self.ui.showSkeletonCheckBox.setChecked(False)
        self.camera.startRecording(video_filename, video_filename_2)

    def changeCamera(self):
        """Change the camera based on input value."""
        self.camera.setCameraId(self.ui.CameraIdInput.value())

    def analyzeFrame(self):
        """Analyze and process each frame from the camera."""
        if not self.camera.frame_buffer.empty() and not self.camera.frame_buffer_2.empty():
            frame = self.camera.frame_buffer.get().copy()
            frame_2 = self.camera.frame_buffer_2.get().copy()
            self.last_frame_cf = frame.copy()
            self.last_frame_cs = frame_2.copy()
            # _, _, fps = self.pose_estimater.detectKpt(frame, is_video=False)
            # self.ui.FPSInfoLabel.setText(f"{fps:02d}")
            self.update_frame(frame, frame_2)

    def savePicture(self):
        """Save current CF/CS snapshots from latest buffered frames."""
        if self.last_frame_cf is None or self.last_frame_cs is None:
            QMessageBox.warning(self, "無法儲存照片", "目前沒有可儲存的影像，請先開啟相機。")
            return

        output_dir = f'{DB_DIR.as_posix()}/Record/Calibrate_Picture'
        cf_dir = os.path.join(output_dir, 'cf')
        cs_dir = os.path.join(output_dir, 'cs')
        os.makedirs(cf_dir, exist_ok=True)
        os.makedirs(cs_dir, exist_ok=True)

        # Use shared running index so cf/cs are always paired (01, 02, ...).
        max_idx = 0
        for d in (cf_dir, cs_dir):
            for name in os.listdir(d):
                stem, ext = os.path.splitext(name)
                if ext.lower() not in ('.jpg', '.jpeg', '.png'):
                    continue
                if stem.isdigit():
                    max_idx = max(max_idx, int(stem))

        next_idx = max_idx + 1
        filename = f"{next_idx:02d}.jpg"
        cf_path = os.path.join(cf_dir, filename)
        cs_path = os.path.join(cs_dir, filename)

        # QImage 顯示用 rgbSwapped，這裡直接用原始 BGR frame 儲存即可。
        ok_cf = cv2.imwrite(cf_path, self.last_frame_cf)
        ok_cs = cv2.imwrite(cs_path, self.last_frame_cs)

        if ok_cf and ok_cs:
            # QMessageBox.information(self, "照片已儲存", f"CF: {cf_path}\nCS: {cs_path}")
            pass
        else:
            QMessageBox.warning(self, "儲存失敗", "照片儲存失敗，請確認資料夾權限或磁碟空間。")

    def update_frame(self, frame: np.ndarray, frame_2: np.ndarray):
        """Update the displayed frame with additional analysis."""
        # drawed_img = self.image_drawer.drawInfo(img = frame, kpt_buffer = self.pose_estimater.kpt_buffer)
        self.showImage(frame, self.camera_scene, self.ui.FrameView)
        self.showImage(frame_2, self.camera_scene_2, self.ui.FrameView_2)

    def showImage(self, image: np.ndarray, scene: QGraphicsScene, GraphicsView: QGraphicsView):
        """Display an image in the QGraphicsView."""
        scene.clear()
        h, w = image.shape[:2]
        qImg = QImage(image, w, h, 3 * w, QImage.Format_RGB888).rgbSwapped()
        pixmap = QPixmap.fromImage(qImg)
        scene.addPixmap(pixmap)
        GraphicsView.setScene(scene)
        GraphicsView.setAlignment(Qt.AlignLeft)
        GraphicsView.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)

    def mousePressEvent(self, event):
        """Handle mouse events for person and keypoint selection."""
        if not self.ui.FrameView.rect().contains(event.pos()):
            return
        
        scene_pos = self.ui.FrameView.mapToScene(event.pos())
        x, y = scene_pos.x(), scene_pos.y()
        search_person_df = self.pose_estimater.pre_person_df

        if self.ui.selectCheckBox.isChecked() and event.button() == Qt.LeftButton:
            print(search_person_df)
            self.person_selector.select(x, y, search_person_df)
            self.pose_estimater.setPersonId(self.person_selector.selected_id)

        if self.ui.selectKptCheckBox.isChecked() and event.button() == Qt.LeftButton:
            self.kpt_selector.select(x, y, search_person_df)
            self.pose_estimater.setKptId(self.kpt_selector.selected_id)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PoseCameraTabControl()
    window.show()
    sys.exit(app.exec_())
