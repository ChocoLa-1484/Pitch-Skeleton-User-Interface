
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

import sys
import os

# 解決 OpenMP 重複初始化問題
os.environ['KMP_DUPLICATE_LIB_OK'] = 'True'

import cv2
import numpy as np
import json
from pathlib import Path
from Src.calibration import calib_store
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QTableWidget, QTableWidgetItem, QMessageBox, QTextEdit, QFileDialog)
from PyQt5.QtCore import Qt, pyqtSignal, QPoint, QTimer, QSize
from PyQt5.QtGui import QImage, QPixmap
from Src.cameras.video_threads import VideoCaptureThread

# 1. 自定義 ClickableLabel 用於滑鼠選點
class ClickableLabel(QLabel):
    point_clicked = pyqtSignal(QPoint)

    def __init__(self, title):
        super().__init__(title)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.CrossCursor)
        self.setStyleSheet("border: 2px solid blue; background-color: #1a1a1a;")
        self.setMinimumSize(QSize(600, 450))
        self.clicked_points = []

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 計算點擊位置相對於縮放後圖像的坐標
            label_rect = self.rect()
            x_ratio = event.x() / label_rect.width() if label_rect.width() > 0 else 0
            y_ratio = event.y() / label_rect.height() if label_rect.height() > 0 else 0
            self.point_clicked.emit(event.pos())

class ExtrinsicCalibWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("棒球系統 - 手動點位外參校正")
        self.resize(1400, 900)

        # 載入內參 (從 stereo_calib.json)
        self.load_intrinsics()

        # 儲存點位數據
        self.points_2d_front = []
        self.points_2d_side = []
        self.points_pixel_front = []
        self.points_pixel_side = []
        
        # 真實世界座標範例 (假設選取投手板四角，單位: mm)
        self.world_points = [
            [0, 0, 0],       # 點 1 (例如投手板左上)
            [0, 0, 20],     # 點 2 (例如投手板右上)
            [7, 0, 20],   # 點 3 (右下)
            [7, 0, 0]      # 點 4 (左下)
        ]

        # 用於顯示的當前幀
        self.current_frame_front = None
        self.current_frame_side = None
        self.frame_width = 0
        self.frame_height = 0
        # 縮放因子和偏移量
        self.scale_front = 1.0
        self.scale_side = 1.0
        self.offset_front = (0, 0)
        self.offset_side = (0, 0)

        # 模式選擇：'camera' 或 'video'
        self.mode = 'camera'
        self.video_thread = None
        self.video_loader_front = None
        self.video_loader_side = None
        self.current_video_frame_front = 0
        self.current_video_frame_side = 0

        self.initUI()
        
        # 初始化視頻線程
        try:
            self.video_thread = VideoCaptureThread()
            self.video_thread.frame_ready.connect(self.on_frame_ready)
            self.video_thread.start_capture()
            self.log(f"✅ 視頻線程初始化成功（攝影機模式）")
            self.mode = 'camera'
        except Exception as e:
            self.log(f"⚠️ 視頻線程初始化失敗: {str(e)}")
            self.video_thread = None
        
        # 使用 QTimer 定時更新顯示
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_displays)
        self.timer.start(30)  # 30ms 更新一次，約 33 FPS

    def load_intrinsics(self):
        """從 stereo_calib.json 載入內參"""
        calib_file = Path(calib_store.OLD_RIG_DIR) / "stereo_calib.json"
        
        if calib_file.exists():
            try:
                with open(calib_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                # 正面相機內參
                self.mtx_f = np.array(data['camera_front']['intrinsic_matrix'], dtype=np.float32)
                self.dist_f = np.array(data['camera_front']['distortion_coefficients'], dtype=np.float32)
                
                # 側面相機內參
                self.mtx_s = np.array(data['camera_side']['intrinsic_matrix'], dtype=np.float32)
                self.dist_s = np.array(data['camera_side']['distortion_coefficients'], dtype=np.float32)
                
                self.log(f"✅ 已載入內參: {calib_file}")
            except Exception as e:
                self.log(f"❌ 讀取內參失敗: {str(e)}")
                self.use_default_intrinsics()
        else:
            self.log(f"⚠️ 找不到內參文件: {calib_file}，使用預設值")
            self.use_default_intrinsics()

    def use_default_intrinsics(self):
        """使用預設內參"""
        self.mtx_f = np.array([[1000, 0, 640], [0, 1000, 360], [0, 0, 1]], dtype=np.float32)
        self.dist_f = np.zeros(5, dtype=np.float32)
        self.mtx_s = np.array([[1000, 0, 640], [0, 1000, 360], [0, 0, 1]], dtype=np.float32)
        self.dist_s = np.zeros(5, dtype=np.float32)

    def log(self, msg):
        """添加日誌"""
        if hasattr(self, 'log_area'):
            self.log_area.append(msg)

    def initUI(self):
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        # 模式切換區
        mode_layout = QHBoxLayout()
        self.btn_camera_mode = QPushButton("📹 攝影機模式")
        self.btn_video_mode = QPushButton("🎬 影片模式")
        self.btn_camera_mode.clicked.connect(self.switch_to_camera_mode)
        self.btn_video_mode.clicked.connect(self.switch_to_video_mode)
        self.btn_camera_mode.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
        mode_layout.addWidget(self.btn_camera_mode)
        mode_layout.addWidget(self.btn_video_mode)
        layout.addLayout(mode_layout)

        # 畫面顯示區
        view_layout = QHBoxLayout()
        self.lbl_front = ClickableLabel("正面相機 (實時)")
        self.lbl_side = ClickableLabel("側面相機 (實時)")
        self.lbl_front.point_clicked.connect(self.add_point_front)
        self.lbl_side.point_clicked.connect(self.add_point_side)
        
        view_layout.addWidget(self.lbl_front)
        view_layout.addWidget(self.lbl_side)
        layout.addLayout(view_layout)

        # 數據與控制區
        bottom_layout = QHBoxLayout()
        
        # 顯示已選點位的表格
        self.table = QTableWidget(4, 3)
        self.table.setHorizontalHeaderLabels(["正面像素 (x,y)", "側面像素 (x,y)", "世界座標 (X,Y,Z)"])
        for i, wp in enumerate(self.world_points):
            self.table.setItem(i, 2, QTableWidgetItem(str(wp)))
        bottom_layout.addWidget(self.table)

        # 日誌區
        self.log_area_widget = QWidget()
        log_layout = QVBoxLayout(self.log_area_widget)
        self.log_area = QTextEdit()
        self.log_area.setStyleSheet("border: 1px solid gray; padding: 5px;")
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(100)
        log_layout.addWidget(self.log_area)
        
        # 控制按鈕
        btn_layout = QVBoxLayout()
        self.btn_run = QPushButton("計算外參 (SolvePnP)")
        self.btn_clear = QPushButton("清除所有點")
        self.btn_run.clicked.connect(self.calculate_extrinsics)
        self.btn_clear.clicked.connect(self.clear_points)
        btn_layout.addWidget(self.btn_run)
        btn_layout.addWidget(self.btn_clear)
        btn_layout.addStretch()
        
        bottom_layout.addLayout(btn_layout)
        layout.addLayout(bottom_layout)
        
        # 添加日誌區
        layout.addWidget(self.log_area)

    def convert_cv_qt(self, cv_img, label_widget, is_front=True):
        """將 OpenCV 圖像轉為 QPixmap，並計算正確的縮放和偏移"""
        if cv_img is None:
            return QPixmap(), 1.0
        
        rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        
        # 獲取 Label 的實際尺寸
        label_w = label_widget.width()
        label_h = label_widget.height()
        
        # 計算縮放比例保持長寬比
        scale = min(label_w / w, label_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        
        # 計算圖片在 Label 內的偏移量 (因為 AlignCenter)
        offset_x = (label_w - new_w) // 2
        offset_y = (label_h - new_h) // 2
        
        # 儲存到物件屬性
        if is_front:
            self.scale_front = scale
            self.offset_front = (offset_x, offset_y)
        else:
            self.scale_side = scale
            self.offset_side = (offset_x, offset_y)
        
        rgb_image = cv2.resize(rgb_image, (new_w, new_h))
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qt_img = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format_RGB888)
        return QPixmap.fromImage(qt_img), scale

    def on_frame_ready(self, frame_front, frame_side):
        """接收視頻線程發送的幀"""
        self.current_frame_front = frame_front
        self.current_frame_side = frame_side
        if self.frame_width == 0:
            self.frame_height, self.frame_width = frame_front.shape[:2]

    def add_point_front(self, pos):
        if len(self.points_2d_front) < 4:
            # 修正後的座標轉換公式：
            # (點擊位置 - 偏移量) / 縮放比例
            original_x = int((pos.x() - self.offset_front[0]) / self.scale_front)
            original_y = int((pos.y() - self.offset_front[1]) / self.scale_front)
            
            # 檢查點擊是否在有效圖片範圍內 (避免點到黑邊)
            if 0 <= original_x < self.frame_width and 0 <= original_y < self.frame_height:
                self.points_2d_front.append([original_x, original_y])
                self.points_pixel_front.append([original_x, original_y])
                row = len(self.points_2d_front) - 1
                self.table.setItem(row, 0, QTableWidgetItem(f"{original_x},{original_y}"))
                self.log(f"✓ 正面相機點 {row+1}: ({original_x}, {original_y})")
            else:
                self.log("⚠️ 點擊位置在圖片範圍外！")

    def add_point_side(self, pos):
        if len(self.points_2d_side) < 4:
            # 轉換座標：從縮放後的座標轉換回原始圖像座標
            original_x = int((pos.x() - self.offset_side[0]) / self.scale_side)
            original_y = int((pos.y() - self.offset_side[1]) / self.scale_side)
            self.points_2d_side.append([original_x, original_y])
            self.points_pixel_side.append([original_x, original_y])
            row = len(self.points_2d_side) - 1
            self.table.setItem(row, 1, QTableWidgetItem(f"{original_x},{original_y}"))
            self.log(f"✓ 側面相機點 {row+1}: ({original_x}, {original_y})")
        else:
            self.log("⚠️ 已選取 4 個側面相機點")

    def update_displays(self):
        """實時更新顯示（從視頻線程接收的幀或影片讀取）"""
        # 獲取幀
        if self.mode == 'camera':
            if self.current_frame_front is None or self.current_frame_side is None:
                return
            frame_front = self.current_frame_front
            frame_side = self.current_frame_side
        else:  # video mode
            if self.video_loader_front is None or self.video_loader_side is None:
                return
            
            ret_f, frame_front = self.video_loader_front.read()
            ret_s, frame_side = self.video_loader_side.read()
            
            if not ret_f or not ret_s:
                # 影片結束，重新開始
                self.video_loader_front.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self.video_loader_side.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret_f, frame_front = self.video_loader_front.read()
                ret_s, frame_side = self.video_loader_side.read()
            
            if not ret_f or not ret_s:
                return
        
        # 繪製已選點位
        frame_front_display = frame_front.copy()
        frame_side_display = frame_side.copy()
        
        # 在正面相機上繪製點位
        for i, pt in enumerate(self.points_pixel_front):
            cv2.circle(frame_front_display, tuple(pt), 5, (0, 255, 0), -1)
            cv2.putText(frame_front_display, str(i+1), (pt[0]+10, pt[1]), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        
        # 在側面相機上繪製點位
        for i, pt in enumerate(self.points_pixel_side):
            cv2.circle(frame_side_display, tuple(pt), 5, (0, 255, 255), -1)
            cv2.putText(frame_side_display, str(i+1), (pt[0]+10, pt[1]), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
        
        # 更新標籤
        pixmap_front, _ = self.convert_cv_qt(frame_front_display, self.lbl_front, is_front=True)
        pixmap_side, _ = self.convert_cv_qt(frame_side_display, self.lbl_side, is_front=False)
        self.lbl_front.setPixmap(pixmap_front)
        self.lbl_side.setPixmap(pixmap_side)

    def calculate_extrinsics(self):
        if len(self.points_2d_front) < 4 or len(self.points_2d_side) < 4:
            QMessageBox.warning(self, "錯誤", "兩台相機都必須選取至少 4 個點")
            self.log("❌ 選點不足，需要至少 4 個點")
            return

        try:
            # 轉換為 numpy 陣列
            obj_pts = np.array(self.world_points, dtype=np.float32)
            img_pts_f = np.array(self.points_2d_front, dtype=np.float32)
            img_pts_s = np.array(self.points_2d_side, dtype=np.float32)

            # 1. 分別對兩台相機求解 PnP (取得世界座標到相機座標的變換)
            _, rvec_f, tvec_f = cv2.solvePnP(obj_pts, img_pts_f, self.mtx_f, self.dist_f)
            _, rvec_s, tvec_s = cv2.solvePnP(obj_pts, img_pts_s, self.mtx_s, self.dist_s)

            # 2. 將旋轉向量轉為矩陣
            R_f, _ = cv2.Rodrigues(rvec_f)
            R_s, _ = cv2.Rodrigues(rvec_s)

            # 3. 計算正面相機到側面相機的相對變換 (Stereo Relation)
            # R_rel = R_side * R_front^T
            # T_rel = T_side - R_rel * T_front
            R_rel = R_s @ R_f.T
            T_rel = tvec_s - R_rel @ tvec_f

            result_msg = f"✅ 外參校正完成！\n\n相對旋轉矩陣 R:\n{R_rel}\n\n相對平移向量 T (mm):\n{T_rel}\n\n相機距離: {np.linalg.norm(T_rel):.2f} mm"
            QMessageBox.information(self, "成功", result_msg)
            self.log(result_msg)
            
            # 儲存結果為 JSON
            extrinsics_data = {
                "rotation_matrix": R_rel.tolist(),
                "translation_vector": T_rel.tolist(),
                "camera_distance_mm": float(np.linalg.norm(T_rel)),
                "rotation_matrix_front": R_f.tolist(),
                "translation_vector_front": tvec_f.tolist(),
                "rotation_matrix_side": R_s.tolist(),
                "translation_vector_side": tvec_s.tolist(),
                "world_points": self.world_points,
                "image_points_front": self.points_2d_front,
                "image_points_side": self.points_2d_side
            }
            
            out_path = os.path.join(calib_store.OLD_RIG_DIR, "extrinsics.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(extrinsics_data, f, indent=2)
            
            self.log(f"✅ 外參已儲存至 {out_path}")
            
        except Exception as e:
            self.log(f"❌ 計算失敗: {str(e)}")
            QMessageBox.critical(self, "錯誤", f"計算失敗: {str(e)}")

    def clear_points(self):
        self.points_2d_front = []
        self.points_2d_side = []
        self.points_pixel_front = []
        self.points_pixel_side = []
        self.table.clearContents()
        # 重新填入世界座標提示
        for i, wp in enumerate(self.world_points):
            self.table.setItem(i, 2, QTableWidgetItem(str(wp)))
        self.log("✓ 已清除所有選點")

    def switch_to_camera_mode(self):
        """切換至攝影機模式"""
        if self.mode == 'camera':
            self.log("ℹ️ 已經在攝影機模式")
            return
        
        # 停止影片播放
        if self.video_loader_front is not None:
            self.video_loader_front = None
        if self.video_loader_side is not None:
            self.video_loader_side = None
        
        # 啟動攝影機線程
        try:
            self.video_thread = VideoCaptureThread()
            self.video_thread.frame_ready.connect(self.on_frame_ready)
            self.video_thread.start_capture()
            self.mode = 'camera'
            self.lbl_front.setText("正面相機 (實時)")
            self.lbl_side.setText("側面相機 (實時)")
            self.btn_camera_mode.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
            self.btn_video_mode.setStyleSheet("background-color: #666; color: white;")
            self.log("✅ 已切換至攝影機模式")
        except Exception as e:
            self.log(f"❌ 攝影機初始化失敗: {str(e)}")

    def switch_to_video_mode(self):
        """切換至影片模式"""
        if self.mode == 'video' and self.video_loader_front is not None and self.video_loader_side is not None:
            self.log("ℹ️ 已經在影片模式")
            return
        
        # 停止攝影機線程
        if self.video_thread is not None:
            self.video_thread.stop_capture()
            self.video_thread = None
        
        # 打開文件選擇對話框
        front_path, _ = QFileDialog.getOpenFileName(
            self, "選擇正面相機影片", "", "視頻文件 (*.mp4 *.avi *.mov);;所有文件 (*)"
        )
        if not front_path:
            self.log("⚠️ 取消選擇正面相機影片")
            return
        
        side_path, _ = QFileDialog.getOpenFileName(
            self, "選擇側面相機影片", "", "視頻文件 (*.mp4 *.avi *.mov);;所有文件 (*)"
        )
        if not side_path:
            self.log("⚠️ 取消選擇側面相機影片")
            return
        
        # 載入影片
        try:
            self.video_loader_front = cv2.VideoCapture(front_path)
            self.video_loader_side = cv2.VideoCapture(side_path)
            
            if not self.video_loader_front.isOpened() or not self.video_loader_side.isOpened():
                raise Exception("無法打開影片文件")
            
            # 獲取影片資訊
            self.frame_width = int(self.video_loader_front.get(cv2.CAP_PROP_FRAME_WIDTH))
            self.frame_height = int(self.video_loader_front.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            self.mode = 'video'
            self.current_video_frame_front = 0
            self.current_video_frame_side = 0
            self.lbl_front.setText("正面相機 (影片)")
            self.lbl_side.setText("側面相機 (影片)")
            self.btn_camera_mode.setStyleSheet("background-color: #666; color: white;")
            self.btn_video_mode.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold;")
            self.log(f"✅ 已載入影片模式")
            self.log(f"正面影片: {Path(front_path).name}")
            self.log(f"側面影片: {Path(side_path).name}")
        except Exception as e:
            self.log(f"❌ 影片載入失敗: {str(e)}")
            self.video_loader_front = None
            self.video_loader_side = None

    def closeEvent(self, event):
        """窗口關閉時釋放資源"""
        if self.video_thread is not None:
            self.video_thread.stop_capture()
        if self.video_loader_front is not None:
            self.video_loader_front.release()
        if self.video_loader_side is not None:
            self.video_loader_side.release()
        self.timer.stop()
        super().closeEvent(event)



def main():
    app = QApplication(sys.argv)
    window = ExtrinsicCalibWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
