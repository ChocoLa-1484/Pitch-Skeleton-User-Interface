"""Regression checks for independent stereo samples and capture guidance."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from Src.UI_Control.tests.test_two_rod_calibration import fixture
from Src.calibration.two_rod_calibration import (IDS, coverage_report,
                                                fit_two_rod_rig, rod_target)


class TwoRodReviewTests(unittest.TestCase):
    def test_renamed_repeated_captures_cannot_count_as_independent_samples(self):
        cameras, original, _, _ = fixture()
        # Previously this passed: all four holdouts were the same physical
        # capture, and each training capture appeared four times.
        pairs = [dict(copy.deepcopy(original[index % 5]), id=str(index))
                 for index in range(20)]
        with patch('Src.calibration.two_rod_calibration.estimate_eight_point') as solve:
            with self.assertRaisesRegex(ValueError, '重複.*15 組不同姿態'):
                fit_two_rod_rig(cameras, rod_target(), pairs)
        solve.assert_not_called()

    def test_marker_jitter_does_not_allow_duplicate_to_leak_into_holdout(self):
        cameras, pairs, _, _ = fixture()
        duplicate = dict(copy.deepcopy(pairs[1]), id='renamed-copy')
        rng = np.random.default_rng(475)
        for role in ('cf', 'cs'):
            for key in IDS:
                duplicate[role]['points'][key] = (
                    np.array(duplicate[role]['points'][key]) + rng.normal(0, .15, 2)).tolist()
        # Index 20 is held out; the original at index 1 is used to fit.
        pairs.append(duplicate)
        with self.assertRaisesRegex(ValueError, 'renamed-copy ≈ 1'):
            fit_two_rod_rig(cameras, rod_target(), pairs)

    def test_excluding_duplicate_restores_valid_fit_and_original_holdout_split(self):
        cameras, pairs, R, t = fixture()
        pairs.append(dict(copy.deepcopy(pairs[0]), id='excluded-copy', excluded=True))
        result = fit_two_rod_rig(cameras, rod_target(), pairs)
        np.testing.assert_allclose(result['R_cs_from_cf'], R, atol=1e-4)
        np.testing.assert_allclose(result['t_cs_from_cf_mm'], t, atol=.1)
        self.assertEqual(result['held_out_pairs'], ['0', '5', '10', '15'])
        self.assertEqual([d['id'] for d in result['candidate_diagnostics'] if d['held_out']],
                         result['held_out_pairs'])
        self.assertTrue(all(d['accepted'] for d in result['candidate_diagnostics']))

    def test_different_rod_sizes_in_one_capture_are_not_distance_variation(self):
        cameras, pairs, _, _ = fixture()
        pair = pairs[0]
        for role in ('cf', 'cs'):
            # One stationary rod is closer than the other. Repeating this
            # capture must still ask the operator to vary the distance.
            for key in IDS[4:]:
                pixel = pair[role]['points'][key]
                pair[role]['points'][key] = [pixel[0], 540 + (pixel[1] - 540)*2]
        for captures in ([pair], [pair]*15):
            report = coverage_report(captures, cameras)
            for role in ('cf', 'cs'):
                self.assertTrue(report[role]['need_distance_variation'])
                self.assertEqual(report[role]['apparent_size_ratio'], 1.)
                self.assertEqual(report[role]['apparent_size_ratio_by_rod'], {'A': 1., 'B': 1.})

    def test_size_change_of_same_rod_across_captures_counts(self):
        cameras, pairs, _, _ = fixture()
        first = pairs[0]
        second = dict(copy.deepcopy(first), id='changed-distance')
        for role in ('cf', 'cs'):
            pixels = np.array([first[role]['points'][key] for key in IDS[:4]])
            center = pixels.mean(axis=0)
            for key, pixel in zip(IDS[:4], center + 1.5*(pixels-center)):
                second[role]['points'][key] = pixel.tolist()
        report = coverage_report([first, second], cameras)
        for role in ('cf', 'cs'):
            self.assertFalse(report[role]['need_distance_variation'])
            self.assertAlmostEqual(report[role]['apparent_size_ratio_by_rod']['A'], 1.5)
            self.assertAlmostEqual(report[role]['apparent_size_ratio_by_rod']['B'], 1.)
        second['excluded'] = True
        self.assertTrue(coverage_report([first, second], cameras)['cf']['need_distance_variation'])


if __name__ == '__main__':
    unittest.main()
