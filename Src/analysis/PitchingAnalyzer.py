class PitchingAnalyzer:
    def __init__(self):
        self.state = "IDLE"
        self.offset = 30  # 像素門檻，視攝影機距離調整
        self.is_active = False
        self.frame_count = 0  # 帧计数器
        self.init_frames = 10  # 初始化缓冲帧数（约2秒）
        self.follow_frame_count = 0  # 帧计数器
        self.follow_frames = 5  # 後續帧数

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

        # 提取 y 座標 (注意：影像座標系 y 向下為正)
        r_wrist_y = keypoints[10][1]
        r_wrist_x = keypoints[10][0]
        r_ankle_x = keypoints[16][0]
        l_ankle_x = keypoints[15][0]
        r_hip_y = keypoints[12][1] # 右肩
        r_hip_x = keypoints[12][0] # 右肩
        l_knee_y = keypoints[13][1] # 左膝
        l_knee_x = keypoints[13][0] # 左膝
        neck_y = keypoints[18][1] # 鼻子
        l_hip_x = keypoints[11][0] # 左肩

        signal = None

        # 狀態機切換邏輯
        if self.state == "IDLE":
            # 偵測抬腿 (膝蓋向上移動，y 值變小)
            # print(f"[Debug] r_wrist_y: {r_wrist_y}, r_hip_y: {r_hip_y}")
            if l_knee_y < (r_hip_y + 80):
                self.state = "KNEEUP"
                self.is_active = True
                signal = "START"
                print("[Action] 偵測到抬腿，開始錄影...")

        elif self.state == "KNEEUP":
            # 偵測到手部舉起，進入投球階段
            if r_wrist_y < neck_y and abs(l_ankle_x - r_ankle_x) > 300 :
                self.state = "DELIVERY"

        elif self.state == "DELIVERY":
            # 偵測到手部落下，動作結束
            if r_wrist_y > (r_hip_y - self.offset):
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