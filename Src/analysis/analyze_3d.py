import numpy as np
import pandas as pd
from Src.pose.detect_skeleton import PoseEstimater
from Src.UI_Control.viewers.triangulate_3d_viewer import Triangulate3DViewer

class PoseAnalyzer:
    def __init__(self, pose_estimater:PoseEstimater, K_L, K_R, F):
        self.pose_estimater = pose_estimater
        self.viewer3D = Triangulate3DViewer(K_L, K_R, F)
        self.angle_dict = self.pose_estimater.joints['haple']['angle_dict']
        self.analyze_info = []
        self.analyze_df = pd.DataFrame()
        self.processed_frames = set()
        # 添加查询缓存，避免重复 DataFrame 查找
        self._frame_data_cache = {}
        self._speed_cache = {}

    def addAnalyzeInfo(self, frame_num: int, frame_L, frame_R):
        """Analyze information for each frame up to the current frame."""
        # if self.pose_estimater.person_id is None:
        #     return pd.DataFrame()
        
        person_kpt = self.pose_estimater.getPersonDf(frame_num= frame_num, is_select= True,is_kpt=True)
        
        if person_kpt is None:
            return
        self.viewer3D.add_2d_keypoints(frame_L, frame_R, frame_num= frame_num)
        
        # 計算 3D 左膝屈曲角（髖 → 膝 → 腳踝）
        left_knee_angle_3d = self.get_3d_knee_angle(frame_num, hip_idx=11, knee_idx=13, ankle_idx=15)
        
        info = {
            'frame_number': frame_num,
            'angle': self._update_analyze_information(person_kpt),
            'left_knee_3d': left_knee_angle_3d,
            'ankle_speed': self.viewer3D.compute_joint_velocity_causal(15, 0.014, frame_num, 0.7, 3),
            'ankle_speed_2d': self._calculate_2d_joint_velocity(15, 0.014, frame_num),
            'ankle_x_diff': self.viewer3D.compute_ankle_x_diff(frame_num, left_idx=15, right_idx=16, absolute=False),
            'shoulder_angle': self.viewer3D.arm_cocking_angle(frame_num),
            'wrist_speed': self.viewer3D.compute_joint_velocity_causal(10, 0.014, frame_num, 0.7, 3),
            'wrist_speed_2d': self._calculate_2d_joint_velocity(10, 0.014, frame_num),
            'shoulder_hiple_angle': self.viewer3D.calculate_angle_between_shoulder_hip_lines_signed(frame_num)
        }

        if frame_num not in self.processed_frames:
            self.processed_frames.add(frame_num)
            self.analyze_info.append(info)
            
            # 性能優化：使用 concat 而非重建整個 DataFrame
            if self.analyze_df.empty:
                self.analyze_df = pd.DataFrame([info])
            else:
                new_row = pd.DataFrame([info])
                self.analyze_df = pd.concat([self.analyze_df, new_row], ignore_index=True)
                # 只在需要時排序（每10幀或查詢時）
                if len(self.analyze_df) % 10 == 0:
                    self.analyze_df = self.analyze_df.sort_values(by='frame_number').reset_index(drop=True)
            
            # 清除缓存，因為 DataFrame 已更新
            self._frame_data_cache.pop(frame_num, None)
            self._speed_cache.pop(frame_num, None)

    def _calculate_2d_joint_velocity(self, joint_idx: int, dt: float = 0.014, frame_num: int = None):
        """計算特定關節的 2D 速度（像素/秒）。
        
        從 person_df 中查詢當前幀和前一幀的關鍵點數據，計算2D速度。
        
        參數:
            joint_idx: 關節索引 (0-25)
            dt: 時間間隔（秒，預設 0.014 對應 69fps）
            frame_num: 當前幀號
        
        回傳:
            float: 2D速度（像素/秒），若資料缺失回傳 NaN
        """
        if frame_num is None or self.pose_estimater.person_df.empty:
            return float('nan')
        
        try:
            # 查詢當前幀的數據
            curr_data = self.pose_estimater.person_df[
                (self.pose_estimater.person_df['frame_number'] == frame_num) &
                (self.pose_estimater.person_df['person_id'] == self.pose_estimater.person_id)
            ]
            
            if curr_data.empty:
                return float('nan')
            
            kpts_current = curr_data.iloc[0]['keypoints']
            if kpts_current is None or len(kpts_current) <= joint_idx:
                return float('nan')
            
            x_current, y_current, conf_current = kpts_current[joint_idx][:3]
            
            # 如果信心度過低，返回 NaN
            if conf_current < 0.3:
                return float('nan')
            
            # 查詢上一幀的數據
            prev_frame_num = frame_num - 1
            prev_data = self.pose_estimater.person_df[
                (self.pose_estimater.person_df['frame_number'] == prev_frame_num) &
                (self.pose_estimater.person_df['person_id'] == self.pose_estimater.person_id)
            ]
            
            if prev_data.empty:
                return float('nan')
            
            kpts_prev = prev_data.iloc[0]['keypoints']
            if kpts_prev is None or len(kpts_prev) <= joint_idx:
                return float('nan')
            
            x_prev, y_prev, conf_prev = kpts_prev[joint_idx][:3]
            
            # 如果信心度過低，返回 NaN
            if conf_prev < 0.3:
                return float('nan')
            
            # 計算像素距離
            pixel_distance = np.sqrt((x_current - x_prev) ** 2 + (y_current - y_prev) ** 2)
            
            # 轉換為每秒像素數（速度）
            velocity_2d = pixel_distance / dt
            
            return float(velocity_2d)
        
        except (KeyError, IndexError, AttributeError, TypeError):
            return float('nan')

    def _calculate_angle(self, A, B, C):
        """Calculate the angle between three points A, B, and C."""
        BA = np.array(A) - np.array(B)
        BC = np.array(C) - np.array(B)
        dot_product = np.dot(BA, BC)
        magnitude_BA = np.linalg.norm(BA)
        magnitude_BC = np.linalg.norm(BC)
        mag = magnitude_BA * magnitude_BC
        if mag == 0 or np.isnan(mag):
            return float('nan')
        cos_angle = dot_product / mag
        angle_rad = np.arccos(np.clip(cos_angle, -1.0, 1.0))
        return np.degrees(angle_rad)

    def _calculate_angle_3d(self, A, B, C):
        """Calculate the 3D angle at point B formed by points A-B-C (degrees).
        
        A, B, C are 3D points (x,y,z). Returns NaN when vectors are degenerate.
        """
        A = np.asarray(A, dtype=float)
        B = np.asarray(B, dtype=float)
        C = np.asarray(C, dtype=float)
        
        BA = A - B
        BC = C - B
        mag = np.linalg.norm(BA) * np.linalg.norm(BC)
        if mag == 0 or np.isnan(mag):
            return float('nan')
        cos_angle = np.dot(BA, BC) / mag
        angle_rad = np.arccos(np.clip(cos_angle, -1.0, 1.0))
        return int(np.degrees(angle_rad))

    def get_3d_knee_angle(self, frame_num: int, hip_idx: int = 11, knee_idx: int = 13, ankle_idx: int = 15):
        """
        計算特定幀的膝蓋屈曲角（3D）。
        
        參數:
            frame_num: frame number (1-based)
            hip_idx: 髖關節索引 (預設 11)
            knee_idx: 膝蓋索引 (預設 13)
            ankle_idx: 腳踝索引 (預設 15)
        
        回傳:
            float: 角度（度），若資料缺失回傳 NaN
        """     
        if not hasattr(self.viewer3D, 'all_3d_frames') or not self.viewer3D.all_3d_frames:
            return float('nan')
        
        if frame_num not in self.viewer3D.all_3d_frames:
            return 0.0  # 如果該幀沒有 3D 資料，直接回傳 0，避免崩潰
        
        pts = self.viewer3D.all_3d_frames[frame_num]
        if pts is None or max(hip_idx, knee_idx, ankle_idx) >= len(pts):
            return float('nan')
        
        hip_pt = pts[hip_idx]
        knee_pt = pts[knee_idx]
        ankle_pt = pts[ankle_idx]
        
        return self._calculate_angle_3d(hip_pt, knee_pt, ankle_pt)

    def _update_analyze_information(self, person_kpt):
        """Update and return analyze information for the given keypoints."""
        info = {}
        for angle_name, kpt_list in self.angle_dict.items():
            A = person_kpt[kpt_list[0]][:2]
            B = person_kpt[kpt_list[1]][:2]
            C = person_kpt[kpt_list[2]][:2]
            info[angle_name] = [self._calculate_angle(A, B, C), [np.array(A), np.array(B), np.array(C)]]

            if angle_name == "右腋窩":
                A = person_kpt[kpt_list[0]][:2]
                B = person_kpt[kpt_list[1]][:2]
                C = [person_kpt[kpt_list[1]][0], person_kpt[kpt_list[2]][1]]
                info[angle_name] = [self._calculate_angle(A, B, C), [np.array(A), np.array(B), np.array(C)]]
        return info

    def get_frame_angleOrSpeed_data(self, frame_num: int = None, angle_name: str = None, speed_name: str = None):
        if self.analyze_df.empty:
            return pd.DataFrame(), []
        
        # 如果指定了 frame_num，先查询缓存
        if frame_num is not None:
            cache_key = (frame_num, angle_name, speed_name)
            if cache_key in self._frame_data_cache:
                return self._frame_data_cache[cache_key]
        
        condition = pd.Series([True] * len(self.analyze_df))

        # 根據 frame_num 過濾數據
        if frame_num is not None:
            condition &= (self.analyze_df['frame_number'] == frame_num)

        data = self.analyze_df.loc[condition]
        
        # 如果數據為空則返回
        if data.empty:
            return None, []

        result = None
        if angle_name is not None:
            # 如果指定了 angle_name 且有特定幀數，返回該幀的角度數據
            if frame_num is not None:
                angle_value = data['angle'].iloc[0][angle_name]
                result = (data, angle_value)
            else:
                # 如果未指定 frame_num，則返回該 angle_name 的所有幀數及對應角度
                frame_numbers = self.analyze_df['frame_number'].unique()
                angles = [row['angle'][angle_name][0] for _, row in self.analyze_df.iterrows() if angle_name in row['angle']]
                result = (frame_numbers, angles)
            
        if speed_name is not None:
            if frame_num is not None:
                speed_value = data[speed_name].iloc[0]
                if isinstance(speed_value, np.ndarray):
                    if speed_value.size == 1:
                        speed_value = float(speed_value)
                    else:
                        speed_value = speed_value.tolist()
                result = (data, speed_value)
            else:
                frame_numbers = self.analyze_df['frame_number'].unique()
                speeds = []
                for val in self.analyze_df[speed_name]:
                    if isinstance(val, np.ndarray):
                        if val.size == 1:
                            val = float(val)
                        else:
                            val = val.tolist()
                    speeds.append(val)
                result = (frame_numbers, speeds)
        
        if result is None:
            result = (data, [])
        
        # 緩存結果（僅當指定 frame_num 時）
        if frame_num is not None and angle_name is not None or speed_name is not None:
            cache_key = (frame_num, angle_name, speed_name)
            self._frame_data_cache[cache_key] = result
        
        return result

    def find_first_matching_frame(self, frames: list, ankle_speed_max: float = 0.8, 
                                  knee_angle_min: float = 40, knee_angle_max: float = 50, sh_angle_min: float = 25):
        """
        找出第一個同時滿足條件的幀：
        1. ankle_speed <= ankle_speed_max (預設 0.8)
        2. left_knee_3d 角度在 [knee_angle_min, knee_angle_max] 之間 (使用 3D 計算，預設 40-50 度)

        參數:
            frames: list of frame numbers (對應 analyze_df 的 frame_number)
            ankle_speed_max: 腳踝速度上限
            knee_angle_min: 膝蓋角度下限 (度)
            knee_angle_max: 膝蓋角度上限 (度)

        回傳:
            int: 符合條件的第一個幀號，若無符合則回傳 None
        """
        if self.analyze_df.empty or not frames:
            return None

        for frame_num in frames:
            # 尋找該幀在 analyze_df 中的資料
            row = self.analyze_df[self.analyze_df['frame_number'] == frame_num]
            if row.empty:
                continue

            # 取得 ankle_speed 和 3D left_knee 角度
            ankle_speed = row['ankle_speed'].iloc[0]
            left_knee_3d = row['left_knee_3d'].iloc[0]
            sh_angle = row['shoulder_hiple_angle'].iloc[0]

            # 判斷 ankle_speed（可能是 ndarray 或 float）
            if isinstance(ankle_speed, np.ndarray):
                ankle_speed = float(ankle_speed) if ankle_speed.size == 1 else float(ankle_speed[0])
            ankle_speed = float(ankle_speed) if not pd.isna(ankle_speed) else np.inf

            # 處理 3D 膝蓋角度
            knee_angle = None
            if isinstance(left_knee_3d, (int, float, np.integer, np.floating)):
                knee_angle = float(left_knee_3d) if not (isinstance(left_knee_3d, float) and np.isnan(left_knee_3d)) else None
            elif isinstance(left_knee_3d, np.ndarray):
                knee_angle = float(left_knee_3d) if left_knee_3d.size == 1 else None

            # 處理 肩髖角度
            sh_angle_ = None
            if isinstance(sh_angle, (int, float, np.integer, np.floating)):
                sh_angle_ = float(sh_angle) if not (isinstance(sh_angle, float) and np.isnan(sh_angle)) else None
            elif isinstance(sh_angle, np.ndarray):
                sh_angle_ = float(sh_angle) if sh_angle.size == 1 else None
            else:
                # 調試：檢查實際類型
                print(f"[DEBUG Frame {frame_num}] sh_angle type: {type(sh_angle)}, value: {sh_angle}")

            # 除錯：列印每一幀的 ankle_speed 和 knee_angle
            # print(f"Frame {frame_num}: ankle_speed={ankle_speed:.4f}, knee_angle_3d={knee_angle}")

            # 檢查是否符合條件
            if (ankle_speed <= ankle_speed_max and 
                knee_angle is not None and 
                knee_angle_min <= knee_angle <= knee_angle_max and sh_angle_ is not None and sh_angle_ >= sh_angle_min):
                print(f"✓ Frame {frame_num} MATCHED!")
                return int(frame_num)
            
        return None

    def find_foot_landing_frame(self, frames: list, high_speed_threshold: float = 0.5, 
                                low_speed_threshold: float = 0.15, 
                                knee_extension_threshold: float = 150):
        """
        找出投手抬腳後落地的關鍵幀 - 綜合判斷速度、膝蓋角度和膝蓋高度變化
        
        定義：綜合檢測多個指標的變化過程：
        1. 抬腳階段：膝蓋角度減小（彎曲）且膝蓋高度上升
        2. 落地階段：膝蓋角度增大（伸展）+ 膝蓋高度下降 + 腳踝速度較低
        
        返回的是落地時（膝蓋完全伸展）的幀號。
        
        參數:
            frames: list of frame numbers (對應 analyze_df 的 frame_number)
            high_speed_threshold: 抬腳時腳踝速度上限，預設 0.5 m/s (因為可能停頓)
            low_speed_threshold: 落地時腳踝速度上限，預設 0.15 m/s
            knee_extension_threshold: 膝蓋伸展角度閾值，預設 150°（接近伸直）
        
        回傳:
            int: 落地時（膝蓋伸展、膝蓋高度下降）的幀號，若無符合則回傳 None
        """
        if self.analyze_df.empty or not frames:
            return None

        # 記錄過程中的關鍵數據
        min_knee_angle = float('inf')  # 抬腳時的最小膝蓋角度（膝蓋最彎曲）
        min_knee_frame = None
        max_knee_height = -float('inf')  # 膝蓋最高的高度
        max_knee_height_frame = None
        is_lifting = False
        landing_detected = False
        
        for i, frame_num in enumerate(frames):
            # 尋找該幀在 analyze_df 中的資料
            row = self.analyze_df[self.analyze_df['frame_number'] == frame_num]
            if row.empty:
                continue

            # 取得數據
            ankle_speed = row['ankle_speed'].iloc[0]
            left_knee_3d = row['left_knee_3d'].iloc[0]
            
            # 處理 ankle_speed
            if isinstance(ankle_speed, np.ndarray):
                ankle_speed = float(ankle_speed) if ankle_speed.size == 1 else float(ankle_speed[0])
            ankle_speed = float(ankle_speed) if not pd.isna(ankle_speed) else np.inf

            # 處理 knee_angle
            knee_angle = None
            if isinstance(left_knee_3d, (int, float, np.integer, np.floating)):
                knee_angle = float(left_knee_3d) if not (isinstance(left_knee_3d, float) and np.isnan(left_knee_3d)) else None
            elif isinstance(left_knee_3d, np.ndarray):
                knee_angle = float(left_knee_3d) if left_knee_3d.size == 1 else None

            if knee_angle is None:
                continue

            # 取得膝蓋高度（3D座標的Y值，負值表示更高）
            if not hasattr(self.viewer3D, 'all_3d_frames') or frame_num not in self.viewer3D.all_3d_frames:
                continue
            
            pts = self.viewer3D.all_3d_frames[frame_num]
            if pts is None or len(pts) < 14:  # knee_idx = 13
                continue
            
            knee_height = pts[13][1]  # Y坐標表示高度（在相機坐標系中）

            # 第一步：檢測抬腳開始（膝蓋角度減小，膝蓋高度上升）
            if not is_lifting:
                if knee_angle < 130:  # 膝蓋開始彎曲
                    is_lifting = True
                    min_knee_angle = knee_angle
                    min_knee_frame = frame_num
                    max_knee_height = knee_height
                    max_knee_height_frame = frame_num
                    print(f"  [抬腳開始] Frame {frame_num}: knee_angle={knee_angle:.1f}°, knee_height={knee_height:.2f}")
                continue

            # 第二步：在抬腳期間持續追蹤膝蓋的最小角度和最高位置
            if is_lifting and not landing_detected:
                if knee_angle < min_knee_angle:
                    min_knee_angle = knee_angle
                    min_knee_frame = frame_num

                if knee_height > max_knee_height:
                    max_knee_height = knee_height
                    max_knee_height_frame = frame_num

                # 第三步：檢測落地（膝蓋伸展 + 膝蓋高度下降 + 腳踝速度降低）
                # 落地的標誌：膝蓋從彎曲恢復到伸直狀態
                if (knee_angle >= knee_extension_threshold and 
                    knee_height < max_knee_height - 0.05 and  # 膝蓋高度明顯下降
                    ankle_speed <= low_speed_threshold):
                    
                    landing_detected = True
                    speed_recovery = f"(speed={ankle_speed:.4f} m/s)"
                    print(f"✓ 投手落地幀 {frame_num}: knee_angle={knee_angle:.1f}° {speed_recovery}")
                    print(f"  抬腳幀: {min_knee_frame} (min_knee_angle={min_knee_angle:.1f}°)")
                    print(f"  最高點: {max_knee_height_frame} (max_knee_height={max_knee_height:.2f})")
                    print(f"  膝蓋高度下降: {(max_knee_height - knee_height):.4f} m")
                    return int(frame_num)
            
        return None

    def reset(self):
        self.viewer3D.reset()
        self.analyze_info = []
        self.analyze_df = pd.DataFrame()
        self.processed_frames = set()
        self._frame_data_cache.clear()
        self._speed_cache.clear()
        # print("reset pose analyzer")

import time
class JointAreaChecker:
    def __init__(self, image_size:tuple, stabillity_threshold:int):
        self.image_width = image_size[0]
        self.image_height = image_size[1]

        # 設置1/5區域的邊界
        self.region_width = image_size[0]
        self.region_height = image_size[1] // 5

        # 區域左上角的坐標
        self.region_top_left = (0, 0)
        self.region_bottom_right = (self.region_width, self.region_height)

        self.last_joint_position = []
        self.stabillity_threshold = stabillity_threshold
        self.stable_start_time = []

    def is_joint_in_area(self, joint_position:tuple)->bool:
        """檢查關節點是否在定義的區域內"""
        if joint_position is None:
            return False
        x, y,_,_ = joint_position
        
        in_area = (self.region_top_left[0] <= x <= self.region_bottom_right[0] and
                   self.region_top_left[1] <= y <= self.region_bottom_right[1])
        return in_area
    
    def is_ready_for_pitching(self,joint_position:tuple):
        """檢查膝蓋關節是否高於臀部關節，進入投球準備動作。"""
        if joint_position is None:
            return False
        _,y1,_,y2=joint_position
        if y1 < y2:
            return True
        else: return False

    def is_stable(self, joint_position:tuple, duration:int):
        """檢查關節點是否在區域內並且穩定"""
        if joint_position is None:
            return False
        x1, y1, x2, y2 = joint_position
        if self.last_joint_position:
            x1_last, y1_last, x2_last, y2_last = self.last_joint_position
            distance = np.sqrt((x1 - x1_last) ** 2 + (y1 - y1_last) ** 2)
            print(distance)
            if distance <= self.stabillity_threshold:
                if not self.stable_start_time:
                    self.stable_start_time.append(time.time()) 
                    print(self.stable_start_time)
                elapsed_time = self.stable_start_time[-1] - self.stable_start_time[0]
                if elapsed_time >= duration:
                    return True
            else:
                self.stable_start_time = []
        else:
            self.last_joint_position = joint_position
            
        self.last_joint_position = joint_position
        return False