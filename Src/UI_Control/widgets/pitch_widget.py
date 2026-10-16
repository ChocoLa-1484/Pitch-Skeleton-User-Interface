
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

from PyQt5.QtWidgets import *
from PyQt5.QtGui import QImage, QPixmap, QColor, QPainter, QFont, QPen
from PyQt5.QtCore import Qt, QTimer, QRect, QEvent, QThread, pyqtSignal
import numpy as np
from Src.analysis.pitch_events import detect_pitch_events, EVENT_METHOD
from Src.UI_Control.widgets.keyframe_navigation import bind_keyframe_navigation
import pandas as pd
import sys
import os
from Src.paths import DB_DIR
import math
from Src.UI_Control.forms.pitch_ui import Ui_pitch_ui
from Src.UI_Control.widgets.pitch_layout import align_pitch_layout
from Src.UI_Control.widgets.playVideo_2 import FullscreenVideoDialog
from datetime import datetime
import time
from Src.common.timer import Timer
from Src.cameras.video_control import Camera, VideoLoader
from Src.UI_Control.controllers.selector import PersonSelector, KptSelector
from Src.analysis.analyze import PoseAnalyzer
from Src.UI_Control.rendering.vis_image import ImageDrawer
from Src.pose.detect_skeleton import PoseEstimater
import pyqtgraph as pg
from Src.pose.model import Model
from Src.analysis.PitchingAnalyzer import PitchingAnalyzer
from Src.cameras.video_threads import FramesToVideoWriterThread
from Src.calibration import calib_store
from Src.analysis.analysis_io import atomic_json, read_optional_json
from Src.analysis.pose_replay import pose_timeline, remap_pose_rows, saved_pose_offset, save_pose_timeline
from Src.rapsodo.capture_tablet_images import TabletImageCaptureThread
import cv2
import json
import base64
import re

class SliderColorOverlay(QWidget):
    def __init__(self, slider: QSlider, segments: list, fps: float = 30):
        """
        slider: 你要疊的 QSlider
        segments: list of tuples (start_frame, end_frame, color)
        """
        super().__init__(slider.parent())
        self.slider = slider
        self.segments = segments
        self.fps = fps
        # 色塊半厚度（像素）。8 代表總厚度約 16px。
        self.segment_half_thickness = 9
        self.setAttribute(Qt.WA_TransparentForMouseEvents)  # 透明且不阻擋滑鼠
        self.setGeometry(self.slider.geometry())
        self.show()

    def updateSegments(self, segments):
        self.segments = segments
        self.update()

    def resizeEvent(self, event):
        """當覆蓋層本身被 resize 時，確保尺寸與 Slider 相同"""
        self.setGeometry(self.slider.geometry())
        super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        self.setGeometry(self.slider.geometry())

        h = self.slider.height()
        center_y = h // 2
        half_thickness = max(3, min(int(self.segment_half_thickness), max(3, h // 2 - 2)))
        groove_rect = QRect(
            2,
            center_y - half_thickness,
            max(1, self.slider.width() - 4),
            max(1, half_thickness * 2)
        )
        total = self.slider.maximum() - self.slider.minimum()

        if self.segments:
            start = self.segments[0][0]
            end = self.segments[-1][1]
            if start is None or end is None:
                return
            per_total = end - start
        else:
            per_total = self.slider.maximum() - self.slider.minimum()

        if per_total == 0:
            return

        # 計算上下可用空間高度
        top_area_height = groove_rect.top()
        bottom_area_height = self.height() - groove_rect.bottom()

        # 設置文字字體
        font = QFont("Arial", 18, QFont.Bold)
        painter.setFont(font)

        for i, (start, end, color) in enumerate(self.segments):
            # 1. 檢查是否為 None
            if end is None or total is None:
                return  # 如果資料還沒準備好，就不要畫這個部分，直接結束繪圖
            # 2. 檢查分母是否為 0 (避免 ZeroDivisionError)
            if total == 0:
                return 
            # 3. 確保 start 也不為 None (如果有用到的話)
            start_val = start if start is not None else 0
            duration = end - start_val
            x1 = groove_rect.left() + (start_val / total) * groove_rect.width()
            x2 = groove_rect.left() + (end / total) * groove_rect.width()
            segment_width = int(x2 - x1)
            painter.fillRect(QRect(int(x1), groove_rect.top(), segment_width, groove_rect.height()), color)
            painter.setPen(color)
            
            # 計算百分比
            percentage = (duration / per_total) * 100
            
            # 格式化標籤：Frame 數 / 百分比
            fps = self.fps if self.fps and self.fps > 0 else 60
            seconds = duration / fps
            label = f"{seconds:.2f}s"
            # label = f"{seconds:.2f}s ({percentage:.1f}%)"
            
            # 定義文字繪圖區域
            # 讓文字繪製在軌道上方，並位於該區塊的中心
            metrics = painter.fontMetrics()
            text_width_needed = metrics.horizontalAdvance(label) + 10 # 4px padding
            text_x = int(x1 + segment_width / 2 - text_width_needed / 2)
            # 第2個phase (i=1) 的文字標籤往左移
            # if i == 1:
            #     text_x -= 30
            # elif i == 3:
            #     text_x += 20
            # 決定 Y 層次： i 為偶數畫上方，i 為奇數畫下方
            if i % 2 == 0:
                # 繪製在上方區域 (Y 從 0 開始)
                text_y_start = 0
                text_height = top_area_height
                
            else:
                # 繪製在下方區域 (Y 從軌道底部開始)
                text_y_start = groove_rect.bottom()
                text_height = bottom_area_height
            
            # 文字矩形：使用整個區塊寬度，但高度只佔上半部分
            text_rect = QRect(text_x, text_y_start, text_width_needed, text_height)

            # 確保文字區塊足夠大，避免繪製過多小區塊的標籤
            painter.drawText(text_rect, Qt.AlignCenter, label)


class SpinDirectionWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._direction = "N/A"
        self.setMinimumSize(180, 120)

    def setDirection(self, direction):
        text = str(direction).strip() if direction is not None else ""
        self._direction = text if text else "N/A"
        self.update()

    @staticmethod
    def _parse_clock_direction(direction_text):
        try:
            hour_text, minute_text = direction_text.split(":", 1)
            hour = int(hour_text)
            minute = int(minute_text)
        except Exception:
            return None

        if hour < 0 or minute < 0 or minute >= 60:
            return None

        # Clock style angle: 12 o'clock is up, increasing clockwise.
        fraction = ((hour % 12) + (minute / 60.0)) / 12.0
        return fraction * 2.0 * math.pi - (math.pi / 2.0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        text_rect = QRect(0, 0, self.width(), 28)
        painter.setPen(QColor("#1f1f1f"))
        painter.setFont(QFont("Arial", 11, QFont.Bold))
        painter.drawText(text_rect, Qt.AlignCenter, f"旋轉方向: {self._direction}")

        chart_rect = QRect(8, 32, max(1, self.width() - 16), max(1, self.height() - 40))
        diameter = max(30, min(chart_rect.width(), chart_rect.height()) - 6)
        cx = chart_rect.center().x()
        cy = chart_rect.center().y()
        radius = diameter // 2

        circle_rect = QRect(cx - radius, cy - radius, radius * 2, radius * 2)
        painter.setPen(QPen(QColor("#6d7782"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(circle_rect)

        painter.setPen(QPen(QColor("#a0a7af"), 1))
        painter.drawLine(cx - radius, cy, cx + radius, cy)
        painter.drawLine(cx, cy - radius, cx, cy + radius)

        angle = self._parse_clock_direction(self._direction)
        if angle is None:
            return

        tip_x = int(round(cx + (radius - 6) * math.cos(angle)))
        tip_y = int(round(cy + (radius - 6) * math.sin(angle)))
        painter.setPen(QPen(QColor("#e53935"), 3, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(cx, cy, tip_x, tip_y)
        painter.setBrush(QColor("#e53935"))
        painter.drawEllipse(tip_x - 4, tip_y - 4, 8, 8)


class SingleVideoKeyframeWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, video_path: str, model: Model, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.model = model

    def run(self):
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            self.error.emit(f"無法開啟影片: {self.video_path}")
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frames = []
        success, image = cap.read()
        while success:
            frames.append(image)
            success, image = cap.read()
        cap.release()

        if not frames:
            self.error.emit("影片沒有可用畫面")
            return

        pose_estimater = PoseEstimater(self.model)
        pose_analyzer = PoseAnalyzer(pose_estimater, fps=int(round(fps)) if fps and fps > 0 else 60)
        image_drawer = ImageDrawer(pose_estimater, pose_analyzer)

        self.finished.emit({
            "pose_estimater": pose_estimater,
            "pose_analyzer": pose_analyzer,
            "image_drawer": image_drawer,
            "frames": frames,
            "fps": fps,
            "total_frames": total_frames if total_frames > 0 else len(frames),
        })

class PosePitchTabControl(QWidget):
    def __init__(self, model:Model, parent=None):
        super().__init__(parent)
        self.ui = Ui_pitch_ui()
        self.ui.setupUi(self)
        align_pitch_layout(self.ui)
        self._applyStartupWindowGeometry()
        self.model = model
        self.setupComponents()
        self.initVar()
        self.bindUI()
        self.fullscreen_view_1 = None  # 對應 FrameView
        self.fullscreen_view_2 = None  # 對應 FrameView_2
        self.play2videos = False
        self.load_video_list(str(DB_DIR / "Record"))
        self.video_filename = None  # 用來保存最新錄製的影片路徑
        self.video_filename_2 = None
        self.overlay = None  # 初始化 SliderColorOverlay
        self.frame_offset = 2
        self.single_video_mode = False
        self.single_video_scan_running = False
        self.single_video_keyframes = {
            "preparation_frame": None,
            "foot_contact_frame": None,
            "max_shoulder_angle_frame": None,
            "wrist_speed_frame": None,
        }
        self.setFocusPolicy(Qt.StrongFocus)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._global_key_handlers = {
            Qt.Key_Space: self.playBtnClicked,
            Qt.Key_A: lambda: self.ui.frameSlider.setValue(self.ui.frameSlider.value() - 1),
            Qt.Key_D: lambda: self.ui.frameSlider.setValue(self.ui.frameSlider.value() + 1),
        }

    def _applyStartupWindowGeometry(self):
        # Relax a few strict minimum heights so the window can fit smaller working areas.
        if hasattr(self.ui, 'fcView') and self.ui.fcView is not None:
            self.ui.fcView.setMinimumHeight(180)
        if hasattr(self.ui, 'merView') and self.ui.merView is not None:
            self.ui.merView.setMinimumHeight(180)
        if hasattr(self.ui, 'brView') and self.ui.brView is not None:
            self.ui.brView.setMinimumHeight(180)

        self.setMinimumSize(1200, 760)

        app = QApplication.instance()
        if app is None:
            return
        screen = app.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        target_w = min(max(self.width(), 1200), available.width())
        target_h = min(max(self.height(), 760), available.height())
        self.resize(target_w, target_h)

    def initVar(self):
        """Initialize variables and timer."""
        self.is_video = True if self.camera is None else False
        self.view_scene = QGraphicsScene()
        self.view_scene_2 = QGraphicsScene()
        self.view_scene.clear()
        self.view_scene_2.clear()
        self.is_pitching = False
        self.output_dir = None
        self.mer_locked = False
        self.release_locked = False
        # 投球錄影快取（已由 Camera 負責管理）
        self.is_auto_recording = False
        self.camera_fps = 60
        self.video_writer_thread = None  # 後台寫入線程
        # 錄影幀率：60 為原本行為（每 3 幀存 1 幀），179 為全幀率（存原始 Bayer，寫檔時轉色）
        self.recordRateLabel = QLabel("錄影幀率：", self.ui.camerSettingGroupBox)
        self.recordRateCombo = QComboBox(self.ui.camerSettingGroupBox)
        self.recordRateCombo.addItems(["60 FPS", "179 FPS（全幀率）"])
        record_rate_row = QHBoxLayout()
        record_rate_row.addWidget(self.recordRateLabel)
        record_rate_row.addWidget(self.recordRateCombo)
        record_rate_row.setStretch(0, 2)
        record_rate_row.setStretch(1, 2)
        self.ui.verticalLayout_9.addLayout(record_rate_row)
        self.pitchCount = 0
        self._resolution_base_text = "(0, 0) - "
        self._perf_ema_ms = {'detect': 0.0, 'analyze': 0.0, 'draw': 0.0, 'total': 0.0}
        self._perf_sample_count = 0
        self._perf_update_interval = 5
        self.tablet_capture_thread = None
        self.tablet_capture_latest_roi_frames = {}
        self.tablet_capture_latest_roi_frames_save = {}
        self.tablet_capture_latest_metrics = None
        self.tablet_source = "0"
        self.tablet_capture_roi = (772, 638, 150, 249)
        self.tablet_capture_roi_2 = (908, 281, 272, 336)
        self.tablet_capture_roi_3 = (507, 312, 270, 254)
        self.tablet_capture_every_n_frames = 5

        self.br_points = []
        self.ball_center = []
        self.ball_center_live = []
        self.show_br_points = False  # 是否顯示釋放點
        self.ROIidx = 0
        self.rapsodo_start_idx = 1
        self.rapsodo_end_idx = 0

        # 關鍵幀導出器
        self.initVideoVar()  

    def _set_resolution_text(self, base_text: str):
        self._resolution_base_text = base_text
        if hasattr(self.ui, 'ResolutionLabel') and self.ui.ResolutionLabel is not None:
            self.ui.ResolutionLabel.setText(base_text)

    def _update_perf_status(self, detect_ms: float, analyze_ms: float, draw_ms: float, total_ms: float, fps: int = 0):
        alpha = 0.2
        self._perf_ema_ms['detect'] = (1 - alpha) * self._perf_ema_ms['detect'] + alpha * detect_ms
        self._perf_ema_ms['analyze'] = (1 - alpha) * self._perf_ema_ms['analyze'] + alpha * analyze_ms
        self._perf_ema_ms['draw'] = (1 - alpha) * self._perf_ema_ms['draw'] + alpha * draw_ms
        self._perf_ema_ms['total'] = (1 - alpha) * self._perf_ema_ms['total'] + alpha * total_ms

        self._perf_sample_count += 1
        if self._perf_sample_count % self._perf_update_interval != 0:
            return

        fps_text = f" | inferFPS:{int(fps)}" if fps and fps > 0 else ""
        perf_text = (
            f" | perf(ms) d:{self._perf_ema_ms['detect']:.1f}"
            f" a:{self._perf_ema_ms['analyze']:.1f}"
            f" r:{self._perf_ema_ms['draw']:.1f}"
            f" t:{self._perf_ema_ms['total']:.1f}{fps_text}"
        )
        if hasattr(self.ui, 'ResolutionLabel') and self.ui.ResolutionLabel is not None:
            self.ui.ResolutionLabel.setText(f"{self._resolution_base_text}{perf_text}")

    def setFrameOffset(self, offset: int):
        """第二部影片的原始幀 = 回放幀 - offset（與 VideoLoader 一致）。"""
        self.frame_offset = offset
        self.video_loader.frame_offset = offset

        # 更新當前幀顯示
        current_frame = self.ui.frameSlider.value()
        print(f"✓ 幀偏移已設置為: {offset}；第二部影片幀 = 回放幀 - offset")

    def initVideoVar(self):
        self.is_play = False
        self.is_processed = False
        self.play_times = 1
        self.is_auto_recorded_video = False  # 標記是否為自動錄影產生的影片
        self._video_end_pending = False
        self._analysis_dirty = False
        self._analysis_finalized = False
        self._video_generation = 0
        # 階段檢測相關變量
        self.preparation_frame = None
        self.min_left_knee_y = np.inf
        self.prev_left_knee_y = None
        self.preparation_tracking_started = False
        self.candidate_preparation_frame = None
        self.preparation_descend_count = 0
        self.foot_contact_frame = None
        self.max_shoulder_angle_frame = None
        self.wrist_speed_frame = None
        self.release_view_rendered = False
        self.max_shoulder_angle_value = -np.inf
        self.max_elbow_angle_value = -np.inf
        self.max_wrist_speed_after_er = -np.inf
        pg.setConfigOptions(foreground=QColor(113,148,116), antialias = True)
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')

    def setupComponents(self): 
        self.camera = Camera()
        self.timer = QTimer()
        self.timer.timeout.connect(self.analyzeFrame)

        self.countdown_timer = None
        self.record_timer = None
        self.pose_estimater = PoseEstimater(self.model)
        self.pose_estimater_2 = PoseEstimater(self.model)
        self.pose_analyzer = PoseAnalyzer(self.pose_estimater)
        self.pose_analyzer_2 = PoseAnalyzer(self.pose_estimater_2)
        self.image_drawer = ImageDrawer(self.pose_estimater, self.pose_analyzer)
        self.image_drawer_2 = ImageDrawer(self.pose_estimater_2, self.pose_analyzer_2)
        self.video_loader = VideoLoader(self.image_drawer,self.image_drawer_2)
        
        # 投球動作分析器
        self.pitching_analyzer = PitchingAnalyzer()

        self.ui.videoTree.setSelectionMode(QAbstractItemView.MultiSelection)
        self.tree =  self.ui.videoTree
        
        # 建立投手選擇互斥按鈕組
        self.pitch_button_group = QButtonGroup()
        self.pitch_button_group.addButton(self.ui.pitchInput_L, 0)  # 左投
        self.pitch_button_group.addButton(self.ui.pitchInput, 1)    # 右投

        self.spin_direction_widget = SpinDirectionWidget()
        spin_layout = QVBoxLayout(self.ui.spinDirectionWidget)
        spin_layout.setContentsMargins(0, 0, 0, 0)
        spin_layout.addWidget(self.spin_direction_widget)

    def resizeEvent(self, event):
        new_size = event.size()
        print(f"PoseCameraTabControl resized to: {new_size.width()}x{new_size.height()}")
        # 在此執行你想要的操作
        if self.video_loader.video_name is not None:
            self.updateFrame(frame_num=self.ui.frameSlider.value())
        if self.video_loader.video_name is not None:
            if hasattr(self.ui, 'fcView') and self.ui.fcView.scene() is not None:
                self._display_keyframe_image(self.foot_contact_frame, self.ui.fcView, keyframe_type='foot_contact')
            if hasattr(self.ui, 'merView') and self.ui.merView.scene() is not None:
                self._display_keyframe_image(self.max_shoulder_angle_frame, self.ui.merView, keyframe_type='max_shoulder_er')
            if hasattr(self.ui, 'brView') and self.ui.brView.scene() is not None:
                self._display_keyframe_image(self.wrist_speed_frame, self.ui.brView, keyframe_type='release')
        super().resizeEvent(event)  
        # Child layouts receive their final sizes after this resize event.
        QTimer.singleShot(0, self._refit_video_views)

    def _refit_video_views(self):
        for view in (self.ui.FrameView, self.ui.FrameView_2, self.ui.fcView, self.ui.merView, self.ui.brView):
            if view.scene() is not None and view.scene().items():
                view.fitInView(view.scene().sceneRect(), Qt.KeepAspectRatio)

    def bindUI(self):
        """Bind UI element to their corresponding functions."""
        self.ui.footContact_ptn.setDisabled(True)
        self.ui.maxER_ptn.setDisabled(True)
        self.ui.release_ptn.setDisabled(True)

        bind_keyframe_navigation(self)
        self.ui.resetBRBtn.clicked.connect(self.resetBRPoints)

        self.ui.PitcherID.setText("01")  # 預設 PitcherID 為 01
        self.changePitcher()  # 立即應用預設值，無需按 OK
        self.ui.pitchInput.toggled.connect(self.changePitcher)
        self.ui.addPitcherButton.clicked.connect(self.onPitcherIDConfirmed)
        self.bindVideoUI()
        self.bindCheckBox()
        self.checkBox(visible=False)  # 初始隱藏分析相關的 checkbox
        self.recordRateCombo.currentIndexChanged.connect(self.onRecordRateChanged)
        self.ui.spinBox.valueChanged.connect(self.onRapsodoSpinChanged)
    
    def bindVideoUI(self):
        self.tree.itemSelectionChanged.connect(self.play_video_from_item)
        self.ui.slowMotionInput.currentIndexChanged.connect(self.updatePlaybackRate)
        self.ui.playBtn.clicked.connect(self.playBtnClicked)
        self.ui.playBtn.setFocusPolicy(Qt.NoFocus)
        self.reanalyze_button = QPushButton("重新分析", self)
        self.reanalyze_button.setToolTip("保留已載入骨架，重新偵測關鍵幀；播放完畢自動儲存 JSON。")
        self.reanalyze_button.clicked.connect(self.restart_video_analysis)
        self.ui.horizontalLayout_4.insertWidget(3, self.reanalyze_button)
        self.ui.backKeyBtn.clicked.connect(
            lambda: self.ui.frameSlider.setValue(self.ui.frameSlider.value() - 1)
        )
        self.ui.forwardKeyBtn.clicked.connect(
            lambda: self.ui.frameSlider.setValue(self.ui.frameSlider.value() + 1)
        )
        self.ui.frameSlider.valueChanged.connect(self.analyzeFrame)
        self.ui.frameSlider.valueChanged.connect(self.syncFullscreenSlider)

        self.ui.FrameView.mousePressEvent = self.mousePressEvent
        self.ui.FrameView_2.mousePressEvent = self.mousePressEvent

    def bindCheckBox(self):
        """Bind UI CheckBox to their corresponding functions."""
        self.ui.cameraCheckBox.stateChanged.connect(self.toggleCamera)
        self.ui.recordCheckBox.stateChanged.connect(self.toggleRecord)
        self.ui.selectCheckBox.stateChanged.connect(self.toggleSelect)
        self.ui.showSkeletonCheckBox.stateChanged.connect(self.toggleShowSkeleton)
        self.ui.selectKptCheckBox.stateChanged.connect(self.toggleKptSelect)
        self.ui.showBboxCheckBox.stateChanged.connect(self.toggleShowBbox)
        self.ui.showLineCheckBox.stateChanged.connect(self.toggleShowGrid)  
        self.ui.startPitchCheckBox.stateChanged.connect(self.togglePitching)
        self.ui.skeletonVideoCheckBox.stateChanged.connect(self.toggleSkeletonVideo)
        self.ui.showBR_checkBox.stateChanged.connect(self.toggleShowBRPoints)

    def onRecordRateChanged(self, index):
        full_rate = index == 1
        if (self.is_auto_recording or self.camera.is_auto_recording or
                (self.camera.video_writer is not None and self.camera.video_writer.is_writing)):
            # 錄影途中切換會讓前後幀來源不一致，先擋下
            self.recordRateCombo.blockSignals(True)
            self.recordRateCombo.setCurrentIndex(1 if self.camera.full_rate_recording else 0)
            self.recordRateCombo.blockSignals(False)
            print("[AutoRecord] 錄影中無法切換錄影幀率")
            return
        self.camera.set_full_rate_recording(full_rate)
        print(f"[AutoRecord] 錄影幀率切換為 {'179（全幀率）' if full_rate else '60'} FPS")

    def playBtnClicked(self):
        if self.single_video_scan_running:
            return
        if self.video_loader.video_name == "" or self.video_loader.video_name_2 == "":
            QMessageBox.warning(self, "無法播放影片", "請讀取影片!")
            return
        if self.video_loader.is_loading:
            QMessageBox.warning(self, "影片讀取中", "請稍等!")
            return
        if self.video_loader.total_frames is None or self.video_loader.total_frames <= 0:
            QMessageBox.warning(self, "無法播放影片", "影片尚未準備完成，請稍後再試!")
            return
        self.is_play = not self.is_play
        self.ui.playBtn.setText("||" if self.is_play else "▶︎")
        if self.is_play:
            # 開始播放時自動啟用骨架檢測
            if not self.single_video_mode and not self._analysis_finalized:
                self.ui.skeletonVideoCheckBox.setCheckState(2)
            self.playFrame(self.ui.frameSlider.value())
        else:
            if hasattr(self, "_play_timer") and self._play_timer.isActive():
                self._play_timer.stop()

    def eventFilter(self, obj, event):
        # Application-wide filters also see typing in other tabs and dialogs.
        focus = QApplication.focusWidget()
        editing = isinstance(focus, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox))
        if (event.type() == QEvent.KeyPress and self.isVisible()
                and QApplication.activeWindow() == self.window() and not editing):
            handler = self._global_key_handlers.get(event.key())
            if handler is not None:
                handler()
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        super().keyPressEvent(event)

    def playFrame(self, start_num:int=0):
        """Start playing frames using a timer instead of blocking calls."""
        if self.video_loader.total_frames is None or self.video_loader.total_frames <= 0:
            return
        if not hasattr(self, "_play_timer"):
            self._play_timer = QTimer()
            self._play_timer.timeout.connect(self._playNextFrame)

        self._current_play_frame = start_num
        self._play_timer.start(self._get_play_interval_ms())

    def _playNextFrame(self):
        """Internal method to play the next frame."""
        total_frames = self.video_loader.total_frames if self.video_loader.total_frames is not None else 0
        if not self.is_play or total_frames <= 0 or self._current_play_frame >= total_frames:
            if hasattr(self, "_play_timer"):
                self._play_timer.stop()
            if self.is_play and not self._video_end_pending:
                self.playBtnClicked()
            return

        if self.ui.frameSlider.value() == self._current_play_frame:
            self.analyzeFrame()  # setValue(0) emits nothing when already at 0.
        else:
            self.ui.frameSlider.setValue(self._current_play_frame)
        self._current_play_frame += 1

    def _get_play_interval_ms(self) -> int:
        """Compute playback interval based on video fps and UI playback rate."""
        base_fps = self.video_loader.video_fps if self.video_loader.video_fps else 60
        try:
            rate = float(self.ui.slowMotionInput.currentText())
        except Exception:
            rate = 1.0
        rate = max(rate, 0.01)
        interval_ms = int(1000 / (base_fps * rate))
        return max(interval_ms, 1)

    def videoSilder(self, visible:bool):
        elements = [
            self.ui.backKeyBtn,
            self.ui.playBtn,
            self.ui.forwardKeyBtn,
            self.ui.frameSlider,
            self.ui.frameNumLabel
        ]
        elements.append(self.reanalyze_button)
        
        for element in elements:
            element.setVisible(visible)

    def checkBox(self, visible:bool):
        elements = [
            # self.ui.showSkeletonCheckBox,
            # self.ui.showBboxCheckBox,

            self.ui.selectCheckBox,
            self.ui.selectKptCheckBox,
            self.ui.skeletonVideoCheckBox,
            self.ui.recordCheckBox,
        ]
        
        for element in elements:
            element.setVisible(visible)

    def groupBox(self, visible:bool):
        elements = [
            self.ui.groupBox
        ]
        
        for element in elements:
            element.setVisible(visible)

    def changePitcher(self):
        """Change the pitcher based on input value. 9: "左腕", 10: "右腕","""
        kpt_id = 10 if self.ui.pitchInput.isChecked() else 9
        self.pose_estimater_2.setKptId(kpt_id)
        self.pose_estimater_2.setPitchHandId(kpt_id)
        # 重置投球計數
        self.pitchCount = 0
        self.ui.PitchCountLabel.setText(f"{self.pitchCount}")

    def onPitcherIDConfirmed(self):
        """當按下 PitcherID 的 OK 按鈕時觸發"""
        pitcher_id = self.ui.PitcherID.text().strip()
        if not pitcher_id:
            QMessageBox.warning(self, "投手背號為空", "請輸入投手背號後再按 OK")
            return
        self.changePitcher()

    def toggleCamera(self, state:int):
        """Toggle the camera on/off based on checkbox state."""
        if state == 2:
            self.reset()
            self.ui.selectCheckBox.setCheckState(0)
            self.ui.showSkeletonCheckBox.setCheckState(0)
            self.ui.skeletonVideoCheckBox.setCheckState(0)
            self.ui.selectKptCheckBox.setCheckState(0)
            self.is_video = False
            frame_width, frame_height, fps = self.camera.toggleCamera(True)
            self.camera_fps = fps if fps else 60
            self.model.setImageSize((frame_width, frame_height))
            self._set_resolution_text(f"(0, 0) - ({frame_width} x {frame_height}), FPS: {fps}")
            timer_interval_ms = max(10, int(1000 / max(1, self.camera_fps)))
            self.timer.start(timer_interval_ms)
            self.videoSilder(visible=False)
        else:
            if self.camera is not None:
                self.camera.toggleCamera(False)
            if self.timer is not None:
                self.timer.stop()

            self.view_scene.clear()
            self.view_scene_2.clear()
            self.ui.selectCheckBox.setCheckState(0)
            self.ui.showSkeletonCheckBox.setCheckState(0)
            self.ui.skeletonVideoCheckBox.setCheckState(0)
            self.is_video = True
            self.videoSilder(visible=True)
            
            # 清理 GPU 記憶體
            if self.model is not None:
                self.model.clear_gpu_cache()

    def togglePitching(self, state):
        if state == 2:
            if not self.ui.cameraCheckBox.isChecked():
                self.ui.startPitchCheckBox.setCheckState(0)
                QMessageBox.warning(self, "無法開始投球模式", "請先開啟相機")
                return
            if not self.ui.showSkeletonCheckBox.isChecked():
                self.ui.showSkeletonCheckBox.setCheckState(2)
             
            self.is_pitching = True
            self.ui.StateLabel.setText("開始投球!")
            self.ui.StateLabel.setStyleSheet("color: white;background-color: red; font-weight: bold;")
        else:
            self.is_pitching = False
            self.ui.StateLabel.setText(" ")
            self.ui.StateLabel.setStyleSheet("color: black;")

    def toggleRecord(self, state:int):
        """Start or stop video recording."""
        if state == 2:
            self.startRecording()
            print("record!!")
        else:
            if self.camera is None:
                return
            print("stop record!!")
            self.camera.stop_recording()
            self.load_video_list(str(DB_DIR / "Record"))

    def saveCalibrationSnapshot(self):
        """把目前的校正值存進這筆錄影的資料夾，3D 分頁日後依此重建。"""
        thread = getattr(self.camera, 'video_thread', None)
        front_sn = getattr(thread, 'serial_front', None)
        side_sn = getattr(thread, 'serial_side', None)
        try:
            ok, msg = calib_store.save_snapshot(self.output_dir, front_sn, side_sn)
        except Exception as e:
            ok, msg = False, str(e)
        print(f"[Calibration] {msg}" if ok else f"[Calibration] 未存校正快照：{msg}")

    def startRecording(self):
        """Start recording the video."""
        if self.camera is None:
            self.ui.recordCheckBox.setCheckState(0)
            return
        pitcher_id = self.ui.PitcherID.text().strip() or "Unknown"
        pitch_no = self.pitchCount + 1
        current_date = datetime.now().strftime("%Y%m%d")
        current_time = datetime.now().strftime("%Y%m%d_%H%M")
        self.output_dir = f'{DB_DIR.as_posix()}/Record/{current_date}_Pitcher{pitcher_id}/{current_time}_P{pitch_no:02d}'
        os.makedirs(self.output_dir, exist_ok=True)
        self.saveCalibrationSnapshot()
        self.video_filename = os.path.join(self.output_dir, f'CF_{current_time}_Pitcher{pitcher_id}_P{pitch_no:02d}.mp4')
        self.video_filename_2 = os.path.join(self.output_dir, f'CS_{current_time}_Pitcher{pitcher_id}_P{pitch_no:02d}.mp4')
        self.camera.startRecording(self.video_filename, self.video_filename_2)

    def startTabletCapture(
        self,
        output_dir: str = None,
        prefix: str = "tablet",
        enable_ocr: bool = False,
        ocr_json_output: str = None,
        ocr_player: str = "1",
        ocr_sample_every_n_frames: int = 10,
    ):
        """啟動平板畫面擷取背景線程。"""
        if self._isTabletCaptureRunning():
            return False

        self.tablet_capture_latest_roi_frames = {}
        self.tablet_capture_latest_roi_frames_save = {}

        output_dir = output_dir or str(DB_DIR / "Record/TabletCapture")
        self.tablet_capture_thread = TabletImageCaptureThread(
            output_dir=output_dir,
            prefix=prefix,
            enable_ocr=enable_ocr,
            ocr_json_output=ocr_json_output,
            ocr_player=ocr_player,
            ocr_sample_every_n_frames=ocr_sample_every_n_frames,
        )
        self.tablet_capture_thread.image_saved.connect(lambda p: print(f"[TabletCapture] saved: {p}"))
        self.tablet_capture_thread.roi_frame_ready.connect(self.onTabletCaptureRoiFrame)
        self.tablet_capture_thread.ocr_data_ready.connect(self.onTabletCaptureMetrics)
        self.tablet_capture_thread.error.connect(lambda e: print(f"[TabletCapture] error: {e}"))
        self.tablet_capture_thread.start()

        source_rois = [self.tablet_capture_roi]
        if self.tablet_capture_roi_2 is not None and self.tablet_capture_roi_3 is not None:
            source_rois.append(self.tablet_capture_roi_2)
            source_rois.append(self.tablet_capture_roi_3)
        source_roi_arg = source_rois[0] if len(source_rois) == 1 else source_rois
        self.tablet_capture_thread.configure_source(
            source=self.tablet_source,
            roi=source_roi_arg,
            capture_every_n_frames=self.tablet_capture_every_n_frames,
        )
        print(f"[TabletCapture] started: {output_dir}, OCR={'ON' if enable_ocr else 'OFF'}")
        return True

    def stopTabletCapture(self):
        """停止平板畫面擷取背景線程。"""
        if not self.show_br_points:
            self.ui.showBR_checkBox.setCheckState(2)
        thread = self.tablet_capture_thread
        if thread is None:
            return
        thread.stop()
        self.tablet_capture_thread = None
        print("[TabletCapture] stopped")

    def onTabletCaptureRoiFrame(self, roi_frame):
        """接收背景擷取的 ROI 影像。"""
        if roi_frame is None:
            if hasattr(self.ui, 'RoiLabel') and self.ui.RoiLabel is not None:
                self.ui.RoiLabel.clear()
            return

        roi_index = 0
        frame = roi_frame
        frame_for_save = None
        if isinstance(roi_frame, dict):
            roi_index = int(roi_frame.get('roi_index', 0))
            frame = roi_frame.get('frame')
            if frame is not None:
                frame_for_save = frame.copy()
            if roi_index == 0 and self.is_auto_recorded_video:
                ball_center = roi_frame.get('ball_center')
                if frame is not None:
                    if ball_center is not None:
                        try:
                            cx, cy = int(ball_center[0]), int(ball_center[1])
                            self.ball_center_live.append((cx, cy))
                        except (TypeError, ValueError, IndexError):
                            pass

                    for cx, cy in self.ball_center_live[:-1]:
                        if 0 <= cx < frame.shape[1] and 0 <= cy < frame.shape[0]:
                            # cv2.circle(frame, (cx, cy), 6, (128, 128, 128), -1)
                            cv2.circle(frame, (cx, cy), 6, (0, 255, 0), 2)
            elif roi_index == 0 and not self.is_auto_recorded_video and self.ball_center is not None:
                for cx, cy in self.ball_center[self.ROIidx:-1]:
                    if frame is not None and 0 <= cx < frame.shape[1] and 0 <= cy < frame.shape[0]:
                        cv2.circle(frame, (cx, cy), 6, (0, 0, 255), 2)
                        # cv2.circle(frame, (cx, cy), 6, (0, 255, 0), 2)
                cx, cy = self.ball_center[-1]
                if frame is not None and 0 <= cx < frame.shape[1] and 0 <= cy < frame.shape[0]:
                    cv2.circle(frame, (cx, cy), 8, (255, 0, 0), 2)
        elif frame is not None:
            frame_for_save = frame.copy()

        if frame is None:
            return

        if frame_for_save is None:
            frame_for_save = frame.copy()

        self.tablet_capture_latest_roi_frames[roi_index] = frame
        self.tablet_capture_latest_roi_frames_save[roi_index] = frame_for_save

        if not hasattr(self.ui, 'RoiLabel') or self.ui.RoiLabel is None:
            return

        preview = self._build_tablet_roi_preview()
        if preview is None:
            return

        preview = np.ascontiguousarray(preview)
        h, w = preview.shape[:2]
        bytes_per_line = preview.strides[0]
        qimg = QImage(preview.data, w, h, bytes_per_line, QImage.Format_RGB888).rgbSwapped().copy()
        pixmap = QPixmap.fromImage(qimg)
        self.ui.RoiLabel.setPixmap(
            pixmap.scaled(self.ui.RoiLabel.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        self.ui.RoiLabel.setAlignment(Qt.AlignCenter)

    def _build_tablet_roi_preview(self):
        if not self.tablet_capture_latest_roi_frames:
            return None

        indexes = sorted(self.tablet_capture_latest_roi_frames.keys())
        frames = [self.tablet_capture_latest_roi_frames[i] for i in indexes if self.tablet_capture_latest_roi_frames[i] is not None]
        if not frames:
            return None
        if len(frames) == 1:
            return frames[0]

        max_h = max(frame.shape[0] for frame in frames)
        padded_frames = []
        for frame in frames:
            h, _ = frame.shape[:2]
            if h < max_h:
                top = (max_h - h) // 2
                bottom = max_h - h - top
                frame = cv2.copyMakeBorder(frame, top, bottom, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
            padded_frames.append(frame)

        gap = np.full((max_h, 6, 3), 80, dtype=np.uint8)
        preview = padded_frames[0]
        for frame in padded_frames[1:]:
            preview = np.hstack((preview, gap, frame))
        return preview

    def onRapsodoSpinChanged(self, selected_pitch_idx: int):
        """依 spinBox 的球次更新回放 ROI：顯示第 N 球到 current_idx。
        
        - is_auto_recorded_video=True 時：使用實時 ROI 帧（onTabletCaptureRoiFrame）
        - is_auto_recorded_video=False 時：從 JSON 加載 ROI 圖像並繪製軌跡點（display_roi_from_json）
        """
        if self.rapsodo_end_idx < self.rapsodo_start_idx:
            self.ROIidx = 0
            return

        selected_pitch_idx = max(self.rapsodo_start_idx, min(selected_pitch_idx, self.rapsodo_end_idx))
        # 轉成 ball_center 的起始索引（0-based）
        self.ROIidx = max(0, min(selected_pitch_idx - self.rapsodo_start_idx, len(self.ball_center)))

        # 實時錄制模式：使用平板實時 ROI 帧
        if self.is_auto_recorded_video:
            frame0 = self.tablet_capture_latest_roi_frames_save.get(0)
            if frame0 is not None:
                self.onTabletCaptureRoiFrame({'roi_index': 0, 'frame': frame0.copy()})
        else:
            # 回放模式：從 JSON 加載 ROI 圖像和軌跡點
            try:
                if not self.video_loader.video_name:
                    return
                # 構建 JSON 路徑（與 play_video_from_item 一致）
                rapsodo_json_path = os.path.join(
                    self.video_loader.folder_path, 
                    f"{os.path.splitext(os.path.basename(self.video_loader.video_name))[0]}_BRandRapsodo.json"
                )
                if os.path.exists(rapsodo_json_path):
                    # 調用 display_roi_from_json 顯示 JSON 中的 ROI 圖像和軌跡點
                    self.display_roi_from_json(
                        rapsodo_json_path, 
                        self.ui.RoiLabel, 
                        self.ui.RapsodoDataLabel, 
                        self.spin_direction_widget
                    )
                else:
                    print(f"[onRapsodoSpinChanged] JSON not found: {rapsodo_json_path}")
            except Exception as e:
                print(f"[onRapsodoSpinChanged] Error loading ROI from JSON: {e}")

    def onTabletCaptureMetrics(self, metrics: dict):
        """接收背景 OCR 數據。"""
        self.tablet_capture_latest_metrics = metrics
        velocity = metrics.get('velocity_mph')
        spin = metrics.get('total_spin_rpm')
        direction = metrics.get('spin_direction')
        efficiency = metrics.get('spin_efficiency_pct')
        frame_index = metrics.get('frame_index')
        if hasattr(self, 'spin_direction_widget') and self.spin_direction_widget is not None:
            self.spin_direction_widget.setDirection(direction)

        velocity_text = f"{velocity:.1f}" if isinstance(velocity, (int, float)) else "N/A"
        spin_text = f"{int(spin)}" if isinstance(spin, (int, float)) else "N/A"
        efficiency_text = f"{efficiency:.1f}" if isinstance(efficiency, (int, float)) else "N/A"
        self.ui.RapsodoDataLabel.setText(
            f"球速:{velocity_text} kph\n轉速:{spin_text} rpm\n旋轉效率:{efficiency_text}%"
            # f"球速:{velocity*1.60934:.1f} kph\n轉速:{spin} rpm"
            # f"球速:{velocity} kph\n轉速:{spin} rpm\n垂直偏移:{metrics.get('vB')} inches\n水平偏移:{metrics.get('hB')} inches"
        )
        # print(f"[TabletCapture][OCR] {metrics}")

    def _isTabletCaptureRunning(self) -> bool:
        return self.tablet_capture_thread is not None and self.tablet_capture_thread.isRunning()

    def toggleSelect(self, state:int):
        """Select a person based on checkbox state."""
        if state == 2:
            if not self.ui.showSkeletonCheckBox.isChecked():
                self.ui.selectCheckBox.blockSignals(True)
                self.ui.selectCheckBox.setCheckState(0)
                self.ui.selectCheckBox.blockSignals(False)
                QMessageBox.warning(self, "無法選擇人", "請選擇顯示人體骨架!")
                return
            self.person_selector = PersonSelector()
            self.person_selector_2 = PersonSelector()
            frame_num = self.ui.frameSlider.value() if self.is_video else None
            search_person_df = self.pose_estimater.getPersonDf(frame_num=frame_num) if frame_num is not None else self.pose_estimater.pre_person_df
            search_person_df_2 = self.pose_estimater_2.getPersonDf(frame_num=frame_num) if frame_num is not None else self.pose_estimater_2.pre_person_df
            self.person_selector.select(search_person_df = search_person_df)
            self.person_selector_2.select(search_person_df = search_person_df_2)
            self.pose_estimater.setPersonId(self.person_selector.selected_id)
            self.pose_estimater_2.setPersonId(self.person_selector_2.selected_id)
        else:
            self.pose_estimater.setPersonId(None)
            self.pose_estimater_2.setPersonId(None)
            self.person_selector = None
            self.person_selector_2 = None

    def toggleKptSelect(self, state:int):
        """Toggle keypoint selection and trajectory visualization."""  
        if state ==2:
            if not self.ui.selectCheckBox.isChecked():
                self.ui.selectKptCheckBox.blockSignals(True)
                self.ui.selectKptCheckBox.setCheckState(0)
                self.ui.selectKptCheckBox.blockSignals(False)
                QMessageBox.warning(self, "無法選擇關節點", "請選擇人!")
                return
            self.kpt_selector = KptSelector()
            self.pose_estimater.setKptId(10)
            self.pose_estimater_2.setKptId(10)
            self.image_drawer.setShowTraj(True)
            self.image_drawer_2.setShowTraj(True)
        else:
            self.kpt_selector = None
            self.pose_estimater.setKptId(None)
            self.pose_estimater_2.setKptId(None)
            self.pose_estimater.clearKptBuffer()
            self.pose_estimater_2.clearKptBuffer()
            self.image_drawer.setShowTraj(False)
            self.image_drawer_2.setShowTraj(False)

    def toggleShowSkeleton(self, state:int):
        """Toggle skeleton detection and FPS control."""
        print("skeleton state: "+ str(state))
        if state == 2 and self.ui.recordCheckBox.isChecked():
            self.ui.showSkeletonCheckBox.blockSignals(True)
            self.ui.showSkeletonCheckBox.setCheckState(0)
            self.ui.showSkeletonCheckBox.blockSignals(False)
            QMessageBox.warning(self, "無法顯示人體骨架!", "正在錄影中!")
            return
        is_checked = state == 2
        if not is_checked:
            self.ui.selectKptCheckBox.blockSignals(True)
            self.ui.selectKptCheckBox.setCheckState(0)
            self.ui.selectKptCheckBox.blockSignals(False)
            self.ui.selectCheckBox.blockSignals(True)
            self.ui.selectCheckBox.setCheckState(0)
            self.ui.selectCheckBox.blockSignals(False)
            
        self.pose_estimater.setDetect(is_checked)
        self.pose_estimater_2.setDetect(is_checked)
        self.image_drawer.setShowSkeleton(is_checked)
        self.image_drawer_2.setShowSkeleton(is_checked)
        # if self.camera is not None and not self.is_video:
        #     self.camera.setFPSControl(30 if is_checked else 15)

    def toggleSkeletonVideo(self, state:int):
        if state==2:
            # Loading a video initializes state once. Replaying/enabling the
            # checkbox must not reload/reset MER, BR or recursively start play.
            self.pose_estimater.setDetect(True)
            self.pose_estimater_2.setDetect(True)
            self.image_drawer.setShowSkeleton(True)
            self.image_drawer_2.setShowSkeleton(True)
            self.ui.showSkeletonCheckBox.blockSignals(True)
            self.ui.showSkeletonCheckBox.setCheckState(2)
            self.ui.showSkeletonCheckBox.blockSignals(False)
            if self.single_video_mode and self.pose_estimater_2.person_df.empty:
                self.scan_single_video_keyframes()
        else:
            self.pose_estimater.setDetect(False)
            self.pose_estimater_2.setDetect(False)

    def toggleShowBbox(self, state:int):
        """Toggle bounding box visibility."""
        self.image_drawer.setShowBbox(state == 2)
        self.image_drawer_2.setShowBbox(state == 2)

    def toggleShowGrid(self, state:int):
        """Toggle gridline visibility."""
        self.image_drawer.setShowGrid(state == 2)
        self.image_drawer_2.setShowGrid(state == 2)

    def _get_frame_analysis_data(self, frame_num: int) -> dict:
        """獲取當前幀的所有分析數據
        
        Args:
            frame_num: 幀號
            
        Returns:
            包含所有速度和角度數據的字典
        """
        # 性能優化：使用緩存避免重複查詢
        if hasattr(self, '_analysis_data_cache') and frame_num in self._analysis_data_cache:
            return self._analysis_data_cache[frame_num]
        
        frame_data = {}
        
        # 從 DataFrame 直接提取該幀的數據
        if self.pose_analyzer_2.analyze_df.empty:
            return frame_data
        
        row = self.pose_analyzer_2.analyze_df[self.pose_analyzer_2.analyze_df['frame_number'] == frame_num]
        if row.empty:
            return frame_data
        
        # 提取 2D 速度（保留 NaN，避免不穩定點被誤判為 0.0）
        frame_data['ankle_speed_2d'] = float(row['ankle_speed_2d'].iloc[0]) if not pd.isna(row['ankle_speed_2d'].iloc[0]) else float('nan')
        frame_data['wrist_speed_2d'] = float(row['wrist_speed_2d'].iloc[0]) if not pd.isna(row['wrist_speed_2d'].iloc[0]) else float('nan')
        frame_data['elbow_angle_2d'] = float(row['elbow_angle_2d'].iloc[0]) if not pd.isna(row['elbow_angle_2d'].iloc[0]) else float('nan')

        # 獲取右肩外旋角度
        angle_data = row['angle'].iloc[0]
        if "右肩外旋" in angle_data:
            frame_data['right_shoulder_external_rotation_angle'] = angle_data["右肩外旋"][0]
        else:
            frame_data['right_shoulder_external_rotation_angle'] = float('nan')

        landed_data = row['left_ankle_landed'].iloc[0]
        frame_data['left_ankle_landed'] = landed_data
        # 緩存結果（限制缓存大小以防止内存持续增长）
        if not hasattr(self, '_analysis_data_cache'):
            self._analysis_data_cache = {}
        
        self._analysis_data_cache[frame_num] = frame_data
        
        # 限制缓存只保留最近的50帧数据，防止无限增长
        if len(self._analysis_data_cache) > 50:
            oldest_key = min(self._analysis_data_cache.keys())
            del self._analysis_data_cache[oldest_key]
        
        return frame_data
    
    def _display_keyframe_image(self, frame_num: int, label_widget, keyframe_type: str = None):
        """擷取指定幀的圖像並顯示在指定的 QLabel/QGraphicsView 上
        
        Args:
            frame_num: 幀號
            label_widget: 顯示圖像的 widget (QLabel 或 QGraphicsView)
            keyframe_type: 關鍵幀類型
                - 'foot_contact': 腳部接觸幀
                - 'max_shoulder_er': 最大肩外旋幀
                - 'release': 釋放點幀
                - None: 不繪製分析信息
        """
        if (frame_num is None or not isinstance(frame_num, (int, np.integer))
                or not 0 <= frame_num < int(self.video_loader.total_frames or 0)):
            return False
        _ , frame_2 = self.video_loader.getVideoImage(frame_num)
        if frame_2 is None or frame_2.size == 0:
            return False
        drawed_img = frame_2.copy()
        
        # 如果指定了 keyframe_type，繪製相應的分析信息
        if keyframe_type is not None and hasattr(self.image_drawer_2, 'drawAngleInfo'):
            try:
                overlay = self.image_drawer_2.drawAngleInfo(frame_2.copy(), frame_num, keyframe_type=keyframe_type, start_frame=self.preparation_frame if self.preparation_frame is not None else 0)
                if overlay is not None and overlay.size:
                    drawed_img = overlay
            except Exception as exc:
                print(f"[Keyframe] Overlay failed at {frame_num}; showing original frame: {exc}")
        
        # 轉換為 QPixmap
        drawed_img = np.ascontiguousarray(drawed_img)
        h, w = drawed_img.shape[:2]
        bytesPerline = drawed_img.strides[0]
        qImg = QImage(drawed_img.data, w, h, bytesPerline, QImage.Format_RGB888).rgbSwapped().copy()
        pixmap = QPixmap.fromImage(qImg)
        
        # 根據 widget 類型显示
        if isinstance(label_widget, QLabel):
            # 縮放以適應 label 尺寸
            scaled_pixmap = pixmap.scaled(label_widget.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            label_widget.setPixmap(scaled_pixmap)
            label_widget.setScaledContents(False)
            label_widget.setAlignment(Qt.AlignCenter)
        elif isinstance(label_widget, QGraphicsView):
            # 使用 QGraphicsScene 显示
            scene = label_widget.scene()
            if scene is None:
                scene = QGraphicsScene(label_widget)
                label_widget.setScene(scene)
            else:
                scene.clear()
            scene.addPixmap(pixmap)
            scene.setSceneRect(pixmap.rect().x(), pixmap.rect().y(), pixmap.width(), pixmap.height())
            label_widget.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)
        return True

    def display_roi_from_json(self, json_path, label_widget: QLabel, text_label: QLabel = None, direction_widget: SpinDirectionWidget = None):
        # 1. 讀取 JSON 檔案
        try:
            data = read_optional_json(json_path)
        except (OSError, ValueError) as exc:
            print(f"[Replay] Cannot read optional ROI JSON: {exc}")
            return False
        if not isinstance(data, dict):
            return False
        
        # 2. 取得 Base64 字串 (假設我們要看 roi_frame_1)
        base64_str = data.get("roi_frame_1")
        base64_str_2 = data.get("roi_frame_2")
        base64_str_3 = data.get("roi_frame_3")
        if not base64_str or not base64_str_2:
            print("找不到影像數據")
            return False

        # 3. 將 Base64 解碼回二進位 Bytes
        img_bytes = base64.b64decode(base64_str)
        img_bytes_2 = base64.b64decode(base64_str_2)
        img_bytes_3 = base64.b64decode(base64_str_3) if base64_str_3 else None

        # 4. 使用 QImage 直接讀取 Bytes
        # QImage.fromData 會自動偵測格式 (你在 export 時用的是 .png)
        q_img = QImage.fromData(img_bytes)
        q_img_2 = QImage.fromData(img_bytes_2)
        q_img_3 = QImage.fromData(img_bytes_3) if img_bytes_3 is not None else None

        def _normalize_points(points):
            if not points:
                return []
            if len(points) >= 2 and isinstance(points[0], (int, float)) and isinstance(points[1], (int, float)):
                return [points]
            return points

        # 5. 轉換為影像陣列後，補齊高度再用 np.hstack 串成預覽圖
        def _qimage_to_rgb_array(qimg: QImage) -> np.ndarray:
            qimg_rgb = qimg.convertToFormat(QImage.Format_BGR888)
            width = qimg_rgb.width()
            height = qimg_rgb.height()
            bytes_per_line = qimg_rgb.bytesPerLine()
            ptr = qimg_rgb.bits()
            ptr.setsize(qimg_rgb.byteCount())
            arr = np.frombuffer(ptr, np.uint8).reshape((height, bytes_per_line))[:, :width * 3]
            return arr.reshape((height, width, 3)).copy()

        frames = [
            _qimage_to_rgb_array(q_img),
            _qimage_to_rgb_array(q_img_2),
        ]
        if q_img_3 is not None and not q_img_3.isNull():
            frames.append(_qimage_to_rgb_array(q_img_3))

        ball_centers = getattr(self, 'ball_center', None) or _normalize_points(data.get('ball_center'))
        if ball_centers and frames:
            first_frame = frames[0]
            try:
                pts = list(ball_centers[self.ROIidx:-1])
            except Exception:
                pts = list(ball_centers)

            for pt in pts:
                try:
                    cx, cy = int(pt[0]), int(pt[1])
                except Exception:
                    continue
                if 0 <= cx < first_frame.shape[1] and 0 <= cy < first_frame.shape[0]:
                    cv2.circle(first_frame, (cx, cy), 6, (0, 0, 255), 2)

            try:
                lx, ly = int(ball_centers[-1][0]), int(ball_centers[-1][1])
                if 0 <= lx < first_frame.shape[1] and 0 <= ly < first_frame.shape[0]:
                    cv2.circle(first_frame, (lx, ly), 8, (255, 0, 0), 1)
            except Exception:
                pass

        max_h = max(frame.shape[0] for frame in frames)
        padded_frames = []
        for frame in frames:
            h, _ = frame.shape[:2]
            if h < max_h:
                top = (max_h - h) // 2
                bottom = max_h - h - top
                frame = cv2.copyMakeBorder(frame, top, bottom, 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
            padded_frames.append(frame)

        preview = padded_frames[0]
        for frame in padded_frames[1:]:
            preview = np.hstack((preview, frame))

        preview = np.ascontiguousarray(preview)
        h, w = preview.shape[:2]
        bytesPerline = preview.strides[0]
        qImg = QImage(preview.data, w, h, bytesPerline, QImage.Format_RGB888).rgbSwapped().copy()
        pixmap = QPixmap.fromImage(qImg)

        label_widget.setPixmap(pixmap)
        label_widget.setScaledContents(True) # 讓圖片自動適應 Label 大小

        metrics = data.get("metrics", {})
        if text_label:
            velocity = metrics.get('velocity_mph')
            spin = metrics.get('total_spin_rpm')
            efficiency = metrics.get('spin_efficiency_pct')
            velocity_text = f"{velocity:.1f}" if isinstance(velocity, (int, float)) else "N/A"
            spin_text = f"{int(spin)}" if isinstance(spin, (int, float)) else "N/A"
            efficiency_text = f"{efficiency:.1f}" if isinstance(efficiency, (int, float)) else "N/A"
            text_label.setText(f"球速:{velocity_text} kph\n轉速:{spin_text} rpm\n旋轉效率:{efficiency_text}%")
            if direction_widget:
                direction_widget.setDirection(metrics.get('spin_direction', 'N/A'))

    def phaseDetection(self, frame_num: int):
        """階段檢測主方法
        
        執行順序：
        1. 獲取當前幀的分析數據
        2. 依序檢測各個投球階段
   """
        if frame_num <= getattr(self, '_last_phase_frame', -1):
            return
        self._last_phase_frame = frame_num
        result = detect_pitch_events(self.pose_estimater_2.person_df,
            self.video_loader.video_fps, 'right' if self.ui.pitchInput.isChecked() else 'left',
            through_frame=frame_num, person_id=self.pose_estimater_2.person_id)
        previous = (self.foot_contact_frame, self.max_shoulder_angle_frame, self.wrist_speed_frame)
        self.preparation_frame, self.foot_contact_frame = result.preparation, result.fc
        self.max_shoulder_angle_frame, self.wrist_speed_frame = result.mer, result.br
        self.mer_locked, self.release_locked = result.mer_confirmed, result.br_confirmed
        if previous != (result.fc, result.mer, result.br):
            self.max_shoulder_angle_value = -np.inf
            self.max_wrist_speed_after_er = -np.inf
            self.release_view_rendered = False
            self._render_keyframe_results()
        elif result.br is not None and frame_num == result.br + 7:
            self._render_keyframe_results()

        # 設置顏色區段（僅加入有效區間）
        segments = []
        if self.preparation_frame is not None and self.foot_contact_frame is not None:
            segments.append((self.preparation_frame, self.foot_contact_frame, QColor("#1C7CDB")))
        if self.foot_contact_frame is not None and self.max_shoulder_angle_frame is not None:
            segments.append((self.foot_contact_frame, self.max_shoulder_angle_frame, QColor("#C90E0E")))
        if self.max_shoulder_angle_frame is not None and self.wrist_speed_frame is not None:
            segments.append((self.max_shoulder_angle_frame, self.wrist_speed_frame, QColor("#F2A900")))
        if self.wrist_speed_frame is not None:
            segments.append((self.wrist_speed_frame, self.wrist_speed_frame + 7, QColor("#00AC2D")))
        
        # 更新 Slider 顏色覆蓋層
        overlay_fps = self.video_loader.video_fps if self.video_loader.video_fps else self.camera_fps
        if self.overlay is None:
            self.overlay = SliderColorOverlay(self.ui.frameSlider, segments, fps=overlay_fps)
        else:
            self.overlay.fps = overlay_fps
            self.overlay.updateSegments(segments)

    def resetPhaseDetection(self):
        """重置階段檢測相關變量"""
        self.preparation_frame = None
        self.min_left_knee_y = np.inf
        self.prev_left_knee_y = None
        self.preparation_tracking_started = False
        self.candidate_preparation_frame = None
        self.preparation_descend_count = 0
        self.foot_contact_frame = None
        self.max_shoulder_angle_frame = None
        self.wrist_speed_frame = None
        self.release_view_rendered = False
        self.max_shoulder_angle_value = -np.inf
        self.max_wrist_speed_after_er = -np.inf
        self.mer_locked = False
        self.release_locked = False
        self._last_phase_frame = -1
        # 清除分析數據緩存
        if hasattr(self, '_analysis_data_cache'):
            self._analysis_data_cache.clear()

    def analyzeFrame(self):
        """Analyze and process each frame from the camera or video"""
        if self.single_video_scan_running:
            return
        fps = 0
        detect_ms = 0.0
        analyze_ms = 0.0
        draw_ms = 0.0
        total_start = time.perf_counter()
        if self.is_video:
            frame_num = self.ui.frameSlider.value()
            video_fps = self.video_loader.video_fps if self.video_loader.video_fps else 60

            if self.preparation_frame is not None and self.wrist_speed_frame is not None:
                start_frame = self.preparation_frame
                end_frame = self.wrist_speed_frame + 7
                total_seconds = max(0.0, (end_frame - start_frame) / video_fps)
                self.ui.frameNumLabel.setText(f'{frame_num}/{self.video_loader.total_frames - 1} ({total_seconds :.2f}s)')
            else:
                total_seconds = self.video_loader.total_frames / video_fps
                self.ui.frameNumLabel.setText(f'{frame_num}/{self.video_loader.total_frames - 1}')

            frame, frame_2 = self.video_loader.getVideoImage(frame_num)
            if self.ui.skeletonVideoCheckBox.isChecked():
                t_detect = time.perf_counter()
                # Fill missing front poses so both views can replay skeletons.
                if not self.single_video_mode and frame_num not in self.pose_estimater.processed_frames:
                    self.pose_estimater.detectKpt(frame, frame_num, is_video=True)
                _, _, fps = self.pose_estimater_2.detectKpt(frame_2, frame_num, is_video=True)
                detect_ms = (time.perf_counter() - t_detect) * 1000.0
                # 只對主視角（側面）進行分析
                person_df_L = self.pose_estimater_2.getPersonDf(frame_num=frame_num)
                if person_df_L is not None and not person_df_L.empty:
                    t_analyze = time.perf_counter()
                    if frame_num not in self.pose_analyzer_2.processed_frames:
                        self._analysis_dirty = True
                        self._analysis_finalized = False
                    self.pose_analyzer_2.addAnalyzeInfo(frame_num)
                    # 執行階段檢測
                    self.phaseDetection(frame_num)
                    analyze_ms = (time.perf_counter() - t_analyze) * 1000.0
            t_draw = time.perf_counter()
            self.updateFrame(frame_num=frame_num)
            draw_ms = (time.perf_counter() - t_draw) * 1000.0

            total_ms = (time.perf_counter() - total_start) * 1000.0
            # self._update_perf_status(detect_ms, analyze_ms, draw_ms, total_ms, fps=fps)
            
            if (self.is_play and frame_num == self.video_loader.total_frames - 1
                    and not self._video_end_pending and not self._analysis_finalized and self.is_auto_recorded_video):
                self.stopTabletCapture()
                self._video_end_pending = True
                if self.timer is not None and self.timer.isActive():
                    self.timer.stop()
                if hasattr(self, '_play_timer'):
                    self._play_timer.stop()
                generation = self._video_generation
                QTimer.singleShot(self._get_video_end_delay_ms(), lambda: self._finishVideoEnd(generation))
            elif frame_num == self.video_loader.total_frames - 1:
                if not self.is_auto_recorded_video and self.ui.skeletonVideoCheckBox.isChecked():
                    self._complete_video_analysis()
                if not self.show_br_points:
                    self.ui.showBR_checkBox.setCheckState(2)
        else:
            if not self.camera.frame_buffer.empty() and not self.camera.frame_buffer_2.empty():
                frame = self.camera.frame_buffer.get().copy()
                frame_2 = self.camera.frame_buffer_2.get().copy()
                t_detect = time.perf_counter()
                _, _, fps = self.pose_estimater_2.detectKpt(frame_2, is_video=False)
                detect_ms = (time.perf_counter() - t_detect) * 1000.0
                # _, _, fps = self.pose_estimater_2.detectKpt(frame_2, is_video=False)
                t_analyze = time.perf_counter()
                if self.is_pitching:
                    if self.ui.startPitchCheckBox.isChecked() and not self.ui.recordCheckBox.isChecked() and self.pose_estimater_2.person_id is None:
                        self.ui.selectCheckBox.setCheckState(0)
                        self.ui.showSkeletonCheckBox.setCheckState(0)
                        self.ui.showSkeletonCheckBox.setCheckState(2)
                        self.ui.selectCheckBox.setCheckState(2)
                    self.pitherAnaylze()
                analyze_ms = (time.perf_counter() - t_analyze) * 1000.0
                t_draw = time.perf_counter()
                self.updateFrame(frame=frame, frame_2=frame_2)
                draw_ms = (time.perf_counter() - t_draw) * 1000.0

                total_ms = (time.perf_counter() - total_start) * 1000.0
                # self._update_perf_status(detect_ms, analyze_ms, draw_ms, total_ms, fps=fps)

                if self.is_auto_recording and self.camera.record_frames is not None:
                    # 以顯示幀數計時（約 60 FPS 下 135 幀 = 2.25 秒），全幀率模式錄影長度相同
                    new_frames_count = self.camera.auto_record_display_frames()
                    if new_frames_count >= 135:
                        total_frames = len(self.camera.record_frames)
                        self.stopAutoRecording()
                        print(f"自動錄影結束，已錄製 {total_frames} 幀")

    def _finishVideoEnd(self, generation=None):
        if not self._video_end_pending or (generation is not None and generation != self._video_generation):
            return
        self._video_end_pending = False
        self.handleVideoEnd()
        self.load_video_list(str(DB_DIR / "Record"))

    def _get_video_end_delay_ms(self) -> int:
        """根據 comboBox 的設定取得影片結束後的等待時間（毫秒）。"""
        default_delay_ms = 5000
        combo = getattr(self.ui, 'comboBox', None)
        if combo is None:
            return default_delay_ms

        try:
            delay_seconds = float(combo.currentText())
        except (TypeError, ValueError):
            return default_delay_ms

        if delay_seconds < 0:
            return 0
        return int(delay_seconds * 1000)
    
    def handleVideoEnd(self):
        """Handle the logic when video reaches its end."""
        self._complete_video_analysis()
        self.play_times -= 1

        if self.play_times > 0:
            # Replay the video
            self.playBtnClicked()
            self.ui.frameSlider.setValue(0)
            self.playBtnClicked()
        elif self.play2videos:
            if self.is_play:
                self.playBtnClicked()
            self.ui.frameSlider.setValue(self.video_loader.total_frames - 1)
            self.play_times = 1
        else:
            self.playBtnClicked()
            self.ui.cameraCheckBox.setCheckState(2)
            self.ui.showSkeletonCheckBox.setCheckState(0)
            self.ui.showSkeletonCheckBox.setCheckState(2)
            self.ui.selectCheckBox.setCheckState(2)
            self.ui.skeletonVideoCheckBox.setCheckState(0)
            self.ui.startPitchCheckBox.setCheckState(2)
            self.play_times = 1

            # print(self.play_times)

    @staticmethod
    def _metric_text(value):
        try:
            return f"{float(value):.1f}" if np.isfinite(float(value)) else "N/A"
        except (ValueError, TypeError):
            return "N/A"

    def _render_keyframe_results(self):
        """Render all known events, including a release within the final 7 frames."""
        events = ((self.foot_contact_frame, self.ui.fcView, 'foot_contact', self.ui.footContact_ptn),
                  (self.max_shoulder_angle_frame, self.ui.merView, 'max_shoulder_er', self.ui.maxER_ptn),
                  (self.wrist_speed_frame, self.ui.brView, 'release', self.ui.release_ptn))
        for frame, view, kind, button in events:
            shown = self._display_keyframe_image(frame, view, kind)
            button.setEnabled(shown)
            if not shown and view.scene() is not None:
                view.scene().clear()
        mer_data = self._get_frame_analysis_data(self.max_shoulder_angle_frame) if self.max_shoulder_angle_frame is not None else {}
        br_data = self._get_frame_analysis_data(self.wrist_speed_frame) if self.wrist_speed_frame is not None else {}
        angle = mer_data.get('right_shoulder_external_rotation_angle', float('nan'))
        speed = br_data.get('wrist_speed_2d', float('nan'))
        if np.isfinite(angle):
            self.max_shoulder_angle_value = angle
        if np.isfinite(speed):
            self.max_wrist_speed_after_er = speed / 100.
        self.ui.FC_label.setText("\n" + self._metric_text(self.image_drawer_2.stride_distance))
        self.ui.FC_label_unit.setText("\ncm")
        self.ui.MER_label.setText(self._metric_text(self.max_shoulder_angle_value) + "\n")
        self.ui.MER_label_unit.setText("°\n")
        self.ui.BR_label.setText(self._metric_text(self.max_wrist_speed_after_er) + "\n" +
                                 self._metric_text(self.image_drawer_2.extension_distance))
        self.ui.BR_label_unit.setText("m/s\ncm")
        self.release_view_rendered = self.wrist_speed_frame is not None
        self._refresh_keyframe_labels()

    def _complete_video_analysis(self):
        if self._analysis_finalized and not self._analysis_dirty:
            return True
        if self.single_video_mode:
            self._mirror_single_video_analysis()
        self._render_keyframe_results()
        try:
            export_dir = self.output_dir or self.video_loader.folder_path
            self.video_loader.saveVideo(export_dir)
            loader = self.video_loader
            for name, estimator in ((loader.video_name, self.pose_estimater), (loader.video_name_2, self.pose_estimater_2)):
                if not estimator.person_df.empty:
                    save_pose_timeline(export_dir, name, loader.video_name, loader.video_name_2, self.frame_offset)
            path = self.exportReleasePointJson()
            self._analysis_dirty = False
            self._analysis_finalized = True
            self.ui.RecordStatelabel.setText("分析 JSON 已儲存")
            self.ui.RecordStatelabel.setToolTip(path)
            return True
        except Exception as exc:
            # Keep dirty state so retrying playback/export can recover after a
            # full disk or temporary permission failure.
            self.ui.RecordStatelabel.setText("JSON 儲存失敗")
            self.ui.RecordStatelabel.setToolTip(str(exc))
            print(f"[Analysis export] {exc}")
            return False

    def save_current_analysis(self):
        if self.single_video_scan_running:
            return False
        if not self.is_video or not self.video_loader.total_frames:
            self.ui.RecordStatelabel.setText("請先載入並分析影片")
            return False
        self._analysis_finalized = False
        return self._complete_video_analysis()

    def restart_video_analysis(self):
        if self.single_video_scan_running or not self.is_video or not self.video_loader.total_frames:
            return
        if hasattr(self, '_play_timer'):
            self._play_timer.stop()
        self.is_play = False
        self._video_end_pending = False
        self._video_generation += 1
        self.resetPhaseDetection()
        for analyzer in (self.pose_analyzer, self.pose_analyzer_2):
            analyzer.reset()
        for drawer in (self.image_drawer, self.image_drawer_2):
            drawer.reset()
        self._analysis_dirty = True
        self._analysis_finalized = False
        self.ui.skeletonVideoCheckBox.blockSignals(True)
        self.ui.skeletonVideoCheckBox.setChecked(True)
        self.ui.skeletonVideoCheckBox.blockSignals(False)
        self.toggleSkeletonVideo(2)
        self._render_keyframe_results()
        self.ui.frameSlider.blockSignals(True)
        self.ui.frameSlider.setValue(0)
        self.ui.frameSlider.blockSignals(False)
        self.ui.RecordStatelabel.setText("重新分析中…")
        self.playBtnClicked()

    def exportReleasePointJson(self):
        """匯出球釋放點、ROI 影像與 OCR metrics 到 JSON。"""
        export_dir = self.output_dir or self.video_loader.folder_path
        if not export_dir or not self.video_loader.video_name:
            raise ValueError("尚未載入影片，不能匯出分析 JSON。")
        os.makedirs(export_dir, exist_ok=True)

        def encode_image_to_base64(image):
            if image is None:
                return None
            success, buffer = cv2.imencode('.png', image)
            if not success:
                return None
            return base64.b64encode(buffer.tobytes()).decode('ascii')

        latest_point = self.br_points[-1] if self.br_points else None
        if latest_point is not None and len(latest_point) >= 2:
            latest_point = [float(latest_point[0]), float(latest_point[1])]
        ball_center = self.ball_center_live[-1] if self.ball_center_live else None
        if ball_center is not None and len(ball_center) >= 2:
            ball_center = [float(ball_center[0]), float(ball_center[1])]

        roi_frame_1 = None
        roi_frame_2 = None
        roi_frame_3 = None
        if isinstance(self.tablet_capture_latest_roi_frames_save, dict):
            if 0 in self.tablet_capture_latest_roi_frames_save:
                roi_frame_1 = encode_image_to_base64(self.tablet_capture_latest_roi_frames_save.get(0))
            if 1 in self.tablet_capture_latest_roi_frames_save:
                roi_frame_2 = encode_image_to_base64(self.tablet_capture_latest_roi_frames_save.get(1))
            if 2 in self.tablet_capture_latest_roi_frames_save:
                roi_frame_3 = encode_image_to_base64(self.tablet_capture_latest_roi_frames_save.get(2))

        payload = {
            "video_name": self.video_loader.video_name,
            "video_name_2": self.video_loader.video_name_2,
            "wrist_point": latest_point,
            "roi_frame_1": roi_frame_1,
            "roi_frame_2": roi_frame_2,
            "roi_frame_3": roi_frame_3,
            "ball_center": ball_center,
            "metrics": self.tablet_capture_latest_metrics,
            "Analysis": {
                "schema": "pitch_2d_analysis_v1",
                "event_detection": EVENT_METHOD,
                "pose_frame_number_space": "playback_timeline",
                "frame_offset": self.video_loader.frame_offset,
                "frame_mapping": "video1 = frame_number; video2 = clip(frame_number - frame_offset)",
                "video_fps": self.video_loader.video_fps,
                "video_total_frames": self.video_loader.total_frames,
                "analyzed_frame_count": len(self.pose_analyzer_2.processed_frames),
                "distance_method": "legacy_pixel_scale_0.224_cm_per_px_not_chessboard_calibrated",
            },
            "Keyframes": {
                "preparation_frame": self.preparation_frame,
                "FC": {
                    "frame": self.foot_contact_frame,
                    "stride_distance": self.image_drawer_2.stride_distance if hasattr(self.image_drawer_2, 'stride_distance') else None
                },
                "MER": {
                    "frame": self.max_shoulder_angle_frame,
                    "confirmed": self.mer_locked,
                    "shoulder_angle": self.max_shoulder_angle_value if self.max_shoulder_angle_value != -np.inf else None
                },
                "BR": {
                    "frame": self.wrist_speed_frame,
                    "confirmed": self.release_locked,
                    "wrist_speed": self.max_wrist_speed_after_er if self.max_wrist_speed_after_er != -np.inf else None,
                    "extension_distance": self.image_drawer_2.extension_distance if hasattr(self.image_drawer_2, 'extension_distance') else None
                }
            }
        }

        json_path = os.path.join(export_dir, f"{self.video_loader.video_name}_BRandRapsodo.json")
        previous = read_optional_json(json_path, {})
        if not isinstance(previous, dict):
            raise ValueError(f"Existing event JSON is not an object: {json_path}")
        # Offline analysis has no new tablet capture. Preserve saved ROI/OCR and
        # other metadata instead of replacing them with null.
        for field in ('wrist_point', 'roi_frame_1', 'roi_frame_2', 'roi_frame_3', 'ball_center', 'metrics'):
            if payload[field] is None:
                payload[field] = previous.get(field)
        atomic_json(json_path, {**previous, **payload})
        print(f"[ReleasePoint] JSON saved: {json_path}")
        return json_path

    def pitherAnaylze(self):
        """使用 PitchingAnalyzer 分析投球動作"""
        # 確認已選擇人物
        if self.pose_estimater_2.person_id is None:
            return
        
        # 取得當前骨架關鍵點
        # 從當前檢測結果中取出選中人物的 keypoints（即時模式）
        keypoints = self.pose_estimater_2.getPersonDf(is_select=True, is_kpt=True)
        if keypoints is None or len(keypoints) == 0:
            return
        
        # 使用 PitchingAnalyzer 處理
        signal = self.pitching_analyzer.process(keypoints)
        # print(f"Detected state: {self.pitching_analyzer.state}, Signal: {signal}")
        
        # 處理信號
        if signal == "START":
            # 從 START 開始錄影，並帶入前60幀預存
            self.startAutoRecording()
        elif signal == "STOP":
            # 收到 STOP 後結束錄影並存檔
            self.stopAutoRecording()
    
    def initRecorderTimer(self, duration:int):
        if self.record_timer is not None and self.record_timer.is_running():
            return
        if self.record_timer is None:
            self.ui.showBboxCheckBox.setCheckState(0)
            self.ui.selectKptCheckBox.setCheckState(0)
            self.ui.selectCheckBox.setCheckState(0)
            self.ui.showSkeletonCheckBox.setCheckState(0)
            self.ui.recordCheckBox.setCheckState(2)
            self.record_timer = Timer(duration)
            self.record_timer.start()

    def updateFrame(self, frame: np.ndarray = None, frame_2: np.ndarray = None, frame_num:int = None):
        """Update the displayed frame with additional analysis."""
        # 更新當前的frame和frame_num
        if self.is_video and frame_num is not None:
            frame, frame_2 = self.video_loader.getVideoImage(frame_num)
            # print(f"frame_num: {frame_num}")
        countdown_time = self.updateTimers() 
        drawed_img = self.image_drawer.drawInfo(frame, frame_num, self.pose_estimater.kpt_buffer, countdown_time)
        drawed_img_2 = self.image_drawer_2.drawInfo(frame_2, frame_num, self.pose_estimater_2.kpt_buffer, countdown_time)

        if self.show_br_points and self.br_points:
            frame = self._draw_br_points(frame)
            drawed_img = self._draw_br_points(drawed_img)

        if self.is_video:
            original, _ = self.video_loader.getVideoImage(frame_num)
            self.showImage(original, self.view_scene, self.ui.FrameView)
        else:
            self.showImage(drawed_img if self.ui.showLineCheckBox.isChecked() else frame,
                           self.view_scene, self.ui.FrameView)
        self.showImage(drawed_img_2, self.view_scene_2, self.ui.FrameView_2)

        if self.fullscreen_view_1:
            if self.is_video:
                frame = original
            qimg = QImage(frame, frame.shape[1], frame.shape[0], QImage.Format_RGB888).rgbSwapped()
            # self.fullscreen_view_1.resize(800, 600)
            self.fullscreen_view_1.set_frame(qimg)
            del qimg  # 显式删除
        if self.fullscreen_view_2:
            qimg2 = QImage(drawed_img_2, drawed_img_2.shape[1], drawed_img_2.shape[0], QImage.Format_RGB888).rgbSwapped()
            # self.fullscreen_view_2.resize(800, 600)
            self.fullscreen_view_2.set_frame(qimg2)
            del qimg2  # 显式删除
        
        # 显式删除临时数组以及时释放内存
        del drawed_img_2
    
    def updateTimers(self):
        countdown_time = None
        if self.record_timer is not None:
            countdown_time = self.record_timer.get_remaining_time()
            if countdown_time == 0:
                self.resetRecordTimer()
        return countdown_time

    def resetRecordTimer(self):
        import gc
        
        self.record_timer = None
        video_path = self.camera.video_path
        video_path_2 = self.camera.video_path_2
        
        self.ui.recordCheckBox.setCheckState(0)
        self.ui.startPitchCheckBox.setCheckState(0)
        self.ui.cameraCheckBox.setCheckState(0)
        
        # 徹底清理舊視頻數據後再加載新視頻
        if hasattr(self, 'video_loader') and self.video_loader is not None:
            self.video_loader.reset()
        
        # 清理舊影片的追踪狀態、濾波器、GPU 內存
        if hasattr(self, 'pose_estimater') and self.pose_estimater is not None:
            self.pose_estimater.reset()
        if hasattr(self, 'pose_estimater_2') and self.pose_estimater_2 is not None:
            self.pose_estimater_2.reset()
        
        gc.collect()  # 強制垃圾回收
        
        self.video_loader.loadVideo(video_path, video_path_2)
        self.checkVideoLoad()
        # self.init3DViewer()

    def checkVideoLoad(self):
        """檢查影片是否讀取完成，並更新 UI 元素。"""
        # 檢查是否有影片名稱，若無則不執行後續操作
        if not self.video_loader.video_name and not self.video_loader.video_name_2:
            return
        if self.is_auto_recorded_video:
            self.startTabletCapture(enable_ocr=True)
        # 若影片正在讀取中，定時檢查讀取狀況
        if self.video_loader.is_loading:
            # self.ui.videoNameLabel.setText("讀取影片中")
            QTimer.singleShot(100, self.checkVideoLoad)  # 每100ms 檢查一次
            return
        # 若載入失敗或無有效幀，提示並返回
        if not self.video_loader.video_frames or not self.video_loader.video_frames_2:
            QMessageBox.warning(self, "無法播放影片", "影片載入失敗或沒有有效畫面，請重新選擇影片")
            return
        # 影片讀取完成後更新 UI 元素
        self.updateVideoInfo()
        # self.initAnalyzeFrame()

    def load_video_list(self, root_folder):
        """掃描所有子資料夾並分類加入 Tree"""
        video_exts = (".mp4", ".avi", ".mov", ".mkv", ".wmv")
        self.tree.clear()  # 清空 TreeWidget
        for pitcher_folder in os.listdir(root_folder):
            pitcher_path = os.path.join(root_folder, pitcher_folder)
            if not os.path.isdir(pitcher_path):
                continue

            pitcher_item = QTreeWidgetItem(self.tree)
            pitcher_item.setText(0, pitcher_folder)

            for play_folder in os.listdir(pitcher_path):
                play_path = os.path.join(pitcher_path, play_folder)
                if not os.path.isdir(play_path):
                    continue

                play_item = QTreeWidgetItem(pitcher_item)
                play_item.setText(0, play_folder)
                play_item.setData(0, Qt.UserRole, play_path)  # 儲存播放資料夾路徑

                for file in os.listdir(play_path):
                    # 過濾掉檔名包含 "Sk26" 的文件
                    if "Sk26" in file:
                        continue
                    
                    if file.lower().endswith(video_exts):
                        video_path = os.path.join(play_path, file)
                        video_item = QTreeWidgetItem(play_item)
                        video_item.setText(0, file)
                        video_item.setData(0, 1, video_path)  # 儲存影片路徑

    @staticmethod
    def _find_side_video(folder):
        """回傳資料夾內的側面原始影片（CS_*.mp4，排除畫好骨架的 _Sk26），沒有則回傳 None"""
        try:
            names = sorted(n for n in os.listdir(folder)
                           if n.upper().startswith("CS_") and n.lower().endswith(".mp4")
                           and "_SK26" not in n.upper())
        except OSError:
            return None
        return os.path.abspath(os.path.join(folder, names[0])) if names else None

    def play_video_from_item(self):
        """從 Tree 中選擇一個影片資料夾（或兩部影片）並播放"""
        import gc
        if self.single_video_scan_running:
            return

        selected_tree_items = self.tree.selectedItems()
        if not selected_tree_items:
            return

        target_item = selected_tree_items[0]
        # 如果點的是影片(有父節點且沒子節點)，就抓父節點；否則抓自己
        folder_item = target_item if target_item.childCount() > 0 else target_item.parent()
        
        if folder_item:
            play_folder_path = folder_item.data(0, Qt.UserRole)
            if play_folder_path:
                # 執行讀取並顯示 P01 到 P_Current 的 JSON 數據
                self.load_cumulative_json_data(play_folder_path)

        selected_items = [item for item in selected_tree_items if item.data(0, 1) is not None]  # 影片節點

        # 支援：只選 1 個「播放資料夾」節點時，自動抓該資料夾內影片
        if len(selected_items) == 0 and len(selected_tree_items) == 1:
            folder_item = selected_tree_items[0]
            folder_videos = []
            for i in range(folder_item.childCount()):
                child = folder_item.child(i)
                if child.data(0, 1) is not None:
                    folder_videos.append(child)
            if len(folder_videos) >= 1:
                selected_items = folder_videos

        # 清除 checkbox 狀態
        self.ui.startPitchCheckBox.setCheckState(0)
        self.ui.showSkeletonCheckBox.setCheckState(0)
        self.ui.skeletonVideoCheckBox.setCheckState(0)
        self.ui.cameraCheckBox.setCheckState(0)

        # 多於 2 個影片則只保留前兩個
        if len(selected_items) > 2:
            video_paths = [os.path.abspath(item.data(0, 1)) for item in selected_items]
            cf_paths = sorted([p for p in video_paths if os.path.basename(p).upper().startswith("CF_")])
            cs_paths = sorted([p for p in video_paths if os.path.basename(p).upper().startswith("CS_")])
            if cf_paths and cs_paths:
                video_paths = [cf_paths[0], cs_paths[0]]
            else:
                video_paths = sorted(video_paths)[:2]
        elif len(selected_items) == 2:
            video_paths = [os.path.abspath(item.data(0, 1)) for item in selected_items]
        elif len(selected_items) == 1:
            video_paths = [os.path.abspath(selected_items[0].data(0, 1))]
        else:
            video_paths = []

        # 只點一個項目（一支影片或一球的資料夾）時，一律改用該球的側面（CS）影片做單影片分析：
        # 2D 生物力學只用側面計算，只選到正面（CF）會分析錯視角（TODO P1-001）。
        # 刻意按住 Ctrl 同時選 CF + CS 時維持雙影片模式。
        if len(selected_tree_items) == 1 and video_paths:
            side_path = self._find_side_video(os.path.dirname(video_paths[0]))
            if side_path:
                if [side_path] != video_paths:
                    print(f"[2D] 自動改用側面影片：{os.path.basename(side_path)}")
                video_paths = [side_path]
            elif os.path.basename(video_paths[0]).upper().startswith("CF_"):
                print("[2D] 這一球沒有側面（CS_）影片，只能用正面分析，結果可能不正確")

        # Analysis uses the second camera as the side view, independently of
        # the order in which two tree items were selected.
        if len(video_paths) == 2:
            by_prefix = {os.path.basename(p)[:3].upper(): p for p in video_paths}
            if set(by_prefix) == {"CF_", "CS_"}:
                video_paths = [by_prefix["CF_"], by_prefix["CS_"]]

        self.single_video_mode = len(video_paths) == 1
        if self.single_video_mode:
            self.frame_offset = 0
            video_paths = [video_paths[0], video_paths[0]]
        else:
            self.frame_offset = 2

        # 確認剛好取得 2 部影片
        if len(video_paths) == 2:
            self.is_video = True
            video_path1, video_path2 = video_paths
            self.output_dir = os.path.dirname(video_path1) 
            self.play2videos = not self.single_video_mode
            self.is_auto_recorded_video = False
            self.tablet_capture_latest_roi_frames_save = {}
            self.tablet_capture_latest_metrics = None
            self.ball_center_live = []
            
            # 清理舊數據
            self.video_loader.reset()
            # 清理舊影片的追踪狀態、濾波器、GPU 內存
            if hasattr(self, 'pose_estimater') and self.pose_estimater is not None:
                self.pose_estimater.reset()
            if hasattr(self, 'pose_estimater_2') and self.pose_estimater_2 is not None:
                self.pose_estimater_2.reset()
            gc.collect()  # 強制垃圾回收
            
            self.video_loader.loadVideo(video_path1, video_path2)
            self.checkVideoLoad()
            # self.init3DViewer()
            rapsodo_json_path = os.path.join(self.video_loader.folder_path, f"{os.path.splitext(os.path.basename(video_path1))[0]}_BRandRapsodo.json")
            self.display_roi_from_json(rapsodo_json_path, self.ui.RoiLabel, self.ui.RapsodoDataLabel, self.spin_direction_widget)

            # 清除選取狀態
            for item in self.tree.selectedItems():
                item.setSelected(False)

    def load_cumulative_json_data(self, current_path):
        """解析 PXX 格式並讀取從 P01 到當前球次的所有球心數據"""
        folder_name = os.path.basename(current_path)
        # 只關心球次編號，時間前綴可不同
        match = re.search(r"_P(\d+)$", folder_name)
        
        if not match:
            print(f"資料夾格式不符，無法解析累加數據: {folder_name}")
            return

        current_idx = int(match.group(1)) # 球次數字 (例如 3)
        parent_dir = os.path.dirname(current_path) # 取得投手目錄

        # 找出目前投手資料夾中，實際存在且不大於 current_idx 的最小球次
        available_indices = []
        for folder in os.listdir(parent_dir):
            folder_path = os.path.join(parent_dir, folder)
            if not os.path.isdir(folder_path):
                continue
            idx_match = re.search(r"_P(\d+)$", folder)
            if not idx_match:
                continue
            idx = int(idx_match.group(1))
            if idx <= current_idx:
                available_indices.append(idx)

        start_idx = min(available_indices) if available_indices else 1
        print(f"[Cumulative] pitch range: P{start_idx:02d} -> P{current_idx:02d}")
        self.rapsodo_start_idx = start_idx
        self.rapsodo_end_idx = current_idx
        self.ui.label_4.setText(f"球到第{current_idx}球")
        self.ui.spinBox.setMinimum(start_idx)
        self.ui.spinBox.setMaximum(current_idx)
        self.ui.spinBox.setValue(start_idx)
        
        cumulative_centers = []
        release_points = []

        # 從實際最小球次迴圈到當前球次，忽略時間前綴差異
        for i in range(start_idx, current_idx + 1):
            target_suffix = f"_P{i:02d}"
            candidate_folders = [
                folder for folder in os.listdir(parent_dir)
                if os.path.isdir(os.path.join(parent_dir, folder)) and folder.endswith(target_suffix)
            ]
            candidate_folders.sort()
            
            if not candidate_folders:
                continue

            json_path = None
            for folder_name_candidate in candidate_folders:
                folder_path = os.path.join(parent_dir, folder_name_candidate)
                try:
                    files_in_folder = os.listdir(folder_path)
                except Exception:
                    files_in_folder = []

                # 優先使用 CF_..._BRandRapsodo(.json)，其次 CS_..._BRandRapsodo(.json)
                cf_candidates = sorted([
                    f for f in files_in_folder
                    if f.startswith("CF_") and "_BRandRapsodo" in f and f.lower().endswith(".json")
                ])
                cs_candidates = sorted([
                    f for f in files_in_folder
                    if f.startswith("CS_") and "_BRandRapsodo" in f and f.lower().endswith(".json")
                ])

                if cf_candidates:
                    json_path = os.path.join(folder_path, cf_candidates[0])
                    break
                if cs_candidates:
                    json_path = os.path.join(folder_path, cs_candidates[0])
                    break

            if json_path is None:
                continue

            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # 假設球心儲存在 "ball_center" 欄位
                    release_point = data.get("wrist_point")
                    center = data.get("ball_center") 
                    if center and release_point:
                        try:
                            cumulative_centers.append((int(center[0]), int(center[1])))
                            release_points.append((int(release_point[0]), int(release_point[1])))
                        except (TypeError, ValueError, IndexError):
                            pass
            except Exception as e:
                print(f"讀取 JSON 失敗 {json_path}: {e}")

        self.ball_center = cumulative_centers
        self.br_points = release_points
        self.onRapsodoSpinChanged(self.ui.spinBox.value())

    def updateVideoInfo(self):
        """更新與影片相關的資訊顯示在 UI 上。"""
        # 重置分析器和UI显示（video_loader已在play_video_from_item中清理过）
        self.reset()
        video_fps = self.video_loader.video_fps if self.video_loader.video_fps else 60
        self.pose_analyzer = PoseAnalyzer(self.pose_estimater, video_fps)
        self.pose_analyzer_2 = PoseAnalyzer(self.pose_estimater_2, video_fps)
        self.pose_analyzer.reset()
        self.pose_analyzer_2.reset()
        self.image_drawer.pose_analyzer = self.pose_analyzer
        self.image_drawer_2.pose_analyzer = self.pose_analyzer_2

        self.initFrameSlider()
        self.model.setImageSize(self.video_loader.video_size)
        video_size = self.video_loader.video_size
        self._set_resolution_text(f"(0,0) - {video_size[0]} x {video_size[1]}, 影片FPS: {video_fps}")
        self.ui.fileNameLabel.setText(f"檔名:{self.video_loader.video_name_2}")
        self.ui.StateLabel.setText("回放模式")
        self.ui.StateLabel.setStyleSheet("color: white; background-color: blue; font-weight: bold;")
        # 以確定性流程啟動播放：先停止舊播放，再啟動新播放
        self.setFrameOffset(self.frame_offset)
        restored = self._load_saved_analysis()
        # Restoring pose rows does not restore the drawer's visibility flag.
        # Display saved skeletons without enabling inference during replay.
        self.ui.showSkeletonCheckBox.blockSignals(True)
        self.ui.showSkeletonCheckBox.setChecked(True)
        self.ui.showSkeletonCheckBox.blockSignals(False)
        self.toggleShowSkeleton(2)
        if restored:
            self.ui.skeletonVideoCheckBox.blockSignals(True)
            self.ui.skeletonVideoCheckBox.setChecked(False)
            self.ui.skeletonVideoCheckBox.blockSignals(False)
            self._render_keyframe_results()
        self.updateFrame(frame_num=0)
        self._refresh_keyframe_labels()
        if self.single_video_mode and not restored:
            self.ui.StateLabel.setText("單影片分析模式")
            self.ui.StateLabel.setStyleSheet("color: white; background-color: #2c7be5; font-weight: bold;")
            QTimer.singleShot(0, self.scan_single_video_keyframes)
            return
        if self.is_play:
            self.playBtnClicked()
        self.playBtnClicked()

    def _load_saved_analysis(self):
        """Restore keyframes even when tablet ROI/OCR images are absent."""
        folder = self.video_loader.folder_path
        event_path = os.path.join(folder, f"{self.video_loader.video_name}_BRandRapsodo.json")
        if not os.path.isfile(event_path):
            event_path = os.path.join(folder, f"{self.video_loader.video_name_2}_BRandRapsodo.json")
        try:
            data = read_optional_json(event_path, {})
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError) as exc:
            print(f"[Replay] Ignoring invalid event JSON: {exc}")
            data = {}
        analysis = data.get('Analysis', {})
        offset = analysis.get('frame_offset') if isinstance(analysis, dict) else None
        if isinstance(offset, int) and not isinstance(offset, bool) and not self.single_video_mode:
            self.setFrameOffset(offset)
        names = (self.video_loader.video_name, self.video_loader.video_name_2)
        for index, (name, estimator, analyzer) in enumerate((
                (self.video_loader.video_name, self.pose_estimater, self.pose_analyzer),
                (self.video_loader.video_name_2, self.pose_estimater_2, self.pose_analyzer_2))):
            try:
                rows = read_optional_json(os.path.join(folder, name + '.json'), [])
                if not isinstance(rows, list) or not rows:
                    continue
                df = remap_pose_rows(rows, pose_timeline(folder, [name, *names]), name,
                    self.frame_offset if index else 0,
                    len(self.video_loader.video_frames_2 if index else self.video_loader.video_frames),
                    self.video_loader.total_frames)
                estimator.setProcessedData(df)
                for frame in sorted(estimator.processed_frames):
                    analyzer.addAnalyzeInfo(int(frame))
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                print(f"[Replay] Cannot restore {name} skeleton: {exc}")
        events = data.get('Keyframes')
        if not isinstance(events, dict):
            return False

        def valid_frame(value):
            if type(value) is int and self.single_video_mode:
                value -= saved_pose_offset({**analysis, 'video_name': data.get('video_name'),
                                           'video_name_2': data.get('video_name_2')}, names[0])
            return value if (isinstance(value, int) and not isinstance(value, bool)
                             and 0 <= value < self.video_loader.total_frames) else None

        def finite_value(value):
            try:
                return float(value) if value is not None and np.isfinite(float(value)) else -np.inf
            except (ValueError, TypeError):
                return -np.inf

        fc, mer, br = (events.get(k) if isinstance(events.get(k), dict) else {} for k in ('FC', 'MER', 'BR'))
        self.preparation_frame = valid_frame(events.get('preparation_frame'))
        self.foot_contact_frame = valid_frame(fc.get('frame'))
        self.max_shoulder_angle_frame = valid_frame(mer.get('frame'))
        self.wrist_speed_frame = valid_frame(br.get('frame'))
        self.max_shoulder_angle_value = finite_value(mer.get('shoulder_angle'))
        self.max_wrist_speed_after_er = finite_value(br.get('wrist_speed'))
        self.mer_locked = self.max_shoulder_angle_frame is not None and mer.get('confirmed', True)
        self.release_locked = self.wrist_speed_frame is not None and br.get('confirmed', True)
        self.image_drawer_2.stride_distance = fc.get('stride_distance')
        self.image_drawer_2.extension_distance = br.get('extension_distance')
        restored = any(f is not None for f in (self.foot_contact_frame, self.max_shoulder_angle_frame, self.wrist_speed_frame))
        if restored:
            self._render_keyframe_results()
            self._last_phase_frame = self.video_loader.total_frames - 1
            self._analysis_finalized = True
        return restored

    def _refresh_keyframe_labels(self):
        if hasattr(self.ui, 'footContact_label'):
            self.ui.footContact_label.setText(f"第{self.foot_contact_frame}幀" if self.foot_contact_frame is not None else "未找到")
        if hasattr(self.ui, 'maxER_label'):
            self.ui.maxER_label.setText((f"第{self.max_shoulder_angle_frame}幀" + ("（候選）" if not self.mer_locked else "")) if self.max_shoulder_angle_frame is not None else "未找到")
        if hasattr(self.ui, 'release_label'):
            self.ui.release_label.setText((f"第{self.wrist_speed_frame}幀" + ("（候選）" if not self.release_locked else "")) if self.wrist_speed_frame is not None else "未找到")

    def _mirror_single_video_analysis(self):
        self.pose_estimater.person_df = self.pose_estimater_2.person_df.copy()
        self.pose_estimater.pre_person_df = self.pose_estimater_2.pre_person_df.copy()
        self.pose_estimater.person_data = list(self.pose_estimater_2.person_data)
        self.pose_estimater.processed_frames = set(self.pose_estimater_2.processed_frames)
        self.pose_estimater.person_df_by_frame = {
            int(frame_num): frame_df.copy()
            for frame_num, frame_df in self.pose_estimater_2.person_df_by_frame.items()
        }
        self.pose_analyzer.analyze_info = list(self.pose_analyzer_2.analyze_info)
        self.pose_analyzer.analyze_df = self.pose_analyzer_2.analyze_df.copy()
        self.pose_analyzer.processed_frames = set(self.pose_analyzer_2.processed_frames)

    def scan_single_video_keyframes(self):
        if self.single_video_scan_running:
            return
        if self.video_loader.video_frames_2 is None or self.video_loader.total_frames is None:
            return

        self.single_video_scan_running = True
        self.tree.setEnabled(False)
        self.ui.frameSlider.setEnabled(False)
        self.ui.playBtn.setEnabled(False)
        self.ui.StateLabel.setText("單影片分析中...")
        self.ui.StateLabel.setStyleSheet("color: white; background-color: #5a5a5a; font-weight: bold;")

        try:
            self.resetPhaseDetection()
            self.pose_estimater.reset()
            self.pose_estimater_2.reset()
            self.pose_analyzer.reset()
            self.pose_analyzer_2.reset()
            self.image_drawer.reset()
            self.image_drawer_2.reset()

            self.pose_estimater.setDetect(True)
            self.pose_estimater_2.setDetect(True)
            self.image_drawer.setShowSkeleton(True)
            self.image_drawer_2.setShowSkeleton(True)

            if hasattr(self.ui, 'showSkeletonCheckBox'):
                self.ui.showSkeletonCheckBox.blockSignals(True)
                self.ui.showSkeletonCheckBox.setCheckState(2)
                self.ui.showSkeletonCheckBox.blockSignals(False)
            if hasattr(self.ui, 'skeletonVideoCheckBox'):
                self.ui.skeletonVideoCheckBox.blockSignals(True)
                self.ui.skeletonVideoCheckBox.setCheckState(2)
                self.ui.skeletonVideoCheckBox.blockSignals(False)

            total_frames = int(self.video_loader.total_frames or 0)
            for frame_num in range(total_frames):
                if not self.single_video_scan_running:
                    break

                frame = self.video_loader.video_frames_2[frame_num]
                self.pose_estimater_2.detectKpt(frame, frame_num, is_video=True)
                person_df = self.pose_estimater_2.getPersonDf(frame_num=frame_num)
                if person_df is not None and not person_df.empty:
                    self._analysis_dirty = True
                    self.pose_analyzer_2.addAnalyzeInfo(frame_num)
                    self.phaseDetection(frame_num)

                if frame_num % 8 == 0 or frame_num == total_frames - 1:
                    self.ui.frameNumLabel.setText(f"{frame_num}/{total_frames - 1}")
                    QApplication.processEvents()

            self._mirror_single_video_analysis()
            self.single_video_keyframes = {
                "preparation_frame": self.preparation_frame,
                "foot_contact_frame": self.foot_contact_frame,
                "max_shoulder_angle_frame": self.max_shoulder_angle_frame,
                "wrist_speed_frame": self.wrist_speed_frame,
            }
            self._refresh_keyframe_labels()
            self._complete_video_analysis()
            self.ui.StateLabel.setText("單影片分析完成")
            self.ui.StateLabel.setStyleSheet("color: white; background-color: #1f9d55; font-weight: bold;")
            self.updateFrame(frame_num=self.ui.frameSlider.value())
        except Exception as e:
            QMessageBox.warning(self, "單影片分析失敗", str(e))
            print(f"[single video scan] {e}")
        finally:
            self.single_video_scan_running = False
            self.tree.setEnabled(True)
            self.ui.frameSlider.setEnabled(True)
            self.ui.playBtn.setEnabled(True)

    def initAnalyzeFrame(self):
        self.ui.showSkeletonCheckBox.setCheckState(2)
        frame,frame_2 = self.video_loader.getVideoImage(0)
        _, _, _= self.pose_estimater.detectKpt(frame, 0, is_video=True)
        _, _, _= self.pose_estimater_2.detectKpt(frame_2, 0, is_video=True)
        self.ui.selectCheckBox.setCheckState(2)
        self.ui.selectKptCheckBox.setCheckState(0)
        if not self.is_play:
            self.playBtnClicked()

    def initFrameSlider(self):
        """初始化影片滑桿和相關的標籤。"""
        total_frames = self.video_loader.total_frames
        self.ui.frameSlider.blockSignals(True)
        self.ui.frameSlider.setMinimum(0)
        self.ui.frameSlider.setMaximum(total_frames - 1)
        self.ui.frameSlider.setValue(0)
        self.ui.frameSlider.blockSignals(False)
        self.ui.frameNumLabel.setText(f'0/{total_frames - 1}')

    def resetFrameSlider(self):
        self.ui.frameSlider.blockSignals(True)
        self.ui.frameSlider.setValue(0)
        self.ui.frameSlider.setRange(0, 0)
        self.ui.frameSlider.blockSignals(False)
        self.ui.frameNumLabel.setText(f'0/0')
        self.ui.RecordStatelabel.setText(" ")
        if hasattr(self.ui, 'FC_label'):
            self.ui.FC_label.setText(" ")
        if hasattr(self.ui, 'MER_label'):
            self.ui.MER_label.setText(" ")
        if hasattr(self.ui, 'BR_label'):
            self.ui.BR_label.setText(" ")

    def reset(self):
        import gc        
        if hasattr(self, '_play_timer'):
            self._play_timer.stop()
        self.is_play = False
        self.ui.playBtn.setText("▶︎")
        # 清理分析数据缓存
        if hasattr(self, '_analysis_data_cache'):
            self._analysis_data_cache.clear()
        self._video_end_pending = False
        self._video_generation = getattr(self, '_video_generation', 0) + 1
        self._analysis_dirty = False
        self._analysis_finalized = False

        self._perf_ema_ms = {'detect': 0.0, 'analyze': 0.0, 'draw': 0.0, 'total': 0.0}
        self._perf_sample_count = 0
        
        # 重置所有分析器和绘制器
        self.pose_estimater.reset()
        self.pose_estimater_2.reset()
        self.pose_analyzer.reset()
        self.pose_analyzer_2.reset()
        self.image_drawer.reset()
        self.image_drawer_2.reset()
        
        # 清理UI显示
        self.resetFrameSlider()
        if self.view_scene is not None:
            self.view_scene.clear()
        if self.view_scene_2 is not None:
            self.view_scene_2.clear()
        
        # 清空關鍵幀視圖
        if hasattr(self.ui, 'fcView') and self.ui.fcView.scene() is not None:
            self.ui.fcView.scene().clear()
        if hasattr(self.ui, 'merView') and self.ui.merView.scene() is not None:
            self.ui.merView.scene().clear()
        if hasattr(self.ui, 'brView') and self.ui.brView.scene() is not None:
            self.ui.brView.scene().clear()
        
        # 重置投球分析器
        self.pitching_analyzer.reset()
        
        # 重置階段檢測
        self.resetPhaseDetection()

        if self.overlay is not None:
            self.overlay.setParent(None)
            self.overlay = None
        
        # 清理摄像头和预存帧及 frame_buffer
        self.camera.pre_frames.clear()
        if self.camera.frame_buffer is not None:
            while not self.camera.frame_buffer.empty():
                try:
                    self.camera.frame_buffer.get_nowait()
                except:
                    break
        if self.camera.frame_buffer_2 is not None:
            while not self.camera.frame_buffer_2.empty():
                try:
                    self.camera.frame_buffer_2.get_nowait()
                except:
                    break
        self.is_auto_recording = False
        self.ui.StateLabel.setText(" ")
        self.ui.StateLabel.setStyleSheet("color: black;")
        
        # 强制垃圾回收和GPU缓存清理
        gc.collect()
        if hasattr(self.model, 'clear_gpu_cache'):
            self.model.clear_gpu_cache()

    def showImage(self, image: np.ndarray, scene: QGraphicsScene, GraphicsView: QGraphicsView):
        """Display an image in the QGraphicsView."""
        if scene is None:
            scene = QGraphicsScene()
            if GraphicsView == self.ui.FrameView:
                self.view_scene = scene
            else:
                self.view_scene_2 = scene
        else:
            scene.clear()
        
        if image is None:
            # Avoid killing the whole app when frame extraction fails near video end.
            print(f"[showImage] image is None, skip rendering. total_frames={self.video_loader.total_frames}")
            return
        
        h, w = image.shape[:2]
        qImg = QImage(image, w, h, 3 * w, QImage.Format_RGB888).rgbSwapped()
        
        pixmap = QPixmap.fromImage(qImg)
        
        scene.addPixmap(pixmap)
        scene.setSceneRect(0, 0, w, h)
        GraphicsView.setScene(scene)
        GraphicsView.setAlignment(Qt.AlignLeft)
        GraphicsView.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)

    def mousePressEvent(self, event):
        """Handle mouse events for person, keypoint selection, and fullscreen view."""
        global_pose = event.globalPos()
        pos = self.ui.FrameView.mapFromGlobal(global_pose)
        clicked_view = None
        if self.ui.FrameView.rect().contains(pos):
            clicked_view = 1
            scene_pos = self.ui.FrameView.mapToScene(self.ui.FrameView.mapFromParent(pos))
        else:
            clicked_view = 2
            scene_pos = self.ui.FrameView_2.mapToScene(self.ui.FrameView_2.mapFromParent(pos))

        x, y = scene_pos.x(), scene_pos.y()
        frame_num = self.ui.frameSlider.value()
        search_person_df = self.pose_estimater.getPersonDf(frame_num) if self.is_video else self.pose_estimater.pre_person_df

    def show_fullscreen(self, view_index):
        if view_index == 1:
            self.fullscreen_view_1 = FullscreenVideoDialog("放大畫面 左", self)
            self.fullscreen_view_1.control_signal.connect(self.handleFullscreenControl)
            self.fullscreen_view_1.set_slider_range(0, self.video_loader.total_frames - 1)
            self.fullscreen_view_1.set_slider_value(self.ui.frameSlider.value())
            self.fullscreen_view_1.slider_signal.connect(self.onFullscreenSliderChanged)
            self.fullscreen_view_1.show()
        elif view_index == 2:
            self.fullscreen_view_2 = FullscreenVideoDialog("放大畫面 右", self)
            self.fullscreen_view_2.control_signal.connect(self.handleFullscreenControl)
            self.fullscreen_view_2.set_slider_range(0, self.video_loader.total_frames - 1)
            self.fullscreen_view_2.set_slider_value(self.ui.frameSlider.value())
            self.fullscreen_view_2.slider_signal.connect(self.onFullscreenSliderChanged)
            self.fullscreen_view_2.show()

    def handleFullscreenControl(self, command):
        """處理全螢幕窗口的控制信號"""
        if command == "prev":
            self.ui.frameSlider.setValue(self.ui.frameSlider.value() - 1)
        elif command == "next":
            self.ui.frameSlider.setValue(self.ui.frameSlider.value() + 1)
        elif command == "pause":
            self.is_play = False
        elif command == "play":
            self.is_play = True

    def onFullscreenSliderChanged(self, value):
        """全螢幕slider移動時，同步主視窗frameSlider"""
        self.ui.frameSlider.setValue(value)

    def syncFullscreenSlider(self, value):
        if self.fullscreen_view_1:
            self.fullscreen_view_1.set_slider_value(value)
        if self.fullscreen_view_2:
            self.fullscreen_view_2.set_slider_value(value)

    def updatePlaybackRate(self):
        """更新播放速率時的處理"""
        if self.is_play and hasattr(self, "_play_timer"):
            self._play_timer.start(self._get_play_interval_ms())
    
    def onVideoWriteFinished(self):
        """視頻寶入完成後自動載入並回放"""
        print("[AutoRecord] Video write finished, loading for playback...")
        self.video_loader.reset()
        self.ui.recordCheckBox.setCheckState(0)
        self.ui.startPitchCheckBox.setCheckState(0)
        self.ui.cameraCheckBox.setCheckState(0)
        self.video_loader.loadVideo(self.video_filename, self.video_filename_2)
        self.is_auto_recorded_video = True  # 標記為自動錄影產生的影片
        self.play2videos = False  # 自動錄影的影片不啟用雙視窗播放
        self.checkVideoLoad()
        # self.init3DViewer()
    
    def onVideoWriteError(self, error_msg):
        """視頻寶入出錯"""
        print(f"[AutoRecord] Error: {error_msg}")
        QMessageBox.warning(self, "錄影錯誤", f"視頻寶入失敗：{error_msg}")

    def startAutoRecording(self):
        """開始自動錄影：包含前60幀預存，直接獲取原始帧（每一帧）。"""
        if self.is_auto_recording:
            return
        pitcher_id = self.ui.PitcherID.text().strip() or "Unknown"
        pitch_no = self.pitchCount + 1
        # 建立輸出路徑
        current_date = datetime.now().strftime("%Y%m%d")
        current_time = datetime.now().strftime("%Y%m%d_%H%M")
        self.output_dir = f'{DB_DIR.as_posix()}/Record/{current_date}_Pitcher{pitcher_id}/{current_time}_P{pitch_no:02d}'
        os.makedirs(self.output_dir, exist_ok=True)
        self.saveCalibrationSnapshot()
        self.video_filename = os.path.join(self.output_dir, f'CF_{current_time}_Pitcher{pitcher_id}_P{pitch_no:02d}.mp4')
        self.video_filename_2 = os.path.join(self.output_dir, f'CS_{current_time}_Pitcher{pitcher_id}_P{pitch_no:02d}.mp4')

        self.camera.start_auto_recording()
        self.is_auto_recording = True
        self.ui.RecordStatelabel.setText("錄影中...")
        
        actual_fps = self.camera.video_thread.camera2.get_fps() if self.camera.video_thread else None
        print(f"[AutoRecord] START, preload {len(self.camera.record_frames)} frames, 相機FPS: {actual_fps}")

    def stopAutoRecording(self):
        """收到 STOP 信號後在後台寫檔並自動回放。"""
        # 停止自動停止計時器
        # if hasattr(self, '_auto_stop_timer') and self._auto_stop_timer.isActive():
        #     self._auto_stop_timer.stop()
        
        if not self.is_auto_recording:
            return
        self.is_auto_recording = False
        
        # 停止 Camera 的自動錄影，取得所有錄製的幀
        frames_copy, actual_fps = self.camera.stop_auto_recording()
        
        # 若沒有任何幀，直接返回
        if not frames_copy:
            print("[AutoRecord] 無可寫入的幀，略過存檔")
            return
        
        write_fps = round(actual_fps)
        print(f"[AutoRecord] Actual recorded FPS: {actual_fps}, writing at {write_fps} FPS")
        self.ui.startPitchCheckBox.setCheckState(0)
        self.ui.RecordStatelabel.setText("寫入影片中...")

        # 在後台執行寫檔，不阻塞 UI
        self.video_writer_thread = FramesToVideoWriterThread(
            frames_copy, 
            self.video_filename, 
            self.video_filename_2, 
            fps=write_fps
        )
        self.video_writer_thread.finished.connect(self.onVideoWriteFinished)
        self.video_writer_thread.error.connect(self.onVideoWriteError)
        self.video_writer_thread.start()  
        print(f"[AutoRecord] STOP, writing {len(frames_copy)} frames in background...")
        self.pitchCount += 1
        self.ui.PitchCountLabel.setText(f"{self.pitchCount}")

    def _draw_br_points(self, image: np.ndarray) -> np.ndarray:
        if not self.br_points or len(self.br_points) == 0:
            return image
        
        result = image.copy()
        
        # 顏色配置
        COLOR_HISTORY_POINT = (0, 0, 255)   # 紅色 - 最新點
        COLOR_LATEST_POINT = (253, 251, 115)  # 淺藍色 - 歷史點
        POINT_RADIUS = 8
        last_index = len(self.br_points) - 1
        
        # 繪製每個釋放點
        for i, point_data in enumerate(self.br_points):
            if point_data is None or len(point_data) < 2:
                continue            
            try:
                x, y = int(point_data[0]), int(point_data[1])
                
                # 檢查座標是否在圖像範圍內
                if 0 <= x < result.shape[1] and 0 <= y < result.shape[0]:
                    point_color = COLOR_LATEST_POINT if i == last_index else COLOR_HISTORY_POINT
                    # 繪製點
                    cv2.circle(result, (x, y), POINT_RADIUS, point_color, 2)
            except (ValueError, TypeError):
                continue
        return result

    def resetBRPoints(self):
        """清除所有釋放點記錄"""
        reply = QMessageBox.question(
            self, "確認", "確定要清除所有釋放點記錄嗎?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.br_points = []
            print("✓ 已清除所有釋放點記錄")
            # 如果當前正在顯示，則刷新畫面
            if self.show_br_points:
                self.updateFrame(frame_num=self.ui.frameSlider.value())
            QMessageBox.information(self, "成功", "所有釋放點記錄已清除")

    def toggleShowBRPoints(self, state: int):
        """切換顯示釋放點
        
        Args:
            state: 複選框狀態 (2=勾選, 0=未勾選)
        """
        self.show_br_points = (state == 2)
        if self.show_br_points:
            print(f"✓ 顯示釋放點 (共 {len(self.br_points)} 個)")
        else:
            print("✓ 隱藏釋放點")
        self.updateFrame(frame_num=self.ui.frameSlider.value())

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PosePitchTabControl()
    window.show()
    sys.exit(app.exec_())
