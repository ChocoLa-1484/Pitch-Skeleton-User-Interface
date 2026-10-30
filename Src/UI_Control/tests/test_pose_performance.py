"""Pose smoothing/caching must preserve data through inference and editing."""
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


class PoseProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from Src.pose.detect_skeleton import PoseEstimater
        from Src.pose.one_euro_filter import OneEuroFilter
        from mmengine.structures import InstanceData
        import torch
        cls.Estimator = PoseEstimater
        cls.Filter = OneEuroFilter
        cls.InstanceData = InstanceData
        cls.torch = torch

    def setUp(self):
        self.estimator = self.Estimator()

    @staticmethod
    def points(offset=0.):
        return np.column_stack((np.arange(26) * 5. + 100. + offset,
                                np.arange(26) * 3. + 80. - offset,
                                np.ones(26) * .95, np.zeros(26)))

    def predictions(self, points):
        result = self.InstanceData()
        result.bboxes = np.array([[20., 20., 400., 500.]])
        result.keypoints = np.array([points[:, :2]])
        result.keypoint_scores = np.array([points[:, 2]])
        return result

    def test_cpu_smoothing_matches_original_scalar_order_and_keeps_metadata(self):
        previous, current = self.points(), self.points(7.)
        previous[4, 0] = 0  # Zero coordinates retain the original skip rule.
        current[9, 3] = 1
        self.estimator.setProcessedData(pd.DataFrame([
            dict(frame_number=0, person_id=1, keypoints=previous),
            dict(frame_number=1, person_id=1, keypoints=current)]))
        # This is the original tensor math, run on CPU for portable regression.
        reference_filter = self.Filter()
        expected = self.torch.tensor(current.copy(), dtype=self.torch.float64)
        for before, after in zip(self.torch.tensor(previous), expected):
            if all(value.item() != 0 for value in (before[0], before[1], after[0], after[1])):
                after[0] = reference_filter(after[0].clone(), before[0])
                after[1] = reference_filter(after[1].clone(), before[1])
        with patch.object(self.torch, 'tensor', side_effect=AssertionError('no GPU/scalar tensors')):
            self.estimator.smoothKpt([1], 1)
        actual = self.estimator.getPersonDf(1, is_kpt=True)
        np.testing.assert_allclose(actual, expected.numpy(), rtol=0, atol=1e-10)
        np.testing.assert_array_equal(np.asarray(actual)[:, 2:], current[:, 2:])

    def test_smoothed_history_survives_the_next_frame_and_exports_agree(self):
        self.estimator.setDetect(True)
        with patch.object(self.estimator, 'processImage', side_effect=[
                (self.predictions(self.points(offset)), [1]) for offset in (0., 7., 12.)]):
            self.estimator.detectKpt(None, 0, is_video=True)
            self.estimator.detectKpt(None, 1, is_video=True)
            smoothed = np.array(self.estimator.getPersonDf(1, is_kpt=True))
            self.assertFalse(np.array_equal(smoothed[:, :2], self.points(7.)[:, :2]))
            self.estimator.detectKpt(None, 2, is_video=True)
        np.testing.assert_array_equal(self.estimator.getPersonDf(1, is_kpt=True), smoothed)
        np.testing.assert_array_equal(self.estimator.person_data[1]['keypoints'], smoothed)
        np.testing.assert_array_equal(self.estimator.person_df.iloc[1]['keypoints'], smoothed)

    def test_manual_edit_invalidates_revision_and_survives_append(self):
        self.estimator.setProcessedData(pd.DataFrame([
            dict(frame_number=0, person_id=1, keypoints=self.points())]))
        self.estimator.setPersonId(1)
        revision = self.estimator.pose_revision
        self.estimator.update_person_df(123., 456., 0, 10)
        self.assertGreater(self.estimator.pose_revision, revision)
        self.estimator.person_df = self.estimator.mergePersonData(
            self.predictions(self.points(4.)), [1], 1)
        self.assertEqual(self.estimator.getPersonDf(0, is_kpt=True)[10], [123., 456., .9, 1.])
        self.assertEqual(self.estimator.person_data[0]['keypoints'][10], [123., 456., .9, 1.])

    def test_frame_lookup_handles_nonstandard_loaded_index_and_missing_frames(self):
        self.estimator.setProcessedData(pd.DataFrame([
            dict(frame_number=20, person_id=1, keypoints=self.points()),
            dict(frame_number=21, person_id=1, keypoints=self.points(4.))], index=[7, 19]))
        self.assertIsNone(self.estimator.getPersonDf(22))
        np.testing.assert_array_equal(self.estimator.getPersonDf(21, is_kpt=True), self.points(4.))
        self.estimator.setPersonId(1)
        self.estimator.update_person_df(7., 8., 21, 9)
        self.assertEqual(self.estimator.getPersonDf(21, is_kpt=True)[9][:2], [7., 8.])

    def test_external_timeline_replacement_does_not_reuse_old_frame_cache(self):
        self.estimator.setProcessedData(pd.DataFrame([
            dict(frame_number=0, person_id=1, keypoints=self.points())]))
        self.estimator.person_df = pd.DataFrame([
            dict(frame_number=0, person_id=1, keypoints=self.points(25.))])
        np.testing.assert_array_equal(self.estimator.getPersonDf(0, is_kpt=True), self.points(25.))

    def test_repeated_queries_do_not_change_revision_or_call_inference(self):
        self.estimator.setProcessedData(pd.DataFrame([
            dict(frame_number=0, person_id=1, keypoints=self.points())]))
        self.estimator.setDetect(True)
        revision = self.estimator.pose_revision
        with patch.object(self.estimator, 'processImage') as infer:
            for _ in range(3):
                self.estimator.detectKpt(None, 0, is_video=True)
                self.estimator.getPersonDf(0)
        infer.assert_not_called()
        self.assertEqual(self.estimator.pose_revision, revision)
        self.estimator.reset()
        self.assertGreater(self.estimator.pose_revision, revision)
        self.assertTrue(self.estimator.person_df.empty)
        self.assertFalse(self.estimator.person_df_by_frame)


if __name__ == '__main__':
    unittest.main()
