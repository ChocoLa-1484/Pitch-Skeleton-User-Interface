
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

import base64
import json
import os
from pathlib import Path

from Src.UI_Control.widgets.pitch_widget import SpinDirectionWidget
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(os.environ["CONDA_PREFIX"], "Library", "plugins", "platforms")
import vispy
vispy.use('pyqt5')
from PyQt5.QtWidgets import *
from PyQt5.QtGui import QColor, QImage, QPixmap, QPainter, QFont
from PyQt5.QtCore import Qt, QTimer, QRect
import numpy as np
import sys
import cv2
from Src.UI_Control.forms.video_ui_2 import Ui_video_widget
# from video_ui_graph import Ui_video_widget
from Src.UI_Control.rendering.vis_image_3d import ImageDrawer
from Src.UI_Control.controllers.selector import PersonSelector, KptSelector
from Src.cameras.video_control import VideoLoader, JsonLoader
from Src.pose.detect_skeleton import PoseEstimater
from Src.UI_Control.rendering.vis_graph_3d import GraphPlotter
from Src.analysis.analyze_3d import PoseAnalyzer
from Src.pose.model import Model
import pyqtgraph as pg
from Src.UI_Control.viewers.triangulate_3d_viewer import Triangulate3DViewer
import time
from Src.analysis.keyframe_export import SimpleKeyframeLogger
from Src.calibration import calib_store
from Src.analysis.analysis_io import atomic_json, read_optional_json
from Src.calibration.chessboard_cross.chessboard_metric import DEFAULT_CALIBRATION
from Src.analysis.metric_session import load_rig
from Src.UI_Control.viewers.metric_3d_viewer import MetricTriangulate3DViewer
from Src.analysis.analyze import PoseAnalyzer as PoseAnalyzer2D

class SliderColorOverlay(QWidget):
    def __init__(self, slider: QSlider, segments: list, segment_half_thickness: int = 10, fps=67.):
        """
        slider: 你要疊的 QSlider
        segments: list of tuples (start_frame, end_frame, color)
        """
        super().__init__(slider.parent())
        self.slider = slider
        self.fps = float(fps) if fps and fps > 0 else 67.
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
            label = f"{duration/self.fps:.2f}s"
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

class PoseVideoTabControl(QWidget):
    def __init__(self, model:Model, parent = None):
        super(PoseVideoTabControl, self).__init__(parent)
        self.ui = Ui_video_widget()
        self.ui.setupUi(self)
        self.model = model
        self.initVar()
        self.setupComponents()
        self.bindUI()
        self.setupCalibrationControls()

    def setupCalibrationControls(self):
        row = QHBoxLayout()
        self.calibration_combo = QComboBox()
        for label, method in [('PnP 棋盤校正', 'pnp'), ('8Points 棋盤尺度', '8point'), ('舊版估算（無公制校正）', 'legacy')]:
            self.calibration_combo.addItem(label, method)
        self.calibration_path = QLineEdit(str(DEFAULT_CALIBRATION / 'pnp' / 'stereo.json'))
        # 預設用學姊原本的方法：讀該球的 calibration.json（沒有才用內建 F）。
        # stereo.json 只對拍棋盤那天的相機位置有效，預設 PnP 會讓其他天的錄影無聲無息套錯校正
        self.calibration_combo.setCurrentIndex(self.calibration_combo.findData('legacy'))
        self.calibration_path.setEnabled(False)
        browse = QPushButton('選擇外參…')
        apply = QPushButton('套用校正')
        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(-1000, 1000)
        self.offset_spin.setToolTip('第二影片實際幀 = 第一影片幀 − 偏移；切換後請重新分析。')
        self.reference_spin = QSpinBox()
        self.reference_spin.setRange(0, 1000000)
        self.heel_combo = QComboBox()
        self.heel_combo.addItem('右後腳', 25)
        self.heel_combo.addItem('左後腳', 24)
        self.save_analysis_button = QPushButton('儲存分析 JSON')
        for widget in (QLabel('3D 外參'), self.calibration_combo, self.calibration_path, browse, apply,
                       QLabel('第二片偏移'), self.offset_spin, QLabel('初始幀'), self.reference_spin,
                       self.heel_combo, self.save_analysis_button):
            row.addWidget(widget)
        self.ui.verticalLayout_6.insertLayout(0, row)
        self.calibration_status = QLabel('預設：舊版估算（讀該球的 calibration.json）。PnP / 8Points：同一後腳腳跟的 3D 位移；需確認相機位置與棋盤校正時相同。')
        self.calibration_status.setWordWrap(True)
        self.ui.verticalLayout_6.insertWidget(1, self.calibration_status)
        self.calibration_combo.currentIndexChanged.connect(self._calibration_method_changed)
        browse.clicked.connect(self._browse_calibration)
        apply.clicked.connect(self._apply_selected_calibration)
        self.offset_spin.valueChanged.connect(self._invalidate_calibration)
        self.reference_spin.valueChanged.connect(self._invalidate_calibration)
        self.heel_combo.currentIndexChanged.connect(self._invalidate_calibration)
        self.calibration_path.textEdited.connect(self._invalidate_calibration)
        self.save_analysis_button.clicked.connect(lambda: self._complete_video_analysis(manual=True))
        events = QHBoxLayout()
        events.addWidget(QLabel('關鍵幀可先回放確認，再以目前幀修正：'))
        for title, attr in [('FC', 'foot_contact_frame'), ('MER', 'max_shoulder_angle_frame'), ('BR', 'wrist_speed_frame')]:
            button = QPushButton('設為 ' + title)
            button.clicked.connect(lambda checked=False, name=attr: self._set_manual_event(name))
            events.addWidget(button)
        events.addStretch()
        self.ui.verticalLayout_6.insertLayout(2, events)
        self._analysis_dirty = False
        self._calibration_ready = False
        self._manual_events = set()
        self.frame_offset = 0

    def _calibration_method_changed(self):
        method = self.calibration_combo.currentData()
        if method != 'legacy':
            self.calibration_path.setText(str(DEFAULT_CALIBRATION / method / 'stereo.json'))
        self.calibration_path.setEnabled(method != 'legacy')
        self._invalidate_calibration()

    def _browse_calibration(self):
        path, _ = QFileDialog.getOpenFileName(self, '選擇棋盤 stereo.json', self.calibration_path.text(), 'JSON (*.json)')
        if path:
            self.calibration_path.setText(path)
            self._invalidate_calibration()

    def _invalidate_calibration(self, *args):
        self.is_play = False
        self.ui.playBtn.setText('▶︎')
        if hasattr(self, '_play_timer'):
            self._play_timer.stop()
        self._calibration_ready = False
        self._analysis_dirty = False
        self.pose_estimater.reset()
        self.pose_estimater_2.reset()
        self.resetPhaseDetection()
        self.pose_analyzer.reset()
        self.pose_analyzer_2.reset()
        self.image_drawer.reset()
        self.image_drawer_2.reset()
        self.graph_plotter.reset()
        self.graph_plotter.plots = []
        for view in (self.ui.fcView, self.ui.merView, self.ui.brView):
            if view.scene() is not None:
                view.scene().clear()
        if self.overlay is not None:
            self.overlay.hide()
            self.overlay.deleteLater()
            self.overlay = None
        self.calibration_status.setText('設定已變更，請套用校正並從頭分析。')

    def _apply_selected_calibration(self):
        if not self.video_loader.total_frames:
            self.calibration_status.setText('請先載入兩部影片。')
            return False
        self._invalidate_calibration()
        try:
            self._create_viewer()
            self.initGraph()
            if self.is_processed:
                self.loadProcessedData()
            self.calibration_status.setText(self._calibration_description)
            return True
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self._calibration_ready = False
            self.calibration_status.setText('無法套用校正：' + str(exc))
            return False

    def _create_viewer(self):
        loader = self.video_loader
        method = self.calibration_combo.currentData()
        self._sources = {name: {'path': str(Path(path).resolve()), 'size': Path(path).stat().st_size,
                                'mtime_ns': Path(path).stat().st_mtime_ns}
                         for name, path in zip((loader.video_name, loader.video_name_2), (loader.video_path, loader.video_path_2))}
        if method == 'legacy':
            self.applyCalibration(loader.folder_path)
            viewer = Triangulate3DViewer(self.K_S, self.K_F, self.F)
            description = '舊版估算：固定倍率，距離與速度不具校正精度。'
        else:
            rig = load_rig(self.calibration_path.text())
            if rig['method'] != method:
                raise ValueError('所選方法與 stereo.json 的 method 不一致。')
            names = [Path(str(n)).name[:2].lower() for n in (loader.video_name, loader.video_name_2)]
            if set(names) != {'cf', 'cs'}:
                raise ValueError('影片檔名須分別以 CF、CS 開頭，以識別內外參方向。')
            fps_values = []
            for camera, path, frames in zip(names, (loader.video_path, loader.video_path_2),
                                            (loader.video_frames, loader.video_frames_2)):
                if list(frames[0].shape[1::-1]) != rig['intrinsics'][camera]['image_size']:
                    raise ValueError(f'{camera.upper()} 影片尺寸與內參不符。')
                cap = cv2.VideoCapture(str(path))
                try:
                    fps_values.append(cap.get(cv2.CAP_PROP_FPS))
                finally:
                    cap.release()
            if not np.isfinite(fps_values).all() or min(fps_values) <= 0 or abs(fps_values[0] - fps_values[1]) > .01:
                raise ValueError('兩影片必須有相同且有效的 FPS；請先完成同步。')
            viewer = MetricTriangulate3DViewer(self.calibration_path.text(), names[0], fps_values[0],
                                              self.reference_spin.value(), self.heel_combo.currentData())
            viewer.analysis_context = {'frame_offset': self.offset_spin.value(), 'sources': self._sources,
                                       'video_name': loader.video_name, 'video_name_2': loader.video_name_2,
                                       'frame_number_space': 'playback_timeline',
                                       'second_source_frame': 'frame_number - frame_offset'}
            description = f'{method}｜{fps_values[0]:g} fps｜後腳 3D 位移；無效關節不計值。校正尚需實尺驗證。'
            if rig.get('warnings'):
                description += ' ' + '；'.join(map(str, rig['warnings']))
        # Share exactly the same reconstruction between the plot, labels and renderer.
        old_viewers = {v for v in (self.viewer3d, self.pose_analyzer.viewer3D, self.pose_analyzer_2.viewer3D) if v is not None}
        layout = self.ui.vispy_widget.layout()
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.setParent(None)
        for old in old_viewers:
            old.timer.stop()
            old.canvas.close()
        self.viewer3d = viewer
        self.pose_analyzer.viewer3D = viewer
        self.pose_analyzer_2.viewer3D = viewer
        self.frame_offset = self.offset_spin.value()
        loader.frame_offset = self.frame_offset
        self._active_method = method
        self._output_name = f'{loader.video_name}_{method}'
        viewer.setJsonPaths(loader.folder_path, self._output_name)
        layout.addWidget(viewer.get_canvas().native)
        self._calibration_description = description
        metric = method != 'legacy'
        self.ui.FC_label_2.setText('肩髖分離角度:\n' + ('同一後腳3D位移:' if metric else '跨步距離:'))
        self.ui.BR_label_2.setText('右手腕速度:\n' + ('初始腳跟至手腕3D距離:' if metric else '出手點距離:'))
        self.ui.MER_label_2.setText('手臂平面／軀幹角:' if metric else '肩外旋角度:')
        if method != 'legacy' and not self._valid_stereo_frame(self.reference_spin.value()):
            self._calibration_description += ' 初始幀超出雙目同步範圍，後腳位移將無法計算；請調整初始幀。'
        self._calibration_ready = True
        self._analysis_dirty = False
        self.ui.startCodeBtn.setEnabled(True)

    def _set_manual_event(self, attr):
        if not self.video_loader.total_frames:
            return
        setattr(self, attr, self.ui.frameSlider.value())
        self._manual_events.add(attr)
        if attr == 'max_shoulder_angle_frame':
            self.mer_locked = True
        elif attr == 'wrist_speed_frame':
            self.release_locked = True
        self._analysis_dirty = True
        self._render_keyframe_results()

    def _valid_stereo_frame(self, frame):
        loader = self.video_loader
        return (0 <= frame < len(loader.video_frames)
                and 0 <= frame - self.frame_offset < len(loader.video_frames_2))

    def _calibrated_phase_detection(self, frame):
        """Find candidate timing in CS images independently of 3D point quality.

        These are right-handed-pitcher image heuristics, not measured ball release.
        Manual FC/MER/BR corrections always take precedence.
        """
        if self.heel_combo.currentData() != 25 or not self._valid_stereo_frame(frame):
            return  # Left-handed timing currently requires the manual event buttons.
        side = self.pose_estimater if self.viewer3d.first_camera == 'cs' else self.pose_estimater_2
        data = side.getPersonDf(frame_num=frame)
        if data is None or len(data) != 1:
            return
        self._analysis_dirty = True
        points = np.asarray(data.iloc[0]['keypoints'], float)
        if points.shape[0] != 26 or not np.isfinite(points[:, :3]).all():
            return
        width = self.video_loader.video_size[0]
        scale = width / 1920.
        # Track the highest raised left knee and confirm with two descending frames.
        if self.preparation_frame is None and self.foot_contact_frame is None and min(points[[11, 13], 2]) >= .5:
            knee_y = points[13, 1]
            if self.prev_left_knee_y is not None:
                if not self.preparation_tracking_started and knee_y < points[11, 1] + 100 * scale and knee_y < self.prev_left_knee_y:
                    self.preparation_tracking_started = True
                    self.min_left_knee_y, self.candidate_preparation_frame = knee_y, frame
                if self.preparation_tracking_started:
                    if knee_y < self.min_left_knee_y:
                        self.min_left_knee_y, self.candidate_preparation_frame = knee_y, frame
                    self.preparation_descend_count = self.preparation_descend_count + 1 if knee_y > self.prev_left_knee_y else 0
                    if self.preparation_descend_count >= 2:
                        self.preparation_frame = self.candidate_preparation_frame
            self.prev_left_knee_y = knee_y
        if self.foot_contact_frame is None and self.preparation_frame is not None and min(points[[15, 16], 2]) >= .5:
            helper = PoseAnalyzer2D(side, fps=self.viewer3d.fps)
            recent = side.person_df[side.person_df.frame_number.between(frame - 4, frame)]
            consecutive = set(recent.frame_number) == set(range(frame - 4, frame + 1))
            if consecutive and abs(points[15, 0] - points[16, 0]) >= 200 * scale and helper.is_left_ankle_landed(frame, spatial_var_threshold=45 * scale ** 2):
                self.foot_contact_frame = frame
                self._render_keyframe_results()
        if self.foot_contact_frame is None or frame <= self.foot_contact_frame or min(points[[6, 8, 10], 2]) < .5:
            return
        forearm = points[10, :2] - points[8, :2]
        angle = np.degrees(np.arccos(np.clip(forearm[0] / max(np.linalg.norm(forearm), 1e-12), -1, 1)))
        if not self.mer_locked:
            if angle > self.max_shoulder_angle_value:
                self.max_shoulder_angle_value, self.max_shoulder_angle_frame = angle, frame
            elif self.max_shoulder_angle_value > 140 and self.max_shoulder_angle_value - angle > 3:
                self.mer_locked = True
                self._render_keyframe_results()
        if self.max_shoulder_angle_frame is None or frame <= self.max_shoulder_angle_frame or self.release_locked:
            return
        prev = side.getPersonDf(frame_num=frame - 1)
        if prev is None or len(prev) != 1:
            return
        previous = np.asarray(prev.iloc[0]['keypoints'], float)
        if previous[10, 2] < .5:
            return
        speed = np.linalg.norm(points[10, :2] - previous[10, :2]) * self.viewer3d.fps
        if points[10, 1] < points[6, 1] and speed > self.max_wrist_speed_after_er:
            self.max_wrist_speed_after_er, self.wrist_speed_frame = speed, frame
        elif self.max_wrist_speed_after_er > 0 and speed < .9 * self.max_wrist_speed_after_er and points[10, 0] > points[6, 0]:
            self.release_locked = True
            self._render_keyframe_results()

    def _restore_keyframe_results(self):
        """Restore only results made with these cameras, calibration and timeline."""
        import pandas as pd
        folder = Path(self.video_loader.folder_path)
        path = folder / f'{self._output_name}_keyframes.json'
        try:
            data = read_optional_json(path, {})
            meta = data.get('Analysis', {})
            expected = {'frame_offset': self.frame_offset, 'video_name': self.video_loader.video_name,
                        'video_name_2': self.video_loader.video_name_2, 'method': self._active_method,
                        'sources': self._sources}
            if getattr(self.viewer3d, 'metric', False):
                expected.update({k: self.viewer3d.provenance()[k] for k in
                                 ('calibration_sha256', 'reference_frame', 'heel_halpe_index', 'fps')})
            if not data or any(meta.get(k) != v for k, v in expected.items()):
                return
            analysis = read_optional_json(folder / f'{self._output_name}_analysis.json', {})
            if any(analysis.get('Analysis', {}).get(k) != v for k, v in expected.items()):
                return
            coordinates = read_optional_json(folder / f'{self._output_name}_3D.json', [])
            if getattr(self.viewer3d, 'metric', False):
                if not isinstance(coordinates, dict) or coordinates.get('calibration_sha256') != meta['calibration_sha256']:
                    return
                rows = coordinates.get('frames', [])
                self.viewer3d.quality = {int(r['frame_number']): r['quality'] for r in rows}
            else:
                rows = coordinates
            self.viewer3d.all_3d_frames = {int(r['frame_number']): np.asarray(r['keypoints'], float) for r in rows}
            self.pose_analyzer.analyze_info = analysis.get('frames', [])
            self.pose_analyzer.analyze_df = pd.DataFrame(self.pose_analyzer.analyze_info)
            self.pose_analyzer.processed_frames = set(meta.get('analyzed_frames', []))
            self.preparation_frame = (data.get('knee_up') or {}).get('frame_number')
            for field, attr in [('foot_contact', 'foot_contact_frame'), ('max_shoulder_er', 'max_shoulder_angle_frame'), ('release', 'wrist_speed_frame')]:
                setattr(self, attr, data.get(field, {}).get('frame_number'))
            self.mer_locked, self.release_locked = meta.get('mer_confirmed', False), meta.get('br_confirmed', False)
            self._manual_events = set(meta.get('manual_events', []))
            self._phase_last_frame = max(meta.get('analyzed_frames', []), default=-1)
            self._render_keyframe_results()
            self.calibration_status.setText('已恢復相同校正與同步設定的分析；' + self._calibration_description)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.calibration_status.setText(f'舊分析無法恢復，請重新分析：{exc}')

    def _render_keyframe_results(self):
        for frame, kind, view, button, label in (
            (self.foot_contact_frame, 'foot_contact', self.ui.fcView, self.ui.footContact_ptn, self.ui.footContact_label),
            (self.max_shoulder_angle_frame, 'max_shoulder_er', self.ui.merView, self.ui.maxER_ptn, self.ui.maxER_label),
            (self.wrist_speed_frame, 'release', self.ui.brView, self.ui.release_ptn, self.ui.release_label)):
            if frame is not None and 0 <= frame < (self.video_loader.total_frames or 0):
                self._display_keyframe_with_text(frame, view, kind)
                self._update_keyframe_label(frame, kind)
                button.setEnabled(True)
                label.setText(f'第 {frame} 幀')

    def _complete_video_analysis(self, manual=False):
        if not self._calibration_ready or not self._analysis_dirty:
            return
        self._render_keyframe_results()
        loader = self.video_loader
        metadata = {'method': self._active_method, 'frame_number_space': 'playback_timeline',
                    'sources': self._sources,
                    'video_name': loader.video_name, 'video_name_2': loader.video_name_2,
                    'frame_offset': self.frame_offset, 'second_source_frame': 'frame_number - frame_offset',
                    'fps': getattr(self.viewer3d, 'fps', loader.video_fps),
                    'analyzed_frames': sorted(self.pose_analyzer.processed_frames),
                    'total_frames': loader.total_frames, 'manual_events': sorted(self._manual_events),
                    'mer_confirmed': self.mer_locked, 'br_confirmed': self.release_locked}
        metadata['event_detection'] = 'CS image heuristics (right-handed) / manual corrections; review timing'
        if getattr(self.viewer3d, 'metric', False):
            metadata.update(self.viewer3d.provenance())
        fc = {'frame': self.foot_contact_frame, 'shoulder_hip_angle': self.image_drawer.keyframe_shoulder_hip_angle,
              'stride_distance': self.image_drawer.keyframe_stride_distance}
        mer = {'frame': self.max_shoulder_angle_frame, 'shoulder_angle': self.image_drawer.keyframe_shoulder_angle}
        br = {'frame': self.wrist_speed_frame, 'wrist_speed': self.image_drawer.keyframe_wrist_speed,
              'extension_distance': self.image_drawer.keyframe_extension_distance}
        try:
            self.viewer3d.save_to_json()
            # Separate output avoids overwriting 2D poses with a different synchronization.
            for estimator, camera_name in ((self.pose_estimater, loader.video_name), (self.pose_estimater_2, loader.video_name_2)):
                atomic_json(Path(loader.folder_path) / f'{camera_name}_{self._active_method}_poses.json',
                            {'Analysis': metadata, 'records': estimator.person_df.to_dict('records')})
            atomic_json(Path(loader.folder_path) / f'{self._output_name}_analysis.json',
                        {'Analysis': metadata, 'frames': self.pose_analyzer.analyze_info})
            result = SimpleKeyframeLogger(loader.folder_path).save_keyframe_data(
                fps=metadata['fps'], video_name=self._output_name, kneeUp_frame=self.preparation_frame,
                fc_data=fc, mer_data=mer, br_data=br, analysis=metadata)
            if result is None:
                raise OSError('無法寫入關鍵幀 JSON')
        except (OSError, ValueError, TypeError) as exc:
            self.calibration_status.setText(f'儲存失敗，可按「儲存分析 JSON」重試：{exc}')
            return
        self._analysis_dirty = False
        quality = ''
        if getattr(self.viewer3d, 'metric', False):
            accepted = sum(int(np.count_nonzero(q['valid'])) for q in self.viewer3d.quality.values())
            total = len(self.viewer3d.quality) * 26
            quality = f'通過重建品質的關節 {accepted}/{total}；'
        self.calibration_status.setText(f'已儲存 {self._output_name}_*.json；' + quality + self._calibration_description)
        
    def initVar(self):
        self.is_play = False
        self.is_video = False
        self.view_scene = QGraphicsScene()
        self.curve_scene = QGraphicsScene()

        self.view_scene.clear()
        self.curve_scene.clear()
        self.correct_kpt_idx = 0
        self.is_processed = False
        self.overlay = None  # 初始化 SliderColorOverlay

        # self.table = self.ui.tableWidget
        self.json_path = None
        self.json_path2 = None
        self.viewer3d = None
        self.preparation_frame = None
        self.min_left_knee_y = np.inf
        self.prev_left_knee_y = None
        self.preparation_tracking_started = False
        self.candidate_preparation_frame = None
        self.preparation_descend_count = 0
        self.foot_contact_frame = None
        self.max_shoulder_angle_frame = None
        self.wrist_speed_frame = None
        self.max_shoulder_angle_value = -np.inf
        self.max_wrist_speed_after_er = -np.inf
        self.speed_angle_names = ["ankle_speed", "left_knee_3d", "shoulder_hiple_angle", "shoulder_angle", "wrist_speed", "ankle_x_diff"]
        self.title_names = ["腳踝速度", "膝蓋角度", "肩峰連線與髖關節連線角度", "肩膀外旋角度", "手腕速度", "腳踝X方向差值"]
        self.frame_offset = -2  # 幀偏移：正值表示 video_path_2 延遲，負值表示 video_path 延遲

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
        # ]) #0327?
        self.F = np.array([
            [-1.00177788e-07,  2.16557273e-06, -7.90363312e-04],
            [ 1.58917237e-07,  2.44307598e-07, -3.48771760e-03],
            [-9.75476979e-05,  7.35402506e-04,  1.00000000e+00]
        ]) #0505
        # 上面寫死的是舊設備的參數：錄影資料夾沒有校正快照（calibration.json）時才使用
        self.default_calibration = (self.K_F, self.K_S, self.F)
        
        pg.setConfigOptions(foreground=QColor(113,148,116), antialias = True)
        pg.setConfigOption('background', 'w')
        pg.setConfigOption('foreground', 'k')

        self.br_points = []
        self.show_br_points = False  # 是否顯示釋放點
        self.mer_locked = False
        self.release_locked = False

    def resizeEvent(self, event):
        new_size = event.size()
        
        # 重新縮放關鍵幀 QGraphicsView 以適應窗口大小
        if hasattr(self.ui, 'fcView') and hasattr(self.ui, 'merView') and hasattr(self.ui, 'brView'):
            for view_widget in [self.ui.fcView, self.ui.merView, self.ui.brView]:
                self._fit_view_no_crop(view_widget)
        
        super().resizeEvent(event)  

    def _fit_view_no_crop(self, view_widget: QGraphicsView):
        """不裁切模式下，將影像盡量放大並靠左上顯示。"""
        if view_widget is None or view_widget.scene() is None:
            return
        if len(view_widget.scene().items()) == 0:
            return
        view_widget.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        view_widget.fitInView(view_widget.scene().sceneRect(), Qt.KeepAspectRatio)

    def initFrameSlider(self):
        """初始化影片滑桿和相關的標籤。"""
        total_frames = self.video_loader.total_frames
        self.ui.frameSlider.blockSignals(True)
        self.ui.frameSlider.setMinimum(0)
        self.ui.frameSlider.setMaximum(total_frames - 1)
        self.ui.frameSlider.setValue(0)
        self.ui.frameSlider.blockSignals(False)
        self.ui.frameNumLabel.setText(f'0/{total_frames - 1}')

    def initGraph(self):
        """初始化圖表和模型設定。"""
        total_frames = self.video_loader.total_frames
        self.graph_plotter._init_graph(total_frames) 
        # 启用肩峰連線與髖關節連線角度曲线显示 (state=2 表示启用，state=0 表示禁用)
        self.toggleShowGraph("ankle_speed", 0)
        self.toggleShowGraph("left_knee_3d", 0)
        self.toggleShowGraph("shoulder_hiple_angle", 2)
        self.toggleShowGraph("shoulder_angle", 0)
        self.toggleShowGraph("wrist_speed", 0)
        self.toggleShowGraph("ankle_x_diff", 0)
        # 显示图表
        if hasattr(self.ui, 'graph_widget'):
            self.showGraph(self.ui.graph_widget)
        # 第一次绘制更新图表
        self.graph_plotter.updateGraph(0)

    def resetPhaseDetection(self):
        self._phase_last_frame = -1
        self._manual_events = set()
        self._analysis_dirty = False
        self.ui.footContact_ptn.setDisabled(True)
        self.ui.maxER_ptn.setDisabled(True)
        self.ui.release_ptn.setDisabled(True)
        self.preparation_frame = None
        self.min_left_knee_y = np.inf
        self.prev_left_knee_y = None
        self.preparation_tracking_started = False
        self.candidate_preparation_frame = None
        self.preparation_descend_count = 0
        self.foot_contact_frame = None
        self.max_shoulder_angle_frame = None
        self.wrist_speed_frame = None
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
        # self._phase_visualization_done = False
        # self._last_phase_segments = None
        if hasattr(self, '_graph_updated_once'):
            delattr(self, '_graph_updated_once')


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

        self.ui.footContact_ptn.clicked.connect(
            lambda: self.ui.frameSlider.setValue(self.foot_contact_frame)
        )
        self.ui.maxER_ptn.clicked.connect(
            lambda: self.ui.frameSlider.setValue(self.max_shoulder_angle_frame)
        )  
        self.ui.release_ptn.clicked.connect(
            lambda: self.ui.frameSlider.setValue(self.wrist_speed_frame)
        )

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
        # self.kpt_table = KeypointTable(self.ui.KptTable,self.pose_estimater)
        # self.ui.KptTable.cellActivated.connect(self.kpt_table.onCellClicked)
        self.ui.FrameView.mousePressEvent = self.mousePressEvent
        # self.ui.IdCorrectBtn.clicked.connect(self.correctId)
        self.ui.startCodeBtn.clicked.connect(self.toggleDetect)
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

        if self.ui.vispy_widget.layout() is None:
            self.ui.vispy_widget.setLayout(QVBoxLayout())

    def playBtnClicked(self):
        if self.video_loader.video_name == "":
            QMessageBox.warning(self, "無法播放影片", "請讀取影片!")
            return
        if self.video_loader.is_loading:
            QMessageBox.warning(self, "影片讀取中", "請稍等!")
            return
        self.is_play = not self.is_play
        self.ui.playBtn.setText("||" if self.is_play else "▶︎")
        if self.is_play:
            self.playFrame(self.ui.frameSlider.value())
        else:
            # Stop the timer if playing is paused
            if hasattr(self, '_play_timer') and self._play_timer.isActive():
                self._play_timer.stop()

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
        self.pose_estimater = PoseEstimater(self.model)
        self.pose_estimater_2 = PoseEstimater(self.model)
        self.pose_analyzer = PoseAnalyzer(self.pose_estimater, self.K_S, self.K_F, self.F)
        self.pose_analyzer_2 = PoseAnalyzer(self.pose_estimater_2, self.K_S, self.K_F, self.F)
        self.graph_plotter = GraphPlotter(self.pose_analyzer, speed_name=self.speed_angle_names, title_names=self.title_names)
        self.image_drawer = ImageDrawer(self.pose_estimater, self.pose_analyzer)
        self.image_drawer_2 = ImageDrawer(self.pose_estimater_2, self.pose_analyzer_2)
        self.video_loader = VideoLoader(self.image_drawer, self.image_drawer_2)
        self.spin_direction_widget = SpinDirectionWidget()
        spin_layout = QVBoxLayout(self.ui.spinDirectionWidget_2)
        spin_layout.setContentsMargins(0, 0, 0, 0)
        spin_layout.addWidget(self.spin_direction_widget)

    def reset(self):
        # Stop any ongoing playback
        if hasattr(self, '_play_timer') and self._play_timer.isActive():
            self._play_timer.stop()
        self.is_play = False
        
        self.resetPhaseDetection()
        # self.reset3DInfo()
        if self.viewer3d is not None:
            self.viewer3d.reset()
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
        self.view_scene.clear()
        self.curve_scene.clear()
        # 清除舊的 SliderColorOverlay
        if self.overlay is not None:
            self.overlay.setParent(None)
            self.overlay = None

        # 清空關鍵幀視圖
        if hasattr(self.ui, 'fcView') and self.ui.fcView.scene() is not None:
            self.ui.fcView.scene().clear()
        if hasattr(self.ui, 'merView') and self.ui.merView.scene() is not None:
            self.ui.merView.scene().clear()
        if hasattr(self.ui, 'brView') and self.ui.brView.scene() is not None:
            self.ui.brView.scene().clear()
   
    def loadVideo(self, is_processed:bool = False, video_path:str = None, video_path_2:str = None):
        if self.is_play:
            self.ui.playBtn.click()
        
        # 清理舊影片的追踪狀態、濾波器、GPU 內存
        if self.pose_estimater is not None:
            self.pose_estimater.reset()
        
        self.is_processed = is_processed
        self.video_loader.loadVideo(video_path, video_path_2)
        if not self.video_loader.video_name:
            return
        self.ui.showSkeletonCheckBox.setChecked(False)
        self.keyframe_logger = SimpleKeyframeLogger(self.video_loader.folder_path)

        # 2D 分頁把球離手資料存成 CF_..._BRandRapsodo.json：先找 CF、再找 CS，都沒有就略過
        video_names = sorted([self.video_loader.video_name, self.video_loader.video_name_2],
                             key=lambda name: not os.path.basename(str(name)).startswith("CF_"))
        rapsodo_json_path = None
        for name in video_names:
            candidate = os.path.join(self.video_loader.folder_path, f"{os.path.splitext(os.path.basename(str(name)))[0]}_BRandRapsodo.json")
            if os.path.exists(candidate):
                rapsodo_json_path = candidate
                break
        if rapsodo_json_path:
            self.display_roi_from_json(rapsodo_json_path, self.ui.RoiLabel, self.ui.RoiLabel_2, self.ui.RapsodoDataLabel_2, self.spin_direction_widget)
        else:
            print("[3D] 此錄影沒有 BRandRapsodo.json，略過球離手畫面顯示")

        self._calibration_ready = False
        self.checkVideoLoad()

    def applyCalibration(self, folder_path):
        """依錄影資料夾內的校正快照設定 K_F、K_S、F；沒有快照則用寫死的舊設備參數。"""
        snapshot = calib_store.load_snapshot(folder_path) if folder_path else None
        if snapshot is not None:
            self.K_F, self.K_S, self.F = snapshot["K_F"], snapshot["K_S"], snapshot["F"]
            print(f"[3D] 使用錄影時的校正快照：正面 SN{snapshot['front']}、側面 SN{snapshot['side']}")
        else:
            self.K_F, self.K_S, self.F = self.default_calibration
            print("[3D] 此錄影沒有校正快照，使用程式內建的舊設備參數")
        # PoseAnalyzer 內的 3D 計算在每次三角化時才讀取 K、F，直接更新即可
        for analyzer in (self.pose_analyzer, self.pose_analyzer_2):
            analyzer.viewer3D.K_L, analyzer.viewer3D.K_R, analyzer.viewer3D.F = self.K_S, self.K_F, self.F

    def loadProcessedData(self):
        import pandas as pd
        loader = self.video_loader
        folder = Path(loader.folder_path)
        legacy_metadata = None
        for name in (loader.video_name, loader.video_name_2):
            data = read_optional_json(folder / f'{name}_BRandRapsodo.json', {})
            if data.get('Analysis', {}).get('pose_frame_number_space') == 'playback_timeline':
                legacy_metadata = {**data['Analysis'], 'video_name': data.get('video_name'),
                                   'video_name_2': data.get('video_name_2')}
                break
        for index, (name, estimator) in enumerate(((loader.video_name, self.pose_estimater),
                                                 (loader.video_name_2, self.pose_estimater_2))):
            path = folder / f'{name}_{self.calibration_combo.currentData()}_poses.json'
            saved = read_optional_json(path, None)
            if isinstance(saved, dict) and 'records' in saved:
                rows, metadata = saved['records'], saved.get('Analysis', {})
            else:
                path = folder / f'{name}.json'
                rows, metadata = read_optional_json(path, []), legacy_metadata
            if not isinstance(rows, list):
                raise ValueError(f'Pose JSON must contain records: {path}')
            df = pd.DataFrame(rows)
            estimator.reset()
            if df.empty:
                continue
            # Older records without timeline metadata are interpreted as source frames.
            old_offset = int(metadata.get('frame_offset', 0)) if metadata and metadata.get('video_name_2') == name else 0
            df['frame_number'] = df['frame_number'] - old_offset + (self.frame_offset if index else 0)
            # Drop entries which were generated by the old loader's boundary clipping.
            source_frame = df.frame_number - (self.frame_offset if index else 0)
            count = len(loader.video_frames_2 if index else loader.video_frames)
            df = df[df.frame_number.between(0, loader.total_frames - 1) & source_frame.between(0, count - 1)]
            estimator.setProcessedData(df.reset_index(drop=True))

    def checkVideoLoad(self):
        """檢查影片是否讀取完成，並更新 UI 元素。"""
        # 檢查是否有影片名稱，若無則不執行後續操作
        if not self.video_loader.video_name:
            return
        # 若影片正在讀取中，定時檢查讀取狀況
        if self.video_loader.is_loading:
            self.ui.videoNameLabel.setText("讀取影片中")
            QTimer.singleShot(100, self.checkVideoLoad)  # 每100ms 檢查一次
            return
        # 影片讀取完成後更新 UI 元素
        if not self.video_loader.total_frames:
            return
        self.updateVideoInfo()

    def updateVideoInfo(self):
        """更新與影片相關的資訊顯示在 UI 上。"""
        self.reset()
        self._calibration_ready = False
        self.frame_offset = self.offset_spin.value()
        self.video_loader.frame_offset = self.frame_offset
        self.initFrameSlider()
        self.initGraph()
        self.updateFrame(0)
        self.model.setImageSize(self.video_loader.video_size)
        self.ui.videoNameLabel.setText(f"{self.video_loader.video_name}")
        self.ui.videoNameLabel_3.setText(f"{self.video_loader.video_name_2}")
        video_size = self.video_loader.video_size
        self.ui.ResolutionLabel.setText(f"(0,0) - {video_size[0]} x {video_size[1]}")
        self.ui.startCodeBtn.setEnabled(True)
        # 設置幀偏移（補償相機延遲）并同步到 3D 查看器
        self._apply_selected_calibration()
        if self.is_processed and self._calibration_ready:
            self._restore_keyframe_results()

    def showImage(self, image: np.ndarray, scene: QGraphicsScene, GraphicsView: QGraphicsView,
                  draw_marker: bool = True, defer_refit: bool = True, process_events: bool = False):
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

    def display_roi_from_json(self, json_path, label_widget: QLabel, label_widget_2: QLabel, text_label: QLabel = None, direction_widget: SpinDirectionWidget = None):
        # 1. 讀取 JSON 檔案
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 2. 取得 Base64 字串 (假設我們要看 roi_frame_1)
        base64_str = data.get("roi_frame_1")
        base64_str_2 = data.get("roi_frame_2")
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

        metrics = data.get("metrics", {})
        if text_label:
            text_label.setText(f"球速:{metrics.get('velocity_mph', 'N/A'):.1f} kph\n轉速:{metrics.get('total_spin_rpm', 'N/A')} rpm")
            if direction_widget:
                direction_widget.setDirection(metrics.get('spin_direction', 'N/A'))

    def _get_frame_analysis_data(self, frame_num: int) -> dict:
        """獲取當前幀的所有分析數據
        
        Args:
            frame_num: 幀號
            
        Returns:
            包含所有速度和角度數據的字典
        """
        speed_names = ["ankle_speed", "left_knee_3d", "shoulder_hiple_angle", "shoulder_angle", "wrist_speed", "ankle_x_diff"]
        frame_data = {}
        
        for speed_name in speed_names:
            _, value = self.pose_analyzer.get_frame_angleOrSpeed_data(frame_num, speed_name=speed_name)
            frame_data[speed_name] = float(value) if np.isscalar(value) and value is not None else float('nan')
        
        return frame_data
    
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

    def _display_keyframe_with_text(self, frame_num, view_widget, keyframe_type, frame_data=None):
        if frame_num is None or not 0 <= frame_num < (self.video_loader.total_frames or 0):
            return
        image, _ = self.video_loader.getVideoImage(frame_num)
        if image is None:
            return
        drawed_img = image.copy()
        try:
            drawed_img = self.image_drawer.drawAngleInfo(image.copy(), frame_num,
                keyframe_type=keyframe_type, start_frame=self.preparation_frame or 0)
        except Exception as exc:
            print(f'[3D replay] overlay unavailable: {exc}')
        scene = view_widget.scene()
        if scene is None:
            scene = QGraphicsScene(view_widget)
        else:
            scene.setParent(view_widget)
        self.showImage(drawed_img, scene, view_widget, draw_marker=False,
                       defer_refit=False, process_events=False)
        scene.setSceneRect(scene.itemsBoundingRect())
        view_widget.fitInView(scene.sceneRect(), Qt.KeepAspectRatio)

    def _update_keyframe_label(self, frame_num, keyframe_type):
        def value(name):
            _, result = self.pose_analyzer.get_frame_angleOrSpeed_data(frame_num, speed_name=name)
            return float(result) if np.isscalar(result) and result is not None and np.isfinite(result) else None
        def formatted(number, factor=1):
            return f'{number * factor:.1f}' if number is not None and np.isfinite(number) else '--'
        if keyframe_type == 'foot_contact':
            angle = value('shoulder_hiple_angle')
            distance = self.image_drawer._calculate_stride_distance(frame_num)
            self.image_drawer.keyframe_shoulder_hip_angle = angle
            self.image_drawer.keyframe_stride_distance = distance
            self.ui.FC_label.setText(formatted(angle) + '\n' + formatted(distance, 100))
            self.ui.FC_label_unit.setText('deg\ncm')
        elif keyframe_type == 'max_shoulder_er':
            angle = value('shoulder_angle')
            self.image_drawer.keyframe_shoulder_angle = angle
            self.ui.MER_label_3.setText(formatted(angle))
            self.ui.MER_label_unit.setText('deg')
        elif keyframe_type == 'release':
            speed = value('wrist_speed')
            distance = self.image_drawer._calculate_extension_distance(frame_num)
            self.image_drawer.keyframe_wrist_speed = speed
            self.image_drawer.keyframe_extension_distance = distance
            self.ui.BR_label_3.setText(formatted(speed) + '\n' + formatted(distance, 100))
            self.ui.BR_label_unit.setText('m/s\ncm')

    def _detect_preparation_phase(self, frame_num: int) -> bool:
        """檢測準備階段
        
        Returns:
            如果檢測到準備階段返回 True
        """
        # 在前導腳著地前：開始抬腳後，追蹤最低點；直到下一幀開始下放才確認關鍵幀
        if self.foot_contact_frame is not None or self.preparation_frame is not None:
            return False
        
        person_data = self.pose_estimater.getPersonDf(frame_num=frame_num, is_select=True)
        if person_data is None or person_data.empty:
            return False
        keypoints = person_data['keypoints'].iloc[0]
        left_knee_y = keypoints[13][1]  # 13: left knee
        left_hip_y = keypoints[11][1]  # 11: left hip

        if self.prev_left_knee_y is None:
            self.prev_left_knee_y = left_knee_y
            return False
        
        # 先判斷是否進入抬腳階段（且左膝高度已達到條件範圍）
        if not self.preparation_tracking_started:
            # print(f"{frame_num}: {left_knee_y < left_hip_y + 100}")
            if left_knee_y < left_hip_y + 100 and left_knee_y < self.prev_left_knee_y:
                self.preparation_tracking_started = True
                self.min_left_knee_y = left_knee_y
                self.candidate_preparation_frame = frame_num
                self.preparation_descend_count = 0
            self.prev_left_knee_y = left_knee_y
            return False

        # 抬腳中：持續更新最低點
        if left_knee_y < self.min_left_knee_y:
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
                self._display_keyframe_with_text(frame_num, self.ui.fcView, keyframe_type='foot_contact', frame_data=frame_data)
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
        
        if self.mer_locked or self.release_locked:
            return False
        
        shoulder_angle = frame_data["shoulder_angle"]
        person_data = self.pose_estimater_2.getPersonDf(frame_num=frame_num, is_select=True)
        if person_data is None or person_data.empty:
            return False
        keypoints = person_data['keypoints'].iloc[0]
        wrist_conf = keypoints[10][2]  # 10: right wrist confidence
                
        if shoulder_angle > self.max_shoulder_angle_value and wrist_conf > 0.5:
            print(f"Frame {frame_num}: shoulder_angle={shoulder_angle:.1f} (updated max)")
            self.max_shoulder_angle_value = shoulder_angle
            self.max_shoulder_angle_frame = frame_num
            return False
        elif self.max_shoulder_angle_value > 150 and (self.max_shoulder_angle_value - shoulder_angle) > 2.0 and wrist_conf > 0.5:
            self.mer_locked = True
            self.ui.maxER_label.setText(f"第{self.max_shoulder_angle_frame}幀")
            self.ui.maxER_ptn.setDisabled(False)
            
            # 無論是否有 merView，都更新文字標籤
            print(f"✓ 檢測到最大肩外旋: frame {self.max_shoulder_angle_frame}, angle={self.max_shoulder_angle_value:.1f}")
            self._update_keyframe_label(self.max_shoulder_angle_frame, keyframe_type='max_shoulder_er')
            
            # 如果有 merView，則額外顯示關鍵幀圖像
            if hasattr(self.ui, 'merView'):
                self._display_keyframe_with_text(self.max_shoulder_angle_frame, self.ui.merView, keyframe_type='max_shoulder_er', frame_data=frame_data)
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

        # Once release is locked, keep the selected frame stable.
        if self.release_locked:
            return False
        
        wrist_speed = frame_data["wrist_speed"]

        # 限制條件：右手腕必須高於右肩（影像座標 y 越小代表位置越高）
        person_data = self.pose_estimater.getPersonDf(frame_num=frame_num, is_select=True)
        if person_data is None or person_data.empty:
            return False

        keypoints = person_data['keypoints'].iloc[0]
        right_shoulder_y = keypoints[6][1]  # 6: right shoulder
        right_wrist_y = keypoints[10][1]    # 10: right wrist
        
        print(f"Frame {frame_num}: wrist_speed={wrist_speed:.1f} (updated max after MER)")

        if wrist_speed > self.max_wrist_speed_after_er and right_wrist_y <= right_shoulder_y:
            self.max_wrist_speed_after_er = wrist_speed
            self.wrist_speed_frame = frame_num
            return False
        elif self.max_wrist_speed_after_er > 0 and (self.max_wrist_speed_after_er - wrist_speed) > 1.0 and self.release_locked == False:
            # Use the confirmation moment as the release keyframe.
            self.release_locked = True
            if not self.mer_locked:
                self.mer_locked = True
            self.ui.release_label.setText(f"第{self.wrist_speed_frame}幀")
            self.ui.release_ptn.setDisabled(False)

            # 記錄右手腕位置 (is_kpt=True 時返回 numpy array)
            person_data_2 = self.pose_estimater_2.getPersonDf(frame_num=self.wrist_speed_frame, is_select=True, is_kpt=True)
            self.br_points.append(person_data_2[10]) if person_data_2 is not None else None
            
            # 無論是否有 brView，都更新文字標籤
            print(f"✓ 檢測到釋放點: frame {self.wrist_speed_frame}, wrist_speed={self.max_wrist_speed_after_er:.1f}")
            self._update_keyframe_label(self.wrist_speed_frame, keyframe_type='release')
            
            # 如果有 brView，則額外顯示關鍵幀圖像
            if hasattr(self.ui, 'brView'):
                self._display_keyframe_with_text(self.wrist_speed_frame, self.ui.brView, keyframe_type='release', frame_data=frame_data)
            return True
        return False
    
    def _update_phase_visualization(self):
        """更新階段視覺化（輔助線和顏色區段）"""
        if None in [self.preparation_frame, self.foot_contact_frame, 
                   self.max_shoulder_angle_frame, self.wrist_speed_frame]:
            return
        # if self._phase_visualization_done:
        #     return
        
        # 繪製輔助線
        # self.graph_plotter.draw_vertical_lines(
        #     x1=self.foot_contact_frame,
        #     x2=self.max_shoulder_angle_frame,
        #     x3=self.wrist_speed_frame,
        #     width=1
        # )
        
        # 設置顏色區段
        segments = [
            (self.preparation_frame, self.foot_contact_frame, QColor("#1C7CDB")),
            (self.foot_contact_frame, self.max_shoulder_angle_frame, QColor("#C90E0E")),
            (self.max_shoulder_angle_frame, self.wrist_speed_frame, QColor("#F2A900")),
            (self.wrist_speed_frame, self.wrist_speed_frame + 7, QColor("#00AC2D"))
        ]

        # segments_key = tuple((int(s), int(e), c.name()) for s, e, c in segments)
        # if self._last_phase_segments != segments_key:
        #     self._last_phase_segments = segments_key
        
        # 更新 Slider 顏色覆蓋層
        if self.overlay is None:
            self.overlay = SliderColorOverlay(self.ui.frameSlider, segments, segment_half_thickness=9,
                                              fps=getattr(self.viewer3d, 'fps', self.video_loader.video_fps))
        else:
            self.overlay.updateSegments(segments)
        
        # 更新圖表的動作階段
        self.graph_plotter.set_motion_phases(segments)
        # self._phase_visualization_done = True

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
        if hasattr(self, '_play_timer') and self._play_timer.isActive():
            self._play_timer.start(self._get_play_interval_ms())

    def playFrame(self, start_num:int=0):
        """Start playing frames using a timer instead of blocking calls"""
        if not hasattr(self, '_play_timer'):
            self._play_timer = QTimer()
            self._play_timer.timeout.connect(self._playNextFrame)
        
        self._current_play_frame = start_num
        self._play_timer.start(self._get_play_interval_ms())

    def _playNextFrame(self):
        """Internal method to play the next frame"""
        if not self.is_play or self._current_play_frame >= self.video_loader.total_frames:
            self._play_timer.stop()
            if self._current_play_frame >= self.video_loader.total_frames:
                self._complete_video_analysis()
            if self.is_play:
                self.playBtnClicked()  # Auto-pause at end
            return
        
        if self.ui.frameSlider.value() == self._current_play_frame:
            self.analyzeFrame()
        else:
            self.ui.frameSlider.setValue(self._current_play_frame)
        self._current_play_frame += 1

    def phaseDetection(self, frame_num: int):
        """階段檢測主方法
        
        執行順序：
        1. 獲取當前幀的分析數據
        2. 更新UI標籤顯示
        3. 依序檢測各個投球階段
        4. 更新視覺化元素（輔助線、顏色區段）
        """
        if frame_num <= self._phase_last_frame:
            return
        self._phase_last_frame = frame_num
        if getattr(self.viewer3d, 'metric', False):
            self._calibrated_phase_detection(frame_num)
            self._update_phase_visualization()
            return
        # 1. 獲取分析數據
        frame_data = self._get_frame_analysis_data(frame_num)
        
        # 輸出肩髖分離角度
        # shoulder_hiple_angle = frame_data.get("shoulder_hiple_angle", 0.0)
        # print(f"Frame {frame_num}: 肩髖分離角度 = {shoulder_hiple_angle:.1f}°, 肩外旋角度 = {frame_data.get('shoulder_angle', 0.0):.1f}°")
        
        # 2. 更新UI標籤
        # self._update_analysis_labels(frame_data)
        
        # 3. 依序檢測各個階段
        self._detect_preparation_phase(frame_num)
        self._detect_foot_contact(frame_num, frame_data)
        self._track_max_shoulder_rotation(frame_num, frame_data)
        self._detect_release_point(frame_num, frame_data)
        
        # 4. 更新視覺化
        self._update_phase_visualization()
        
        # 5. 只在所有階段都已檢測完成時更新圖表一次（避免每幀都檢查）
        if (self.preparation_frame is not None and 
            self.foot_contact_frame is not None and
            self.max_shoulder_angle_frame is not None and 
            self.wrist_speed_frame is not None and
            not hasattr(self, '_graph_updated_once')):
            self._graph_updated_once = True
            self.graph_plotter.updateGraph(frame_num)

    def analyzeFrame(self):
        if not self.video_loader.total_frames or self.video_loader.is_loading:
            return
        fps = 0
        frame_num = self.ui.frameSlider.value()
        video_fps = float(getattr(self.video_loader, 'video_fps', 0) or 0)
        if video_fps <= 0:
            video_fps = 30.0

        if self.preparation_frame is not None and self.wrist_speed_frame is not None:
            start_frame = self.preparation_frame
            end_frame = self.wrist_speed_frame + 7
            total_seconds = max(0.0, (end_frame - start_frame) / video_fps)
            self.ui.frameNumLabel.setText(f'{frame_num}/{self.video_loader.total_frames - 1} ({total_seconds :.2f}s)')
        else:
            total_seconds = self.video_loader.total_frames / video_fps
            self.ui.frameNumLabel.setText(f'{frame_num}/{self.video_loader.total_frames - 1}')

        frame, frame_2 = self.video_loader.getVideoImage(frame_num)
        t0 = time.time()
        # if not self.is_processed:
        _, _, fps= self.pose_estimater.detectKpt(frame, frame_num, is_video=True, is_processed=self.is_processed)
        t1 = time.time()
        # if not self.is_processed:
        _, _, fps= self.pose_estimater_2.detectKpt(frame_2, frame_num, is_video=True, is_processed=self.is_processed)
        t2 = time.time()
        # self.ui.FPSInfoLabel.setText(f"{fps:02d}")
        t3 = time.time()
        if self.viewer3d and self._calibration_ready and self._valid_stereo_frame(frame_num):
        # 取得2D骨架資料，考慮幀偏移以補償相機延遲
            person_df_L = self.pose_estimater.getPersonDf(frame_num=frame_num)
            # 應用幀偏移：frame_num - frame_offset 使 camera_R 與 camera_L 對齐
            # 確保調整後的幀號在有效範圍內 [0, total_frames-1]
            # adjusted_frame_num = int(max(0, min(frame_num - self.frame_offset, self.video_loader.total_frames - 1)))
            person_df_R = self.pose_estimater_2.getPersonDf(frame_num=frame_num)
            # person_df_R = self.pose_estimater_2.getPersonDf(frame_num=frame_num)
            # print(f"L:{person_df_L}, R:{person_df_R}")
            if person_df_L is not None and person_df_R is not None and len(person_df_L) == 1 and len(person_df_R) == 1:
                # 取第一個人的 keypoints
                frame_L = {"keypoints": person_df_L.iloc[0]["keypoints"]}
                frame_R = {"keypoints": person_df_R.iloc[0]["keypoints"]}
                self.viewer3d.add_2d_keypoints(frame_L, frame_R, frame_num=frame_num)
                # pairs = [(5, 7), (7, 9), (6, 8), (8, 10), (11, 13), (13, 15), (12, 14), (14, 16), (18, 19)]
                # max_3d_idx = max(self.viewer3d.all_3d_frames.keys())
                # for i, (idx1, idx2) in enumerate(pairs):
                #     if 0 <= frame_num <= max_3d_idx:
                #         dist = self.viewer3d.get_distance_between(idx1, idx2, frame_num-1)
                #         self.table.setItem(i, 3, QTableWidgetItem(f"{dist*1992.586:.2f}"))
                #     else:
                #         self.table.setItem(i, 3, QTableWidgetItem("N/A"))
                self.pose_analyzer.addAnalyzeInfo(frame_num, frame_L, frame_R)
                self._analysis_dirty = True
                
                # 執行階段檢測（包括已處理影片，因為關鍵帧信息未保存）
                if not getattr(self.viewer3d, 'metric', False):
                    self.phaseDetection(frame_num)
        if self._calibration_ready and getattr(self.viewer3d, 'metric', False):
            self.phaseDetection(frame_num)
        t4 = time.time()

        if frame_num == self.video_loader.total_frames - 1:
            self._complete_video_analysis()

        self._updateFrame(frame, frame_2, frame_num)

    def _updateFrame(self, frame, frame_2, frame_num: int):
        """內部更新幀顯示，直接使用已獲取的圖像數據"""
        if getattr(self.viewer3d, 'metric', False):
            self.viewer3d.set_frame(frame_num)  # Clear the skeleton when this frame has no valid reconstruction.
        drawed_img = self.image_drawer.drawInfo(frame, frame_num, self.pose_estimater.kpt_buffer)
        drawed_img_2 = self.image_drawer_2.drawInfo(frame_2, frame_num, self.pose_estimater_2.kpt_buffer)
        
        # 如果啟用顯示釋放點，則在 frame_2 上繪製
        if self.show_br_points and self.br_points:
            drawed_img_2 = self._draw_br_points(drawed_img_2)
        
        self.showImage(drawed_img, self.view_scene, self.ui.FrameView)
        self.showImage(drawed_img_2, self.curve_scene, self.ui.FrameView_2)
        
        # 更新圖表曲線
        if hasattr(self, 'graph_plotter') and self.graph_plotter is not None:
            self.graph_plotter.updateGraph(frame_num)

    def updateFrame(self, frame_num: int):
        """更新幀顯示（重新讀取圖像）"""
        image, image_2 = self.video_loader.getVideoImage(frame_num)
        self._updateFrame(image, image_2, frame_num)

    def checkBox(self, visible:bool):
        elements = [
            # self.ui.showSkeletonCheckBox,
            # self.ui.showBboxCheckBox,
            # self.ui.selectCheckBox,
            self.ui.selectKptCheckBox,
            # self.ui.tab
        ]
        
        for element in elements:
            element.setVisible(visible)

    def toggleDetect(self):
        if not self.video_loader.total_frames:
            return
        if not self._calibration_ready and not self._apply_selected_calibration():
            return
        self.is_play = False
        if hasattr(self, '_play_timer'):
            self._play_timer.stop()
        manual = {attr: getattr(self, attr) for attr in self._manual_events}
        self.resetPhaseDetection()
        for attr, value in manual.items():
            setattr(self, attr, value)
        self._manual_events = set(manual)
        self.mer_locked = 'max_shoulder_angle_frame' in manual
        self.release_locked = 'wrist_speed_frame' in manual
        self.pose_analyzer.reset()
        self.pose_analyzer_2.reset()
        if self.is_processed:
            self.loadProcessedData()
        else:
            self.pose_estimater.reset()
            self.pose_estimater_2.reset()
        self.ui.showSkeletonCheckBox.setChecked(True)
        self.toggleShowSkeleton(2)
        self.ui.frameSlider.blockSignals(True)
        self.ui.frameSlider.setValue(0)
        self.ui.frameSlider.blockSignals(False)
        self.playBtnClicked()

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

    def toggleShowSkeleton(self, state:int):
        is_checked = state == 2
        self.pose_estimater.setDetect(is_checked)
        self.image_drawer.setShowSkeleton(is_checked)
        self.pose_estimater_2.setDetect(is_checked)
        self.image_drawer_2.setShowSkeleton(is_checked)
        self.updateFrame(self.ui.frameSlider.value())

    def toggleShowBbox(self, state:int):
        if state == 2:  
            self.image_drawer.setShowBbox(True)
            self.image_drawer_2.setShowBbox(True)
            self.updateFrame(self.ui.frameSlider.value())
        if state == 0:
            self.image_drawer.setShowBbox(False)
            self.image_drawer_2.setShowBbox(False)
            self.updateFrame(self.ui.frameSlider.value())

    # def toggleShowAngleInfo(self, state:int):
    #     if not self.ui.selectCheckBox.isChecked():
    #         self.ui.showAngleCheckBox.setCheckState(0)
    #         QMessageBox.warning(self, "無法顯示關節點角度資訊", "請選擇人!")
    #         return
    #     if state == 2:  
    #         self.image_drawer.setShowAngleInfo(True)
    #     else:
    #         self.image_drawer.setShowAngleInfo(False)

    def toggleShowGraph(self, name, state:int):
        enabled = state == 2
        self.graph_plotter.setPlotVisibility(name, enabled)
        self.graph_plotter.updateGraph(self.ui.frameSlider.value())

    def setFrameOffset(self, offset: int):
        """
        設置幀偏移以同步兩個視頻
        
        Args:
            offset: 幀偏移值
                   負值表示 video_path_2 延遲（需要向前讀取視頻_2 的幀）
                   正值表示 video_path 延遲（需要向前讀取視頻_1 的幀）
                   
        例如：如果 video_path 比 video_path_2 快 2 幀，設置 offset=-2
        """
        if hasattr(self, 'offset_spin') and self.offset_spin.value() != offset:
            self.offset_spin.setValue(offset)
            return
        self.frame_offset = offset
        self.video_loader.frame_offset = offset

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
        
        # 顏色配置
        COLOR_POINT = (0, 0, 255)        # 紅色 - 個別點
        COLOR_CENTER = (0, 0, 255)       # 藍色 - 中心位置
        COLOR_CIRCLE = (255, 255, 255)       # 藍色 - 外圍圓
        POINT_RADIUS = 10
        
        # 繪製每個釋放點
        for i, point_data in enumerate(self.br_points):
            if point_data is None or len(point_data) < 2:
                continue
            
            try:
                x, y = int(point_data[0]), int(point_data[1])
                
                # 檢查座標是否在圖像範圍內
                if 0 <= x < result.shape[1] and 0 <= y < result.shape[0]:
                    # 繪製點
                    cv2.circle(result, (x, y), POINT_RADIUS, COLOR_POINT, -1)
                    # 繪製邊框
                    cv2.circle(result, (x, y), POINT_RADIUS, COLOR_CIRCLE, 2)
                    # 繪製序號
                    cv2.putText(result, str(i+1), (x - 5, y - POINT_RADIUS - 5),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
            except (ValueError, TypeError):
                continue
        
        # # 如果有多個點，繪製中心位置
        # if len(self.br_points) > 1:
        #     try:
        #         points_arr = np.array([p[:2] for p in self.br_points if p is not None and len(p) >= 2], dtype=np.float32)
        #         if len(points_arr) > 0:
        #             center = np.mean(points_arr, axis=0).astype(int)
        #             cv2.circle(result, tuple(center), POINT_RADIUS + 3, COLOR_CENTER, -1)
        #             cv2.circle(result, tuple(center), POINT_RADIUS + 3, (255, 255, 255), 2)
        #             # 添加統計信息
        #             cv2.putText(result, f"BR Points: {len(self.br_points)}", (10, 30),
        #                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        #             cv2.putText(result, f"Center: ({center[0]}, {center[1]})", (10, 60),
        #                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        #     except (ValueError, TypeError):
        #         pass
        # else:
        #     # 單個點的情況下，显示點數和座標
        #     if len(self.br_points) == 1 and self.br_points[0] is not None and len(self.br_points[0]) >= 2:
        #         x, y = int(self.br_points[0][0]), int(self.br_points[0][1])
        #         cv2.putText(result, f"BR Points: 1", (10, 30),
        #                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        #         cv2.putText(result, f"Position: ({x}, {y})", (10, 60),
        #                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        
        return result

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
                self.updateFrame(self.ui.frameSlider.value())
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
        self.updateFrame(self.ui.frameSlider.value())

if __name__ == "__main__":
    app = QApplication(sys.argv)
    model = Model()
    window = PoseVideoTabControl(model)
    window.show()
    sys.exit(app.exec_())
