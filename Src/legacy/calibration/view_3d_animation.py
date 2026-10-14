
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

import sys
import os

# 解決 OpenMP 重複初始化問題
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'

import numpy as np
import json
from pathlib import Path
from Src.calibration import calib_store
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QSlider)
from PyQt5.QtCore import Qt, QTimer
from vispy import scene


class Animation3DViewer(QMainWindow):
    def __init__(self, json_file):
        super().__init__()
        self.json_file = Path(json_file)
        
        # 骨架連接定義
        self.head_links = [[0, 1], [0, 2], [1, 3], [2, 4]]
        self.left_links = [[5, 18], [5, 7], [7, 9], [19, 11], [11, 13], [13, 24]]
        self.right_links = [[6, 18], [6, 8], [8, 10], [19, 12], [12, 14], [14, 25]]
        self.trunk_links = [[17, 18], [18, 19]]
        
        # 載入數據
        self.load_data()
        
        # 當前幀索引
        self.current_frame_idx = 0
        self.is_playing = False
        
        # 初始化UI
        self.init_ui()
        
        # 初始化VisPy場景
        self.init_vispy()
        
        # 定時器用於播放動畫
        self.timer = QTimer()
        self.timer.timeout.connect(self.next_frame)
        self.timer.setInterval(50)  # 50ms = 20 FPS

    def load_data(self):
        """載入JSON數據"""
        if not self.json_file.exists():
            raise FileNotFoundError(f"找不到文件: {self.json_file}")
        
        with open(self.json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        self.frames_data = []
        for frame in data:
            keypoints = np.array(frame['keypoints'])
            self.frames_data.append(keypoints)
        
        print(f"✅ 載入 {len(self.frames_data)} 幀數據")

    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle(f"3D 動畫查看器 - {self.json_file.name}")
        self.resize(1200, 800)
        
        # 主佈局
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        # 控制面板
        control_layout = QHBoxLayout()
        
        self.btn_play = QPushButton("播放")
        self.btn_play.clicked.connect(self.toggle_play)
        
        self.btn_reset = QPushButton("重置")
        self.btn_reset.clicked.connect(self.reset)
        
        self.btn_prev = QPushButton("◀ 上一幀")
        self.btn_prev.clicked.connect(self.prev_frame)
        
        self.btn_next = QPushButton("下一幀 ▶")
        self.btn_next.clicked.connect(self.next_frame)
        
        self.lbl_frame = QLabel(f"Frame: 1 / {len(self.frames_data)}")
        
        # 速度控制
        self.lbl_speed = QLabel("速度: 20 FPS")
        self.slider_speed = QSlider(Qt.Horizontal)
        self.slider_speed.setMinimum(5)
        self.slider_speed.setMaximum(60)
        self.slider_speed.setValue(20)
        self.slider_speed.valueChanged.connect(self.change_speed)
        
        control_layout.addWidget(self.btn_play)
        control_layout.addWidget(self.btn_reset)
        control_layout.addWidget(self.btn_prev)
        control_layout.addWidget(self.btn_next)
        control_layout.addWidget(self.lbl_frame)
        control_layout.addStretch()
        control_layout.addWidget(self.lbl_speed)
        control_layout.addWidget(self.slider_speed)
        
        main_layout.addLayout(control_layout)
        
        # 幀滑桿
        frame_slider_layout = QHBoxLayout()
        self.slider_frame = QSlider(Qt.Horizontal)
        self.slider_frame.setMinimum(0)
        self.slider_frame.setMaximum(len(self.frames_data) - 1)
        self.slider_frame.setValue(0)
        self.slider_frame.valueChanged.connect(self.slider_changed)
        
        frame_slider_layout.addWidget(QLabel("幀:"))
        frame_slider_layout.addWidget(self.slider_frame)
        
        main_layout.addLayout(frame_slider_layout)

    def init_vispy(self):
        """初始化VisPy 3D場景"""
        self.canvas = scene.SceneCanvas(keys='interactive', bgcolor='white')
        self.view = self.canvas.central_widget.add_view()
        self.view.camera = scene.cameras.TurntableCamera(
            fov=45, azimuth=110, elevation=10, up='-y', distance=2.5
        )
        
        # 添加座標軸
        axis = scene.visuals.XYZAxis(parent=self.view.scene)
        axis.transform = scene.transforms.STTransform(scale=(0.1, 0.1, 0.1))
        
        # 創建骨架視覺元素
        self.scatter = scene.visuals.Markers(parent=self.view.scene)
        self.head_lines = scene.visuals.Line(connect='segments', color='orange', width=3, parent=self.view.scene)
        self.left_lines = scene.visuals.Line(connect='segments', color='red', width=3, parent=self.view.scene)
        self.right_lines = scene.visuals.Line(connect='segments', color='blue', width=3, parent=self.view.scene)
        self.trunk_lines = scene.visuals.Line(connect='segments', color='green', width=3, parent=self.view.scene)
        
        # 添加到主佈局
        self.centralWidget().layout().addWidget(self.canvas.native)
        
        # 設置相機範圍
        self.update_camera_range()
        
        # 顯示第一幀
        self.update_display()

    def update_camera_range(self):
        """更新相機視野範圍"""
        all_pts = np.concatenate(self.frames_data, axis=0)
        
        x_range = tuple(np.percentile(all_pts[:, 0], [2, 98]))
        y_range = tuple(np.percentile(all_pts[:, 1], [2, 98]))
        z_range = tuple(np.percentile(all_pts[:, 2], [2, 98]))
        
        self.view.camera.set_range(
            x=(x_range[0] - 0.2, x_range[1] + 0.2),
            y=(y_range[0] - 0.2, y_range[1] + 0.2),
            z=(z_range[0] - 0.2, z_range[1] + 0.2)
        )

    def get_skeleton_lines(self, points_3d):
        """生成骨架線段"""
        def get_links(links):
            segs = []
            for (i, j) in links:
                if i < len(points_3d) and j < len(points_3d):
                    segs.append([points_3d[i], points_3d[j]])
            return np.array(segs) if segs else np.zeros((0, 2, 3))
        
        return (
            get_links(self.head_links),
            get_links(self.left_links),
            get_links(self.right_links),
            get_links(self.trunk_links)
        )

    def update_display(self):
        """更新3D顯示"""
        if not self.frames_data:
            return
        
        points_3d = self.frames_data[self.current_frame_idx]
        
        # 更新關鍵點
        self.scatter.set_data(points_3d, face_color='orange', size=8)
        
        # 更新骨架線條
        head_segs, left_segs, right_segs, trunk_segs = self.get_skeleton_lines(points_3d)
        
        if len(head_segs) > 0:
            self.head_lines.set_data(pos=head_segs.reshape(-1, 3), connect='segments')
        if len(left_segs) > 0:
            self.left_lines.set_data(pos=left_segs.reshape(-1, 3), connect='segments')
        if len(right_segs) > 0:
            self.right_lines.set_data(pos=right_segs.reshape(-1, 3), connect='segments')
        if len(trunk_segs) > 0:
            self.trunk_lines.set_data(pos=trunk_segs.reshape(-1, 3), connect='segments')
        
        # 更新UI
        self.lbl_frame.setText(f"Frame: {self.current_frame_idx + 1} / {len(self.frames_data)}")
        self.slider_frame.blockSignals(True)
        self.slider_frame.setValue(self.current_frame_idx)
        self.slider_frame.blockSignals(False)
        
        self.canvas.update()

    def toggle_play(self):
        """播放/暫停"""
        if self.is_playing:
            self.timer.stop()
            self.btn_play.setText("播放")
            self.is_playing = False
        else:
            self.timer.start()
            self.btn_play.setText("暫停")
            self.is_playing = True

    def reset(self):
        """重置到第一幀"""
        self.current_frame_idx = 0
        self.update_display()

    def prev_frame(self):
        """上一幀"""
        if self.current_frame_idx > 0:
            self.current_frame_idx -= 1
            self.update_display()

    def next_frame(self):
        """下一幀"""
        if self.current_frame_idx < len(self.frames_data) - 1:
            self.current_frame_idx += 1
        else:
            # 循環播放
            self.current_frame_idx = 0
        self.update_display()

    def slider_changed(self, value):
        """滑桿改變"""
        self.current_frame_idx = value
        self.update_display()

    def change_speed(self, value):
        """改變播放速度"""
        interval = int(1000 / value)  # 轉換為毫秒
        self.timer.setInterval(interval)
        self.lbl_speed.setText(f"速度: {value} FPS")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # 預設使用舊設備資料夾的 calibrated_3d_frames.json
    json_file = Path(calib_store.OLD_RIG_DIR) / "calibrated_3d_frames.json"
    
    # 也可以從命令列參數指定文件
    if len(sys.argv) > 1:
        json_file = Path(sys.argv[1])
    
    if not json_file.exists():
        print(f"❌ 找不到文件: {json_file}")
        print(f"使用方式: python view_3d_animation.py [json_file_path]")
        sys.exit(1)
    
    window = Animation3DViewer(json_file)
    window.show()
    
    sys.exit(app.exec_())
