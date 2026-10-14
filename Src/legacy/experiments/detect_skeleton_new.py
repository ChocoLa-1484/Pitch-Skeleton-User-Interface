
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

import os
import sys
import numpy as np
import pandas as pd
import cv2
import torch
from scipy.signal import savgol_filter

current_dir = os.path.dirname(os.path.abspath(__file__))
ui_control_dir = os.path.abspath(os.path.join(current_dir, ".."))
if ui_control_dir not in sys.path:
    sys.path.insert(0, ui_control_dir)

from Src.pose.one_euro_filter import OneEuroFilter
from Src.common.timer import FPSTimer
from Src.pose.model import Model


def draw_pose_result(image: np.ndarray, person_df: pd.DataFrame, score_thr: float = 0.2) -> np.ndarray:
    vis_img = image.copy()
    skeleton_links = [
        [0, 1], [0, 2], [1, 3], [2, 4],
        [5, 18], [6, 18], [17, 18], [18, 19],
        [5, 7], [7, 9],
        [6, 8], [8, 10],
        [19, 11], [11, 13], [13, 15],
        [19, 12], [12, 14], [14, 16],
        [20, 24], [22, 24], [15, 24],
        [21, 25], [23, 25], [16, 25]
    ]

    if person_df is None or person_df.empty:
        return vis_img

    for _, row in person_df.iterrows():
        bbox = row.get('bbox', None)
        if bbox is not None and len(bbox) == 4:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            cv2.rectangle(vis_img, (x1, y1), (x2, y2), (0, 255, 255), 2)
            cv2.putText(
                vis_img,
                f"ID {row.get('person_id', -1)}",
                (x1, max(20, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 255),
                2,
                cv2.LINE_AA
            )

        # 🚩 優先使用平滑後的點進行繪製
        kpt_col = 'smoothed_keypoints' if 'smoothed_keypoints' in row and row['smoothed_keypoints'] else 'keypoints'
        keypoints = np.asarray(row.get(kpt_col, []), dtype=np.float32)
        if keypoints.ndim != 2 or keypoints.shape[1] < 2:
            continue

        scores = None
        if keypoints.shape[1] >= 3:
            scores = keypoints[:, 2]

        for a, b in skeleton_links:
            if a >= len(keypoints) or b >= len(keypoints):
                continue

            if scores is not None and (scores[a] < score_thr or scores[b] < score_thr):
                continue

            p1 = (int(keypoints[a][0]), int(keypoints[a][1]))
            p2 = (int(keypoints[b][0]), int(keypoints[b][1]))
            cv2.line(vis_img, p1, p2, (255, 200, 0), 2, cv2.LINE_AA)

        for idx, kp in enumerate(keypoints):
            x, y = int(kp[0]), int(kp[1])
            score = float(kp[2]) if keypoints.shape[1] >= 3 else 1.0
            if score < score_thr:
                continue

            cv2.circle(vis_img, (x, y), 4, (0, 0, 255), -1)
            cv2.putText(
                vis_img,
                str(idx),
                (x + 4, y - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (255, 255, 255),
                1,
                cv2.LINE_AA
            )

    return vis_img


class PoseEstimater:
    def __init__(self, model: Model = None):
        self.model = model
        self.compute_device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.primary_person_id = 1
        self.primary_track_id = None
        self.primary_max_lost_frames = 5
        self.primary_lost_frames = 0
        self.primary_last_bbox = None
        self.primary_min_iou = 0.7
        self.pose_bbox_scale_factor = 1.25

        self.person_df = pd.DataFrame()
        self.pre_person_df = pd.DataFrame()
        self.person_data = []
        self.processed_frames = set()
        self.person_df_by_frame = {}

        self.person_id = int(self.primary_person_id)
        self.kpt_id = None
        self.pitch_hand_id = 10
        self.is_detect = False

        self.kpt_buffer = []
        self.fps_timer = FPSTimer()
        self.smooth_filter = OneEuroFilter()
        self.smooth_filters = {}
        # MMPose-aligned inference switches.
        self.pose_flip_test = True
        self.pose_shift_heatmap = False
        # HALPE-26 symmetric keypoint index mapping.
        self.pose_flip_indices = [
            0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15, 17,
            18, 19, 21, 20, 23, 22, 25, 24
        ]
        # Strict temporal identity lock for symmetric HALPE-26 joints.
        self.lr_swap_pairs = [
            (1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12),
            (13, 14), (15, 16), (20, 21), (22, 23), (24, 25)
        ]
        self.lr_prev_kpts_cache = {}
        self.lr_upper_body_indices = [5, 6, 7, 8, 9, 10]
        self.lr_min_reliable_upper_points = 4

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
                    '左膝蓋': [11, 13, 15],
                    '右肩外旋': [10, 8, 8],
                    # 'l_knee_angle': [11, 13, 15],
                    # 'r_knee_angle': [12, 14, 16]
                }
            },
        }

    def setDetect(self, status: bool):
        self.is_detect = status

    def setPersonId(self, person_id):
        """Ignore manual selection and keep logical target id fixed to primary id."""
        self.person_id = int(self.primary_person_id)

    def setKptId(self, kpt_id):
        self.kpt_id = kpt_id

    def setPitchHandId(self, kpt_id):
        self.pitch_hand_id = kpt_id

    def reset_for_new_video(self):
        """清理所有累積的追踪狀態、濾波器和內存 - 必須在加載新影片時調用"""
        # 清理 Model 的追踪器狀態
        if self.model is not None:
            self.model.reset_tracker()
            self.model.clear_gpu_cache()
        
        # 清理本地濾波器緩存（防止內存洩漏）
        self.smooth_filters.clear()
        self.lr_prev_kpts_cache.clear()
        
        # 重置追踪狀態
        self.primary_track_id = None
        self.primary_last_bbox = None
        self.primary_lost_frames = 0
        self.processed_frames.clear()
        
        # 清理數據幀
        self.person_df = pd.DataFrame()
        self.pre_person_df = pd.DataFrame()
        self.person_data = []
        self.person_df_by_frame.clear()
        self.kpt_buffer.clear()

    def _pick_device_for_yolo(self):
        if self.compute_device == 'cuda':
            return self.model.detect_args.device
        return 'cpu'

    def _flip_heatmaps_like_mmpose(self, heatmaps: np.ndarray) -> np.ndarray:
        """Mirror mmpose.models.utils.tta.flip_heatmaps in heatmap mode."""
        flipped = np.flip(heatmaps, axis=-1).copy()
        if flipped.ndim == 4 and len(self.pose_flip_indices) == flipped.shape[1]:
            flipped = flipped[:, self.pose_flip_indices, :, :]
        if self.pose_shift_heatmap:
            flipped[..., 1:] = flipped[..., :-1].copy()
        return flipped

    def _preprocess_pose_input(self, crop_bgr: np.ndarray) -> np.ndarray:
        """Match mmpose PoseDataPreprocessor (bgr_to_rgb + normalize + NCHW)."""
        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        mean = np.array([123.675, 116.28, 103.53], dtype=np.float32).reshape(1, 1, 3)
        std = np.array([58.395, 57.12, 57.375], dtype=np.float32).reshape(1, 1, 3)
        tensor = (rgb.astype(np.float32) - mean) / std
        return np.transpose(tensor, (2, 0, 1))[None, ...]

    def _swap_joint_filter_states(self, person_id: int, left_idx: int, right_idx: int):
        """Keep temporal filters coherent after a left-right joint swap."""
        for axis in ('x', 'y'):
            s_left = (int(person_id), int(left_idx), axis)
            s_right = (int(person_id), int(right_idx), axis)
            has_left = s_left in self.smooth_filters
            has_right = s_right in self.smooth_filters
            if has_left and has_right:
                self.smooth_filters[s_left], self.smooth_filters[s_right] = (
                    self.smooth_filters[s_right], self.smooth_filters[s_left])
            elif has_left and not has_right:
                self.smooth_filters[s_right] = self.smooth_filters.pop(s_left)
            elif has_right and not has_left:
                self.smooth_filters[s_left] = self.smooth_filters.pop(s_right)

    def _get_prev_person_keypoints(self, person_id: int, frame_num=None):
        """Get latest keypoints for the same person from previous frames."""
        if self.person_df.empty or 'person_id' not in self.person_df.columns:
            return None

        prev_data = self.person_df[self.person_df['person_id'] == int(person_id)]
        if frame_num is not None and 'frame_number' in prev_data.columns:
            prev_data = prev_data[prev_data['frame_number'] < int(frame_num)]

        if prev_data.empty:
            return None

        return np.asarray(prev_data.iloc[-1]['keypoints'], dtype=np.float32)

    def _get_lr_lock_key(self, person_id: int) -> int:
        """Stable key for LR lock, robust to tracker id drift."""
        if self.person_id is not None:
            return int(self.person_id)
        return int(self.primary_person_id if self.primary_person_id is not None else person_id)

    def _is_lr_frame_reliable(self, kpts: np.ndarray, conf_thr: float) -> bool:
        """Check whether current frame has enough upper-body evidence."""
        if kpts is None or len(kpts) == 0:
            return False

        arr = np.asarray(kpts, dtype=np.float32)
        valid = 0
        for idx in self.lr_upper_body_indices:
            if idx >= len(arr):
                continue
            x, y = float(arr[idx][0]), float(arr[idx][1])
            c = float(arr[idx][2]) if arr.shape[1] > 2 else 1.0
            if np.isfinite(x) and np.isfinite(y) and x > 0.0 and y > 0.0 and c >= conf_thr:
                valid += 1

        return valid >= int(self.lr_min_reliable_upper_points)

    def _stabilize_lr_on_dropout(self, curr_kpts: np.ndarray, prev_kpts: np.ndarray,
                                 conf_thr: float) -> np.ndarray:
        """Fill weak upper-body points from previous frame before LR matching."""
        if prev_kpts is None:
            return curr_kpts

        out = np.asarray(curr_kpts, dtype=np.float32).copy()
        prev = np.asarray(prev_kpts, dtype=np.float32)
        n = min(len(out), len(prev))

        for idx in self.lr_upper_body_indices:
            if idx >= n:
                continue
            curr_conf = float(out[idx][2]) if out.shape[1] > 2 else 1.0
            prev_conf = float(prev[idx][2]) if prev.shape[1] > 2 else 1.0

            if curr_conf < conf_thr and prev_conf > 0.0:
                out[idx][0] = prev[idx][0]
                out[idx][1] = prev[idx][1]
                if out.shape[1] > 2:
                    out[idx][2] = max(curr_conf, min(prev_conf, 0.65))

        return out

    def _enforce_temporal_lr_consistency(self, person_id: int, curr_kpts: np.ndarray,
                                         prev_kpts: np.ndarray, frame_num=None) -> np.ndarray:
        """Hard-lock upper-limb identity using previous frame assignment."""
        if prev_kpts is None or len(curr_kpts) < 2:
            return curr_kpts

        out = np.asarray(curr_kpts, dtype=np.float32).copy()
        prev = np.asarray(prev_kpts, dtype=np.float32)
        num_kpts = min(len(out), len(prev))

        for left_idx, right_idx in self.lr_swap_pairs:
            if left_idx >= num_kpts or right_idx >= num_kpts:
                continue

            cl = out[left_idx]
            cr = out[right_idx]
            pl = prev[left_idx]
            pr = prev[right_idx]

            if (pl[0] == 0 and pl[1] == 0 and pr[0] == 0 and pr[1] == 0):
                continue

            direct = float(np.hypot(cl[0] - pl[0], cl[1] - pl[1]) + np.hypot(cr[0] - pr[0], cr[1] - pr[1]))
            cross = float(np.hypot(cl[0] - pr[0], cl[1] - pr[1]) + np.hypot(cr[0] - pl[0], cr[1] - pl[1]))

            # If cross assignment is closer to previous identities, enforce swap.
            if cross < direct:
                out[[left_idx, right_idx]] = out[[right_idx, left_idx]]
                self._swap_joint_filter_states(person_id, left_idx, right_idx)

        return out


    def _decode_heatmap_to_keypoints(self, heatmaps: np.ndarray, M_inv: np.ndarray):
        if heatmaps.ndim == 4:
            heatmaps = heatmaps[0]

        num_kpts, hm_h, hm_w = heatmaps.shape
        flat = heatmaps.reshape(num_kpts, -1)
        max_idx = np.argmax(flat, axis=1)
        max_score = np.max(flat, axis=1)

        preds_x = (max_idx % hm_w).astype(np.float32)
        preds_y = (max_idx // hm_w).astype(np.float32)

        # 準備接收實際座標的陣列
        res_x = np.zeros(num_kpts, dtype=np.float32)
        res_y = np.zeros(num_kpts, dtype=np.float32)

        # 從 heatmap 放大回模型輸入圖 (W x H) 的比例
        input_h = float(self.model.pose_input_shape[2])
        input_w = float(self.model.pose_input_shape[3])
        scale_w = input_w / hm_w 
        scale_h = input_h / hm_h

        for k in range(num_kpts):
            px, py = int(preds_x[k]), int(preds_y[k])
            
            # DARK 高精度解碼
            if 0 < px < hm_w - 1 and 0 < py < hm_h - 1:
                dx = 0.5 * (heatmaps[k, py, px + 1] - heatmaps[k, py, px - 1])
                dxx = heatmaps[k, py, px + 1] - 2 * heatmaps[k, py, px] + heatmaps[k, py, px - 1]
                preds_x[k] += np.clip(-dx / (dxx + 1e-9), -0.5, 0.5)
                
                dy = 0.5 * (heatmaps[k, py + 1, px] - heatmaps[k, py - 1, px])
                dyy = heatmaps[k, py + 1, px] - 2 * heatmaps[k, py, px] + heatmaps[k, py - 1, px]
                preds_y[k] += np.clip(-dy / (dyy + 1e-9), -0.5, 0.5)

            # 轉回 192x256 的畫布座標
            img_px = preds_x[k] * scale_w
            img_py = preds_y[k] * scale_h

            # 透過 M_inv 反向仿射變換，完美映射回原始影片大圖
            res_x[k] = M_inv[0, 0] * img_px + M_inv[0, 1] * img_py + M_inv[0, 2]
            res_y[k] = M_inv[1, 0] * img_px + M_inv[1, 1] * img_py + M_inv[1, 2]

        keypoints = np.stack([res_x, res_y, max_score.squeeze()], axis=1)
        return keypoints.astype(np.float32), max_score.squeeze()

    def _run_pose_onnx(self, img: np.ndarray, bbox):
        input_h = int(self.model.pose_input_shape[2])
        input_w = int(self.model.pose_input_shape[3])
        x1, y1, x2, y2 = [float(v) for v in bbox]
        center_x = (x1 + x2) * 0.5
        center_y = (y1 + y2) * 0.5
        box_w = max(x2 - x1, 1.0) * float(self.pose_bbox_scale_factor)
        box_h = max(y2 - y1, 1.0) * float(self.pose_bbox_scale_factor)

        # Keep target aspect ratio the same as pose input.
        aspect_ratio = float(input_w) / float(input_h)
        if box_w > box_h * aspect_ratio:
            box_h = box_w / aspect_ratio
        else:
            box_w = box_h * aspect_ratio

        scale_x = float(input_w) / box_w
        scale_y = float(input_h) / box_h
        tx = float(input_w) * 0.5 - center_x * scale_x
        ty = float(input_h) * 0.5 - center_y * scale_y
        M = np.array([[scale_x, 0, tx], [0, scale_y, ty]], dtype=np.float32)

        resized = cv2.warpAffine(
            img,
            M,
            (input_w, input_h),
            flags=cv2.INTER_LINEAR,
            borderValue=(114, 114, 114))
        input_tensor = self._preprocess_pose_input(resized)

        outputs_ori = self.model.run_pose(input_tensor)
        if not outputs_ori:
            return None, None
        heatmap_ori = outputs_ori[0]

        if self.pose_flip_test:
            input_flipped = np.flip(input_tensor, axis=3).copy()
            outputs_flipped = self.model.run_pose(input_flipped)
            if not outputs_flipped:
                return None, None
            heatmap_flipped = self._flip_heatmaps_like_mmpose(outputs_flipped[0])
            heatmaps = (heatmap_ori + heatmap_flipped) * 0.5
        else:
            heatmaps = heatmap_ori
        
        # 6. 計算反向矩陣供解碼使用
        M_inv = cv2.invertAffineTransform(M)
        return self._decode_heatmap_to_keypoints(heatmaps, M_inv)

    def _bbox_center(self, bbox):
        x1, y1, x2, y2 = [float(v) for v in bbox]
        return (x1 + x2) * 0.5, (y1 + y2) * 0.5

    def _bbox_iou(self, bbox_a, bbox_b):
        ax1, ay1, ax2, ay2 = [float(v) for v in bbox_a]
        bx1, by1, bx2, by2 = [float(v) for v in bbox_b]

        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)

        inter_w = max(0.0, inter_x2 - inter_x1)
        inter_h = max(0.0, inter_y2 - inter_y1)
        inter_area = inter_w * inter_h

        area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
        area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
        union = area_a + area_b - inter_area
        if union <= 0.0:
            return 0.0
        return inter_area / union

    def _select_primary_target(self, bboxes, ids, frame_shape=None):
        """Basic target selection: keep same track_id if present, else choose bbox by area and center distance."""
        if len(bboxes) == 0:
            self.primary_lost_frames += 1
            if self.primary_last_bbox is not None:
                return [self.primary_last_bbox], [int(self.primary_person_id)]
            return [], []

        frame_center_x = None
        frame_center_y = None
        if frame_shape is not None and len(frame_shape) >= 2:
            frame_h, frame_w = frame_shape[:2]
            frame_center_x = float(frame_w) * 0.5
            frame_center_y = float(frame_h) * 0.5

        keep = []
        for bbox, pid in zip(bboxes, ids):
            x1, y1, x2, y2 = bbox
            area = max(0.0, float(x2 - x1)) * max(0.0, float(y2 - y1))
            if area > 5000:
                bbox_center_x = (float(x1) + float(x2)) * 0.5
                bbox_center_y = (float(y1) + float(y2)) * 0.5
                if frame_center_x is None or frame_center_y is None:
                    center_dist = 0.0
                else:
                    dx = bbox_center_x - frame_center_x
                    dy = bbox_center_y - frame_center_y
                    center_dist = float(np.hypot(dx, dy))
                keep.append((bbox, int(pid), area, center_dist))

        if not keep:
            self.primary_lost_frames += 1
            if self.primary_last_bbox is not None:
                return [self.primary_last_bbox], [int(self.primary_person_id)]
            return [], []

        if self.primary_track_id is not None:
            for bbox, pid, _, _ in keep:
                if pid == int(self.primary_track_id):
                    if self.primary_last_bbox is not None:
                        curr_iou = self._bbox_iou(self.primary_last_bbox, bbox)
                        if curr_iou < float(self.primary_min_iou):
                            continue

                    self.primary_last_bbox = np.asarray(bbox, dtype=np.float32).tolist()
                    self.primary_lost_frames = 0
                    return [bbox], [int(self.primary_person_id)]

            self.primary_lost_frames += 1
            if self.primary_last_bbox is not None and self.primary_lost_frames <= self.primary_max_lost_frames:
                return [self.primary_last_bbox], [int(self.primary_person_id)]
            
            # Timeout: find best match by IoU before re-init
            if keep and self.primary_last_bbox is not None:
                best_iou_bbox = None
                best_iou_pid = None
                best_iou_val = -1.0
                for bbox, pid, _, _ in keep:
                    iou = self._bbox_iou(self.primary_last_bbox, bbox)
                    if iou > best_iou_val:
                        best_iou_val = iou
                        best_iou_bbox = bbox
                        best_iou_pid = pid
                
                if best_iou_bbox is not None and best_iou_val >= float(self.primary_min_iou):
                    self.primary_track_id = int(best_iou_pid)
                    self.primary_last_bbox = np.asarray(best_iou_bbox, dtype=np.float32).tolist()
                    self.primary_lost_frames = 0
                    return [best_iou_bbox], [int(self.primary_person_id)]

                return [self.primary_last_bbox], [int(self.primary_person_id)]
            
            # If no good match by IoU, reset and fall through to re-init
            self.primary_track_id = None
            self.primary_last_bbox = None
            self.primary_lost_frames = 0

        max_area = max(item[2] for item in keep)
        max_dist = max(item[3] for item in keep)
        if max_area <= 0.0:
            max_area = 1.0
        if max_dist <= 0.0:
            max_dist = 1.0

        scored = []
        for bbox, pid, area, center_dist in keep:
            norm_area = area / max_area
            norm_center = 1.0 - (center_dist / max_dist)
            score = (0.7 * norm_area) + (0.3 * norm_center)
            scored.append((bbox, pid, score))

        scored.sort(key=lambda item: item[2], reverse=True)
        best_bbox, best_track_id, _ = scored[0]
        self.primary_track_id = int(best_track_id)
        self.primary_last_bbox = np.asarray(best_bbox, dtype=np.float32).tolist()
        self.primary_lost_frames = 0
        return [best_bbox], [int(self.primary_person_id)]

    def processImage(self, img: np.ndarray, select_id=None, frame_num=None):
        # 1. 執行 YOLO 追蹤 (使用 botsort，但優化速度)
        # 優化建議: imgsz=480 降低至 2/3 分辨率可顯著加速，conf 提高減少追蹤目標
        track_results = self.model.detector.track(
            source=img,
            persist=True,
            verbose=False,
            conf=float(self.model.detect_args.score_thr),
            iou=float(self.model.detect_args.nms_thr),
            imgsz=960,  # 從 640 降至 480: 速度提升約 40-50%
            tracker='bytetrack.yaml',
            classes=[int(self.model.detect_args.det_cat_id)],
            device=self._pick_device_for_yolo()
        )

        if not track_results:
            return [], []

        result = track_results[0]
        boxes = result.boxes
        
        # 確保有抓到框，且有分配到追蹤 ID
        if boxes is None or boxes.xyxy is None or len(boxes.xyxy) == 0 or boxes.id is None:
            return [], []

        bboxes = boxes.xyxy.detach().cpu().numpy().astype(np.float32)
        ids = boxes.id.detach().cpu().numpy().astype(np.int32)

        # 2. 選擇追蹤目標（基本版本）
        online_bbox, online_ids = self._select_primary_target(bboxes, ids, frame_shape=img.shape)
        
        if not online_bbox:
            return [], []

        init_bbox = online_bbox[0]
        init_pid = int(online_ids[0])

        keypoints, keypoint_scores = self._run_pose_onnx(img, init_bbox)
        
        if keypoints is None or keypoint_scores is None:
            return [], []

        return [{
            'bbox': np.asarray(init_bbox, dtype=np.float32).tolist(),
            'keypoints': keypoints[np.newaxis, ...],
            'keypoint_scores': keypoint_scores[np.newaxis, ...]
        }], [init_pid]

    def mergePersonData(self, pred_instances, person_ids, frame_num=None):
        if frame_num is None:
            self.person_data = []
            self.person_df_by_frame = {}

        if not pred_instances or not person_ids:
            return self.person_df if frame_num is not None else pd.DataFrame(self.person_data)

        max_kpts = len(self.joints['haple']['keypoints'])
        rows = []
        for person, pid in zip(pred_instances, person_ids):
            bbox = person.get('bbox')
            keypoints = np.asarray(person.get('keypoints', []), dtype=np.float32)
            keypoint_scores = np.asarray(person.get('keypoint_scores', []), dtype=np.float32)

            if keypoints.ndim == 3:
                keypoints = keypoints[0]
            if keypoint_scores.ndim == 2:
                keypoint_scores = keypoint_scores[0]

            if keypoints.size == 0 or keypoint_scores.size == 0 or bbox is None:
                continue

            valid_count = min(len(keypoints), max_kpts)
            
            # Ensure keypoints have x, y, conf structure [N, 3]
            # If input is [N, 2], merge with score to get [N, 3]
            cur_kp = keypoints[:valid_count, :2]
            cur_score = keypoint_scores[:valid_count].reshape(-1, 1)
            
            keypoints_data = np.hstack((
                np.round(cur_kp, 2),                              # [x, y]
                np.round(cur_score, 2),                           # [conf]
                np.full((valid_count, 1), 0.0, dtype=np.float32)  # [is_corrected 標記]
            ))

            new_kpts = np.zeros((max_kpts, 4), dtype=np.float32)
            new_kpts[:valid_count] = keypoints_data

            row = {
                'person_id': int(pid),
                'bbox': np.asarray(bbox, dtype=np.float32).tolist(),
                'keypoints': new_kpts.tolist(),
                # 🚩 預留平滑後的點欄位，初始值與原始點相同
                'smoothed_keypoints': new_kpts.tolist()
            }
            if frame_num is not None:
                row['frame_number'] = frame_num
            rows.append(row)

        if frame_num is None:
            self.person_data = rows
            self.person_df = pd.DataFrame(self.person_data)
            return self.person_df

        new_rows_df = pd.DataFrame(rows)
        if self.person_df.empty:
            self.person_df = new_rows_df
        else:
            self.person_df = pd.concat([self.person_df, new_rows_df], ignore_index=True)

        self.person_df_by_frame[int(frame_num)] = new_rows_df
        return self.person_df

    def smoothKpt(self, person_ids: list, frame_num=None):
        if self.person_df.empty:
            return

        for person_id in person_ids:
            if frame_num is not None:
                curr_data = self.person_df.loc[
                    (self.person_df['frame_number'] == frame_num) &
                    (self.person_df['person_id'] == person_id)
                ]
            else:
                curr_data = self.person_df.loc[self.person_df['person_id'] == person_id]

            if curr_data.empty:
                continue

            kpt_thr = float(getattr(self.model.pose_args, 'kpt_thr', 0.7)) if self.model is not None else 0.2
            curr_kpts_np = np.asarray(curr_data.iloc[0]['keypoints'], dtype=np.float32)
            prev_kpts = None
            if frame_num is not None and 'frame_number' in self.person_df.columns:
                prev_data = self.person_df.loc[
                    (self.person_df['person_id'] == person_id) &
                    (self.person_df['frame_number'] < frame_num)
                ]
                if not prev_data.empty:
                    prev_kpts = np.asarray(prev_data.iloc[-1]['keypoints'], dtype=np.float32)
            else:
                if not self.pre_person_df.empty:
                    prev_data = self.pre_person_df.loc[self.pre_person_df['person_id'] == person_id]
                    if not prev_data.empty:
                        prev_kpts = np.asarray(prev_data.iloc[0]['keypoints'], dtype=np.float32)

            lock_key = self._get_lr_lock_key(person_id)
            if prev_kpts is None:
                prev_kpts = self.lr_prev_kpts_cache.get(lock_key)

            if prev_kpts is not None:
                curr_kpts_np = self._stabilize_lr_on_dropout(curr_kpts_np, prev_kpts, conf_thr=kpt_thr)
                curr_kpts_np = self._enforce_temporal_lr_consistency(
                    lock_key, curr_kpts_np, prev_kpts, frame_num=frame_num)
                # 🚩 将 L/R 稳定性处理的结果存入 smoothed 栏位，保持 keypoints 为原始输出
                self.person_df.at[curr_data.index[0], 'smoothed_keypoints'] = curr_kpts_np.tolist()

            curr_kpts = torch.tensor(curr_kpts_np, device=self.compute_device)

            smoothed = []

            for joint_idx, ck in enumerate(curr_kpts):
                cx, cy = ck[0], ck[1]
                conf, is_corrected = ck[2], ck[3]

                prev_x = None
                prev_y = None
                if prev_kpts is not None and joint_idx < len(prev_kpts):
                    prev_x = float(prev_kpts[joint_idx][0])
                    prev_y = float(prev_kpts[joint_idx][1])

                fx = self._get_smooth_filter(person_id, joint_idx, 'x')
                fy = self._get_smooth_filter(person_id, joint_idx, 'y')

                # 模型原始輸出
                raw_x = float(cx)
                raw_y = float(cy)

                has_meas = (
                    raw_x != 0.0 and
                    raw_y != 0.0 and
                    np.isfinite(raw_x) and
                    np.isfinite(raw_y)
                )
                conf_ok = float(conf) >= kpt_thr
                valid_meas = has_meas and conf_ok

                # 如果測量無效且有前一幀點，則使用前一幀點 (補點)
                if not valid_meas and prev_x is not None and prev_y is not None:
                    kx, ky = prev_x, prev_y
                    final_conf = max(float(conf), 0.5)
                else:
                    kx, ky = raw_x, raw_y
                    final_conf = float(conf)

                # One Euro 平滑
                sx = fx(float(kx), prev_x)
                sy = fy(float(ky), prev_y)

                # 保持手動校正標記
                is_corrected_val = float(is_corrected)

                smoothed.append([float(sx), float(sy), final_conf, is_corrected_val])

            smoothed_arr = np.asarray(smoothed, dtype=np.float32)
            self.person_df.at[curr_data.index[0], 'smoothed_keypoints'] = smoothed_arr.tolist()
            # Do not overwrite identity anchor with weak/dropout frames.
            if self._is_lr_frame_reliable(smoothed_arr, conf_thr=kpt_thr):
                self.lr_prev_kpts_cache[lock_key] = smoothed_arr.copy()

    def _get_smooth_filter(self, person_id: int, joint_idx: int, axis: str) -> OneEuroFilter:
        key = (int(person_id), int(joint_idx), axis)
        if key not in self.smooth_filters:
            self.smooth_filters[key] = OneEuroFilter(min_cutoff=0.7, beta=0.3, d_cutoff=1.0)
        return self.smooth_filters[key]

    def detectKpt(self, image: np.ndarray, frame_num: int = None, is_video: bool = False, is_processed: bool = False):
        if not self.is_detect:
            return image, pd.DataFrame(), 0

        self.fps_timer.tic()

        # Processed playback mode: use loaded JSON keypoints as-is.
        # Do not run detector/smoothing again, otherwise displayed points
        # may deviate from stored coordinates.
        if is_video and is_processed:
            if self.kpt_id is not None:
                self.kpt_buffer = self.updateKptBuffer(frame_num)
            avg_time = self.fps_timer.toc()
            fps = int(1 / max(avg_time, 1e-5))
            fps = max(0, fps)
            return image, self.person_df, fps

        if is_video:
            if frame_num not in self.processed_frames:
                pred_instances, person_ids = self.processImage(image, select_id=self.person_id, frame_num=frame_num)
                self.person_df = self.mergePersonData(pred_instances, person_ids, frame_num)
                self.smoothKpt(person_ids, frame_num)
                self.pre_person_df = self.person_df.copy()
                self.processed_frames.add(frame_num)

            if self.kpt_id is not None:
                self.kpt_buffer = self.updateKptBuffer(frame_num)
        else:
            pred_instances, person_ids = self.processImage(image, select_id=self.person_id, frame_num=frame_num)
            self.person_df = self.mergePersonData(pred_instances, person_ids)
            # self.smoothKpt(person_ids, frame_num)
            self.pre_person_df = self.person_df.copy()

            if self.kpt_id is not None:
                person_data = self.getPersonDf(is_select=True, is_kpt=True)
                if person_data is not None:
                    keypoint = person_data[self.kpt_id][:2]
                    self.kpt_buffer.append(keypoint)

        avg_time = self.fps_timer.toc()
        fps = int(1 / max(avg_time, 1e-5))
        fps = max(0, fps)
        return image, self.person_df, fps

    def updateKptBuffer(self, frame_num: int, window_length=3, polyorder=2):
        filtered_df = self.person_df[
            (self.person_df['person_id'] == self.person_id) &
            (self.person_df['frame_number'] < frame_num)
        ]
        if filtered_df.empty:
            return None

        if not filtered_df['frame_number'].is_monotonic_increasing:
            filtered_df = filtered_df.sort_values(by='frame_number')
        points = []
        for kpts in filtered_df['keypoints']:
            kpt = kpts[self.kpt_id]
            if kpt is not None and len(kpt) >= 2:
                points.append((kpt[0], kpt[1]))

        if len(points) < window_length:
            return points

        if window_length > len(points):
            window_length = len(points) if len(points) % 2 == 1 else len(points) - 1
        current_poly = min(polyorder, window_length - 1)

        x = np.array([pt[0] for pt in points])
        y = np.array([pt[1] for pt in points])

        x_smooth = savgol_filter(x, window_length=window_length, polyorder=current_poly)
        y_smooth = savgol_filter(y, window_length=window_length, polyorder=current_poly)
        return list(zip(x_smooth, y_smooth))

    def getPersonDf(self, frame_num=None, is_select=False, is_kpt=False, use_smoothed=False):
        if self.person_df.empty:
            return None if is_kpt else pd.DataFrame()

        if frame_num is not None and frame_num in self.person_df_by_frame:
            data = self.person_df_by_frame[frame_num].copy()
            if use_smoothed and 'smoothed_keypoints' in data.columns:
                data['keypoints'] = data['smoothed_keypoints']
            if is_select and self.person_id is not None:
                data = data.loc[data['person_id'] == self.person_id]
            if data.empty:
                return None if is_kpt else pd.DataFrame()
            if is_kpt:
                return data['keypoints'].iloc[0]
            return data

        cond = pd.Series([True] * len(self.person_df))
        if frame_num is not None and 'frame_number' in self.person_df.columns:
            cond &= (self.person_df['frame_number'] == frame_num)
        if is_select and self.person_id is not None:
            cond &= (self.person_df['person_id'] == self.person_id)

        data = self.person_df.loc[cond].copy()
        if use_smoothed and 'smoothed_keypoints' in data.columns:
            data['keypoints'] = data['smoothed_keypoints']

        if data.empty:
            return None if is_kpt else pd.DataFrame()
        if is_kpt:
            return data['keypoints'].iloc[0]
        return data

    def getPrePersonDf(self, *joint_ids):
        if self.pre_person_df.empty:
            return tuple(None for _ in joint_ids)

        cond = self.pre_person_df['person_id'] == self.person_id
        data = self.pre_person_df.loc[cond]
        if data.empty:
            return tuple(None for _ in joint_ids)

        return tuple(data['keypoints'].iloc[0][joint_id] for joint_id in joint_ids)

    def setProcessedData(self, person_df: pd.DataFrame):
        if person_df.empty:
            return

        # Use loaded data as-is to preserve temporal synchronization across cameras.
        # Normalizing frames to start at 0 independently for each camera can cause
        # fatal misalignment in 3D reconstruction.
        self.person_df = person_df.copy()
        
        if 'frame_number' in self.person_df.columns:
            self.person_df['frame_number'] = self.person_df['frame_number'].astype(int)

        self.processed_frames = set(self.person_df['frame_number'].tolist())
        self.person_df_by_frame = {
            int(frame): frame_df.copy()
            for frame, frame_df in self.person_df.groupby('frame_number', sort=False)
        }

    def update_person_df(self, x: float, y: float, frame_num: int, correct_kpt_idx: int):
        # 🚩 同時更新原始 dot 與平滑 dot 欄位，確保手動標記立即生效
        target_row = self.person_df.loc[
            (self.person_df['frame_number'] == frame_num) &
            (self.person_df['person_id'] == self.person_id)
        ]
        if not target_row.empty:
            target_row.iloc[0]['keypoints'][correct_kpt_idx] = [x, y, 0.9, 1]
            if 'smoothed_keypoints' in self.person_df.columns:
                target_row.iloc[0]['smoothed_keypoints'][correct_kpt_idx] = [x, y, 0.9, 1]

    def correct_person_id(self, before_correctId: int, after_correctId: int):
        if self.person_df.empty:
            return
        if (before_correctId not in self.person_df['person_id'].unique()) or (after_correctId not in self.person_df['person_id'].unique()):
            return

        max_frame = int(max(self.processed_frames)) if self.processed_frames else -1
        for i in range(max_frame + 1):
            cond = (self.person_df['frame_number'] == i) & (self.person_df['person_id'] == before_correctId)
            self.person_df.loc[cond, 'person_id'] = after_correctId

    def clearKptBuffer(self):
        self.kpt_buffer = []

    def reset(self):
        self.person_df = pd.DataFrame()
        self.pre_person_df = pd.DataFrame()
        self.person_data = []
        self.processed_frames = set()
        self.person_df_by_frame = {}

        self.person_id = int(self.primary_person_id)
        self.kpt_id = None
        self.kpt_buffer = []
        self.primary_track_id = None
        self.primary_lost_frames = 0
        self.primary_last_bbox = None

        self.fps_timer = FPSTimer()
        self.smooth_filter = OneEuroFilter()
        self.smooth_filters = {}
        self.is_detect = False
        self.lr_prev_kpts_cache = {}

        if self.model is not None:
            self.model.reset_tracker()


# if __name__ == '__main__':
#     model = Model()
#     pose = PoseEstimater(model)
#     pose.setDetect(True)

#     image_path = sys.argv[1] if len(sys.argv) > 1 else None
#     if image_path is not None:
#         image = cv2.imread(image_path)
#         if image is None:
#             print(f"讀取圖片失敗: {image_path}")
#             sys.exit(1)

#         _, df, fps = pose.detectKpt(image, frame_num=0, is_video=False)
#         print('Rows:', len(df), 'FPS:', fps)
#         if len(df) > 0:
#             print('Detected person_id:', df['person_id'].tolist())
#             print('First bbox:', df.iloc[0]['bbox'])

#             vis_img = draw_pose_result(image, df, score_thr=0.2)
#             input_root, _ = os.path.splitext(image_path)
#             out_path = f"{input_root}_kpt_vis.png"
#             ok = cv2.imwrite(out_path, vis_img)
#             if ok:
#                 print(f'Visualization saved: {out_path}')
#             else:
#                 print('Visualization save failed.')
#         else:
#             print('未偵測到人物，請換有人像且清晰的畫面再測試。')
#     else:
#         dummy = np.zeros((720, 1280, 3), dtype=np.uint8)
#         _, df, fps = pose.detectKpt(dummy, frame_num=0, is_video=False)
#         print('Rows:', len(df), 'FPS:', fps)
#         print('目前是黑底假圖測試，Rows=0 屬於正常。可改用:')
#         print('python skeleton/detect_skeleton_new.py path_to_image.jpg')