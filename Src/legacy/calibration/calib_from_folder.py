
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

import os
import cv2
import numpy as np
from pathlib import Path


class CameraCalibrator:
    def __init__(self, pattern_size=(10, 7), square_size=11):
        """
        初始化相機校正器
        
        Args:
            pattern_size: 棋盤格內角點數量 (寬, 高)
            square_size: 每個格子的實際尺寸 (mm)
        """
        self.pattern_size = pattern_size
        self.square_size = square_size
        self.objpoints = []  # 3D 空間點
        self.imgpoints = []  # 2D 圖像點
        self.image_size = None
        self.valid_images = []
        
    def load_images_from_folder(self, folder_path):
        """
        從資料夾讀取所有棋盤格圖片
        
        Args:
            folder_path: 圖片所在資料夾路徑
            
        Returns:
            圖片路徑列表
        """
        supported_formats = ('.jpg', '.jpeg', '.png', '.bmp', '.tiff')
        images = []
        
        folder_path = Path(folder_path)
        if not folder_path.exists():
            print(f"❌ 資料夾不存在: {folder_path}")
            return images
        
        for ext in supported_formats:
            images.extend(folder_path.glob(f'*{ext}'))
            images.extend(folder_path.glob(f'*{ext.upper()}'))
        
        print(f"✅ 找到 {len(images)} 張圖片")
        return sorted(images)
    
    def detect_chessboard(self, image_path):
        """
        偵測單張圖片中的棋盤格角點
        
        Args:
            image_path: 圖片路徑
            
        Returns:
            (是否成功, 角點坐標, 灰度圖像)
        """
        img = cv2.imread(str(image_path))
        if img is None:
            print(f"❌ 無法讀取圖片: {image_path}")
            return False, None, None
        
        # 記錄圖片尺寸
        if self.image_size is None:
            self.image_size = (img.shape[1], img.shape[0])
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # 尋找棋盤格角點
        ret, corners = cv2.findChessboardCorners(gray, self.pattern_size, None)
        
        if ret:
            # 提高角點精度
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
            corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
            return True, corners, img
        
        return False, None, img
    
    def calibrate(self, folder_path, output_file="camera_calib.npz", visualize=False):
        """
        從資料夾中的圖片進行相機校正
        
        Args:
            folder_path: 包含棋盤格圖片的資料夾
            output_file: 校正結果保存檔案名稱
            visualize: 是否顯示檢測結果
            
        Returns:
            (相機內參矩陣, 畸變係數, RMS誤差)
        """
        # 定義 3D 世界座標 (Z=0)
        objp = np.zeros((self.pattern_size[0] * self.pattern_size[1], 3), np.float32)
        objp[:, :2] = np.mgrid[0:self.pattern_size[0], 0:self.pattern_size[1]].T.reshape(-1, 2)
        objp *= self.square_size
        
        # 讀取圖片
        image_paths = self.load_images_from_folder(folder_path)
        if not image_paths:
            print("❌ 資料夾內沒有圖片")
            return None, None, None
        
        # 逐張處理圖片
        print("\n🔍 開始偵測棋盤格...")
        successful_count = 0
        
        for idx, image_path in enumerate(image_paths, 1):
            ret, corners, img = self.detect_chessboard(image_path)
            
            if ret:
                self.objpoints.append(objp)
                self.imgpoints.append(corners)
                self.valid_images.append(str(image_path))
                successful_count += 1
                print(f"  ✅ [{idx}/{len(image_paths)}] {image_path.name}")
                
                # 可選：顯示檢測結果
                if visualize:
                    img_vis = img.copy()
                    cv2.drawChessboardCorners(img_vis, self.pattern_size, corners, ret)
                    cv2.imshow(f"Detected: {image_path.name}", img_vis)
                    cv2.waitKey(500)
            else:
                print(f"  ❌ [{idx}/{len(image_paths)}] {image_path.name} - 未偵測到棋盤格")
        
        if visualize:
            cv2.destroyAllWindows()
        
        print(f"\n📊 成功偵測: {successful_count}/{len(image_paths)} 張圖片")
        
        if successful_count < 10:
            print("⚠️ 樣本不足（少於10張），校正結果可能不準確")
            if successful_count == 0:
                return None, None, None
        
        # 執行校正
        print("\n⏳ 計算相機內參中...")
        ret, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(
            self.objpoints, self.imgpoints, self.image_size, None, None
        )
        
        if not ret:
            print("❌ 校正失敗")
            return None, None, None
        
        # 計算重投影誤差
        total_error = 0
        total_points = 0
        
        for objp, imgp, rvec, tvec in zip(self.objpoints, self.imgpoints, rvecs, tvecs):
            projected_points, _ = cv2.projectPoints(objp, rvec, tvec, mtx, dist)
            error = cv2.norm(imgp, projected_points, cv2.NORM_L2) / len(projected_points)
            total_error += error
            total_points += 1
        
        mean_error = total_error / total_points
        
        # 顯示結果
        print("\n" + "="*60)
        print("🎉 相機校正完成！")
        print("="*60)
        print(f"成功使用的樣本數: {len(self.objpoints)}")
        print(f"圖片解析度: {self.image_size[0]} x {self.image_size[1]}")
        print(f"RMS 重投影誤差: {mean_error:.4f} 像素")
        print(f"\n相機內參矩陣 (Camera Matrix):")
        print(mtx)
        print(f"\n畸變係數 (Distortion Coefficients):")
        print(dist.ravel())
        print("="*60)
        
        # 保存結果
        output_path = Path(folder_path) / output_file
        np.savez(str(output_path), mtx=mtx, dist=dist, image_size=self.image_size, 
                 mean_error=mean_error, successful_images=self.valid_images)
        print(f"\n✅ 校正結果已保存至: {output_path}")
        
        return mtx, dist, mean_error
    
    def print_calibration_info(self, mtx, dist):
        """顯示校正信息"""
        if mtx is None:
            print("❌ 沒有校正結果")
            return
        
        print("\n" + "="*60)
        print("相機內參詳細信息")
        print("="*60)
        
        # 焦距
        fx = mtx[0, 0]
        fy = mtx[1, 1]
        print(f"焦距 (Focal Length):")
        print(f"  fx = {fx:.2f} pixels")
        print(f"  fy = {fy:.2f} pixels")
        
        # 主點 (光學中心)
        cx = mtx[0, 2]
        cy = mtx[1, 2]
        print(f"\n主點 (Principal Point):")
        print(f"  cx = {cx:.2f} pixels")
        print(f"  cy = {cy:.2f} pixels")
        
        # 畸變係數
        print(f"\n畸變係數 (Distortion Coefficients):")
        print(f"  k1 (徑向 1) = {dist[0, 0]:.6f}")
        print(f"  k2 (徑向 2) = {dist[0, 1]:.6f}")
        print(f"  p1 (切向 1) = {dist[0, 2]:.6f}")
        print(f"  p2 (切向 2) = {dist[0, 3]:.6f}")
        if len(dist[0]) > 4:
            print(f"  k3 (徑向 3) = {dist[0, 4]:.6f}")
        
        print("="*60)


def main():
    """主程序"""
    import sys
    
    # 使用方式：python calib_from_folder.py <圖片資料夾路徑>
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]
    else:
        # 預設資料夾（可自行修改）
        folder_path = input("請輸入包含棋盤格圖片的資料夾路徑: ")
    
    if not folder_path:
        print("❌ 資料夾路徑不能為空")
        return
    
    # 創建校正器
    calibrator = CameraCalibrator(pattern_size=(10, 7), square_size=11)
    
    # 執行校正
    mtx, dist, mean_error = calibrator.calibrate(
        folder_path, 
        output_file="camera_calib.npz",
        visualize=False  # 設為 True 可顯示檢測過程
    )
    
    # 顯示詳細信息
    if mtx is not None:
        calibrator.print_calibration_info(mtx, dist)


if __name__ == "__main__":
    main()
