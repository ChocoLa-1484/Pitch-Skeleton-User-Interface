
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
from Src.UI_Control.widgets.camera_settings import CameraSettingsPanel

class PoseCameraTabControl(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ui = Ui_camera_ui()
        self.ui.setupUi(self)
        # self.model = model
        # self.init_pose_estimater()
        self.initVar()
        self.bindUI()

    def configure_settings(self, pitch_tab, sources):
        """Keep the live camera controls and 2D preferences in one place."""
        self.pitch_tab = pitch_tab
        self._sharing_pitch_preview = False
        self.ui.settingGroupBox.setTitle("相機與 2D 設定")
        self.ui.settingGroupBox.setMinimumSize(440, 0)
        self.ui.settingGroupBox.setMaximumWidth(480)
        self.ui.displayGroupbox.hide()  # This preview tab has no pose estimator.
        self.ui.CameraSettingGroupBox.setTitle("相機預覽／拍照")
        for view in (self.ui.FrameView, self.ui.FrameView_2):
            view.setMinimumSize(280, 240)
        self.ui.horizontalLayout_2.setStretch(0, 1)
        self.ui.horizontalLayout_2.setStretch(1, 1)

        # Move the actual controls so the analysis tab keeps its existing signal
        # bindings and reads the same values during playback and recording.
        preferences = QGroupBox("2D 回放與錄影")
        form = QFormLayout(preferences)
        for label, control in (("回放速率", pitch_tab.ui.slowMotionInput),
                               ("回放後等待（秒）", pitch_tab.ui.comboBox),
                               ("錄影幀率", pitch_tab.recordRateCombo)):
            form.addRow(label, control)
        pitch_tab.ui.slowMotionInput.setToolTip("調整「2D」分頁的影片回放速度。")
        pitch_tab.ui.comboBox.setToolTip("原「更新速率」：回放結束後，等待幾秒再返回即時畫面。")
        pitch_tab.recordRateCombo.setToolTip("套用到「2D」分頁的投球錄影。")
        for label in (pitch_tab.ui.label, pitch_tab.ui.label_5,
                      pitch_tab.ui.label_6, pitch_tab.recordRateLabel):
            label.hide()

        hardware = QGroupBox("曝光、調色與鏡頭")
        self.settings_panel = CameraSettingsPanel(sources, hardware)
        QVBoxLayout(hardware).addWidget(self.settings_panel)
        self.ui.verticalLayout_7.insertWidget(1, preferences)
        self.ui.verticalLayout_7.insertWidget(2, hardware)

        # The controls remain usable on laptops without forcing a tall window.
        self.ui.horizontalLayout_4.removeWidget(self.ui.settingGroupBox)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMinimumWidth(465)
        scroll.setMaximumWidth(505)
        scroll.setWidget(self.ui.settingGroupBox)
        self.ui.horizontalLayout_4.addWidget(scroll)
        self.preview_timer = QTimer(self)
        self.preview_timer.timeout.connect(self._sync_pitch_preview)
        self.preview_timer.start(250)

    def _sync_pitch_preview(self):
        # Reuse the displayed scenes, without consuming the analysis camera's
        # frame queues. Settings remain visible beside the active live preview.
        shared = self.pitch_tab.camera.is_opened and not self.camera.is_opened
        self.ui.cameraCheckBox.setEnabled(not shared)
        self.ui.recordCheckBox.setEnabled(not shared)
        self.ui.pictureButton.setEnabled(not shared)
        if shared:
            for view, source in ((self.ui.FrameView, self.pitch_tab.ui.FrameView),
                                 (self.ui.FrameView_2, self.pitch_tab.ui.FrameView_2)):
                scene = source.scene()
                view.setScene(scene)
                if scene is not None and scene.items():
                    view.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)
            self.ui.ResolutionLabel.setText("目前顯示「2D」使用中的相機；可直接在右側調整設定。")
        elif self._sharing_pitch_preview:
            self.ui.FrameView.setScene(self.camera_scene)
            self.ui.FrameView_2.setScene(self.camera_scene_2)
            self.ui.ResolutionLabel.setText("請開啟相機以預覽畫面。")
        self._sharing_pitch_preview = shared

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
