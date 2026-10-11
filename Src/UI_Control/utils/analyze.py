import numpy as np
import pandas as pd
from skeleton.detect_skeleton import PoseEstimater

class PoseAnalyzer:
    def __init__(self, pose_estimater:PoseEstimater, fps: int = 30):
        self.pose_estimater = pose_estimater
        self.fps = fps
        self.angle_dict = self.pose_estimater.joints['haple']['angle_dict']
        self.analyze_info = []
        self.analyze_df = pd.DataFrame()
        self.processed_frames = set()

    def addAnalyzeInfo(self, frame_num: int):
        """Analyze information for each frame up to the current frame."""    
        person_kpt = self.pose_estimater.getPersonDf(frame_num= frame_num, is_select= True,is_kpt=True)
        
        if person_kpt is None:
            return
        info = {
            'frame_number': frame_num,
            'angle': self._update_analyze_information(person_kpt),
            'elbow_angle_2d': self._calculate_2d_angle_velocity(6, 8, 10),
            # 'ankle_speed_2d': self._calculate_2d_joint_velocity(24, frame_num), # 左腳跟
            'ankle_speed_2d': self._calculate_2d_joint_velocity(15, frame_num),
            'wrist_speed_2d': self._calculate_2d_joint_velocity(10, frame_num),
            'left_ankle_landed': self.is_left_ankle_landed(frame_num=frame_num)
        }

        if frame_num not in self.processed_frames:
            self.processed_frames.add(frame_num)
            self.analyze_info.append(info)
            new_row = pd.DataFrame([info])
            if self.analyze_df.empty:
                self.analyze_df = new_row
            else:
                self.analyze_df = pd.concat([self.analyze_df, new_row], ignore_index=True)

    def _calculate_angle(self, A, B, C):
        """Calculate the angle between three points A, B, and C."""
        BA = np.array(A) - np.array(B)
        BC = np.array(C) - np.array(B)
        dot_product = np.dot(BA, BC)
        magnitude_BA = np.linalg.norm(BA)
        magnitude_BC = np.linalg.norm(BC)
        
        # 防止除以零：檢查向量的長度是否為零
        if magnitude_BA == 0 or magnitude_BC == 0:
            return 0.0
        
        cos_angle = dot_product / (magnitude_BA * magnitude_BC)
        angle_rad = np.arccos(np.clip(cos_angle, -1.0, 1.0))
        return np.degrees(angle_rad)

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
            if angle_name == "右肩外旋":
                A = person_kpt[kpt_list[0]][:2]
                B = person_kpt[kpt_list[1]][:2]
                C = [person_kpt[kpt_list[1]][0] + 100, person_kpt[kpt_list[2]][1]]
                info[angle_name] = [self._calculate_angle(A, B, C), [np.array(A), np.array(B), np.array(C)]]    
        return info
    
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
    
    def _calculate_2d_joint_velocity(self, joint_idx: int, frame_num: int = None):
        """計算特定關節的 2D 速度（像素/秒）。
        
        從 person_df 中查詢當前幀和前一幀的關鍵點數據，計算2D速度。
        
        參數:
            joint_idx: 關節索引 (0-25)
            frame_num: 當前幀號
        
        回傳:
            float: 2D速度（像素/秒），若資料缺失回傳 NaN
        """
        if frame_num is None or self.pose_estimater.person_df.empty:
            return float('nan')
        
        try:
            # 🚩 使用 getPersonDf 獲取資料，預設會使用 smoothed_keypoints (平滑後的點)
            curr_data = self.pose_estimater.getPersonDf(frame_num=frame_num, is_select=True)
            
            if curr_data.empty:
                return float('nan')
            
            kpts_current = curr_data.iloc[0]['keypoints']
            if kpts_current is None or len(kpts_current) <= joint_idx:
                return float('nan')
            
            x_current, y_current, conf_current = kpts_current[joint_idx][:3]
            
            # 如果信心度過低，返回 NaN
            if conf_current < 0.5:
                return float('nan')
            
            # 查詢上一幀的數據
            prev_frame_num = frame_num - 1
            # 🚩 同樣使用 getPersonDf 確保速度計算基於平滑後的連續軌跡
            prev_data = self.pose_estimater.getPersonDf(frame_num=prev_frame_num, is_select=True)
            
            if prev_data.empty:
                return float('nan')
            
            kpts_prev = prev_data.iloc[0]['keypoints']
            if kpts_prev is None or len(kpts_prev) <= joint_idx:
                return float('nan')
            
            x_prev, y_prev, conf_prev = kpts_prev[joint_idx][:3]
            
            # 如果信心度過低，返回 NaN
            if conf_prev < 0.5:
                return float('nan')
            
            # 計算像素距離
            pixel_distance = np.sqrt((x_current - x_prev) ** 2 + (y_current - y_prev) ** 2)
            
            # 過濾異常大的距離變化（可能是檢測不穩定）
            # 假設解析度 1920x1084，一幀內不太可能移動超過 400 像素
            max_reasonable_distance = 400
            if pixel_distance > max_reasonable_distance:
                return float('nan')
            
            # 轉換為每秒像素數（速度）
            fps = self.fps if self.fps and self.fps > 0 else 60
            dt = 1 / fps
            # velocity_2d = pixel_distance *0.278 / dt # 6mm鏡頭
            # velocity_2d = pixel_distance *0.36 / dt # 8mm鏡頭
            velocity_2d = pixel_distance *0.224 / dt # 8mm鏡頭
            
            return float(velocity_2d)
        
        except (KeyError, IndexError, AttributeError, TypeError):
            return float('nan')

    def _calculate_2d_angle_velocity(self, joint_idx1: int, joint_idx2: int, joint_idx3: int, frame_num: int = None):
        """計算由三個關節形成的角度在 2D 平面上的變化率（度/秒）。
        
        從 person_df 中查詢當前幀和前一幀的關鍵點數據，計算角速度。
        
        參數:
            joint_idx1, joint_idx2, joint_idx3: 三個關節索引 (0-25)
            frame_num: 當前幀號
        """
        if frame_num is None or self.pose_estimater.person_df.empty:
            return float('nan')

        try:
            # 🚩 使用 getPersonDf 獲取資料，預設會使用 smoothed_keypoints (平滑後的點)
            curr_data = self.pose_estimater.getPersonDf(frame_num=frame_num, is_select=True)

            if curr_data.empty:
                return float('nan')

            kpts_current = curr_data.iloc[0]['keypoints']
            if kpts_current is None or len(kpts_current) <= max(joint_idx1, joint_idx2, joint_idx3):
                return float('nan')

            # 獲取三個關節的座標
            x1_current = kpts_current[joint_idx1][:2]
            x2_current = kpts_current[joint_idx2][:2]
            x3_current = kpts_current[joint_idx3][:2]

            # 計算角度變化率
            angle_diff = self._calculate_angle(x1_current, x2_current, x3_current)
            if angle_diff is None:
                return float('nan')

            # 查詢上一幀的數據
            prev_frame_num = frame_num - 1
            prev_data = self.pose_estimater.getPersonDf(frame_num=prev_frame_num, is_select=True)

            if prev_data.empty:
                return float('nan')

            kpts_prev = prev_data.iloc[0]['keypoints']
            if kpts_prev is None or len(kpts_prev) <= max(joint_idx1, joint_idx2, joint_idx3):
                return float('nan')

            # 獲取上一幀的座標
            x1_prev = kpts_prev[joint_idx1][:2]
            x2_prev = kpts_prev[joint_idx2][:2]
            x3_prev = kpts_prev[joint_idx3][:2]

            # 計算角度變化率
            angle_diff_prev = self._calculate_angle(x1_prev, x2_prev, x3_prev)
            if angle_diff_prev is None:
                return float('nan')

            # 計算角速度（度/f）
            angle_velocity_2d = (angle_diff - angle_diff_prev)
            print(f"Frame {frame_num}: Angle Velocity 2D = {angle_velocity_2d:.2f}°/frame")

            return int(angle_velocity_2d)

        except (KeyError, IndexError, AttributeError, TypeError):
            return float('nan')

    def is_left_ankle_landed(
        self,
        frame_num: int = None,
        window_size: int = 5,
        spatial_var_threshold: float = 45.0
    ):
        """用 pandas rolling variance 判斷左腳踝是否著地。"""
        if self.pose_estimater.person_df.empty:
            return False

        person_df = self.pose_estimater.person_df
        selected_id = self.pose_estimater.person_id
        if selected_id is not None:
            person_df = person_df[person_df['person_id'] == selected_id]
        elif person_df['person_id'].nunique() != 1:
            # Single-person offline analysis need not require a manual person
            # selection. Multiple people still require an explicit selection.
            return False

        if frame_num is not None:
            person_df = person_df[person_df['frame_number'] <= frame_num]

        if person_df.empty:
            return False

        def _extract_left_ankle(kpts):
            if kpts is None or len(kpts) <= 15:
                return pd.Series({'l_ankle_x': np.nan, 'l_ankle_y': np.nan})
            x, y = kpts[15][:2]
            return pd.Series({'l_ankle_x': x, 'l_ankle_y': y})

        # 🚩 優先使用平滑後的數據進行分析，避免抖動影響判斷
        joint_df = person_df[['frame_number', 'keypoints', 'smoothed_keypoints']].copy() if 'smoothed_keypoints' in person_df.columns else person_df[['frame_number', 'keypoints']].copy()
        
        if 'smoothed_keypoints' in joint_df.columns:
            joint_df['keypoints'] = joint_df['smoothed_keypoints']

        joint_df[['l_ankle_x', 'l_ankle_y']] = joint_df['keypoints'].apply(_extract_left_ankle)
        joint_df = joint_df.dropna(subset=['l_ankle_x', 'l_ankle_y'])
        joint_df = joint_df.sort_values('frame_number')

        if len(joint_df) < window_size:
            return False

        # 計算過去 window_size 幀 x 與 y 的變異數
        joint_df['x_var'] = joint_df['l_ankle_x'].rolling(window=window_size).var()
        joint_df['y_var'] = joint_df['l_ankle_y'].rolling(window=window_size).var()

        # 將兩者相加，作為總體空間變動指標
        joint_df['spatial_variance'] = joint_df['x_var'] + joint_df['y_var']

        latest_spatial_var = joint_df['spatial_variance'].iloc[-1]
        return pd.notna(latest_spatial_var) and float(latest_spatial_var) <= spatial_var_threshold

    def get_frame_angle_data(self, frame_num: int = None, angle_name: str = None):
        if self.analyze_df.empty:
            return pd.DataFrame(), []
        condition = pd.Series([True] * len(self.analyze_df))

        # 根據 frame_num 過濾數據
        if frame_num is not None:
            condition &= (self.analyze_df['frame_number'] == frame_num)

        data = self.analyze_df.loc[condition]
        
        # 如果數據為空則返回
        if data.empty:
            return None, []

        if angle_name is not None:
            # 如果指定了 angle_name 且有特定幀數，返回該幀的角度數據
            if frame_num is not None:
                angle_value = data['angle'].iloc[0][angle_name]
                return data, angle_value
            else:
                # 如果未指定 frame_num，則返回該 angle_name 的所有幀數及對應角度
                frame_numbers = self.analyze_df['frame_number'].unique()
                angles = [row['angle'][angle_name][0] for _, row in self.analyze_df.iterrows() if angle_name in row['angle']]
                return frame_numbers, angles
        
        return data, []

    def reset(self):
        # 清理列表数据
        if self.analyze_info:
            self.analyze_info.clear()
        self.analyze_info = []
        
        # 重置 DataFrame
        self.analyze_df = pd.DataFrame()
        
        # 清理集合
        self.processed_frames.clear()

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
