"""Chessboard-calibrated 3D reconstruction, in the CF camera frame (metres)."""
import hashlib
from pathlib import Path

import numpy as np

from cv_utils.analysis_io import atomic_json
from cv_utils.chessboard_metric import triangulate
from cv_utils.metric_session import load_rig, DEFINITION
from triangulate_3d_viewer import Triangulate3DViewer


class MetricTriangulate3DViewer(Triangulate3DViewer):
    metric = True

    def __init__(self, rig_path, first_camera, fps, reference_frame=0, heel=25):
        self.rig_path = Path(rig_path).resolve()
        self.rig = load_rig(self.rig_path)
        self.rig_sha256 = hashlib.sha256(self.rig_path.read_bytes()).hexdigest()
        if first_camera not in ('cf', 'cs') or not np.isfinite(fps) or fps <= 0:
            raise ValueError('需要 CF / CS 相機名稱及有效 FPS。')
        self.first_camera, self.fps = first_camera, float(fps)
        self.reference_frame, self.heel = int(reference_frame), int(heel)
        self.quality = {}
        super().__init__(np.eye(3), np.eye(3), np.eye(3))
        self.auto_save = False

    def add_2d_keypoints(self, frame_L, frame_R, frame_num):
        if frame_num not in self.all_3d_frames:
            first, second = (np.asarray(f['keypoints'], float) for f in (frame_L, frame_R))
            cf, cs = (first, second) if self.first_camera == 'cf' else (second, first)
            if cf.ndim != 2 or cs.ndim != 2 or cf.shape[0] != 26 or cs.shape[0] != 26 or min(cf.shape[1], cs.shape[1]) < 3:
                raise ValueError('公制重建需要 HALPE 26 點及每點信心值。')
            candidates = (np.isfinite(cf[:, :3]).all(axis=1) & np.isfinite(cs[:, :3]).all(axis=1)
                          & (cf[:, 2] >= .5) & (cs[:, 2] >= .5))
            xyz = np.full((26, 3), np.nan)
            error, angle = np.full(26, np.nan), np.full(26, np.nan)
            valid = np.zeros(26, bool)
            indices = np.flatnonzero(candidates)
            if len(indices):
                # One degenerate correspondence must not discard other joints.
                for idx in indices:
                    try:
                        points, positive, errors, angles = triangulate(
                            cf[idx:idx+1, :2], cs[idx:idx+1, :2], self.rig['intrinsics'],
                            np.asarray(self.rig['R_cs_from_cf']), np.asarray(self.rig['t_cs_from_cf_mm']))
                    except ValueError:
                        continue
                    accepted = positive[0] and np.isfinite(points).all() and errors[0] <= 3. and angles[0] >= 1.
                    if accepted:
                        xyz[idx] = points[0] / 1000.
                    valid[idx] = accepted
                    error[idx], angle[idx] = errors[0], angles[0]
            self.all_3d_frames[frame_num] = xyz
            self.quality[frame_num] = {'valid': valid, 'reprojection_error_px': error, 'ray_angle_deg': angle}
        self.frame_idx = frame_num
        self.update()

    def update(self, event=None):
        points = self.all_3d_frames.get(self.frame_idx, np.full((26, 3), np.nan))
        if not self._camera_range_initialized and np.isfinite(points).all(axis=1).any():
            finite = points[np.isfinite(points).all(axis=1)]
            self.view.camera.set_range(**{axis: (float(finite[:, i].min() - .2), float(finite[:, i].max() + .2))
                                          for i, axis in enumerate(('x', 'y', 'z'))})
            self._camera_range_initialized = True
        for visual, links in ((self.head_lines, self.head_links), (self.left_lines, self.left_links),
                              (self.right_lines, self.right_links), (self.trunk_lines, self.trunk_links)):
            segments = [points[pair] for pair in links if np.isfinite(points[pair]).all()]
            visual.set_data(pos=np.asarray(segments, dtype=float).reshape(-1, 3), connect='segments')

    def set_frame(self, frame_idx):
        self.frame_idx = frame_idx
        self.update()

    def reset(self):
        super().reset()
        self.quality.clear()
        self.update()

    def displacement(self, frame_num, target_joint=None):
        origin = self.all_3d_frames.get(self.reference_frame)
        target = self.all_3d_frames.get(frame_num)
        if origin is None or target is None:
            return None
        value = np.linalg.norm(target[self.heel if target_joint is None else target_joint] - origin[self.heel])
        return float(value) if np.isfinite(value) else None

    def compute_joint_velocity_causal(self, joint_idx, delta_t, frame_idx=None, ema_alpha=.3, window=5):
        # A missing frame or rejected joint breaks the derivative; never bridge a gap.
        keys = sorted(self.all_3d_frames)
        speeds = []
        for key in keys:
            current, previous = self.all_3d_frames[key], self.all_3d_frames.get(key - 1)
            value = np.nan if previous is None else np.linalg.norm(current[joint_idx] - previous[joint_idx]) * self.fps
            speeds.append(float(value))
        if frame_idx is None:
            return np.asarray(speeds)
        return speeds[keys.index(frame_idx)] if frame_idx in keys else float('nan')

    compute_joint_velocity = compute_joint_velocity_causal

    def compute_ankle_x_diff(self, frame_idx=None, left_idx=15, right_idx=16, absolute=False):
        pts = self.all_3d_frames.get(self.frame_idx if frame_idx is None else frame_idx)
        if pts is None:
            return float('nan')
        value = pts[left_idx, 0] - pts[right_idx, 0]
        return float(abs(value) if absolute else value)

    def arm_cocking_angle(self, frame_idx=None):
        p = self.all_3d_frames.get(frame_idx)
        if p is None or not np.isfinite(p[[6, 8, 10, 18, 19]]).all():
            return float('nan')
        normal, torso = np.cross(p[6] - p[8], p[10] - p[8]), p[19] - p[18]
        norm = np.linalg.norm(normal) * np.linalg.norm(torso)
        return float(np.degrees(np.arccos(np.clip(np.dot(normal, torso) / norm, -1, 1)))) if norm > 1e-12 else float('nan')

    def calculate_angle_between_shoulder_hip_lines_signed(self, frame_idx=None):
        p = self.all_3d_frames.get(frame_idx)
        if p is None or not np.isfinite(p[[5, 6, 11, 12, 18, 19]]).all():
            return float('nan')
        axis = p[18] - p[19]
        norm = np.linalg.norm(axis)
        if norm < 1e-12:
            return float('nan')
        axis /= norm
        shoulder, hip = p[6] - p[5], p[12] - p[11]
        shoulder -= np.dot(shoulder, axis) * axis
        hip -= np.dot(hip, axis) * axis
        if min(np.linalg.norm(shoulder), np.linalg.norm(hip)) < 1e-12:
            return float('nan')
        return float(np.degrees(np.arctan2(np.dot(axis, np.cross(hip, shoulder)), np.dot(hip, shoulder))))

    calculate_angle_between_shoulder_hip_lines = calculate_angle_between_shoulder_hip_lines_signed

    def provenance(self):
        return {**getattr(self, 'analysis_context', {}),
                'method': self.rig['method'], 'units': 'm', 'coordinate_frame': 'CF camera',
                'calibration_path': str(self.rig_path), 'calibration_sha256': self.rig_sha256,
                'calibration_snapshot': self.rig, 'fps': self.fps, 'first_camera': self.first_camera,
                'reference_frame': self.reference_frame, 'heel_halpe_index': self.heel,
                'measurement_definition': DEFINITION, 'distance_type': '3D Euclidean; not ground-plane distance',
                'extension_definition': 'initial rear heel to release right wrist, 3D Euclidean',
                'velocity_definition': 'backward difference between consecutive accepted frames; m/s',
                'shoulder_angle_definition': 'forearm/upper-arm plane normal vs torso; not clinical external rotation',
                'shoulder_hip_angle_definition': 'signed angle projected perpendicular to torso axis',
                'quality_limits': {'confidence_min': .5, 'reprojection_max_px': 3., 'ray_angle_min_deg': 1.}}

    def save_to_json(self, path=None):
        atomic_json(path or self.save_json_path, {'schema': 'metric_pose_3d_v1', **self.provenance(),
                    'frames': [{'frame_number': key, 'keypoints': points, 'quality': self.quality[key]}
                               for key, points in sorted(self.all_3d_frames.items())]})
