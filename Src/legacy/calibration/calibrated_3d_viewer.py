
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
import cv2
import json
from vispy import scene, app
from pathlib import Path
from Src.calibration import calib_store


class Calibrated3DViewer:
    """
    使用已校正的內參和外參進行3D重建與可視化
    """
    def __init__(self, calib_folder=None):
        """
        初始化3D查看器
        
        Args:
            calib_folder: 校正文件所在文件夾，如果為None則使用當前目錄
        """
        if calib_folder is None:
            calib_folder = Path(calib_store.OLD_RIG_DIR)
        else:
            calib_folder = Path(calib_folder)
        
        # 載入內參和外參
        self.load_calibration(calib_folder)
        
        # 骨架連接定義（與Triangulate3DViewer相同）
        self.head_links = [[0, 1], [0, 2], [1, 3], [2, 4]]
        self.left_links = [[5, 18], [5, 7], [7, 9], [19, 11], [11, 13], [13, 24]]
        self.right_links = [[6, 18], [6, 8], [8, 10], [19, 12], [12, 14], [14, 25]]
        self.trunk_links = [[17, 18], [18, 19]]
        
        # 3D 幀數據存儲
        self.all_3d_frames = {}
        self.frame_idx = 0
        
        # 初始化 VisPy 場景
        self.init_vispy()
        
        # 自動存檔設置
        self.save_json_path = os.path.join(calib_store.OLD_RIG_DIR, "calibrated_3d_frames.json")
        self.auto_save = True

    def load_calibration(self, calib_folder):
        """載入內參和外參"""
        intrinsic_file = calib_folder / "stereo_calib.json"
        extrinsic_file = calib_folder / "extrinsics.json"
        
        # 載入內參
        if not intrinsic_file.exists():
            raise FileNotFoundError(f"找不到內參文件: {intrinsic_file}")
        
        with open(intrinsic_file, 'r', encoding='utf-8') as f:
            intrinsic_data = json.load(f)
        
        self.K_front = np.array([
            [7131.197489913678, 0.0, 1190.6066203243458],
            [0.0, 7107.943256043428, 665.2288622157741],
            [0.0, 0.0, 1.0]
        ])
        self.K_side = np.array([
            [1063.0766604691114, 0.0, 962.1115766023166],
            [0.0, 1061.6146093201994, 570.8085693424371],
            [0.0, 0.0, 1.0]
        ])
        
        # self.K_front = np.array(intrinsic_data['camera_front']['intrinsic_matrix'], dtype=np.float32)
        # self.dist_front = np.array(intrinsic_data['camera_front']['distortion_coefficients'], dtype=np.float32)
        # self.K_side = np.array(intrinsic_data['camera_side']['intrinsic_matrix'], dtype=np.float32)
        # self.dist_side = np.array(intrinsic_data['camera_side']['distortion_coefficients'], dtype=np.float32)
        
        print(f"✅ 已載入內參從: {intrinsic_file}")
        print(f"正面相機內參:\n{self.K_front}")
        print(f"側面相機內參:\n{self.K_side}")
        
        # 載入外參
        if extrinsic_file.exists():
            with open(extrinsic_file, 'r', encoding='utf-8') as f:
                extrinsic_data = json.load(f)
            
            self.R = np.array([
                [ 0.06319786, -0.19727105,  0.97830985],
                [ 0.3345249,   0.9277469,   0.16546531],
                [-0.94026545,  0.31681195,  0.1246237 ]])
            self.T = np.array([-0.98997318,  0.07845736, -0.11746298]).reshape(3, 1)
            
            print(f"✅ 已載入外參從: {extrinsic_file}")
            print(f"相機相對旋轉矩陣:\n{self.R}")
            print(f"相機相對平移向量:\n{self.T.T}")
            print(f"相機距離: {extrinsic_data['camera_distance_mm']:.2f} mm")
        else:
            print(f"⚠️ 找不到外參文件: {extrinsic_file}，使用默認值")
            self.R = np.eye(3, dtype=np.float32)
            self.T = np.array([[100], [0], [0]], dtype=np.float32)
        
        # 構建投影矩陣
        self.build_projection_matrices()

    def build_projection_matrices(self):
        """構建兩個相機的投影矩陣"""
        # 正面相機投影矩陣 (世界坐標系原點在正面相機)
        self.P_front = self.K_front @ np.hstack((np.eye(3), np.zeros((3, 1))))
        
        # 側面相機投影矩陣 (使用外參變換)
        self.P_side = self.K_side @ np.hstack((self.R, self.T))
        
        print(f"\n投影矩陣已構建:")
        print(f"P_front shape: {self.P_front.shape}")
        print(f"P_side shape: {self.P_side.shape}")

    def init_vispy(self):
        """初始化VisPy 3D可視化場景"""
        self.canvas = scene.SceneCanvas(keys='interactive', show=False, bgcolor='white')
        self.view = self.canvas.central_widget.add_view()
        self.view.camera = scene.cameras.TurntableCamera(
            fov=45, azimuth=110, elevation=0, up='-y', distance=2.0
        )
        
        # 添加座標軸
        axis = scene.visuals.XYZAxis(parent=self.view.scene)
        axis.transform = scene.transforms.STTransform(scale=(0.1, 0.1, 0.1))
        
        # 創建骨架線條
        self.scatter = scene.visuals.Markers(parent=self.view.scene)
        self.head_lines = scene.visuals.Line(connect='segments', color='orange', width=3, parent=self.view.scene)
        self.left_lines = scene.visuals.Line(connect='segments', color='red', width=3, parent=self.view.scene)
        self.right_lines = scene.visuals.Line(connect='segments', color='blue', width=3, parent=self.view.scene)
        self.trunk_lines = scene.visuals.Line(connect='segments', color='green', width=3, parent=self.view.scene)
        
        # 創建定時器（用於動畫播放）
        self.timer = app.Timer(interval=0.2, connect=self.update, start=False)

    def triangulate_points(self, points_front, points_side):
        """
        使用校正後的投影矩陣進行三角測量
        
        Args:
            points_front: 正面相機的2D點 (N, 2)
            points_side: 側面相機的2D點 (N, 2)
        
        Returns:
            points_3d: 三角測量得到的3D點 (N, 3)
        """
        points_front = np.array(points_front, dtype=np.float32)
        points_side = np.array(points_side, dtype=np.float32)
        
        # 確保形狀正確
        if points_front.shape != points_side.shape:
            raise ValueError(f"點數不匹配: {points_front.shape} vs {points_side.shape}")
        
        # 轉置為 (2, N) 格式 (cv2.triangulatePoints 要求)
        pts_front = points_front.T
        pts_side = points_side.T
        
        # 使用 OpenCV 三角測量
        points_4d = cv2.triangulatePoints(self.P_front, self.P_side, pts_front, pts_side)
        
        # 轉換為3D點 (除以齊次坐標)
        points_3d = (points_4d[:3] / points_4d[3]).T
        
        return points_3d

    def add_frame_from_keypoints(self, frame_front, frame_side, frame_num):
        """
        從2D關鍵點添加一幀3D數據
        
        Args:
            frame_front: dict, 包含 "keypoints" 欄位 (正面相機)
            frame_side: dict, 包含 "keypoints" 欄位 (側面相機)
            frame_num: int, 幀編號
        """
        if frame_num in self.all_3d_frames:
            print(f"Frame {frame_num} 已存在，跳過")
            return
        
        # 提取關鍵點座標
        kp_front = np.array(frame_front["keypoints"])[:, :2]  # (26, 2)
        kp_side = np.array(frame_side["keypoints"])[:, :2]    # (26, 2)
        
        if kp_front.shape[0] != 26 or kp_side.shape[0] != 26:
            print(f'⚠️ 關鍵點數量不對: {kp_front.shape}, {kp_side.shape}')
            return
        
        # 三角測量
        try:
            points_3d = self.triangulate_points(kp_front, kp_side)
            self.all_3d_frames[frame_num] = points_3d
            self.frame_idx = frame_num
            self.update_camera_range()
            self.update()
            
            if self.auto_save:
                self.save_to_json()
            
            print(f"✅ Frame {frame_num} 重建完成，共 {len(points_3d)} 個3D點")
            
        except Exception as e:
            print(f"❌ Frame {frame_num} 三角測量失敗: {str(e)}")

    def update_camera_range(self):
        """根據所有3D點更新相機視野範圍"""
        if not self.all_3d_frames:
            return
        
        all_pts = np.concatenate(list(self.all_3d_frames.values()), axis=0)
        
        x_range = tuple(np.percentile(all_pts[:, 0], [2, 98]))
        y_range = tuple(np.percentile(all_pts[:, 1], [2, 98]))
        z_range = tuple(np.percentile(all_pts[:, 2], [2, 98]))
        
        self.view.camera.set_range(
            x=(x_range[0] - 0.1, x_range[1] + 0.1),
            y=(y_range[0] - 0.1, y_range[1] + 0.1),
            z=(z_range[0] - 0.1, z_range[1] + 0.1)
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

    def update(self, event=None):
        """更新3D顯示"""
        if not self.all_3d_frames:
            return
        
        if self.frame_idx not in self.all_3d_frames:
            valid_frames = sorted(self.all_3d_frames.keys())
            if not valid_frames:
                return
            self.frame_idx = valid_frames[0]
        
        points_3d = self.all_3d_frames[self.frame_idx]
        
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

    def set_frame(self, frame_idx):
        """設置當前顯示的幀"""
        if frame_idx in self.all_3d_frames:
            self.frame_idx = frame_idx
            self.update()
            self.canvas.update()

    def save_to_json(self):
        """保存3D數據到JSON"""
        if not self.all_3d_frames:
            return
        
        data_to_save = []
        for frame_num in sorted(self.all_3d_frames.keys()):
            points_3d = self.all_3d_frames[frame_num]
            keypoints = [[float(x), float(y), float(z)] for x, y, z in points_3d]
            data_to_save.append({
                "frame_number": frame_num,
                "keypoints": keypoints
            })
        
        with open(self.save_json_path, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, indent=2)
        
        print(f"✅ 已保存 {len(data_to_save)} 幀到: {self.save_json_path}")

    def get_canvas(self):
        """獲取VisPy畫布"""
        return self.canvas

    def start_animation(self):
        """開始動畫播放"""
        self.timer.start()

    def stop_animation(self):
        """停止動畫播放"""
        self.timer.stop()

    def reset(self):
        """重置所有數據"""
        self.all_3d_frames = {}
        self.frame_idx = 0
        self.update()


# 測試代碼
if __name__ == "__main__":
    import sys
    from PyQt5.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget
    
    # 創建應用
    app_qt = QApplication(sys.argv)
    
    # 創建3D查看器
    viewer = Calibrated3DViewer()
    
    # 創建測試窗口
    window = QMainWindow()
    window.setWindowTitle("校正後的3D重建查看器")
    window.resize(800, 600)
    
    central_widget = QWidget()
    layout = QVBoxLayout(central_widget)
    layout.addWidget(viewer.get_canvas().native)
    window.setCentralWidget(central_widget)
    
    # 顯示窗口
    window.show()
    
    # 加載真實數據
    print("\n正在加載數據...")
    c0_file = "C:\\Users\\Syyin\\Desktop\\Pitch-Skeleton-User-Interface_v6\\Db\\Record\\Pitcher01_20250828\\20250828_090508\\CS_Fps180_20250828_090508.json"
    c1_file = "C:\\Users\\Syyin\\Desktop\\Pitch-Skeleton-User-Interface_v6\\Db\\Record\\Pitcher01_20250828\\20250828_090508\\CF_Fps180_20250828_090508.json"
    
    if c0_file and c1_file:
        print(f"✅ C0: {c0_file}")
        print(f"✅ C1: {c1_file}")
        
        with open(c0_file, 'r') as f:
            data_c0 = json.load(f)
        with open(c1_file, 'r') as f:
            data_c1 = json.load(f)
        
        print(f"C0 幀數: {len(data_c0)}, C1 幀數: {len(data_c1)}")
        
        # 重建所有幀
        frame_count = min(len(data_c0), len(data_c1))
        for i in range(frame_count):
            viewer.add_frame_from_keypoints(data_c0[i], data_c1[i], frame_num=i+1)
            if (i + 1) % 20 == 0:
                print(f"已處理 {i+1}/{frame_count} 幀")
        
        print(f"✅ 完成! 共重建 {len(viewer.all_3d_frames)} 幀")
    else:
        print(f"❌ 找不到數據文件")
    
    sys.exit(app_qt.exec_())
