import numpy as np
from Src.analysis.pitch_hand import pitch_joints


class PitchingAnalyzer:
    def __init__(self, hand='right'):
        self.hand = hand
        self.state = "IDLE"
        self.offset = 30  # 像素門檻，視攝影機距離調整
        self.is_active = False
        self.frame_count = 0  # 帧计数器
        self.init_frames = 10  # 初始化缓冲帧数（约2秒）
        self.follow_frame_count = 0  # 帧计数器
        self.follow_frames = 5  # 後續帧数

    def set_hand(self, hand):
        pitch_joints(hand)
        if hand != self.hand:
            self.hand = hand
            self.reset()

    def process(self, keypoints):
        """
        輸入：PoseEstimater 輸出的 keypoints (26, 4)
        輸出：action_signal ("START", "STOP", None)
        """
        if keypoints is None:
            return None

        # 初始化缓冲期：跳过前10帧
        if self.frame_count < self.init_frames:
            self.frame_count += 1
            return None

        joints = pitch_joints(self.hand)
        required = {'IDLE': [joints.knee, joints.rear_hip],
                    'KNEEUP': [joints.wrist, 18, joints.ankle, joints.rear_ankle],
                    'DELIVERY': [joints.wrist, joints.rear_hip]}.get(self.state, [])
        points = np.asarray(keypoints, dtype=float)
        if points.ndim != 2 or points.shape[0] < 26 or points.shape[1] < 3:
            return None
        if required and (not np.isfinite(points[required, :3]).all()
                         or (points[required, 2] < .6).any()):
            return None
        wrist_y = points[joints.wrist, 1]
        hip_y = points[joints.rear_hip, 1]

        signal = None

        # 狀態機切換邏輯
        if self.state == "IDLE":
            # 偵測抬腿 (膝蓋向上移動，y 值變小)
            # print(f"[Debug] r_wrist_y: {r_wrist_y}, r_hip_y: {r_hip_y}")
            if points[joints.knee, 1] < (hip_y + 80):
                self.state = "KNEEUP"
                self.is_active = True
                signal = "START"
                print("[Action] 偵測到抬腿，開始錄影...")

        elif self.state == "KNEEUP":
            # 偵測到手部舉起，進入投球階段
            if wrist_y < points[18, 1] and abs(points[joints.ankle, 0] - points[joints.rear_ankle, 0]) > 300:
                self.state = "DELIVERY"

        elif self.state == "DELIVERY":
            # 偵測到手部落下，動作結束
            if wrist_y > (hip_y - self.offset):
                self.state = "FOLLOW"
                self.follow_frame_count = 0
                print("[Action] 偵測到手部落下，進入後續動作階段...")

        elif self.state == "FOLLOW":
            self.follow_frame_count += 1
            print(f"[Debug] FOLLOW frame count: {self.follow_frame_count}")
            if self.follow_frame_count >= self.follow_frames:
                self.state = "IDLE"
                self.is_active = False
                signal = "STOP"
                print("[Action] 投球結束，準備存檔回傳。")
                self.follow_frame_count = 0

        return signal

    def reset(self):
        self.state = "IDLE"
        self.is_active = False
        self.frame_count = 0  # 重置帧计数器
        self.follow_frame_count = 0  # 重置後續帧计数器
