"""Independent extrinsic calibration with known geometry and no video inputs."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import calibrate_chessboard_extrinsic as cli
from cv_utils.chessboard_extrinsic import fit_chessboard_rig, load_rig
from cv_utils.chessboard_metric import object_points, read_json, write_json


class ChessboardExtrinsicTests(unittest.TestCase):
    def setUp(self):
        cv2.setNumThreads(1)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calibration_dir = self.root / 'intrinsic'
        self.K = np.array([[1300., 0, 960], [0, 1280., 542], [0, 0, 1]])
        camera = {'intrinsic_matrix': self.K.tolist(), 'distortion_coefficients': [0.] * 5,
                  'image_size': [1920, 1084], 'warnings': []}
        self.calibration = {'schema': 'chessboard_intrinsics_v1', 'units': 'mm',
                            'pattern': [7, 7], 'square_mm': 100., 'input': 'intrinsic_photos',
                            'cameras': {c: copy.deepcopy(camera) for c in ('cf', 'cs')}}
        self.detections = {'pattern': [7, 7], 'input': 'intrinsic_photos',
                           'cameras': {c: {'image_size': [1920, 1084], 'views': [], 'failed': [],
                                           'total_images': 12} for c in ('cf', 'cs')}}
        self.R = cv2.Rodrigues(np.array([.04, -.19, .02]))[0]
        self.t = np.array([1100., 40., 200.])
        obj = object_points()
        rng = np.random.default_rng(34)
        for i in range(12):
            rotation = cv2.Rodrigues(rng.uniform(-.45, .45, 3))[0]
            xyz = obj @ rotation.T + rng.uniform([-500, -500, 3200], [100, 0, 6200])
            for c in ('cf', 'cs'):
                R, t = (np.eye(3), np.zeros(3)) if c == 'cf' else (self.R, self.t)
                pixels = cv2.projectPoints(xyz, cv2.Rodrigues(R)[0], t, self.K, None)[0].reshape(-1, 2)
                self.detections['cameras'][c]['views'].append({'name': f'{i}.png', 'corners': pixels.tolist()})
        write_json(self.calibration_dir / 'intrinsics.json', self.calibration)
        write_json(self.calibration_dir / 'detections.json', self.detections)

    def test_both_cli_outputs_work_without_videos_and_leave_intrinsics_unchanged(self):
        before = {p.name: p.read_bytes() for p in self.calibration_dir.iterdir()}
        output = self.root / 'extrinsic'
        with patch('cv2.VideoCapture', side_effect=AssertionError('No videos during calibration')):
            cli.main(['--calibration', str(self.calibration_dir), '--output', str(output), '--method', 'both'])
        for method in ('pnp', '8point'):
            rig = load_rig(output / method / 'stereo.json')
            self.assertEqual(rig['method'], method)
            np.testing.assert_allclose(rig['R_cs_from_cf'], self.R, atol=1e-4)
            np.testing.assert_allclose(rig['t_cs_from_cf_mm'], self.t, atol=.1)
            fitted = set(rig['training_pairs'])
            held = {v['name'] for v in rig['held_out_board_checks']}
            self.assertTrue(fitted and held)
            self.assertFalse(fitted & held)
            self.assertTrue((output / method / 'calibration_summary.txt').is_file())
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.calibration_dir.iterdir()})
        self.assertFalse(list(output.rglob('video_audit.json')))

    def test_separate_extrinsic_photos_need_no_intrinsic_detection_cache(self):
        (self.calibration_dir / 'detections.json').unlink()
        images = self.root / 'separate_photos'
        output = self.root / 'extrinsic'
        def detect(folder, pattern, preview, interval):
            self.assertEqual(folder.parent, images)
            self.assertEqual(pattern, (7, 7))
            return self.detections['cameras'][folder.name]
        with patch.object(cli, 'detect_folder', side_effect=detect) as detector:
            cli.main(['--calibration', str(self.calibration_dir), '--images', str(images),
                      '--output', str(output), '--method', 'pnp', '--preview-every', '0'])
        self.assertEqual(detector.call_count, 2)
        rig = load_rig(output / 'pnp/stereo.json')
        self.assertEqual(rig['source_images'], str(images.resolve()))
        self.assertEqual(read_json(output / 'extrinsic_detections.json')['input'], str(images.resolve()))
        self.assertFalse((self.calibration_dir / 'detections.json').exists())

    def test_rejects_wrong_photo_size_pattern_or_insufficient_pairs(self):
        wrong_size = copy.deepcopy(self.detections)
        wrong_size['cameras']['cs']['image_size'] = [960, 542]
        with self.assertRaisesRegex(ValueError, 'image size'):
            fit_chessboard_rig(self.calibration, wrong_size, 'pnp')
        wrong_pattern = copy.deepcopy(self.detections)
        wrong_pattern['pattern'] = [8, 11]
        with self.assertRaisesRegex(ValueError, 'pattern mismatch'):
            fit_chessboard_rig(self.calibration, wrong_pattern, 'pnp')
        too_few = copy.deepcopy(self.detections)
        for camera in too_few['cameras'].values():
            camera['views'] = camera['views'][:2]
        with self.assertRaisesRegex(ValueError, 'synchronized'):
            fit_chessboard_rig(self.calibration, too_few, 'pnp')


if __name__ == '__main__':
    unittest.main()
