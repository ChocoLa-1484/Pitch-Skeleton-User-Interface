import time
import numpy as np
import pandas as pd
from utils.one_euro_filter import OneEuroFilter
import os
import sys
import numpy as np
from utils.model import Model
from mmengine.logging import print_log
from scipy.signal import savgol_filter
from mmpose.apis import inference_topdown
from mmpose.apis import init_model as init_pose_estimator
from mmpose.evaluation.functional import nms
from mmpose.structures import merge_data_samples
from utils.timer import FPSTimer
import torch
import torch.autograd.profiler as profiler

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(current_dir, "..", "tracker"))

try:
    from mmdet.apis import inference_detector
    has_mmdet = True
except (ImportError, ModuleNotFoundError):
    has_mmdet = False

class PoseEstimater:
    def __init__(self, model: Model =None):
        self.model = model
        self.img_shape = (0,0,0)
        self.person_df = pd.DataFrame()
        self.pre_person_df = pd.DataFrame()
        self.person_id = None
        self.kpt_id = None
        self.pitch_hand_id = 10
        self.fps = None
        self.person_data = []
        self.processed_frames = set()
        # 以幀號為索引的快取，供單影片分析結束時複製分析結果使用
        # （pitch_widget._mirror_single_video_analysis 依賴此屬性）
        self.person_df_by_frame = {}
        self.fps_timer = FPSTimer()
        self.smooth_filter = OneEuroFilter()
        self.is_detect = False
        self.kpt_buffer = []
        self.joints = {
            "coco": {
                "keypoints": {
                    0: "鼻子",
                    1: "左眼",
                    2: "右眼",
                    3: "左耳",
                    4: "右耳",
                    5: "左肩",
                    6: "右肩",
                    7: "左手肘",
                    8: "右手肘",
                    9: "左手腕",
                    10: "右手腕",
                    11: "左髋",
                    12: "右髋",
                    13: "左膝",
                    14: "右膝",
                    15: "左腳踝",
                    16: "右腳踝"
                },
                "skeleton_links": [
                    [0, 1], [0, 2], [1, 3], [2, 4], # 頭
                    #軀幹
                    [5, 7], [7, 9],                 #左手
                    [6, 8], [8, 10],                #右手
                    [11, 13], [13, 15],   #左腿
                    [12, 14], [14, 16],   #右腿
                ],
                "left_points_indices": [[5, 7], [7, 9], [11, 13], [13, 15]],  # Indices of left hand, leg, and foot keypoints
                "right_points_indices": [[6, 8], [8, 10], [12, 14], [14, 16]]  # Indices of right hand, leg, and foot keypoints
            },
            "haple":{
                "keypoints": {
                    0: "鼻子",
                    1: "左眼",
                    2: "右眼",
                    3: "左耳",
                    4: "右耳",
                    5: "左肩",
                    6: "右肩",
                    7: "左肘",
                    8: "右肘",
                    9: "左腕",
                    10: "右腕",
                    11: "左髖",
                    12: "右髖",
                    13: "左膝",
                    14: "右膝",
                    15: "左踝",
                    16: "右踝",
                    17: "頭部",
                    18: "頸部",
                    19: "臀部",
                    20: "左大腳趾",
                    21: "右大腳趾",
                    22: "左小腳趾",
                    23: "右小腳趾",
                    24: "左腳跟",
                    25: "右腳跟"
                },
                "skeleton_links":[
                    [0, 1], [0, 2], [1, 3], [2, 4], # 頭
                    [5, 18], [6, 18], [17, 18],[18, 19],#軀幹
                    [5, 7], [7, 9],                 #左手
                    [6, 8], [8, 10],                #右手
                    [19, 11], [11, 13], [13, 15],   #左腿
                    [19, 12], [12, 14], [14, 16],   #右腿
                    [20, 24], [22, 24], [15, 24],   #左腳
                    [21, 25], [23, 25], [16, 25]    #右腳
                ],

                "left_points_indices": [[5, 18], [5, 7], [7, 9],[19, 11], [11, 13], [13, 15], [20, 24], [22, 24], [15, 24]],  # Indices of left hand, leg, and foot keypoints
                "right_points_indices": [[6, 18], [6, 8], [8, 10], [19, 12], [12, 14], [14, 16], [21, 25], [23, 25], [16, 25]],  # Indices of right hand, leg, and foot keypoints
                "angle_dict":{
                    # 'l_elbow_angle': [5, 7, 9],
                    '右手肘': [6, 8, 10],
                    # 'l_shoulder_angle': [18, 5, 7],
                    '右肩膀': [18, 6, 8],
                    '右腋窩': [8, 6, 19],
                    # The 2D phase detector and MER overlay both consume this
                    # angle: right forearm relative to the elbow's horizontal.
                    '右肩外旋': [10, 8, 8],
                    # 'l_knee_angle': [11, 13, 15],
                    # 'r_knee_angle': [12, 14, 16]
                }
            },
        }

    def mergePersonData(self, pred_instances, person_ids: list, frame_num: int = None):
        person_bboxes = pred_instances['bboxes']
        if frame_num is None:
            self.person_data = []

        for person, pid, bbox in zip(pred_instances, person_ids, person_bboxes):
            keypoints_data = np.hstack((
                np.round(person['keypoints'][0], 2),
                np.round(person['keypoint_scores'][0], 2).reshape(-1, 1),
                np.full((len(person['keypoints'][0]), 1), False, dtype=bool)
            ))

            new_kpts = np.full((len(self.joints['haple']['keypoints']), keypoints_data.shape[1]), 0.9)
            new_kpts[:26] = keypoints_data
            person_info = {
                'person_id': pid,
                'bbox': bbox,
                'keypoints': new_kpts
            }
            if frame_num is not None:
                person_info['frame_number'] = frame_num

            self.person_data.append(person_info)

        result_df = pd.DataFrame(self.person_data)

        # 同步維護以幀號為索引的快取
        if frame_num is not None and not result_df.empty:
            self.person_df_by_frame[int(frame_num)] = \
                result_df[result_df['frame_number'] == frame_num].copy()

        return result_df

    def detectKpt(self, image:np.ndarray, frame_num:int = None, is_video:bool = False, is_processed:bool=False):
        if not self.is_detect:
            # print('not detect')
            return image, pd.DataFrame(), 0

        fps = 0
        self.fps_timer.tic()
        # if not is_processed:
        if is_video:
            #影片處理方式
            if frame_num not in self.processed_frames:
                pred_instances, person_ids = self.processImage(image, select_id=self.person_id)
                self.person_df = self.mergePersonData(pred_instances, person_ids, frame_num)
                self.smoothKpt(person_ids, frame_num)
                self.processed_frames.add(frame_num)
            if self.kpt_id is not None:
                self.kpt_buffer = self.updateKptBuffer(frame_num)
        else:
            #real time 處理方式
            pred_instances, person_ids = self.processImage(image, select_id=self.person_id)
            self.person_df = self.mergePersonData(pred_instances, person_ids)
            self.smoothKpt(person_ids, frame_num)
            self.pre_person_df = self.person_df.copy()
            if self.kpt_id:
                person_data = self.getPersonDf(is_select=True, is_kpt=True)
                if person_data is not None:
                    keypoint = person_data[self.kpt_id][:2]  # 確保取出的是有效的 [x, y] 數據
                    self.kpt_buffer.append(keypoint)

        average_time = self.fps_timer.toc()
        fps = int(1/max(average_time, 0.00001))
        fps = fps if fps < 100 else 0
        return image, self.person_df, fps

    def smoothKpt(self, person_ids: list, frame_num=None):
        # 如果是即時處理，則初始化前一幀的數據，否則依賴 frame_slider 進行處理
        if frame_num is not None:
            curr_frame = frame_num
            if curr_frame == 0:
                return  # 初始幀，無需處理
            pre_frame_num = curr_frame - 1

        # 用於即時處理時的前一幀數據
        if frame_num is None and self.pre_person_df.empty:
            self.pre_person_df = self.person_df.copy()

        # 當前幀無數據時，跳過處理
        if self.person_df.empty:
            return

        for person_id in person_ids:
            # 如果使用 frame_slider，根據前後幀數據進行處理
            if frame_num is not None:
                pre_person_data = self.person_df.loc[(self.person_df['frame_number'] == pre_frame_num) &
                                                    (self.person_df['person_id'] == person_id)]
                curr_person_data = self.person_df.loc[(self.person_df['frame_number'] == curr_frame) &
                                                    (self.person_df['person_id'] == person_id)]
            # 即時處理時，使用 self.pre_person_df 作為前幀數據
            else:
                pre_person_data = self.pre_person_df.loc[self.pre_person_df['person_id'] == person_id]
                curr_person_data = self.person_df.loc[self.person_df['person_id'] == person_id]

            if curr_person_data.empty or pre_person_data.empty:
                continue  # 當前幀或前幀沒有該 person_id 的數據，跳過

            pre_kpts = torch.tensor(pre_person_data.iloc[0]['keypoints'], device='cuda')
            curr_kpts = torch.tensor(curr_person_data.iloc[0]['keypoints'], device='cuda')
            smoothed_kpts = []

            # 使用張量運算來進行平滑
            for pre_kpt, curr_kpt in zip(pre_kpts, curr_kpts):
                pre_kptx, pre_kpty = pre_kpt[0], pre_kpt[1]
                curr_kptx, curr_kpty, curr_conf, curr_label = curr_kpt[0], curr_kpt[1], curr_kpt[2], curr_kpt[3]

                if all([pre_kptx.item() != 0, pre_kpty.item() != 0, curr_kptx.item() != 0, curr_kpty.item() != 0]):
                    curr_kptx = self.smooth_filter(curr_kptx, pre_kptx)
                    curr_kpty = self.smooth_filter(curr_kpty, pre_kpty)

                smoothed_kpts.append([curr_kptx.cpu().item(), curr_kpty.cpu().item(), curr_conf.item(), curr_label.item()])

            # 更新當前幀的數據
            self.person_df.at[curr_person_data.index[0], 'keypoints'] = smoothed_kpts


    def processImage(self, img, select_id=None):
        """
        處理單張圖像，進行物件偵測、跟蹤和姿態估計。

        Args:
            model (dict): 包含檢測器、姿態估計器和跟蹤器的模型。
            img (np.ndarray): 輸入的圖像。
            select_id (int, optional): 選擇指定的追蹤ID，如果為None，則處理所有目標。

        Returns:
            Tuple: 預測的姿態實例和在線的追蹤ID。
        """

        # 進行物件偵測

        result = inference_detector(self.model.detector, img, test_pipeline= self.model.detector_test_pipeline) # prediction
        pred_instances = result.pred_instances
        det_result = pred_instances[pred_instances.scores > self.model.detect_args.score_thr].cpu().numpy()
        # 篩選指定類別的邊界框
        bboxes = det_result.bboxes[det_result.labels == self.model.detect_args.det_cat_id]
        scores = det_result.scores[det_result.labels == self.model.detect_args.det_cat_id]
        bboxes = bboxes[nms(np.hstack((bboxes, scores[:, None])), self.model.detect_args.nms_thr), :4]
        # 將新偵測的邊界框更新到跟蹤器
        online_targets = []
        track_id = 1  # 手动编号 track_id

        for bbox in bboxes:
            x1, y1, x2, y2 = bbox  # bbox 是 [x1, y1, x2, y2]
            w = x2 - x1
            h = y2 - y1
            tlwh = [x1, y1, w, h]  # 转换为 tlwh 格式

            online_targets.append({
                'tlwh': tlwh,   # 原本 tracker 提供的 tlwh
                'track_id': track_id  # 人工分配 track_id
            })
            track_id += 1  # 递增 ID

        # 過濾出有效的邊界框和追蹤ID
        # online_bbox, online_ids = self.filterValidTargets(online_targets, select_id)
        online_bbox, online_ids = self.filterBiggestTargets(online_targets, select_id)

        # 姿態估計
        pose_results = inference_topdown(self.model.pose_estimator, img, np.array(online_bbox))
        data_samples = merge_data_samples(pose_results)

        return data_samples.get('pred_instances', None), online_ids

    def filterValidTargets(self, online_targets, select_id: int = None):
        """
        過濾出有效的追蹤目標。

        Args:
            online_targets (List): 所有在線追蹤的目標。
            select_id (int, optional): 選擇指定的追蹤ID。

        Returns:
            Tuple: 有效的邊界框和追蹤ID。
        """
        if not online_targets:
            return [], []

        # 將所有在線目標的邊界框和ID提取為兩個列表
        tlwhs = []
        track_ids = []

        for target in online_targets:
            tlwhs.append(target.tlwh)
            track_ids.append(target.track_id)

        # 將列表轉換為 NumPy array
        tlwhs = np.array(tlwhs)
        track_ids = np.array(track_ids)

        # 將數據轉為張量並放到 GPU 上
        tlwhs = torch.tensor(tlwhs, device='cuda')  # shape: (n, 4)
        track_ids = torch.tensor(track_ids, device='cuda')  # shape: (n,)

        # 計算面積 w * h
        areas = tlwhs[:, 2] * tlwhs[:, 3]  # w * h

        # 過濾面積大於 10 的邊界框
        valid_mask = areas > 10

        # 如果指定了 select_id，則進一步過濾
        if select_id is not None:
            valid_mask &= (track_ids == select_id)

        # 過濾有效的邊界框和追蹤ID
        valid_tlwhs = tlwhs[valid_mask]
        valid_track_ids = track_ids[valid_mask]

        # 將 (x1, y1, w, h) 轉為 (x1, y1, x2, y2)
        valid_bbox = torch.cat([valid_tlwhs[:, :2], valid_tlwhs[:, :2] + valid_tlwhs[:, 2:4]], dim=1)

        # 返回結果
        return valid_bbox.cpu().tolist(), valid_track_ids.cpu().tolist()

    def filterBiggestTargets(self, online_targets, select_id: int = None):
        if not online_targets:
            return [], []

        tlwhs = []
        track_ids = []

        for target in online_targets:
            tlwhs.append(target['tlwh'])  # 使用字典的 'tlwh' 键
            track_ids.append(target['track_id'])  # 使用字典的 'track_id' 键

        # 轉換為 NumPy array
        tlwhs = np.array(tlwhs)
        track_ids = np.array(track_ids)

        # 將數據轉為張量並放到 GPU 上
        tlwhs = torch.tensor(tlwhs, device='cuda')  # shape: (n, 4)
        track_ids = torch.tensor(track_ids, device='cuda')  # shape: (n,)

        # 計算面積 w * h
        areas = tlwhs[:, 2] * tlwhs[:, 3]  # w * h

        # 過濾掉面積過小的
        valid_mask = areas > 10

        if select_id is not None:
            valid_mask &= (track_ids == select_id)  # 確保符合 select_id

        # 過濾有效數據
        valid_tlwhs = tlwhs[valid_mask].cpu()
        valid_track_ids = track_ids[valid_mask].cpu()
        valid_areas = areas[valid_mask].cpu()

        if len(valid_tlwhs) == 0:
            return [], []

        # 找出面積最大的索引
        max_idx = torch.argmax(valid_areas).item()


        # 轉換 (x, y, w, h) -> (x1, y1, x2, y2)
        max_tlwh = valid_tlwhs[max_idx]
        max_bbox = [max_tlwh[0], max_tlwh[1], max_tlwh[0] + max_tlwh[2], max_tlwh[1] + max_tlwh[3]]
        max_id = valid_track_ids[max_idx]

        return [max_bbox], [1]

    def correct_person_id(self, before_correctId:int, after_correctId:int):
        if self.person_df.empty:
            return

        if (before_correctId not in self.person_df['person_id'].unique()) or (after_correctId not in self.person_df['person_id'].unique()):
            return

        if (before_correctId in self.person_df['person_id'].unique()) and (after_correctId in self.person_df['person_id'].unique()):
            for i in range(0, max(self.processed_frames)):
                condition_1 = (self.person_df['frame_number'] == i) & (self.person_df['person_id'] == before_correctId)
                self.person_df.loc[condition_1, 'person_id'] = after_correctId

    def setPersonId(self, person_id):
        self.person_id = person_id
        print(f'person id: {self.person_id}')

    def setKptId(self, kpt_id):
        self.kpt_id = kpt_id
        # print(f'kpt id: {self.kpt_id}')

    def setPitchHandId(self,kpt_id):
        self.pitch_hand_id = kpt_id

    def setDetect(self, status:bool):
        self.is_detect = status

    def updateKptBuffer(self, frame_num:int, window_length=3, polyorder=2):
        filtered_df = self.person_df[
            (self.person_df['person_id'] == self.person_id) &
            (self.person_df['frame_number'] < frame_num)
        ]
        if filtered_df.empty:
            return None
        filtered_df = filtered_df.sort_values(by='frame_number')
        kpt_buffer = []
        for kpts in filtered_df['keypoints']:
            kpt = kpts[self.kpt_id]
            if kpt is not None and len(kpt) >= 2:
                kpt_buffer.append((kpt[0], kpt[1]))

        # 如果緩衝區長度大於等於窗口長度，則應用Savgol濾波器進行平滑
        if len(kpt_buffer) >= window_length:
            # 確保窗口長度為奇數且不超過緩衝區長度
            if window_length > len(kpt_buffer):
                window_length = len(kpt_buffer) if len(kpt_buffer) % 2 == 1 else len(kpt_buffer) - 1
            # 確保多項式階數小於窗口長度
            current_polyorder = min(polyorder, window_length - 1)

            # 分別提取x和y座標
            x = np.array([point[0] for point in kpt_buffer])
            y = np.array([point[1] for point in kpt_buffer])

            # 應用Savgol濾波器
            x_smooth = savgol_filter(x, window_length=window_length, polyorder=current_polyorder)
            y_smooth = savgol_filter(y, window_length=window_length, polyorder=current_polyorder)

            # 將平滑後的座標重新打包
            smoothed_points = list(zip(x_smooth, y_smooth))
        else:
            # 緩衝區長度不足，直接使用原始座標
            smoothed_points = kpt_buffer

        return smoothed_points

    def getPersonDf(self, frame_num=None, is_select=False, is_kpt=False):
        if self.person_df.empty:
            return pd.DataFrame()
        condition = pd.Series([True] * len(self.person_df))  # 初始條件設為全為 True
        if frame_num is not None:
            condition &= (self.person_df['frame_number'] == frame_num)

        if is_select and self.person_id is not None:
            condition &= (self.person_df['person_id'] == self.person_id)

        data = self.person_df.loc[condition].copy()

        if data.empty:
            return None

        if is_kpt:
            data = data['keypoints'].iloc[0]

        return data

    def getPrePersonDf(self, *joint_ids):
        if self.pre_person_df.empty:
            return tuple(None for _ in joint_ids)

        condition = self.pre_person_df['person_id'] == self.person_id
        data = self.pre_person_df.loc[condition]

        if data.empty:
            return tuple(None for _ in joint_ids)

        joint_data = tuple(data['keypoints'].iloc[0][joint_id] for joint_id in joint_ids)
        return joint_data

    def setProcessedData(self, person_df:pd.DataFrame):
        if person_df.empty:
            return
        self.person_df = person_df
        self.processed_frames = {frame_num for frame_num in self.person_df['frame_number']}
        # 從既有資料重建幀號快取（此路徑不經過 mergePersonData）
        self.person_df_by_frame = {
            int(fn): group.copy()
            for fn, group in self.person_df.groupby('frame_number')
        }

    def update_person_df(self, x:float, y:float,frame_num:int, correct_kpt_idx:int):
        self.person_df.loc[(self.person_df['frame_number'] == frame_num) &
                            (self.person_df['person_id'] == self.person_id), 'keypoints'].iloc[0][correct_kpt_idx] = [x, y, 0.9, 1]

    def clearKptBuffer(self):
        self.kpt_buffer = []

    def reset(self):
        self.person_df = pd.DataFrame()
        self.pre_person_df = pd.DataFrame()
        self.person_id = None
        self.kpt_id = None
        self.fps = None
        self.person_data = []
        self.processed_frames = set()
        self.person_df_by_frame = {}
        self.fps_timer = FPSTimer()
        self.smooth_filter = OneEuroFilter()
        self.is_detect = False
        self.kpt_buffer = []
