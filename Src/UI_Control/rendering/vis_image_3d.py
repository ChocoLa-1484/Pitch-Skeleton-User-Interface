import cv2
import numpy as np
import matplotlib.pyplot as plt
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from Src.analysis.analyze_3d import PoseAnalyzer
from Src.pose.detect_skeleton import PoseEstimater
import os
from scipy.signal import savgol_filter
from Src.common.timer import Timer
import sys
import time

try:
    colors = np.round(
        np.array(plt.get_cmap('gist_rainbow').colors) * 255
    ).astype(np.uint8)[:, ::-1].tolist()
except AttributeError:  # if palette has not pre-defined colors
    colors = np.round(
        np.array(plt.get_cmap('gist_rainbow')(np.linspace(0, 1, 10))) * 255
    ).astype(np.uint8)[:, -2::-1].tolist()

if getattr(sys, 'frozen', False):
    application_path = sys._MEIPASS
else:
    application_path = os.path.dirname(__file__)

from Src.UI_Control.rendering.vis_image import ImageDrawer as SkeletonImageDrawer


class ImageDrawer(SkeletonImageDrawer):
    def __init__(self, pose_estimater: PoseEstimater=None, pose_analyzer:PoseAnalyzer=None, angle_name:str = "右手肘",angle_name2:str="右腋窩"):
        self.font_path = os.path.join(application_path, 'R-PMingLiU-TW-2.ttf')
        self.fontStyle = ImageFont.truetype(self.font_path, 20)
        self.pose_estimater = pose_estimater
        self.pose_analyzer = pose_analyzer
        self.angle_name = angle_name
        self.angle_name2=angle_name2
        self.show_grid = False
        self.show_bbox = False
        self.show_skeleton = False
        self.show_traj = False
        self.show_countdown = False
        self.show_angle_info = False
        self.angle_info_pos = (0,0)
        self.region = [(100, 250), (450, 600)]
        self.keyframe_shoulder_hip_angle = None
        self.keyframe_stride_distance = None
        self.keyframe_shoulder_angle = None
        self.keyframe_wrist_speed = None
        self.keyframe_extension_distance = None
        # 指標文字框位置微調：可全域調整上下與左右位移
        self.metric_offset_x = 150
        self.metric_offset_y = -120
        self.metric_text_shift_x = 0
        self.metric_text_shift_y = 0

        self.timer = None
        
        # 預先計算顏色調色板緩存，避免每次都重新計算
        self._color_cache = {}
        self._init_color_cache()
    
    def _init_color_cache(self):
        """預先計算常用顏色調色板"""
        palettes = [
            ('gist_rainbow', 10),
            ('gist_rainbow', 16),
            ('Set2', 8),
            ('Set2', 'jet'),
        ]
        for palette_name, palette_samples in palettes:
            try:
                if isinstance(palette_samples, str):
                    # 對於 'jet' 這類字符串，使用預設數量
                    palette_samples = 8
                colors = np.round(
                    np.array(plt.get_cmap(palette_name).colors) * 255
                ).astype(np.uint8)[:, ::-1].tolist()
            except AttributeError:
                colors = np.round(
                    np.array(plt.get_cmap(palette_name)(np.linspace(0, 1, palette_samples))) * 255
                ).astype(np.uint8)[:, -2::-1].tolist()
            self._color_cache[(palette_name, palette_samples)] = colors
    
    def _get_colors(self, palette_name, palette_samples):
        """從緩存中獲取顏色，避免重複計算"""
        key = (palette_name, palette_samples)
        if key in self._color_cache:
            return self._color_cache[key]
        
        # 如果緩存中沒有，計算並存儲
        try:
            colors = np.round(
                np.array(plt.get_cmap(palette_name).colors) * 255
            ).astype(np.uint8)[:, ::-1].tolist()
        except AttributeError:
            colors = np.round(
                np.array(plt.get_cmap(palette_name)(np.linspace(0, 1, palette_samples))) * 255
            ).astype(np.uint8)[:, -2::-1].tolist()
        self._color_cache[key] = colors
        return colors
    
    def drawInfo(self, img:np.ndarray, frame_num:int=None, kpt_buffer:list = None, countdown_time:int = None, br_points:list = None):
        if img is None:
            return
        image = img.copy()
        curr_person_df = self.pose_estimater.getPersonDf(frame_num = frame_num, is_select=True)
        if curr_person_df is None:
            return image
        
        if countdown_time is not None:
            self.show_countdown = True

        if self.show_countdown:
            image = self.drawCountdown(image, countdown_time)

        if self.show_grid :
            image = self.drawGrid(image)
        
        if self.show_bbox:
            image = self.drawBbox(image, curr_person_df)
        
        if self.show_skeleton:
            image = self.drawPointsandSkeleton(image, curr_person_df, self.pose_estimater.joints['haple']['skeleton_links'], 
                                                points_palette_samples=10)
        
        if self.show_traj:
            image = self.drawTraj(image, kpt_buffer)

        if self.show_angle_info:
            image = self.drawAngleInfo(image, frame_num)
        
        return image

    def drawCross(self, image, x, y, length=5, color=(0, 0, 255), thickness=2):
        cv2.line(image, (x, y - length), (x, y + length), color, thickness)
        cv2.line(image, (x - length, y), (x + length, y), color, thickness)

    def drawGrid(self, image:np.ndarray):
        #return image:np.ndarray
        
        height, width = image.shape[:2]
        self.drawCross(image,int(width/2),int(height/2),length=20,color=(0,0,255),thickness = 3)
        # 計算垂直線的位置
        vertical_interval = width // 5
        vertical_lines = [vertical_interval * i for i in range(1, 5)]

        # 計算水平線的位置
        horizontal_interval = height // 5
        horizontal_lines = [horizontal_interval * i for i in range(1, 5)]

        # 畫垂直線
        for x in vertical_lines:
            cv2.line(image, (x, 0), (x, height), (0, 255, 0), 2)

        # 畫水平線
        for y in horizontal_lines:
            cv2.line(image, (0, y), (width, y), (0, 255, 0), 2)

        return image

    def drawBbox(self, image:np.ndarray, person_df:pd.DataFrame):
        if person_df.empty:
            return image
        person_ids = person_df['person_id']
        person_bbox = person_df['bbox']
        for id, bbox in zip(person_ids, person_bbox):
            x1, y1, x2, y2 = int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])
            color = tuple(colors[id % len(colors)])
            color = (0,255,0)
            image = cv2.rectangle(image, (x1, y1), (x2, y2), color, 4)
            image = cv2.putText(image, str(id), (x1, y1-10), cv2.FONT_HERSHEY_COMPLEX, 1.5, color, 2)
        return image

    def drawTraj(self, img: np.ndarray, kpt_buffer: list):
        if not kpt_buffer or len(kpt_buffer) < 2:
            return img

        # 將座標轉換為整數
        int_kpt_buffer = [tuple(map(int, kpt)) for kpt in kpt_buffer]
        
        # 迭代相鄰的兩個點，並畫出軌跡線
        for (f_kptx, f_kpty), (s_kptx, s_kpty) in zip(int_kpt_buffer[:-1], int_kpt_buffer[1:]):
            cv2.line(img, (f_kptx, f_kpty), (s_kptx, s_kpty), (0, 255, 0), 5)

        return img

    def draw_extended_angle(self, img, p1, p2, p3, offset=50, return_angle=False):
        """
        p1: 支點1 (例如肩膀)，欲凸出的方向
        p2: 關節中心 (例如手肘)
        p3: 支點2 (例如手腕)
        offset: 凸出去的距離 (像素)
        """
        # 轉為 numpy 陣列
        p1, p2, p3 = np.array(p1[:2]), np.array(p2[:2]), np.array(p3[:2])

        # 1. 計算向量
        v1 = p1 - p2

        # 2. 單位化
        v1_unit = v1 / np.linalg.norm(v1)

        # 3. 定義延伸線的終點
        # 重點：沿著骨骼的方向「反向」延伸或延伸出關節點
        # 這裡我們讓它從關節點 p2 出發，往 p1 和 p3 的方向畫出長度為 offset 的線
        ext_p3 = p2 - v1_unit * offset  # 這條線是往反方向延伸，讓 V 字型更明顯

        # 4. 繪製兩條紅色的線 (組成一個 V 字型)
        # 線條起點都是關節中心 p2
        cv2.line(img, tuple(p2.astype(int)), tuple(p1.astype(int)), (0, 0, 255), 7, cv2.LINE_AA)
        cv2.line(img, tuple(p2.astype(int)), tuple(p3.astype(int)), (0, 0, 255), 7, cv2.LINE_AA)
        cv2.line(img, tuple(p2.astype(int)), tuple(ext_p3.astype(int)), (0, 0, 255), 7, cv2.LINE_AA)
        if return_angle:
            angle = self._calculate_angle_between_vectors(ext_p3 - p2, p3 - p2)
            return img, int(angle)
        return img
    
    def _calculate_angle_between_vectors(self, v1: np.ndarray, v2: np.ndarray):
        """計算兩向量夾角（度）。若向量長度為 0，回傳 None。"""
        norm_v1 = np.linalg.norm(v1)
        norm_v2 = np.linalg.norm(v2)
        if norm_v1 == 0 or norm_v2 == 0:
            return None

        cos_theta = np.dot(v1, v2) / (norm_v1 * norm_v2)
        cos_theta = np.clip(cos_theta, -1.0, 1.0)
        angle_deg = np.degrees(np.arccos(cos_theta))
        return float(angle_deg)

    def draw_styled_metric(self, img, point, label, value, unit="", direction='right',
                           value_color=(255, 255, 255), label_color=(220, 220, 220),
                           offset_x=None, offset_y=None, shift_x=0, shift_y=0):
        """
        point: (x, y) 關節點坐標
        label: 標題文字 (例如 "SHOULDER ABDUCTION")
        value: 數值
        unit: 單位 (例如 "°" 或 "m/s")
        direction: 'right' 或 'left'，控制標籤延伸方向
        """
        x, y = int(point[0]), int(point[1])
        h, w = img.shape[:2]
        
        # 1. 繪製關節點圓圈
        cv2.circle(img, (x, y), 5, (255, 255, 255), -1) # 實心白圓
        cv2.circle(img, (x, y), 8, (0, 255, 0), 2)     # 綠色外圈

        # 2. 計算引導線路徑（可調整左右與高度）
        if offset_x is None:
            offset_x = self.metric_offset_x
        if offset_y is None:
            offset_y = self.metric_offset_y

        signed_offset_x = offset_x if direction == 'right' else -offset_x
        end_point = (x + signed_offset_x, y + offset_y)
        text_line_end = (end_point[0] + (100 if direction == 'right' else -100), end_point[1])

        # 繪製折線
        pts = np.array([[x, y], end_point, text_line_end], np.int32)
        cv2.polylines(img, [pts], False, (255, 255, 255), 2, cv2.LINE_AA)

        # 3. 繪製文字
        val_text = f"{value} {unit}"
        label_text = label
        
        # 增加 size 數值可以讓字變大
        size_val = 60  # 主數據字體大小 (原本約 28-32)
        size_lbl = 50  # 標籤字體大小 (原本約 16)
        
        font_val = ImageFont.truetype(self.font_path, size_val)
        font_label = ImageFont.truetype(self.font_path, size_lbl)

        # 3. 計算背景框 (自動縮放)
        v_bbox = font_val.getbbox(val_text)
        l_bbox = font_label.getbbox(label_text)
        box_w = max(v_bbox[2]-v_bbox[0], l_bbox[2]-l_bbox[0]) + 40
        box_h = 160 # 稍微加高以容納大字
        
        tx = end_point[0] if direction == 'right' else text_line_end[0] - box_w + 20
        ty = end_point[1] - 75
        tx += self.metric_text_shift_x + int(shift_x)
        ty += self.metric_text_shift_y + int(shift_y)
        tx = max(10, min(tx, w - box_w - 10))
        ty = max(10, min(ty, h - box_h - 10))

        # 4. 繪製半透明背景 (加深一點點，讓文字更清楚)
        overlay = img.copy()
        cv2.rectangle(overlay, (int(tx - 15), int(ty)), (int(tx + box_w), int(ty + box_h)), (0, 0, 0), -1)
        alpha = 0.6  # 將透明度從 0.5 調整為 0.6，背景更黑文字更明顯
        img = cv2.addWeighted(overlay, alpha, img, 1 - alpha, 0)

        # 5. 切換到 PIL 繪製
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        img_pil = Image.fromarray(img_rgb)
        draw = ImageDraw.Draw(img_pil)

        # --- 調整重點：粗細模擬 (使用 stroke_width) ---
        # stroke_width: 描邊寬度，數值越大字看起來越粗
        # stroke_fill: 描邊顏色，設定與字體顏色相同即可模擬加粗
        
        # 繪製主數值 (加粗效果)
        draw.text((tx, ty + 15), val_text, font=font_val, fill=value_color,
              stroke_width=2, stroke_fill=value_color)
        
        # 繪製中文標籤 (微加粗)
        draw.text((tx, ty + 95), label_text, font=font_label, fill=label_color,
              stroke_width=1, stroke_fill=label_color)

        return cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)

    def drawAngleInfo(self, img: np.ndarray, frame_num: int, keyframe_type: str = None, start_frame: int = 1) -> np.ndarray:
        """
        根據關鍵幀類型繪製不同的分析信息。
        
        Args:
            img: 圖像
            frame_num: 幀號
            keyframe_type: 關鍵幀類型
                - 'foot_contact': 腳部接觸幀（肩髖分離角度 + 跨步距離）
                - 'max_shoulder_er': 最大肩外旋幀（肩外旋角度）
                - 'release': 釋放點幀（最大手腕速度）
                - None: 使用原始邏輯
        """
        if keyframe_type is None:
            # 原始邏輯
            return self._drawAngleInfo_original(img, frame_num)
        elif keyframe_type == 'foot_contact':
            return self._drawKeyframeFootContact(img, frame_num, start_frame)
        elif keyframe_type == 'max_shoulder_er':
            return self._drawKeyframeMaxShoulderER(img, frame_num)
        elif keyframe_type == 'release':
            return self._drawKeyframeRelease(img, frame_num, start_frame)
        else:
            return img

    def _drawAngleInfo_original(self, img: np.ndarray, frame_num: int) -> np.ndarray:
        """原始的 drawAngleInfo 邏輯"""
        # 嘗試使用新的方法 (analyze_3d)
        if hasattr(self.pose_analyzer, 'get_frame_angleOrSpeed_data'):
            _, angle_info = self.pose_analyzer.get_frame_angleOrSpeed_data(frame_num, angle_name=self.angle_name)
            _, angle_info2 = self.pose_analyzer.get_frame_angleOrSpeed_data(frame_num, angle_name=self.angle_name2)
            
            if angle_info and len(angle_info) > 0:
                angle_value = int(angle_info[0]) if isinstance(angle_info, (list, tuple)) else int(angle_info)
                if angle_info2 and len(angle_info2) > 0:
                    angle_value2 = int(angle_info2[0]) if isinstance(angle_info2, (list, tuple)) else int(angle_info2)
                    # 簡單繪製文字
                    cv2.putText(img, f"{angle_value}°", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2)
                    cv2.putText(img, f"{angle_value2}°", (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 2)
            return img
        
        # 回退到舊的方法 (analyze)
        if not hasattr(self.pose_analyzer, 'get_frame_angle_data'):
            return img
            
        _, angle_info = self.pose_analyzer.get_frame_angle_data(frame_num, self.angle_name)
        if (len(angle_info) == 0):
            return img
        # 提取角度值，并将其转换为整数
        angle_value = int(angle_info[0])        
        # 提取坐标并将其转换为整数元组
        p = tuple(map(int, angle_info[1][1]))

        _, angle_info2 = self.pose_analyzer.get_frame_angle_data(frame_num, self.angle_name2)
        if (len(angle_info2) == 0):
            return img
        # 提取角度值，并将其转换为整数
        angle_value2 = int(angle_info2[0])        
        # 提取坐标并将其转换为整数元组
        p2 = tuple(map(int, angle_info2[1][1]))

        # 使用 cv2.line 绘制线条
        cv2.line(img, p, (self.angle_info_pos[0] + 20, self.angle_info_pos[1] + 320), (0, 0, 0), 2)        
        # 使用 cv2.putText 绘制角度值
        img = cv2.putText(img, str(angle_value), (self.angle_info_pos[0] - 50, self.angle_info_pos[1] + 420), 
                        cv2.FONT_HERSHEY_COMPLEX, 3.5, (0, 255, 0), 3)
        
        # 使用 cv2.line 绘制线条
        cv2.line(img, p2, (self.angle_info_pos[0] + 500, self.angle_info_pos[1] + 320), (0, 0, 0), 2)        
        # 使用 cv2.putText 绘制角度值
        img = cv2.putText(img, str(angle_value2), (self.angle_info_pos[0] + 450, self.angle_info_pos[1] + 420), 
                        cv2.FONT_HERSHEY_COMPLEX, 3.5, (0, 255, 0), 3)
        return img

    def _drawKeyframeFootContact(self, img: np.ndarray, frame_num: int, start_frame: int = 1) -> np.ndarray:
        """
        繪製第一個關鍵幀的信息：肩髖分離角度 + 跨步距離
        """
        self.keyframe_shoulder_hip_angle = None
        self.keyframe_stride_distance = None
        try:
            keypoints = self.pose_estimater.getPersonDf(frame_num=frame_num, is_select=True, is_kpt=True)
            
            # 繪製臀部軌跡（索引 19）
            try:
                img = self._draw_keypoint_trajectory(img, frame_num, kpt_index=19, start_frame=start_frame)
            except Exception as e:
                print(f"Error drawing left hip trajectory: {e}")

            # 獲取肩髖分離角度
            try:
                _, shoulder_hip_angle = self.pose_analyzer.get_frame_angleOrSpeed_data(
                    frame_num, speed_name="shoulder_hiple_angle"
                )
                if shoulder_hip_angle is not None and keypoints is not None:
                    # 確保轉換為數字
                    if isinstance(shoulder_hip_angle, (list, tuple, np.ndarray)):
                        shoulder_hip_angle = int(shoulder_hip_angle[0])
                    else:
                        shoulder_hip_angle = int(shoulder_hip_angle)
                    self.keyframe_shoulder_hip_angle = shoulder_hip_angle
                    hip_center = keypoints[19]
                    img = self.draw_styled_metric(img, hip_center, "肩髖分離角度", shoulder_hip_angle, "°", direction='left', offset_y=100)
            except Exception as e:
                print(f"Error getting shoulder_hip_angle: {e}")
            
            # 獲取跨步距離
            try:
                base_x = self._get_baseline_right_ankle_x()
                stride_distance = self._calculate_stride_distance(frame_num)
                if stride_distance is not None and keypoints is not None:
                    self.keyframe_stride_distance = float(stride_distance)
                    metric = getattr(self.pose_analyzer.viewer3D, 'metric', False)
                    left_ankle = keypoints[self.pose_analyzer.viewer3D.heel if metric else 15]
                    if base_x is not None and np.isfinite(left_ankle[0]) and np.isfinite(left_ankle[1]):
                        pts = np.array([
                            (int(base_x), int(left_ankle[1]) + 70),
                            (int(base_x), int(left_ankle[1]) + 100),
                            (int(left_ankle[0]), int(left_ankle[1]) + 100),
                            (int(left_ankle[0]), int(left_ankle[1]) + 70)
                        ], dtype=np.int32).reshape((-1, 1, 2))
                        cv2.polylines(img, [pts], False, (219, 124, 28), 5, cv2.LINE_AA)
                    img, elbow_angle = self.draw_extended_angle(img, keypoints[6], keypoints[8], keypoints[10], offset=80, return_angle=True)
                    img, knee_angle = self.draw_extended_angle(img, keypoints[15], keypoints[13], keypoints[11], offset=80, return_angle=True)
                                        
                    img = self.draw_styled_metric(img, left_ankle, "後腳3D位移" if metric else "跨步距離", f"{stride_distance*100:.1f}", "cm", direction='right')
                    if elbow_angle is not None and knee_angle is not None:
                        img = self.draw_styled_metric(img, keypoints[8], "肘彎曲", f"{elbow_angle}", "°", direction='left', offset_y=-150)
                        img = self.draw_styled_metric(img, keypoints[13], "膝彎曲", f"{knee_angle}", "°", direction='right', offset_y=-150)
            except Exception as e:
                print(f"Error calculating stride distance: {e}")
        
        except Exception as e:
            print(f"Error in _drawKeyframeFootContact: {e}")
        
        return img

    def _drawKeyframeMaxShoulderER(self, img: np.ndarray, frame_num: int) -> np.ndarray:
        """
        繪製第二個關鍵幀的信息：最大肩外旋角度
        """
        self.keyframe_shoulder_angle = None
        try:
            keypoints = self.pose_estimater.getPersonDf(frame_num=frame_num, is_select=True, is_kpt=True)
            
            # 獲取肩外旋角度
            try:
                _, shoulder_angle = self.pose_analyzer.get_frame_angleOrSpeed_data(
                    frame_num, speed_name="shoulder_angle"
                )
                if shoulder_angle is not None and keypoints is not None:
                    # 確保轉換為數字
                    if isinstance(shoulder_angle, (list, tuple, np.ndarray)):
                        shoulder_angle = int(shoulder_angle[0])
                    else:
                        shoulder_angle = int(shoulder_angle)
                    self.keyframe_shoulder_angle = shoulder_angle
                    img = self.draw_extended_angle(img, keypoints[10], keypoints[8], (keypoints[8][0] + 100, keypoints[8][1]), offset=0)
                    img, knee_angle = self.draw_extended_angle(img, keypoints[15], keypoints[13], keypoints[11], offset=80, return_angle=True)
                    img = self.draw_styled_metric(img, keypoints[6], "最大肩外旋角度", shoulder_angle, "°", direction='left')
                    if knee_angle is not None:
                        img = self.draw_styled_metric(img, keypoints[13], "膝彎曲", f"{knee_angle}", "°", direction='right')
            except Exception as e:
                print(f"Error getting shoulder_angle: {e}")
        
        except Exception as e:
            print(f"Error in _drawKeyframeMaxShoulderER: {e}")
        
        return img

    def _drawKeyframeRelease(self, img: np.ndarray, frame_num: int, start_frame: int = 1) -> np.ndarray:
        """
        繪製第三個關鍵幀的信息：最大手腕速度
        """
        self.keyframe_wrist_speed = None
        self.keyframe_extension_distance = None
        try:
            keypoints = self.pose_estimater.getPersonDf(frame_num=frame_num, is_select=True, is_kpt=True)
            
            try:
                # 繪製手腕軌跡（索引 10）
                img = self._draw_keypoint_trajectory(img, frame_num, kpt_index=10, start_frame=start_frame)
            except Exception as e:
                print(f"Error drawing wrist trajectory: {e}")

            # 獲取手腕速度
            try:
                _, wrist_speed = self.pose_analyzer.get_frame_angleOrSpeed_data(
                    frame_num, speed_name="wrist_speed"
                )
                if wrist_speed is not None and keypoints is not None:
                    # 確保轉換為數字
                    if isinstance(wrist_speed, (list, tuple, np.ndarray)):
                        wrist_speed = float(wrist_speed[0])
                    else:
                        wrist_speed = float(wrist_speed)
                    self.keyframe_wrist_speed = wrist_speed
                    img = self.draw_styled_metric(img, keypoints[10], "手腕速度", f"{wrist_speed:.1f}", "m/s", direction='left')
            except Exception as e:
                print(f"Error getting wrist_speed: {e}")

            # 獲取出手點距離
            try:
                extension_distance = self._calculate_extension_distance(frame_num)
                if extension_distance is not None and keypoints is not None:
                    base_x = self._get_baseline_right_ankle_x()
                    if base_x is not None:
                        pts = np.array([
                            (int(base_x), int(keypoints[15][1]) - 800),
                            (int(base_x), int(keypoints[15][1]) - 830),
                            (int(keypoints[10][0]), int(keypoints[15][1]) - 830),
                            (int(keypoints[10][0]), int(keypoints[15][1]) - 800)
                        ], dtype=np.int32).reshape((-1, 1, 2))
                        cv2.polylines(img, [pts], False, (12, 170, 200), 5, cv2.LINE_AA)
                    img, knee_angle = self.draw_extended_angle(img, keypoints[15], keypoints[13], keypoints[11], offset=80, return_angle=True)
                    img = self.draw_styled_metric(
                        img, keypoints[10], "初始腳跟至手腕3D距離" if getattr(self.pose_analyzer.viewer3D, 'metric', False) else "出手點距離", f"{extension_distance*100:.1f}", "cm",
                        direction='right', value_color=(255, 255, 0), label_color=(255, 255, 0)
                    )    
                    self.keyframe_extension_distance = float(extension_distance)        
                    if knee_angle is not None:
                        img = self.draw_styled_metric(img, keypoints[13], "膝彎曲", f"{knee_angle}", "°", direction='right', offset_y=100)
            except Exception as e:
                print(f"Error calculating extension distance: {e}")
        
        except Exception as e:
            print(f"Error in _drawKeyframeRelease: {e}")
        
        return img
    
    def _draw_keypoint_trajectory(self, img: np.ndarray, frame_num: int, kpt_index: int, start_frame: int = 1) -> np.ndarray:
        """
        繪製任意關節在指定時間段的軌跡
        
        Args:
            img: 圖像
            frame_num: 當前幀號（結束幀）
            kpt_index: 關節索引
            start_frame: 起始幀號（預設為1）
            
        Returns:
            繪製軌跡後的圖像
        """
        try:
            max_jump_distance = 200.0
            min_conf = 0.3

            # 收集關節的所有位置點（從 start_frame 到 frame_num）
            kpt_points = []
            
            for frame in range(start_frame, frame_num + 1):
                keypoints = self.pose_estimater.getPersonDf(frame_num=frame, is_select=True, is_kpt=True)
                if keypoints is not None and len(keypoints) > kpt_index:
                    kpt = keypoints[kpt_index]
                    if kpt is None or len(kpt) < 2:
                        continue
                    if len(kpt) >= 3 and float(kpt[2]) < min_conf:
                        continue
                    x, y = float(kpt[0]), float(kpt[1])
                    if x != 0 and y != 0 and np.isfinite(x) and np.isfinite(y):
                        kpt_points.append((x, y))
            
            if len(kpt_points) < 2:
                return img

            pts = np.array(kpt_points, dtype=np.float32)

            # 異常大位移改為以前2幀與後2幀做局部插值
            # 條件：當前點與前一點距離過大，且有足夠上下文（i-2, i-1, i+1, i+2）
            for i in range(2, len(pts) - 2):
                jump_dist = np.hypot(pts[i, 0] - pts[i - 1, 0], pts[i, 1] - pts[i - 1, 1])
                if jump_dist > max_jump_distance:
                    prev_mean = (pts[i - 2] + pts[i - 1]) * 0.5
                    next_mean = (pts[i + 1] + pts[i + 2]) * 0.5
                    pts[i] = (prev_mean + next_mean) * 0.5

            # 時域平滑：點數足夠時才做 Savitzky-Golay
            if len(pts) >= 5:
                win = min(7, len(pts) if len(pts) % 2 == 1 else len(pts) - 1)
                if win >= 5:
                    pts[:, 0] = savgol_filter(pts[:, 0], window_length=win, polyorder=2)
                    pts[:, 1] = savgol_filter(pts[:, 1], window_length=win, polyorder=2)

            pts = np.round(pts).astype(np.int32)
            for (x1, y1), (x2, y2) in zip(pts[:-1], pts[1:]):
                cv2.line(img, (x1, y1), (x2, y2), (0, 255, 0), 5, cv2.LINE_AA)
            
            return img
        except Exception as e:
            print(f"Error in _draw_keypoint_trajectory: {e}")
            return img

    def _get_baseline_right_ankle_x(self) -> float:
        """
        獲取第1幀右腳踝的x位置作為基準點
        
        Returns:
            float: x位置（像素），若無法計算則返回 None
        """
        try:
            if getattr(self.pose_analyzer.viewer3D, 'metric', False):
                return None  # A pixel horizontal bar is not a metric ground line.
            keypoints = self.pose_estimater.getPersonDf(frame_num=1, is_select=True, is_kpt=True)
            if keypoints is None or len(keypoints) < 17:
                return None
            
            # 右腳踝索引 16
            right_ankle = keypoints[16][:2]  # 取 (x, y)
            if right_ankle is None:
                return None
            
            return float(right_ankle[0])  # 返回x座標
        except Exception as e:
            print(f"Error getting baseline right ankle x: {e}")
            return None

    def _calculate_stride_distance(self, frame_num: int) -> float:
        """
        計算跨步距離（左右腳踝在 X 方向的距離）
        
        Returns:
            float: 距離（單位：米），若無法計算則返回 None
        """
        try:
            if self.pose_analyzer is None or not hasattr(self.pose_analyzer, 'viewer3D'):
                return None
            
            viewer3d = self.pose_analyzer.viewer3D
            if not hasattr(viewer3d, 'all_3d_frames') or frame_num not in viewer3d.all_3d_frames:
                return None
            
            pts = viewer3d.all_3d_frames[frame_num]
            if pts is None or len(pts) < 17:  # 需要至少17個關鍵點
                return None
            
            # 左腳踝索引 15，右腳踝索引 16
            if getattr(viewer3d, 'metric', False):
                return viewer3d.displacement(frame_num)
            left_ankle = pts[15]
            right_ankle = viewer3d.all_3d_frames[2][16]
            
            if left_ankle is None or right_ankle is None:
                return None
            
            # 只取 X 方向差值，並套用同樣的比例尺
            distance = abs(float(left_ankle[0]) - float(right_ankle[0])) * 21.556
            return distance
        except Exception as e:
            print(f"Error calculating stride distance: {e}")
            return None
        
    def _calculate_extension_distance(self, frame_num: int) -> float:
        """
        計算伸展距離（X 方向距離）
        
        Returns:
            float: 距離（單位：米），若無法計算則返回 None
        """
        try:
            if self.pose_analyzer is None or not hasattr(self.pose_analyzer, 'viewer3D'):
                return None
            
            viewer3d = self.pose_analyzer.viewer3D
            if not hasattr(viewer3d, 'all_3d_frames') or frame_num not in viewer3d.all_3d_frames:
                return None
            
            pts = viewer3d.all_3d_frames[frame_num]
            if pts is None or len(pts) < 17:  # 需要至少17個關鍵點
                return None
            
            # 左手腕索引 9，右手腕索引 10
            if getattr(viewer3d, 'metric', False):
                return viewer3d.displacement(frame_num, target_joint=10)
            right_wrist = pts[10]
            right_ankle = viewer3d.all_3d_frames[2][16]
            
            if right_wrist is None or right_ankle is None:
                return None
            
            # 只取 x 方向的距離（投手伸展長度），並套用同樣的比例尺
            distance_x = abs(float(right_wrist[0]) - float(right_ankle[0])) * 21.556
            return distance_x
        except Exception as e:
            print(f"Error calculating extension distance: {e}")
            return None

    def drawCountdown(self, img:np.ndarray,countdown_time:int):
        height, width, _ = img.shape
        text = str(countdown_time) 
        
        # 設置文字的大小、顏色與位置
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 10
        color = (0, 0, 255)  # 紅色
        thickness = 15

        # 計算文字的邊界框來居中顯示
        text_size = cv2.getTextSize(text, font, font_scale, thickness)[0]
        # text_x = (width - text_size[0]) // 2
        # text_y = (height + text_size[1]) // 2
        text_x = text_size[0] // 2
        text_y = text_size[1] + 10
        # 在透明圖層上繪製文字
        cv2.putText(img, text, (text_x,text_y), font, font_scale, color, thickness, cv2.LINE_AA)
        if countdown_time == 0:
            self.show_countdown = False

        return img


    def setShowBbox(self, status:bool):
        self.show_bbox = status
    
    def setShowSkeleton(self, status:bool):
        self.show_skeleton = status
    
    def setShowGrid(self, status:bool):
        self.show_grid = status
        
    def setShowRegion(self, status:bool):
        self.show_region = status

    def setShowTraj(self, status:bool):
        self.show_traj = status

    def setShowAngleInfo(self, status:bool):
        self.show_angle_info = status
        if status:
            self.setAngleInfoPos()
    
    def setShowCountdown(self,status:bool):
        self.show_countdown = status
        
    def setAngleInfoPos(self):
        person_df = self.pose_estimater.getPersonDf(is_select=True)
        if person_df is None:
            return
        self.angle_info_pos = person_df.iloc[0]['keypoints'][19]
        self.angle_info_pos = tuple(map(int,self.angle_info_pos))        

    def reset(self):
        self.show_grid = False
        self.show_bbox = False
        self.show_skeleton = False
        self.show_traj = False
        self.show_angle_info = False
        self.angle_info_pos = (0,0)
        self.keyframe_shoulder_hip_angle = None
        self.keyframe_stride_distance = None
        self.keyframe_shoulder_angle = None
        self.keyframe_wrist_speed = None
        self.keyframe_extension_distance = None
        
        # 清空可能儲存的軌跡和數據
        if hasattr(self, 'trajectory_points'):
            if isinstance(self.trajectory_points, dict):
                self.trajectory_points.clear()
            self.trajectory_points = {}
        
        # 清空其他可能的緩存
        if hasattr(self, 'cached_frames'):
            if isinstance(self.cached_frames, list):
                self.cached_frames.clear()
            self.cached_frames = []
