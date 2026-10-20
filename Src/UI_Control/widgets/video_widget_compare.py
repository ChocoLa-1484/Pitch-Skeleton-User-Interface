
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

import os
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(os.environ["CONDA_PREFIX"], "Library", "plugins", "platforms")
# import vispy
# vispy.use('pyqt5')
from PyQt5.QtWidgets import *
from PyQt5.QtGui import QColor, QImage, QPixmap, QPainter, QFont, QPen, QBrush, QPalette
from PyQt5.QtCore import Qt, QTimer, QRect
import numpy as np
import sys
import cv2
import json
import re
import base64
from Src.UI_Control.forms.video_ui_compare import Ui_video_widget
from Src.UI_Control.widgets.compare_video_dialog import CompareVideoDialog, validate_comparison_paths
from Src.UI_Control.rendering.vis_image_3d import ImageDrawer
from Src.UI_Control.controllers.selector import PersonSelector, KptSelector
from Src.cameras.video_control import VideoLoader, JsonLoader, KeyframeJsonLoader
from Src.pose.detect_skeleton import PoseEstimater
from Src.UI_Control.rendering.vis_graph_3d_compare import GraphPlotter
from Src.analysis.analyze_3d import PoseAnalyzer
from Src.pose.model import Model
import pyqtgraph as pg
from Src.UI_Control.viewers.triangulate_3d_viewer import Triangulate3DViewer
import math

class SliderColorOverlay(QWidget):
    def __init__(self, slider: QSlider, segments: list, segment_half_thickness: int = 10):
        """
        slider: 你要疊的 QSlider
        segments: list of tuples (start_frame, end_frame, color)
        """
        super().__init__(slider.parent())
        self.slider = slider
        self.segments = segments
        self.segment_half_thickness = max(2, int(segment_half_thickness))
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
        self.setGeometry(self.slider.geometry())
        center_y = self.slider.height() // 2
        groove_rect = QRect(
            2,
            max(0, center_y - self.segment_half_thickness),
            max(1, self.slider.width() - 4),
            min(self.slider.height(), self.segment_half_thickness * 2),
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

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

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
            label = f"{duration*0.015:.2f}s"
            # label = f"{duration*0.047:.2f}s ({percentage:.1f}%)"
            
            # 定義文字繪圖區域
            # 讓文字繪製在軌道上方，並位於該區塊的中心
            metrics = painter.fontMetrics()
            text_width_needed = metrics.horizontalAdvance(label) + 10 # 4px padding
            text_x = int(x1 + segment_width / 2 - text_width_needed / 2)
            # 第2個phase (i=1) 的文字標籤往左移
            # if i == 1:
            #     text_x -= 20
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

class PoseVideoCompareTabControl(QWidget):
    def __init__(self, model:Model, parent = None):
        super(PoseVideoCompareTabControl, self).__init__(parent)
        self.ui = Ui_video_widget()
        self.ui.setupUi(self)
        self._setup_comparison_guidance()
        self.model = model
        self.initVar()
        self.setupComponents()
        self.bindUI()
        self._setup_keyframe_editors()
        self.initGraph()

    def _setup_comparison_guidance(self):
        self.ui.startCodeBtn.hide()
        self.ui.loadOriginalVideoBtn.setText("載入 2D 比較影片…")
        self.ui.loadProcessedVideoBtn.setText("載入 3D 比較影片…")
        hint = QLabel("上組 A、下組 B：兩次不同投球；每組左正面 CF、右側面 CS。請載入同次投球的同步影片與既有分析 JSON。")
        hint.setWordWrap(True)
        self.ui.verticalLayout_5.insertWidget(1, hint)
        self.comparison_path_fields = []
        for layout, group, front, side in (
                (self.ui.horizontalLayout_7, "上組 A", self.ui.FrameView, self.ui.FrameView_2),
                (self.ui.horizontalLayout_5, "下組 B", self.ui.FrameView_3, self.ui.FrameView_4)):
            layout.removeWidget(front)
            layout.removeWidget(side)
            for index, (role, view) in enumerate((("正面 CF", front), ("側面 CS", side))):
                column = QVBoxLayout()
                title = QLabel(f"{group} · {role}")
                column.addWidget(title)
                path = QLineEdit()
                path.setReadOnly(True)
                path.setPlaceholderText(f"尚未載入{role}影片")
                path.setToolTip("載入後顯示影片位置；可選取複製完整路徑。")
                column.addWidget(path)
                column.addWidget(view, 1)
                layout.insertLayout(index, column, 1)
                self.comparison_path_fields.append(path)
        self.ui.NameLabel.setText("上組 A：")
        self.ui.NameLabel_2.setText("下組 B：")
        self.ui.horizontalLayout_22.insertWidget(0, QLabel("上組 A"))
        self.ui.horizontalLayout_9.insertWidget(0, QLabel("下組 B"))
        self.ui.horizontalLayout_3.insertWidget(3, QLabel("同步拖曳"))
        self.ui.frameSlider.setToolTip("同時移動上下兩組，保留目前的對齊差距。上／下組拉桿可分別定位。")
        self.ui.frameSlider_2.setToolTip("只調整上組 A 的正面、側面影片。")
        self.ui.frameSlider_3.setToolTip("只調整下組 B 的正面、側面影片。")
        self.ui.graph_widget.setMinimumHeight(190)
        self.graph_status = QLabel("肩髖分離角度比較：載入兩組影片後顯示曲線。")
        self.ui.verticalLayout_5.insertWidget(
            self.ui.verticalLayout_5.indexOf(self.ui.graph_widget), self.graph_status)
        self.ui.verticalLayout_5.setStretch(self.ui.verticalLayout_5.indexOf(self.ui.graph_widget), 2)
        for view in (self.ui.FrameView, self.ui.FrameView_2, self.ui.FrameView_3, self.ui.FrameView_4):
            view.setMinimumSize(280, 180)
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for label in self.findChildren(QLabel):
            if label.font().pointSize() > 14:
                font = label.font()
                font.setPointSize(11)
                label.setFont(font)

    def _setup_keyframe_editors(self):
        self.keyframe_edits = {}
        rows = ((self.ui.horizontalLayout_2, self.ui.horizontalLayout_10, self.ui.horizontalLayout_13),
                (self.ui.horizontalLayout_14, self.ui.horizontalLayout_18, self.ui.horizontalLayout_19))
        for group, layouts in enumerate(rows):
            for event, layout in zip(("foot_contact_frame", "max_shoulder_angle_frame", "wrist_speed_frame"), layouts):
                spin = QSpinBox()
                spin.setRange(-1, -1)
                spin.setSpecialValueText("未標記")
                spin.setPrefix("幀 ")
                spin.setKeyboardTracking(False)
                spin.setToolTip("微調此組關鍵幀，僅用於本次比較。")
                spin.setEnabled(False)
                spin.valueChanged.connect(lambda value, g=group, e=event: self._edit_keyframe(g, e, value))
                layout.addWidget(spin)
                self.keyframe_edits[group, event] = spin

    def _edit_keyframe(self, group, event, value):
        setattr(self, event + ("_2" if group else ""), value if value >= 0 else None)
        self._sync_keyframe_controls()
        metrics = ((self.ui.FC_label, self.ui.MER_label_3, self.ui.BR_label_3),
                   (self.ui.FC_label_3, self.ui.MER_label_4, self.ui.BR_label_7))
        metric = metrics[group][("foot_contact_frame", "max_shoulder_angle_frame", "wrist_speed_frame").index(event)]
        metric.setText("—")
        metric.setToolTip("關鍵幀已調整；此幀的量測值需重新分析。")
        self._update_phase_visualization()
        if value >= 0:
            (self.ui.frameSlider_2, self.ui.frameSlider_3)[group].setValue(value)
        self._update_graph()

    def _sync_keyframe_controls(self):
        for group, (loader, buttons, labels) in enumerate((
                (self.video_loader, (self.ui.footContact_ptn, self.ui.maxER_ptn, self.ui.release_ptn),
                 (self.ui.footContact_label, self.ui.maxER_label, self.ui.release_label)),
                (self.video_loader_2, (self.ui.footContact_ptn_2, self.ui.maxER_ptn_2, self.ui.release_ptn_2),
                 (self.ui.footContact_label_2, self.ui.maxER_label_2, self.ui.release_label_2)))):
            total = int(loader.total_frames or 0)
            for event, button, label in zip(("foot_contact_frame", "max_shoulder_angle_frame", "wrist_speed_frame"), buttons, labels):
                frame = getattr(self, event + ("_2" if group else ""))
                valid = isinstance(frame, int) and 0 <= frame < total
                button.setEnabled(valid)
                button.setMinimumHeight(28)
                label.setText("")  # The editable frame field displays the number.
                spin = self.keyframe_edits[group, event]
                spin.blockSignals(True)
                spin.setRange(-1, max(-1, total - 1))
                spin.setValue(frame if valid else -1)
                spin.setEnabled(total > 0)
                spin.blockSignals(False)
        
    def initVar(self):
        self.is_play = False
        self.is_video = False
        self.view_scene = QGraphicsScene()
        self.curve_scene = QGraphicsScene()
        self.curve_scene_3 = QGraphicsScene()
        self.curve_scene_4 = QGraphicsScene()

        self.view_scene.clear()
        self.curve_scene.clear()
        self.curve_scene_3.clear()
        self.curve_scene_4.clear()
        self.correct_kpt_idx = 0
        self.is_processed = False
        self.overlay = None  # 初始化 SliderColorOverlay

        # self.table = self.ui.tableWidget
        self.json_path = None
        self.json_path2 = None
        self.viewer3d = None
        self.viewer3d_2 = None
        self.preparation_frame = None
        self.preparation_frame_2 = None
        self.min_left_knee_y = np.inf
        self.prev_left_knee_y = None
        self.preparation_tracking_started = False
        self.candidate_preparation_frame = None
        self.preparation_descend_count = 0
        self.foot_contact_frame = None
        self.max_shoulder_angle_frame = None
        self.wrist_speed_frame = None
        self.foot_contact_frame_2 = None
        self.max_shoulder_angle_frame_2 = None
        self.wrist_speed_frame_2 = None
        self.max_shoulder_angle_value = -np.inf
        self.max_wrist_speed_after_er = -np.inf
        self.speed_angle_names = ["ankle_speed", "left_knee_3d", "shoulder_hiple_angle", "shoulder_angle", "wrist_speed", "ankle_x_diff"]
        self.title_names = ["腳踝速度", "膝蓋角度", "肩髖分離角度", "肩膀外旋角度", "手腕速度", "腳踝X方向差值"]
        self.frame_offset = -2  # 幀偏移：正值表示 video_path_2 延遲，負值表示 video_path 延遲
        self.compare_frame_offset = 0  # 第二組相對第一組的對齊偏移（以 preparation_frame 對齊）

        # self.K_F = np.array([
        #     [11229.920949545698, 0.0, 937.199020465423],
        #     [0.0, 11240.535783693984, 586.8641683267341],
        #     [0.0, 0.0, 1.0]
        # ])
        # self.K_F = np.array([
        #     [7131.197489913678, 0.0, 1190.6066203243458],
        #     [0.0, 7107.943256043428, 665.2288622157741],
        #     [0.0, 0.0, 1.0]
        # ])
        # self.K_S = np.array([
        #     [1063.0766604691114, 0.0, 962.1115766023166],
        #     [0.0, 1061.6146093201994, 570.8085693424371],
        #     [0.0, 0.0, 1.0]
        # ])

        self.K_F = np.array([
            [7.92787455e+03, 0.0, 8.69870446e+02],
            [0.0, 8.03959073e+03, 7.16810004e+02],
            [0.0, 0.0, 1.0]
        ])

        self.K_S = np.array([
            [1.40583815e+03, 0.0, 9.64222231e+02],
            [0.0, 1.40662233e+03, 5.75695704e+02],
            [0.0, 0.0, 1.0]
        ])

        # self.F = np.array([
        #     [-1.29643278e-07,  2.61221033e-06, -1.03359762e-03],
        #     [4.98361315e-07,  1.40330685e-06, -3.43221269e-03],
        #     [-1.95226676e-04,  -1.72748402e-05,  1.00000000e+00]
        # ])  3

        # self.F = np.array([
        #     [1.20973516e-07, -5.15926324e-06,  2.69228899e-03],
        #     [-2.18398570e-06, -7.16241028e-07,  8.11364487e-03],
        #     [5.00374629e-04, -4.56687478e-03,  1.00000000e+00]
        # ])  1

        # self.F = np.array([
        #     [-1.23527137e-06,  3.74634658e-05, -1.36469340e-02],
        #     [1.59530549e-05,  9.03916270e-07, -6.06394789e-02],
        #     [-4.66549815e-03,  3.39782280e-02,  1.00000000e+00]
        # ])  2
        # self.F = np.array([
        #     [-6.14357015e-08,  2.22505318e-06, -8.55658761e-04],
        #     [7.93662298e-07,  8.14124289e-08, -4.02123194e-03],
        #     [-2.90068577e-04,  8.68353278e-04,  1.00000000e+00]])
        # self.F = np.array([
        #     [-3.04878957e-10,  1.94256712e-06, -6.54124262e-04],
        #     [-2.65714088e-07,  9.78457141e-07, -3.39866658e-03],
        #     [1.14349770e-04,  9.60193018e-04,  1.00000000e+00]
        # ])
        self.F = np.array([
            [-1.00177788e-07,  2.16557273e-06, -7.90363312e-04],
            [ 1.58917237e-07,  2.44307598e-07, -3.48771760e-03],
            [-9.75476979e-05,  7.35402506e-04,  1.00000000e+00]
        ])
 
        pg.setConfigOptions(foreground=QColor(113,148,116), antialias = True)
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')

        self.br_points = []
        self.show_br_points = False  # 是否顯示釋放點
        self.mer_locked = False
        self.release_locked = False
        self._play_timer = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._refit_comparison_views)

    def _refit_comparison_views(self):
        for view in (self.ui.FrameView, self.ui.FrameView_2, self.ui.FrameView_3, self.ui.FrameView_4):
            self._fit_view_no_crop(view)

    def _fit_view_no_crop(self, view_widget: QGraphicsView):
        """不裁切模式下，將影像盡量放大並靠左上顯示。"""
        if view_widget is None or view_widget.scene() is None:
            return
        if len(view_widget.scene().items()) == 0:
            return
        view_widget.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        view_widget.fitInView(view_widget.scene().sceneRect(), Qt.KeepAspectRatio)

    def initFrameSlider(self, total_frames, frameSlider, frameNumLabel):
        frameSlider.blockSignals(True)
        frameSlider.setRange(0, max(0, int(total_frames or 0) - 1))
        frameSlider.setValue(0)
        frameSlider.blockSignals(False)
        frameNumLabel.setText(f'0/{frameSlider.maximum()}')

    def initGraph(self):
        self.graph_plotter._init_graph(max(1, int(self.video_loader.total_frames or 0)))
        self.graph_plotter.zoomIn(self.ui.frameSlider.minimum(), self.ui.frameSlider.maximum())
        self.showGraph(self.ui.graph_widget)
        self._update_graph()

    def resetPhaseDetection(self):
        self.ui.footContact_ptn.setDisabled(True)
        self.ui.maxER_ptn.setDisabled(True)
        self.ui.release_ptn.setDisabled(True)
        self.ui.footContact_ptn_2.setDisabled(True)
        self.ui.maxER_ptn_2.setDisabled(True)
        self.ui.release_ptn_2.setDisabled(True)

        self.preparation_frame = None
        self.preparation_frame_2 = None
        self.min_left_knee_y = np.inf
        self.prev_left_knee_y = None
        self.preparation_tracking_started = False
        self.candidate_preparation_frame = None
        self.preparation_descend_count = 0
        self.foot_contact_frame = None
        self.max_shoulder_angle_frame = None
        self.wrist_speed_frame = None
        self.foot_contact_frame_2 = None
        self.max_shoulder_angle_frame_2 = None
        self.wrist_speed_frame_2 = None
        self.max_shoulder_angle_value = -np.inf
        self.max_wrist_speed_after_er = -np.inf
        self.ui.footContact_label.setText("")
        self.ui.maxER_label.setText("")
        self.ui.release_label.setText("")
        self.prev_left_knee_y = None
        if hasattr(self.ui, 'FC_label'):
            self.ui.FC_label.setText(" ")
        if hasattr(self.ui, 'MER_label_3'):
            self.ui.MER_label_3.setText(" ")
        if hasattr(self.ui, 'BR_label_3'):
            self.ui.BR_label_3.setText(" ")
        self.mer_locked = False
        self.release_locked = False
        self._phase_visualization_done = False

    # def reset3DInfo(self):
    #     self.ui.ankle_label.setText("(m/s)")
    #     self.ui.knee_label.setText("°")
    #     self.ui.shouhip_label.setText("°")
    #     self.ui.shoulder_label.setText("°")
    #     self.ui.wrist_label.setText("(m/s)")
    #     self.ui.ankleGraphCheckbox.setChecked(False)
    #     self.ui.kneeGraphCheckbox.setChecked(False)
    #     self.ui.shouhipGraphCheckbox.setChecked(False)
    #     self.ui.shoulderGraphCheckbox.setChecked(False)
    #     self.ui.wristGraphCheckbox.setChecked(False)

    def bindUI(self):
        self.ui.footContact_ptn.setDisabled(True)
        self.ui.maxER_ptn.setDisabled(True)
        self.ui.release_ptn.setDisabled(True)
        self.ui.footContact_ptn_2.setDisabled(True)
        self.ui.maxER_ptn_2.setDisabled(True)
        self.ui.release_ptn_2.setDisabled(True)

        for slider, buttons, suffix in (
                (self.ui.frameSlider_2, (self.ui.footContact_ptn, self.ui.maxER_ptn, self.ui.release_ptn), ""),
                (self.ui.frameSlider_3, (self.ui.footContact_ptn_2, self.ui.maxER_ptn_2, self.ui.release_ptn_2), "_2")):
            for button, event in zip(buttons, ("foot_contact_frame", "max_shoulder_angle_frame", "wrist_speed_frame")):
                button.clicked.connect(lambda checked=False, sl=slider, attr=event + suffix:
                                       self._jump_to_keyframe(sl, attr))

        self.checkBox(False)

        self.ui.loadOriginalVideoBtn.clicked.connect(
            lambda: self.loadVideo(is_processed=False))
        self.ui.loadProcessedVideoBtn.clicked.connect(
            lambda: self.loadVideo(is_processed=True))
        
        self.ui.playBtn.clicked.connect(self.playBtnClicked)
        self.ui.backKeyBtn.clicked.connect(
            lambda: self.ui.frameSlider.setValue(self.ui.frameSlider.value() - 1)
        )
        self.ui.forwardKeyBtn.clicked.connect(
            lambda: self.ui.frameSlider.setValue(self.ui.frameSlider.value() + 1)
        )
        self.ui.slowMotionInput.currentTextChanged.connect(self._on_play_speed_changed)
        self.ui.frameSlider.valueChanged.connect(self.analyzeFrame)
        self.ui.frameSlider_2.valueChanged.connect(self._on_group1_slider_changed)
        self.ui.frameSlider_3.valueChanged.connect(self._on_group2_slider_changed)
        # self.kpt_table = KeypointTable(self.ui.KptTable,self.pose_estimater)
        # self.ui.KptTable.cellActivated.connect(self.kpt_table.onCellClicked)
        self.ui.FrameView.mousePressEvent = self.mousePressEvent
        # self.ui.IdCorrectBtn.clicked.connect(self.correctId)
        self.ui.selectCheckBox.stateChanged.connect(self.toggleSelect)
        self.ui.showSkeletonCheckBox.stateChanged.connect(self.toggleShowSkeleton)
        self.ui.selectKptCheckBox.stateChanged.connect(self.toggleKptSelect)
        self.ui.showBboxCheckBox.stateChanged.connect(self.toggleShowBbox)
        
        # 釋放點相關按鈕和複選框
        if hasattr(self.ui, 'resetBRBtn'):
            self.ui.resetBRBtn.clicked.connect(self.resetBRPoints)
        if hasattr(self.ui, 'showBR_checkBox'):
            self.ui.showBR_checkBox.stateChanged.connect(self.toggleShowBRPoints)
        # self.ui.showAngleCheckBox.stateChanged.connect(self.toggleShowAngleInfo)
        # self.ui.ankleGraphCheckbox.stateChanged.connect(lambda state: self.toggleShowGraph("ankle_speed", state))
        # self.ui.kneeGraphCheckbox.stateChanged.connect(lambda state: self.toggleShowGraph("left_knee_3d", state))
        # self.ui.shouhipGraphCheckbox.stateChanged.connect(lambda state: self.toggleShowGraph("shoulder_hiple_angle", state))
        # self.ui.shoulderGraphCheckbox.stateChanged.connect(lambda state: self.toggleShowGraph("shoulder_angle", state))
        # self.ui.wristGraphCheckbox.stateChanged.connect(lambda state: self.toggleShowGraph("wrist_speed", state))

        # self.ui.zoomInButton.toggled.connect(self.toggleZoomIn)

        # if self.ui.vispy_widget.layout() is None:
        #     self.ui.vispy_widget.setLayout(QVBoxLayout())

    def _jump_to_keyframe(self, slider, attribute):
        frame = getattr(self, attribute)
        if isinstance(frame, int):
            slider.setValue(frame)

    def playBtnClicked(self):
        if any(loader.is_loading or not loader.total_frames
               for loader in (self.video_loader, self.video_loader_2)):
            QMessageBox.information(self, "無法播放", "請先完成上下兩組影片的載入。")
            return
        self.is_play = not self.is_play
        self.ui.playBtn.setText("||" if self.is_play else "▶︎")
        if self.is_play:
            self.playFrame()
        elif self._play_timer is not None:
            self._play_timer.stop()

    def _on_group1_slider_changed(self, frame_num):
        self._on_independent_seek(0, frame_num)

    def _on_group2_slider_changed(self, frame_num):
        self._on_independent_seek(1, frame_num)

    def _on_independent_seek(self, group, frame_num):
        # The two local sliders define the manual alignment. The master slider
        # and playback then move both groups by the same number of frames.
        self.compare_frame_offset = self.ui.frameSlider_3.value() - self.ui.frameSlider_2.value()
        self._configure_master_slider(self.ui.frameSlider_2.value())
        self._render_group(group, frame_num)
        self._update_graph()

    def _configure_master_slider(self, current_frame=0):
        end1 = max(0, int(self.video_loader.total_frames or 0) - 1)
        end2 = max(0, int(self.video_loader_2.total_frames or 0) - 1)
        slider = self.ui.frameSlider
        slider.blockSignals(True)
        slider.setRange(min(0, -self.compare_frame_offset), max(end1, end2 - self.compare_frame_offset))
        slider.setValue(current_frame)
        slider.blockSignals(False)
        self.graph_plotter.set_compare_frame_offset(self.compare_frame_offset)
        self.graph_plotter.zoomIn(slider.minimum(), slider.maximum())

    def _render_group(self, group, frame_num):
        loader, drawers, estimators, scenes, views, slider, label = (
            (self.video_loader, (self.image_drawer, self.image_drawer_2),
             (self.pose_estimater, self.pose_estimater_2), (self.view_scene, self.curve_scene),
             (self.ui.FrameView, self.ui.FrameView_2), self.ui.frameSlider_2, self.ui.frameNumLabel_2),
            (self.video_loader_2, (self.image_drawer_3, self.image_drawer_4),
             (self.pose_estimater_3, self.pose_estimater_4), (self.curve_scene_3, self.curve_scene_4),
             (self.ui.FrameView_3, self.ui.FrameView_4), self.ui.frameSlider_3, self.ui.frameNumLabel_3)
        )[group]
        if not loader.total_frames:
            return
        frame_num = max(0, min(int(frame_num), loader.total_frames - 1))
        for index, (frame, drawer, estimator, scene, view) in enumerate(zip(
                loader.getVideoImage(frame_num), drawers, estimators, scenes, views)):
            drawn = drawer.drawInfo(frame, frame_num, estimator.kpt_buffer)
            if index == 1 and self.show_br_points and self.br_points and drawn is not None:
                drawn = self._draw_br_points(drawn)
            self.showImage(drawn, scene, view, process_events=False)
        slider.blockSignals(True)
        slider.setValue(frame_num)
        slider.blockSignals(False)
        label.setText(f'{frame_num}/{loader.total_frames - 1}')

    def _refresh_current_frames(self):
        self._render_group(0, self.ui.frameSlider_2.value())
        self._render_group(1, self.ui.frameSlider_3.value())
        self._update_graph()

    def _update_graph(self):
        if not hasattr(self, 'graph_plotter'):
            return
        first, second = self.ui.frameSlider_2.value(), self.ui.frameSlider_3.value()
        self.ui.frameNumLabel.setText(f'A {first} / B {second}')
        self.graph_plotter.updateGraph(first, "上組 A", "下組 B", frame_num_2=second)

    def mousePressEvent(self, event):
        view_rect = self.ui.FrameView.rect()
        pos = event.pos()

        if not view_rect.contains(pos):
            return
        if self.video_loader.video_name is not None:
            self.updateFrame(frame_num=self.ui.frameSlider.value())

    def keyPressEvent(self, event):
        if event.key() == ord('D') or event.key() == ord('d'):
            self.ui.frameSlider.setValue(self.ui.frameSlider.value() + 1)
        elif event.key() == ord('A') or event.key() == ord('a'):
            self.ui.frameSlider.setValue(self.ui.frameSlider.value() - 1)
        else:
            super().keyPressEvent(event)

    def setupComponents(self): 
        self.person_selector = PersonSelector()
        self.person_selector_2 = PersonSelector()
        self.kpt_selector = KptSelector()
        
        # 組件 1 & 2 (現有)
        self.pose_estimater = PoseEstimater(self.model)
        self.pose_estimater_2 = PoseEstimater(self.model)
        self.pose_analyzer = PoseAnalyzer(self.pose_estimater, self.K_S, self.K_F, self.F)
        self.pose_analyzer_2 = PoseAnalyzer(self.pose_estimater_2, self.K_S, self.K_F, self.F)
        self.image_drawer = ImageDrawer(self.pose_estimater, self.pose_analyzer)
        self.image_drawer_2 = ImageDrawer(self.pose_estimater_2, self.pose_analyzer_2)
        self.video_loader = VideoLoader(self.image_drawer, self.image_drawer_2)

        # 【新增】組件 3 & 4
        self.pose_estimater_3 = PoseEstimater(self.model)
        self.pose_estimater_4 = PoseEstimater(self.model)
        self.pose_analyzer_3 = PoseAnalyzer(self.pose_estimater_3, self.K_S, self.K_F, self.F)
        self.pose_analyzer_4 = PoseAnalyzer(self.pose_estimater_4, self.K_S, self.K_F, self.F)
        self.image_drawer_3 = ImageDrawer(self.pose_estimater_3, self.pose_analyzer_3)
        self.image_drawer_4 = ImageDrawer(self.pose_estimater_4, self.pose_analyzer_4)
        self.video_loader_2 = VideoLoader(self.image_drawer_3, self.image_drawer_4)

        self.graph_plotter = GraphPlotter(self.pose_analyzer, self.pose_analyzer_3, speed_name=self.speed_angle_names, title_names=self.title_names)
        for analyzer in (self.pose_analyzer, self.pose_analyzer_2, self.pose_analyzer_3, self.pose_analyzer_4):
            analyzer.viewer3D.auto_save = False

        self.spin_direction_widget = SpinDirectionWidget()
        spin_layout = QVBoxLayout(self.ui.spinDirectionWidget)
        spin_layout.setContentsMargins(0, 0, 0, 0)
        spin_layout.addWidget(self.spin_direction_widget)
        self.spin_direction_widget2 = SpinDirectionWidget()
        spin_layout2 = QVBoxLayout(self.ui.spinDirectionWidget_2)
        spin_layout2.setContentsMargins(0, 0, 0, 0)
        spin_layout2.addWidget(self.spin_direction_widget2)

    def reset(self):
        # Stop any ongoing playback
        if getattr(self, '_play_timer', None) is not None and self._play_timer.isActive():
            self._play_timer.stop()
        self.is_play = False
        self.ui.playBtn.setText("▶︎")
        self.br_points = []
        self.compare_frame_offset = 0
        self.resetPhaseDetection()
        # self.reset3DInfo()
        if self.viewer3d is not None:
            self.viewer3d.reset()
        if self.viewer3d_2 is not None:
            self.viewer3d_2.reset()
        self.person_selector.reset()
        self.person_selector_2.reset() 
        self.kpt_selector.reset()
        self.pose_estimater.reset()
        self.pose_analyzer.reset()
        self.graph_plotter.reset()
        self.image_drawer.reset()
        self.pose_estimater_2.reset()
        self.pose_analyzer_2.reset()
        self.image_drawer_2.reset()

        self.pose_estimater_3.reset()
        self.pose_analyzer_3.reset()
        self.pose_analyzer_3.reset()
        self.image_drawer_3.reset()
        self.pose_estimater_4.reset()
        self.pose_analyzer_4.reset()
        self.pose_analyzer_4.reset()
        self.image_drawer_4.reset()

        self.view_scene.clear()
        self.curve_scene.clear()
        self.curve_scene_3.clear()
        self.curve_scene_4.clear()
        # 清除舊的 SliderColorOverlay
        if self.overlay is not None:
            self.overlay.setParent(None)
            self.overlay = None

        # 清空關鍵幀視圖
        if hasattr(self.ui, 'fcView') and self.ui.fcView.scene() is not None:
            self.ui.fcView.scene().clear()
            self.ui.fcView.scene().deleteLater()
        if hasattr(self.ui, 'merView') and self.ui.merView.scene() is not None:
            self.ui.merView.scene().clear()
            self.ui.merView.scene().deleteLater()
        if hasattr(self.ui, 'brView') and self.ui.brView.scene() is not None:
            self.ui.brView.scene().clear()
            self.ui.brView.scene().deleteLater()
   
    def loadVideo(self, is_processed:bool = False, video_path:str = None, video_path_2:str = None, video_path_3:str = None, video_path_4:str = None):
        if self.video_loader.is_loading or self.video_loader_2.is_loading:
            QMessageBox.information(self, "影片讀取中", "請等待兩組影片載入完成後再選擇。")
            return
        paths = (video_path, video_path_2, video_path_3, video_path_4)
        if not all(paths):
            dialog = CompareVideoDialog(is_processed, paths, self)
            if dialog.exec_() != QDialog.Accepted:
                return
            paths = dialog.paths()
        error = validate_comparison_paths(paths)
        if error:
            QMessageBox.warning(self, "無法載入比較影片", error)
            return
        # Commit all four selections together; cancel leaves the old comparison intact.
        video_path, video_path_2, video_path_3, video_path_4 = paths
        if self.is_play:
            self.ui.playBtn.click()
        
        # 清理舊影片的追踪狀態、濾波器、GPU 內存
        for pose_est in [self.pose_estimater, self.pose_estimater_2, self.pose_estimater_3, self.pose_estimater_4]:
            if pose_est is not None:
                pose_est.reset()
        
        self.viewer3d = self.pose_analyzer.viewer3D
        self.viewer3d_2 = self.pose_analyzer_3.viewer3D
        self.is_processed = is_processed
        self.video_loader.loadVideo(video_path, video_path_2)
        self.video_loader_2.loadVideo(video_path_3, video_path_4)
        for field, path in zip(self.comparison_path_fields, paths):
            field.setText(os.path.abspath(path))
            field.setToolTip(os.path.abspath(path))
            field.setCursorPosition(0)
        self.checkVideoLoad()

    def loadProcessedData(self):
        self._load_saved_comparison(True)

    def load2DProcessedData(self):
        self._load_saved_comparison(False)

    def _load_saved_comparison(self, is_processed):
        loaders = (self.video_loader, self.video_loader_2)
        rapsodo_paths = [os.path.join(loader.folder_path, f"{loader.video_name_2}_BRandRapsodo.json")
                         for loader in loaders]
        event_paths = ([os.path.join(loader.folder_path, f"{loader.video_name}_keyframes.json")
                        for loader in loaders] if is_processed else rapsodo_paths)
        self.display_keyframe_data_from_json(*event_paths, is_processed=is_processed)
        for path, widgets in zip(rapsodo_paths, (
                (self.ui.label_3, self.ui.label_8, self.ui.label_4, self.spin_direction_widget),
                (self.ui.label_6, self.ui.label_7, self.ui.label_9, self.spin_direction_widget2))):
            self.display_roi_from_json(path, *widgets)

        issues = []
        for group, (loader, estimators, analyzer, metadata_path) in enumerate(zip(
                loaders, ((self.pose_estimater, self.pose_estimater_2), (self.pose_estimater_3, self.pose_estimater_4)),
                (self.pose_analyzer, self.pose_analyzer_3), rapsodo_paths)):
            metadata = KeyframeJsonLoader(metadata_path).load() or {}
            loader.frame_offset = int(metadata.get('Analysis', {}).get('frame_offset', -2))
            for camera_index, (name, estimator) in enumerate(zip((loader.video_name, loader.video_name_2), estimators)):
                result = JsonLoader(loader.folder_path, name).load()
                if result is None:
                    issues.append(f"{'上組 A' if group == 0 else '下組 B'} {('CF', 'CS')[camera_index]} 缺少骨架 JSON")
                    continue
                estimator.setProcessedData(result[1])
            # Prepare each group's complete curve on its own timeline. Alignment
            # is applied only when drawing, never while building analysis data.
            for frame in range(int(loader.total_frames or 0)):
                first, second = (estimator.getPersonDf(frame_num=frame) for estimator in estimators)
                if first is None or second is None or first.empty or second.empty:
                    continue
                try:
                    analyzer.addAnalyzeInfo(frame, {"keypoints": first.iloc[0]['keypoints']},
                                           {"keypoints": second.iloc[0]['keypoints']})
                except (ValueError, TypeError, IndexError, KeyError, np.linalg.LinAlgError) as exc:
                    print(f"[Compare] group {group + 1}, frame {frame}: {exc}")
            if analyzer.analyze_df.empty:
                issues.append(f"{'上組 A' if group == 0 else '下組 B'} 沒有可用角度資料")
        self.graph_status.setText("；".join(issues) if issues else
                                 "肩髖分離角度比較｜上下拉桿分別定位；底部同步拖曳保留目前對齊差距。")
        self._update_phase_visualization()

    def checkVideoLoad(self):
        """檢查影片是否讀取完成，並更新 UI 元素。"""
        # 檢查是否有影片名稱，若無則不執行後續操作
        if not self.video_loader.video_name:
            return

        # 若影片正在讀取中，定時檢查讀取狀況
        if self.video_loader.is_loading or self.video_loader_2.is_loading:
            self.ui.videoNameLabel.setText("讀取影片中")
            QTimer.singleShot(100, self.checkVideoLoad)  # 每100ms 檢查一次
            return
        if not self.video_loader.total_frames or not self.video_loader_2.total_frames:
            self.ui.videoNameLabel.setText("載入失敗，請重新選擇兩組影片")
            return
        # 影片讀取完成後更新 UI 元素
        self.updateVideoInfo()

    def updateVideoInfo(self):
        skeleton = self.ui.showSkeletonCheckBox.isChecked()
        bbox = self.ui.showBboxCheckBox.isChecked()
        self.reset()
        for loader, slider, label in ((self.video_loader, self.ui.frameSlider_2, self.ui.frameNumLabel_2),
                                      (self.video_loader_2, self.ui.frameSlider_3, self.ui.frameNumLabel_3)):
            self.initFrameSlider(loader.total_frames, slider, label)
        self.model.setImageSize(self.video_loader.video_size)
        self.ui.videoNameLabel.setText(self.video_loader_2.video_name)
        self.ui.videoNameLabel_3.setText(self.video_loader.video_name)
        width, height = self.video_loader.video_size
        self.ui.ResolutionLabel.setText(f"{width} × {height}")
        self._load_saved_comparison(self.is_processed)
        self._configure_master_slider(0)
        self.initGraph()
        for drawer in (self.image_drawer, self.image_drawer_2, self.image_drawer_3, self.image_drawer_4):
            drawer.setShowSkeleton(skeleton)
            drawer.setShowBbox(bbox)
        self.updateFrame(0)

    def _update_compare_frame_offset(self):
        """以兩組 preparation_frame 對齊，更新第二組播放偏移。"""
        if self.preparation_frame is None or self.preparation_frame_2 is None:
            self.compare_frame_offset = 0
            return
        self.compare_frame_offset = int(self.preparation_frame_2) - int(self.preparation_frame)

    def _get_aligned_frame_num_for_group2(self, frame_num_group1: int) -> int:
        """將第一組當前幀映射到第二組對齊幀，超界時以邊界幀填補。"""
        total_2 = int(getattr(self.video_loader_2, 'total_frames', 0) or 0)
        if total_2 <= 0:
            return 0
        aligned = int(frame_num_group1) + int(self.compare_frame_offset)
        return max(0, min(aligned, total_2 - 1))

    def showImage(self, image: np.ndarray, scene: QGraphicsScene, GraphicsView: QGraphicsView,
                  draw_marker: bool = True, defer_refit: bool = True, process_events: bool = True): 
        scene.clear()
        # 處理 None 圖像的情況（視頻加載過程中可能發生）
        if image is None:
            return
        image = image.copy()
        if draw_marker:
            image = cv2.circle(image, (0, 0), 10, (0, 0, 255), -1)
        w, h = image.shape[1], image.shape[0]
        bytesPerline = 3 * w
        qImg = QImage(image, w, h, bytesPerline, QImage.Format_RGB888).rgbSwapped()   
        pixmap = QPixmap.fromImage(qImg)
        scene.addPixmap(pixmap)
        GraphicsView.setScene(scene)
        # 處理待處理事件，確保 View 大小已正確初始化（關鍵幀可關閉避免跳動）
        if process_events:
            QApplication.processEvents()
        self._fit_view_no_crop(GraphicsView)
        # 主畫面保留延遲二次縮放；關鍵幀可關閉以避免瞬間放大/縮小感
        if defer_refit:
            QTimer.singleShot(0, lambda view=GraphicsView: self._fit_view_no_crop(view))

    def _get_frame_analysis_data(self, frame_num: int) -> dict:
        """獲取當前幀的所有分析數據
        
        Args:
            frame_num: 幀號
            
        Returns:
            包含所有速度和角度數據的字典
        """
        speed_names = ["ankle_speed", "left_knee_3d", "shoulder_hiple_angle", "shoulder_angle", "wrist_speed", "ankle_x_diff"]
        frame_data = {}
        frame_data_2 = {}
        
        for speed_name in speed_names:
            _, value = self.pose_analyzer.get_frame_angleOrSpeed_data(frame_num, speed_name=speed_name)
            _, value_2 = self.pose_analyzer_3.get_frame_angleOrSpeed_data(frame_num, speed_name=speed_name)
            frame_data[speed_name] = value if value is not None else 0.0
            frame_data_2[speed_name] = value_2 if value_2 is not None else 0.0

        return frame_data, frame_data_2
    
    # def _update_analysis_labels(self, frame_data: dict):
        """更新分析數據標籤顯示
        
        Args:
            frame_data: 包含分析數據的字典
        """
        # self.ui.ankle_label.setText(f'{frame_data["ankle_speed"]:.2f}(m/s)')
        # self.ui.knee_label.setText(f'{frame_data["left_knee_3d"]}°')
        # self.ui.shouhip_label.setText(f'{frame_data["shoulder_hiple_angle"]}°')
        # self.ui.shoulder_label.setText(f'{frame_data["shoulder_angle"]}°')
        # self.ui.wrist_label.setText(f'{frame_data["wrist_speed"]:.2f}(m/s)')

    def _display_keyframe_with_text(self, frame_num: int, view_widget: QGraphicsView, keyframe_type: str, frame_data: dict = None):
        """顯示關鍵幀影像並同步更新 UI 文字資訊"""
        # 獲取圖像並繪製骨骼信息
        image, _ = self.video_loader.getVideoImage(frame_num)
        drawed_img = self.image_drawer.drawInfo(image, frame_num, self.pose_estimater.kpt_buffer)
        
        # 如果指定了 keyframe_type，繪製相應的分析信息
        if keyframe_type is not None:
            drawed_img = self.image_drawer.drawAngleInfo(image, frame_num, keyframe_type=keyframe_type, start_frame=self.preparation_frame)
        
        # 先更新文字造成的 layout 變化，再做關鍵幀 fit，避免先放大後縮小
        temp_scene = QGraphicsScene()
        self.showImage(drawed_img, temp_scene, view_widget, draw_marker=False, defer_refit=False, process_events=False)

    def _update_keyframe_label(self, frame_num: int, keyframe_type: str):
        """更新關鍵幀的UI標籤（無論是否有對應的圖視圖）
        
        Args:
            frame_num: 關鍵幀幀號
            keyframe_type: 關鍵幀類型 ('foot_contact', 'max_shoulder_er', 'release')
        """
        # 先提取數據
        image, _ = self.video_loader.getVideoImage(frame_num)
        if image is None:
            return
        
        # 調用 drawAngleInfo 來提取分析數據
        self.image_drawer.drawAngleInfo(image, frame_num, keyframe_type=keyframe_type, start_frame=self.preparation_frame)
        
        # 根據類型更新對應的 label
        if keyframe_type == 'foot_contact':
            shoulder_hip_angle = self.image_drawer.keyframe_shoulder_hip_angle
            stride_distance = self.image_drawer.keyframe_stride_distance
            if shoulder_hip_angle is not None and stride_distance is not None:
                self.ui.FC_label.setText(f"{shoulder_hip_angle}\n{stride_distance*100*0.818:.1f}")
                self.ui.FC_label_unit.setText("°\ncm")
        elif keyframe_type == 'max_shoulder_er':
            shoulder_angle = self.image_drawer.keyframe_shoulder_angle
            if shoulder_angle is not None:
                self.ui.MER_label_3.setText(f"{shoulder_angle}")
                self.ui.MER_label_unit.setText("°\n")
        elif keyframe_type == 'release':
            wrist_speed = self.image_drawer.keyframe_wrist_speed
            extension_distance = self.image_drawer.keyframe_extension_distance
            if wrist_speed is not None and extension_distance is not None:
                self.ui.BR_label_3.setText(f"{wrist_speed:.1f}\n{extension_distance*100*0.818:.1f}")
                self.ui.BR_label_unit.setText("m/s\ncm")

    @staticmethod
    def _metric_text(value):
        try:
            return f"{float(value):.1f}" if np.isfinite(float(value)) else "—"
        except (TypeError, ValueError):
            return "—"

    def display_keyframe_data_from_json(self, json_file_path, json_file_path_2=None, is_processed=False):
        loaded = False
        fps_values = []
        for group, (path, loader) in enumerate(zip((json_file_path, json_file_path_2),
                                                  (self.video_loader, self.video_loader_2))):
            data = KeyframeJsonLoader(path).load() or {}
            loaded = loaded or bool(data)
            fps_values.append(float(data.get('fps') or loader.video_fps or 60))
            suffix = "_2" if group else ""
            if is_processed:
                preparation = (data.get('knee_up') or {}).get('frame_number')
                events = [data.get(key) or {} for key in ('foot_contact', 'max_shoulder_er', 'release')]
                frame_key = 'frame_number'
                values = ((events[0].get('shoulder_hip_angle_deg'), events[0].get('stride_distance_cm')),
                          (events[1].get('shoulder_angle_deg'),),
                          (events[2].get('wrist_speed_mps'), events[2].get('extension_distance_cm')))
            else:
                keyframes = data.get('Keyframes') or {}
                preparation = keyframes.get('preparation_frame')
                events = [keyframes.get(key) or {} for key in ('FC', 'MER', 'BR')]
                frame_key = 'frame'
                values = ((None, events[0].get('stride_distance')), (events[1].get('shoulder_angle'),),
                          (events[2].get('wrist_speed'), events[2].get('extension_distance')))
            setattr(self, 'preparation_frame' + suffix, preparation if isinstance(preparation, int) else None)
            labels = ((self.ui.FC_label, self.ui.MER_label_3, self.ui.BR_label_3),
                      (self.ui.FC_label_3, self.ui.MER_label_4, self.ui.BR_label_7))[group]
            for attr, event, label, metrics in zip(('foot_contact_frame', 'max_shoulder_angle_frame', 'wrist_speed_frame'),
                                                  events, labels, values):
                frame = event.get(frame_key)
                valid = isinstance(frame, int) and 0 <= frame < int(loader.total_frames or 0)
                setattr(self, attr + suffix, frame if valid else None)
                label.setText('\n'.join(self._metric_text(value) for value in metrics))
                label.setToolTip("")
        self.ui.FC_label_unit.setText("°\ncm")
        self.ui.FC_label_unit_3.setText("°\ncm")
        self.ui.MER_label_unit.setText("°")
        self.ui.MER_label_unit_2.setText("°")
        self.ui.BR_label_unit.setText("m/s\ncm")
        self.ui.BR_label_unit_3.setText("m/s\ncm")
        self.graph_plotter.set_video_fps(*fps_values)
        self._sync_keyframe_controls()
        self._update_compare_frame_offset()
        return loaded

    def display_roi_from_json(self, json_path, label_widget: QLabel, label_widget_2: QLabel, text_label: QLabel = None, direction_widget: SpinDirectionWidget = None):
        label_widget.clear()
        label_widget_2.clear()
        if text_label is not None:
            text_label.clear()
        if direction_widget is not None:
            direction_widget.setDirection(None)
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        
        # 2. 取得 Base64 字串 (假設我們要看 roi_frame_1)
        base64_str = data.get("roi_frame_1")
        base64_str_2 = data.get("roi_frame_2")
        wrist_point = data.get("wrist_point", None)
        if isinstance(wrist_point, list) and len(wrist_point) >= 2:
            self.br_points.append(wrist_point)
        if not base64_str or not base64_str_2:
            print("找不到影像數據")
            return

        # 3. 將 Base64 解碼回二進位 Bytes
        img_bytes = base64.b64decode(base64_str)
        img_bytes_2 = base64.b64decode(base64_str_2)

        # 4. 使用 QImage 直接讀取 Bytes
        # QImage.fromData 會自動偵測格式 (你在 export 時用的是 .png)
        q_img = QImage.fromData(img_bytes)
        q_img_2 = QImage.fromData(img_bytes_2)
        
        # 5. 轉換為 Pixmap 並顯示在 Label 上
        pixmap = QPixmap.fromImage(q_img)
        label_widget.setPixmap(pixmap)
        label_widget.setScaledContents(True) # 讓圖片自動適應 Label 大小

        pixmap_2 = QPixmap.fromImage(q_img_2)
        label_widget_2.setPixmap(pixmap_2)
        label_widget_2.setScaledContents(True) # 讓圖片自動適應 Label 大小

        metrics = data.get("metrics") or {}
        if text_label:
            text_label.setText(f"球速:{self._metric_text(metrics.get('velocity_mph'))} kph\n轉速:{metrics.get('total_spin_rpm', 'N/A')} rpm")
            if direction_widget:
                direction_widget.setDirection(metrics.get('spin_direction', 'N/A'))

    def _detect_preparation_phase(self, frame_num: int) -> bool:
        """檢測準備階段
        
        Returns:
            如果檢測到準備階段返回 True
        """
        # 在前導腳著地前：開始抬腳後，追蹤最低點；直到下一幀開始下放才確認關鍵幀
        if self.foot_contact_frame is not None or self.preparation_frame is not None:
            return False
        
        person_data = self.pose_estimater_2.getPersonDf(frame_num=frame_num, is_select=True)
        if person_data is None:
            return False
        keypoints = person_data['keypoints'].iloc[0]
        left_knee_y = keypoints[13][1]  # 13: left knee
        left_hip_y = keypoints[11][1]  # 11: left hip

        if self.prev_left_knee_y is None:
            self.prev_left_knee_y = left_knee_y
            return False
        
        # 先判斷是否進入抬腳階段（且左膝高度已達到條件範圍）
        if not self.preparation_tracking_started:
            if left_knee_y < left_hip_y + 50 and left_knee_y < self.prev_left_knee_y:
                self.preparation_tracking_started = True
                self.min_left_knee_y = left_knee_y
                self.candidate_preparation_frame = frame_num
                self.preparation_descend_count = 0
            self.prev_left_knee_y = left_knee_y
            return False

        # 抬腳中：持續更新最低點
        if left_knee_y < left_hip_y + 50 and left_knee_y < self.min_left_knee_y:
            self.min_left_knee_y = left_knee_y
            self.candidate_preparation_frame = frame_num

        # 連續 2 幀下放（y 連續變大）才確認關鍵幀
        if left_knee_y > self.prev_left_knee_y:
            self.preparation_descend_count += 1
        else:
            self.preparation_descend_count = 0

        if self.candidate_preparation_frame is not None and self.preparation_descend_count >= 2:
            self.preparation_frame = self.candidate_preparation_frame
            print(f"✓ 檢測到抬腳關鍵幀: frame {self.preparation_frame}, y={self.min_left_knee_y:.2f}")
            self.prev_left_knee_y = left_knee_y
            return True

        self.prev_left_knee_y = left_knee_y
        return False
    
    def _detect_foot_contact(self, frame_num: int, frame_data: dict) -> bool:
        """檢測足踝接觸階段
        
        Args:
            frame_num: 當前幀號
            frame_data: 當前幀的分析數據
            
        Returns:
            如果檢測到足踝接觸返回 True
        """
        if self.foot_contact_frame is not None or self.preparation_frame is None:
            return False
        
        ankle_speed = frame_data["ankle_speed"]
        knee_angle = frame_data["left_knee_3d"]
        shoulder_angle = frame_data["shoulder_angle"]
        ankle_diff = frame_data["ankle_x_diff"]
        # print(f"Frame {frame_num}: ankle_speed={ankle_speed:.2f}, ankle_x_diff={ankle_diff:.2f}")
        if ankle_speed <= 3.0 and ankle_diff > 1.0:
        # if ankle_speed <= 2.5 and 100 <= knee_angle <= 140 and shoulder_angle >= 10:
            self.foot_contact_frame = frame_num
            self.ui.footContact_label.setText(f"第{frame_num}幀")
            self.ui.footContact_ptn.setDisabled(False)
            print(f"✓ 檢測到足踝接觸: frame {frame_num}")
            
            # 無論是否有 fcView，都更新文字標籤
            self._update_keyframe_label(frame_num, keyframe_type='foot_contact')
            
            # 如果有 fcView，則額外顯示關鍵幀圖像
            if hasattr(self.ui, 'fcView'):
                QTimer.singleShot(100, lambda: self._display_keyframe_with_text(frame_num, self.ui.fcView, keyframe_type='foot_contact', frame_data=frame_data))
            return True
        return False
    
    def _track_max_shoulder_rotation(self, frame_num: int, frame_data: dict) -> bool:
        """追蹤最大肩外旋
        
        Args:
            frame_num: 當前幀號
            frame_data: 當前幀的分析數據
            
        Returns:
            如果更新了最大外旋幀返回 True
        """
        if self.foot_contact_frame is None or frame_num <= self.foot_contact_frame:
            return False
        
        if self.mer_locked is True:
            return False
        
        shoulder_angle = frame_data["shoulder_angle"]
        
        if shoulder_angle > self.max_shoulder_angle_value:
            self.max_shoulder_angle_value = shoulder_angle
            self.max_shoulder_angle_frame = frame_num
            return False
        elif self.max_shoulder_angle_value > 0 and (self.max_shoulder_angle_value - shoulder_angle) > 3.0:
            self.mer_locked = True
            self.ui.maxER_label.setText(f"第{self.max_shoulder_angle_frame}幀")
            self.ui.maxER_ptn.setDisabled(False)
            
            # 無論是否有 merView，都更新文字標籤
            self._update_keyframe_label(self.max_shoulder_angle_frame, keyframe_type='max_shoulder_er')
            
            # 如果有 merView，則額外顯示關鍵幀圖像
            if hasattr(self.ui, 'merView'):
                QTimer.singleShot(100, lambda: self._display_keyframe_with_text(self.max_shoulder_angle_frame, self.ui.merView, keyframe_type='max_shoulder_er', frame_data=frame_data))
            return True
        return False
    
    def _detect_release_point(self, frame_num: int, frame_data: dict) -> bool:
        """檢測釋放點
        
        Args:
            frame_num: 當前幀號
            frame_data: 當前幀的分析數據
            
        Returns:
            如果更新了釋放點幀返回 True
        """
        if self.max_shoulder_angle_frame is None or frame_num <= self.max_shoulder_angle_frame:
            return False
        
        wrist_speed = frame_data["wrist_speed"]

        # 限制條件：右手腕必須高於右肩（影像座標 y 越小代表位置越高）
        person_data = self.pose_estimater.getPersonDf(frame_num=frame_num, is_select=True)
        if person_data is None or person_data.empty:
            return False

        keypoints = person_data['keypoints'].iloc[0]
        right_shoulder_y = keypoints[6][1]  # 6: right shoulder
        right_wrist_y = keypoints[10][1]    # 10: right wrist

        
        if wrist_speed > self.max_wrist_speed_after_er and right_wrist_y <= right_shoulder_y:
            self.max_wrist_speed_after_er = wrist_speed
            self.wrist_speed_frame = frame_num
            return False
        elif self.max_wrist_speed_after_er > 0 and (self.max_wrist_speed_after_er - wrist_speed) > 1.0 and self.release_locked == False:
            self.release_locked = True
            self.ui.release_label.setText(f"第{self.wrist_speed_frame}幀")
            self.ui.release_ptn.setDisabled(False)

            # 記錄右手腕位置 (is_kpt=True 時返回 numpy array)
            person_data_2 = self.pose_estimater_2.getPersonDf(frame_num=self.wrist_speed_frame, is_select=True, is_kpt=True)
            self.br_points.append(person_data_2[10]) if person_data_2 is not None else None
            
            # 無論是否有 brView，都更新文字標籤
            self._update_keyframe_label(self.wrist_speed_frame, keyframe_type='release')
            
            # 如果有 brView，則額外顯示關鍵幀圖像
            if hasattr(self.ui, 'brView'):
                QTimer.singleShot(100, lambda: self._display_keyframe_with_text(self.wrist_speed_frame, self.ui.brView, keyframe_type='release', frame_data=frame_data))
            return True
        return False
    
    def _update_phase_visualization(self):
        phases = []
        for suffix in ("", "_2"):
            frames = [getattr(self, attr + suffix) for attr in (
                'preparation_frame', 'foot_contact_frame', 'max_shoulder_angle_frame', 'wrist_speed_frame')]
            frames.append(frames[-1] + 7 if frames[-1] is not None else None)
            phases.append([(start, end, QColor(color)) for start, end, color in zip(
                frames, frames[1:], ("#1C7CDB", "#C90E0E", "#F2A900", "#00AC2D"))
                if start is not None and end is not None and end > start])
        self.graph_plotter.set_motion_phases(*phases)
        self.graph_plotter.set_compare_frame_offset(self.compare_frame_offset)

    def showGraph(self, container_widget: QWidget):
        layout = container_widget.layout()
        
        # 1. 如果沒有 Layout，就建立一個新的
        if layout is None:
            layout = QVBoxLayout(container_widget)
        
        # 2. 【關鍵】無論是新建立的還是原本就有的，都要強制去除邊距
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 3. 如果圖表還沒被加入，才加入 (避免重複加入報錯)
        # 檢查 layout 中是否已經包含 graph widget
        if layout.indexOf(self.graph_plotter.graph) == -1:
            layout.addWidget(self.graph_plotter.graph)

    def _get_playback_rate(self) -> float:
        """讀取慢動作倍率（1.0x 正常，小於 1.0x 再額外放慢）。"""
        try:
            rate = float(self.ui.slowMotionInput.currentText())
        except (TypeError, ValueError):
            rate = 1.0
        if rate < 1.0:
            rate *= 0.5
        return max(0.05, min(rate, 4.0))

    def _get_play_interval_ms(self) -> int:
        """依影片 FPS 與倍率計算播放間隔（毫秒）。"""
        fps = float(getattr(self.video_loader, 'video_fps', 0) or 0)
        if fps <= 0:
            fps = 30.0
        rate = self._get_playback_rate()
        return max(1, int(round(1000.0 / (fps * rate))))

    def _on_play_speed_changed(self, _text: str):
        """播放中切換倍率時，立即更新 timer 間隔。"""
        if getattr(self, '_play_timer', None) is not None and self._play_timer.isActive():
            self._play_timer.start(self._get_play_interval_ms())

    def playFrame(self, start_num=0):
        if self._play_timer is None:
            self._play_timer = QTimer(self)
            self._play_timer.timeout.connect(self._playNextFrame)
        if self.ui.frameSlider.value() >= self.ui.frameSlider.maximum():
            self.ui.frameSlider.setValue(self.ui.frameSlider.minimum())
        self._play_timer.start(self._get_play_interval_ms())

    def _playNextFrame(self):
        if not self.is_play:
            return
        slider = self.ui.frameSlider
        if slider.value() < slider.maximum():
            slider.setValue(slider.value() + 1)
        else:
            self._play_timer.stop()
            self.is_play = False
            self.ui.playBtn.setText("▶︎")

    def phaseDetection(self, frame_num: int):
        """階段檢測主方法
        
        執行順序：
        1. 獲取當前幀的分析數據
        2. 更新UI標籤顯示
        3. 依序檢測各個投球階段
        4. 更新視覺化元素（輔助線、顏色區段）
        """
        # 1. 獲取分析數據
        frame_data, frame_data_2 = self._get_frame_analysis_data(frame_num)
        
        # 輸出肩髖分離角度
        # shoulder_hiple_angle = frame_data.get("shoulder_hiple_angle", 0.0)
        # print(f"Frame {frame_num}: 肩髖分離角度 = {shoulder_hiple_angle:.1f}°, 肩外旋角度 = {frame_data.get('shoulder_angle', 0.0):.1f}°")
        
        # 2. 更新UI標籤
        # self._update_analysis_labels(frame_data)
        
        # 3. 依序檢測各個階段
        # self._detect_preparation_phase(frame_num)
        # self._detect_foot_contact(frame_num, frame_data)
        # self._track_max_shoulder_rotation(frame_num, frame_data)
        # self._detect_release_point(frame_num, frame_data)
        
        # 4. 更新視覺化
        self._update_phase_visualization()

    def analyzeFrame(self):
        # Comparison only replays saved results; scrubbing never runs inference.
        self.updateFrame(self.ui.frameSlider.value())

    def updateFrame(self, frame_num):
        self._render_group(0, frame_num)
        self._render_group(1, frame_num + self.compare_frame_offset)
        self._update_graph()

    def checkBox(self, visible:bool):
        elements = [
            # self.ui.showSkeletonCheckBox,
            # self.ui.showBboxCheckBox,
            self.ui.selectCheckBox,
            self.ui.selectKptCheckBox,
            # self.ui.tab
        ]
        
        for element in elements:
            element.setVisible(visible)

    def toggleSelect(self, state:int):
        if not self.ui.showSkeletonCheckBox.isChecked():
            # self.ui.selectCheckBox.setCheckState(0)
            QMessageBox.warning(self, "無法選擇人", "請選擇顯示人體骨架!")
            return
        if state == 2: 
            frame_num = self.ui.frameSlider.value()
            self.person_selector.select(search_person_df=self.pose_estimater.getPersonDf(frame_num=frame_num))
            self.person_selector_2.select(search_person_df=self.pose_estimater_2.getPersonDf(frame_num=frame_num))

            self.pose_estimater.setPersonId(self.person_selector.selected_id)
            self.pose_estimater_2.setPersonId(self.person_selector_2.selected_id)
        else:
            self.pose_estimater.setPersonId(None)
            self.pose_estimater_2.setPersonId(None)
        self.updateFrame(self.ui.frameSlider.value())

    def toggleKptSelect(self, state:int):
        """Toggle keypoint selection and trajectory visualization."""
        if not self.ui.selectCheckBox.isChecked():
            self.ui.selectKptCheckBox.setCheckState(0)
            QMessageBox.warning(self, "無法選擇關節點", "請選擇人!")
            return
        if state == 2:  
            self.pose_estimater.setKptId(10)
            self.image_drawer.setShowTraj(True)
        else:
            self.pose_estimater.setKptId(None)
            self.image_drawer.setShowTraj(False)
        self.updateFrame(self.ui.frameSlider.value())

    def toggleShowSkeleton(self, state):
        for drawer in (self.image_drawer, self.image_drawer_2, self.image_drawer_3, self.image_drawer_4):
            drawer.setShowSkeleton(state == Qt.Checked)
        self._refresh_current_frames()

    def toggleShowBbox(self, state):
        for drawer in (self.image_drawer, self.image_drawer_2, self.image_drawer_3, self.image_drawer_4):
            drawer.setShowBbox(state == Qt.Checked)
        self._refresh_current_frames()

    def toggleShowGraph(self, name, state:int):
        enabled = state == 2
        self.graph_plotter.setPlotVisibility(name, enabled)
        self.graph_plotter.updateGraph(self.ui.frameSlider.value(), self.video_loader.video_name, self.video_loader_2.video_name)

    def setFrameOffset(self, offset: int):
        """
        設置幀偏移以同步兩個視頻
        
        Args:
            offset: 幀偏移值
                   負值表示 video_path_2 延遲（需要向前讀取視頻_2 的幀）
                   正值表示 video_path 延遲（需要向前讀取視頻_1 的幀）
                   
        例如：如果 video_path 比 video_path_2 快 2 幀，設置 offset=-2
        """
        self.frame_offset = offset
        self.video_loader.frame_offset = offset
        self.video_loader_2.frame_offset = offset
        # 更新當前幀顯示
        current_frame = self.ui.frameSlider.value()
        self.updateFrame(current_frame)
        print(f"✓ 幀偏移已設置為: {offset} (負值: video_path_2 延遲，正值: video_path 延遲)")

    def toggleZoomIn(self, checked:bool):
        """
        當 zoomInButton 被選中時放大，取消選中時顯示原本的範圍。
        
        checked: True 時放大到關鍵動作幀段，False 時顯示全景
        """
        if not hasattr(self.graph_plotter, 'plots') or not self.graph_plotter.plots:
            return
        
        total_frames = self.video_loader.total_frames if self.video_loader.video_name else 100
        
        if checked:
            # 放大模式：顯示從腳踝接觸到釋放的關鍵動作段
            x_min = self.max_shoulder_angle_frame - 10 if self.max_shoulder_angle_frame else 0
            x_max = self.max_shoulder_angle_frame + 10 if self.max_shoulder_angle_frame else total_frames
            self.graph_plotter.zoomIn(x_min, x_max)
        else:
            # 原景模式：顯示全部幀
            self.graph_plotter.zoomIn(0, total_frames)

    def _draw_br_points(self, image: np.ndarray) -> np.ndarray:
        """在圖像上繪製釋放點 (BR Points)
        
        Args:
            image: 輸入圖像 (H x W x 3, BGR)
            
        Returns:
            繪製後的圖像
        """
        if not self.br_points or len(self.br_points) == 0:
            return image
        
        result = image.copy()
        label = self.getBRpointsLabel()
        
        # 顏色配置
        COLOR_POINT = (0, 0, 255)        # 紅色 - 個別點
        COLOR_CIRCLE = (253, 251, 115)       # 藍色 - 外圍圓
        POINT_RADIUS = 10
        
        # 繪製每個釋放點
        for i, point_data in enumerate(self.br_points):
            if point_data is None or len(point_data) < 2:
                continue
            
            try:
                x, y = int(point_data[0]), int(point_data[1])
                
                # 檢查座標是否在圖像範圍內
                if 0 <= x < result.shape[1] and 0 <= y < result.shape[0]:
                    label_text = label[i] if i < len(label) and label[i] != "N/A" else str(i + 1)
                    # 繪製點
                    # cv2.circle(result, (x, y), POINT_RADIUS, COLOR_POINT, -1)
                    # 繪製邊框
                    cv2.circle(result, (x, y), POINT_RADIUS, COLOR_CIRCLE, 4)
                    # 繪製序號
                    # cv2.putText(result, label_text, (x, y-POINT_RADIUS-10),
                    #            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 3)
            except (ValueError, TypeError):
                continue
     
        return result
    
    def getBRpointsLabel(self):
        video_name = getattr(self.video_loader, "video_name", "") or ""
        video_name_2 = getattr(self.video_loader_2, "video_name", "") or ""
        match = re.search(r"_P(\d+)$", video_name)
        match_2 = re.search(r"_P(\d+)$", video_name_2)
        if not match or not match_2:
            return ("N/A", "N/A")
        return (str(int(match.group(1))), str(int(match_2.group(1))))

    def resetBRPoints(self):
        """清除所有釋放點記錄"""
        reply = QMessageBox.question(
            self, "確認", "確定要清除所有釋放點 (BR) 記錄嗎?",
            QMessageBox.Yes | QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.br_points = []
            print("✓ 已清除所有釋放點記錄")
            # 如果當前正在顯示，則刷新畫面
            if self.show_br_points:
                self._refresh_current_frames()
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
        self._refresh_current_frames()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    model = Model()
    window = PoseVideoCompareTabControl(model)
    window.show()
    sys.exit(app.exec_())
