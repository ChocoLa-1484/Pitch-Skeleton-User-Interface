"""Single-pitcher selection and the production estimator's missing-frame path."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skeleton.pitcher_target import PitcherTargetSelector


class PitcherTargetTests(unittest.TestCase):
    shape = (1000, 1000, 3)
    pitcher = [400, 200, 600, 800]
    bystander = [0, 50, 300, 950]

    def setUp(self):
        self.selector = PitcherTargetSelector()

    def test_prefers_central_pitcher_over_larger_bystander(self):
        self.assertEqual(self.selector.select([self.bystander, self.pitcher], self.shape),
                         [self.pitcher])

    def test_no_acquisition_at_edge_or_from_tiny_noise(self):
        self.assertEqual(self.selector.select([self.bystander, [498, 490, 502, 510]], self.shape), [])

    def test_selection_does_not_depend_on_detection_order(self):
        self.selector.select([self.pitcher, self.bystander], self.shape)
        moved = [420, 210, 620, 810]
        self.assertEqual(self.selector.select([self.bystander, moved], self.shape), [moved])

    def test_pitcher_can_stride_outside_acquisition_region(self):
        self.selector.select([self.pitcher], self.shape)
        for shift in range(50, 351, 50):
            moved = [400 + shift, 200, 600 + shift, 800]
            self.assertEqual(self.selector.select([self.bystander, moved], self.shape), [moved])
        # A newcomer at the center must not steal an already acquired target.
        self.assertEqual(self.selector.select([self.pitcher, moved], self.shape), [moved])

    def test_short_dropout_returns_no_pose_and_recovers_same_target(self):
        self.selector.select([self.pitcher], self.shape)
        for boxes in ([], [self.bystander], []):
            self.assertEqual(self.selector.select(boxes, self.shape), [])
        moved = [430, 190, 660, 810]
        self.assertEqual(self.selector.select([self.bystander, moved], self.shape), [moved])

    def test_rejects_sudden_jump_even_with_large_overlap(self):
        self.selector.select([self.pitcher], self.shape)
        self.assertEqual(self.selector.select([[0, 0, 1000, 1000]], self.shape), [])

    def test_long_dropout_reacquires_only_from_center(self):
        self.selector.select([self.pitcher], self.shape)
        for _ in range(self.selector.max_lost_frames):
            self.assertEqual(self.selector.select([self.bystander], self.shape), [])
        self.assertIsNone(self.selector.last_bbox)
        self.assertEqual(self.selector.select([self.bystander], self.shape), [])
        next_pitcher = [600, 250, 800, 750]
        self.assertEqual(self.selector.select([next_pitcher], self.shape), [next_pitcher])

    def test_relative_rules_work_at_different_resolutions(self):
        for scale in (0.5, 2):
            selector = PitcherTargetSelector()
            boxes = np.array([self.bystander, self.pitcher]) * scale
            self.assertEqual(selector.select(boxes, (int(1000 * scale), int(1000 * scale))),
                             [boxes[1].tolist()])

    def test_invalid_boxes_and_empty_input_are_safe(self):
        for boxes in ([], [[np.nan, 0, 500, 900]], [[600, 800, 400, 200]],
                      [[-100, -100, -10, -10]]):
            self.assertEqual(self.selector.select(boxes, self.shape), [])

    def test_reset_and_resolution_changes_do_not_reuse_old_lock(self):
        self.selector.select([self.pitcher], self.shape)
        self.selector.reset()
        self.assertIsNone(self.selector.last_bbox)
        self.selector.select([self.pitcher], self.shape)
        self.assertEqual(self.selector.select([[200, 100, 300, 400]], (500, 500)),
                         [[200, 100, 300, 400]])


class PitcherInferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from skeleton import detect_skeleton
        from mmengine.structures import InstanceData
        import torch
        cls.module, cls.instances, cls.torch = detect_skeleton, InstanceData, torch

    def setUp(self):
        self.estimator = self.module.PoseEstimater(SimpleNamespace(
            detector=object(), detector_test_pipeline=object(), pose_estimator=object(),
            detect_args=SimpleNamespace(score_thr=0.5, det_cat_id=0, nms_thr=0.5)))
        self.image = np.zeros((1000, 1000, 3), dtype=np.uint8)
        self.estimator.setDetect(True)

    def detections(self, boxes):
        data = self.instances()
        data.bboxes = self.torch.tensor(boxes, dtype=self.torch.float32).reshape(-1, 4)
        data.scores = self.torch.full((len(boxes),), 0.95)
        data.labels = self.torch.zeros(len(boxes), dtype=self.torch.int64)
        return SimpleNamespace(pred_instances=data)

    def pose(self, box):
        data = self.instances()
        data.bboxes = np.array([box], dtype=np.float32)
        data.keypoints = np.ones((1, 26, 2), dtype=np.float32) * 300
        data.keypoint_scores = np.ones((1, 26), dtype=np.float32)
        return data

    def test_no_target_never_calls_full_frame_pose_inference(self):
        for boxes in ([], [[0, 50, 300, 950]]):
            with patch.object(self.module, 'inference_detector', return_value=self.detections(boxes)), \
                 patch.object(self.module, 'inference_topdown') as infer:
                _, df, _ = self.estimator.detectKpt(self.image)
                self.assertTrue(df.empty)
                infer.assert_not_called()

    def test_manual_logical_id_does_not_filter_by_detection_position(self):
        pitcher = [400, 200, 600, 800]
        boxes = [[0, 50, 300, 950], pitcher]
        pred = self.pose(pitcher)
        with patch.object(self.module, 'inference_detector', return_value=self.detections(boxes)), \
             patch.object(self.module, 'inference_topdown', return_value=[object()]) as infer, \
             patch.object(self.module, 'merge_data_samples', return_value={'pred_instances': pred}):
            _, ids = self.estimator.processImage(self.image, select_id=1)
            self.assertEqual(ids, [1])
            np.testing.assert_array_equal(infer.call_args[0][2], [pitcher])

    def test_live_dropout_clears_previous_skeleton(self):
        pred = self.pose([400, 200, 600, 800])
        with patch.object(self.estimator, 'processImage', side_effect=[(pred, [1]), (None, [])]):
            self.estimator.detectKpt(self.image)
            self.assertEqual(len(self.estimator.person_df), 1)
            self.estimator.detectKpt(self.image)
            self.assertTrue(self.estimator.person_df.empty)
            self.assertTrue(self.estimator.pre_person_df.empty)

    def test_video_dropout_preserves_history_but_has_no_current_pose(self):
        pred = self.pose([400, 200, 600, 800])
        with patch.object(self.estimator, 'processImage', side_effect=[(pred, [1]), (None, [])]):
            self.estimator.detectKpt(self.image, frame_num=0, is_video=True)
            self.estimator.detectKpt(self.image, frame_num=1, is_video=True)
        self.assertEqual(len(self.estimator.getPersonDf(0)), 1)
        self.assertIsNone(self.estimator.getPersonDf(1))
        self.assertTrue(self.estimator.person_df_by_frame[1].empty)
        self.assertEqual(self.estimator.processed_frames, {0, 1})

    def test_estimators_keep_independent_locks_and_reset_clears_lock(self):
        self.estimator.pitcher_target.select([[400, 200, 600, 800]], self.image.shape)
        other = self.module.PoseEstimater()
        self.assertIsNone(other.pitcher_target.last_bbox)
        self.estimator.reset()
        self.assertIsNone(self.estimator.pitcher_target.last_bbox)


if __name__ == '__main__':
    unittest.main()
