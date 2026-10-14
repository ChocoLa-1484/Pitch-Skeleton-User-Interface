"""Known-geometry tests: metric scale, distortion, planar degeneracy and QC."""
import sys
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from Src.calibration.chessboard_cross.chessboard_metric import (align_pairs, board_edges, camera_arrays,
    estimate_eight_point, fit_eight_point, fit_pnp, object_points, triangulate)
from Src.analysis.stride_workflow import measure_annotations, pose_heel
from Src.calibration.chessboard_cross.chessboard_metric import detect_folder


class ChessboardDetectionTests(unittest.TestCase):
    def test_normalized_detection_retries_only_failures_and_records_used_flags(self):
        points = np.mgrid[0:7, 0:7].T.reshape(-1, 1, 2).astype(np.float32) * 20 + 40
        normal = cv2.CALIB_CB_NORMALIZE_IMAGE
        exhaustive = normal | cv2.CALIB_CB_EXHAUSTIVE
        calls = []

        def detect(gray, pattern, *, flags):
            # Keyword-only flags catch the Python binding mistake. Images 1,
            # 2 and 3 model immediate success, retry success and total failure.
            image_id = int(gray[0, 0])
            calls.append((image_id, flags))
            success = image_id == 1 or (image_id == 2 and flags == exhaustive)
            return success, points.copy() if success else None

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for image_id in (1, 2, 3):
                (root / f'{image_id}.png').touch()
            with patch('Src.calibration.chessboard_cross.chessboard_metric.read_image', side_effect=lambda p:
                       np.full((240, 320, 3), int(p.stem), np.uint8)), \
                 patch('Src.calibration.chessboard_cross.chessboard_metric.cv2.findChessboardCornersSB', side_effect=detect):
                result = detect_folder(root, (7, 7), root / 'previews', preview_every=0)
            self.assertFalse((root / 'previews').exists())

        self.assertEqual(calls, [(1, normal), (2, normal), (2, exhaustive),
                                 (3, normal), (3, exhaustive)])
        self.assertEqual(result['total_images'], 3)
        self.assertEqual(result['failed'], ['3.png'])
        self.assertEqual([v['name'] for v in result['views']], ['1.png', '2.png'])
        self.assertEqual([v['detection_flags'] for v in result['views']], [normal, exhaustive])
        for view in result['views']:
            self.assertEqual(view['detector'], 'findChessboardCornersSB')
            self.assertEqual(np.asarray(view['corners']).shape, (49, 2))


class MetricGeometryTests(unittest.TestCase):
    def setUp(self):
        cv2.setNumThreads(1)
        self.cameras = {
            "cf": {"intrinsic_matrix": [[1300., 0, 960], [0, 1280., 542], [0, 0, 1]],
                   "distortion_coefficients": [-.08, .015, .001, -.002, 0.], "image_size": [1920, 1084]},
            "cs": {"intrinsic_matrix": [[1450., 0, 930], [0, 1440., 560], [0, 0, 1]],
                   "distortion_coefficients": [-.04, .005, -.002, .001, 0.], "image_size": [1920, 1084]}}
        self.R = cv2.Rodrigues(np.array([.04, -.19, .02]))[0]
        self.t = np.array([1100., 40., 200.])
        self.obj = object_points()
        self.pairs = []
        rng = np.random.default_rng(34)
        for i in range(12):
            rotation = cv2.Rodrigues(rng.uniform(-.45, .45, 3))[0]
            trans = rng.uniform([-500, -500, 3200], [100, 0, 6200])
            xyz = self.obj @ rotation.T + trans
            self.pairs.append({"name": str(i), "cf": self.project(xyz, "cf"),
                               "cs": self.project(xyz, "cs"), "cs_order": 0})

    def project(self, xyz, camera):
        K, dist = camera_arrays(self.cameras[camera])
        R, t = (np.eye(3), np.zeros(3)) if camera == "cf" else (self.R, self.t)
        return cv2.projectPoints(np.array(xyz, float), cv2.Rodrigues(R)[0], t, K, dist)[0].reshape(-1, 2)

    def annotations(self, xyz):
        return {"schema": "stride_annotations_v1", "pixel_space": "original_distorted_pixels",
                "frame_cf": 95, "frame_cs": 97, "reference_description": "Synthetic rubber point",
                "image_sizes": {c: self.cameras[c]["image_size"] for c in self.cameras},
                "points": {name: {c: self.project([p], c)[0].tolist() for c in self.cameras} for name, p in xyz.items()}}

    def test_triangulation_undoes_distortion_and_preserves_mm(self):
        xyz = np.array([[-100., 120, 3600], [300, -100, 4100], [50, 320, 5000]])
        actual, positive, errors, angles = triangulate(self.project(xyz, "cf"), self.project(xyz, "cs"), self.cameras, self.R, self.t)
        np.testing.assert_allclose(actual, xyz, atol=1e-4)
        self.assertTrue(positive.all())
        self.assertLess(max(errors), 1e-4)
        self.assertGreater(min(angles), 1.)

    def test_pnp_rig_recovers_metric_baseline(self):
        R, t, info = fit_pnp(self.pairs, self.cameras, (7, 7), 100.)
        np.testing.assert_allclose(R, self.R, atol=1e-5)
        np.testing.assert_allclose(t, self.t, atol=.02)
        self.assertLess(info["stereo_rms_px"], .001)

    def test_eight_point_recovers_scale_without_pnp_pose(self):
        with patch("Src.calibration.chessboard_cross.chessboard_metric.solve_board_pose", side_effect=AssertionError("PnP must not fit 8point pose/scale")):
            R, t, info = fit_eight_point(self.pairs, self.cameras, (7, 7), 100.)
        np.testing.assert_allclose(R, self.R, atol=1e-4)
        np.testing.assert_allclose(t, self.t, atol=.1)
        self.assertLess(info["scale_relative_mad"], 1e-4)

    def test_scale_changes_only_with_known_square_size(self):
        _, t1, _ = fit_eight_point(self.pairs, self.cameras, (7, 7), 100.)
        _, t2, _ = fit_eight_point(self.pairs, self.cameras, (7, 7), 50.)
        np.testing.assert_allclose(t2, t1 / 2, atol=1e-5)

    def test_single_planar_board_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "degeneracy"):
            estimate_eight_point(self.pairs[0]["cf"], self.pairs[0]["cs"], self.cameras)

    def test_square_board_order_changes_are_resolved(self):
        scrambled = []
        for i, p in enumerate(self.pairs):
            grid = p["cs"].reshape(7, 7, 2)
            scrambled.append({**p, "cs": np.rot90(grid, i % 4).reshape(-1, 2)})
        aligned, rejected = align_pairs(scrambled, self.cameras, (7, 7), 100.)
        self.assertEqual(rejected, [])
        for p in aligned:
            np.testing.assert_allclose(p["pnp_R"], self.R, atol=1e-4)
            np.testing.assert_allclose(p["pnp_t_mm"], self.t, atol=.1)

    def test_horizontal_distance_does_not_assume_camera_xy_is_ground(self):
        document = self.annotations({"reference": [0, 0, 5000], "heel": [650, 350, 5000],
            "ground0": [-400, 0, 4600], "ground1": [400, 0, 4600], "ground2": [-400, 0, 5400]})
        result = measure_annotations(document, self.cameras, self.R, self.t)
        self.assertAlmostEqual(result["ground_distance_cm"], 65., places=4)
        self.assertAlmostEqual(result["absolute_height_difference_mm"], 350., places=3)
        self.assertGreater(result["distance_3d_cm"], 65.)

    def test_missing_ground_returns_3d_only(self):
        document = self.annotations({"reference": [0, 0, 5000], "heel": [650, 350, 5000]})
        result = measure_annotations(document, self.cameras, self.R, self.t)
        self.assertIsNone(result["ground_distance_cm"])

    def test_reference_and_contact_frames_remain_separate(self):
        document = self.annotations({"reference": [0, 0, 5000], "heel": [650, 0, 5000]})
        document.update(reference_frame_cf=0, reference_frame_cs=2)
        result = measure_annotations(document, self.cameras, self.R, self.t)
        self.assertEqual((result["reference_frame_cf"], result["reference_frame_cs"]), (0, 2))
        self.assertEqual((result["frame_cf"], result["frame_cs"]), (95, 97))
        self.assertAlmostEqual(result["distance_3d_cm"], 65., places=4)

    def test_sparse_pose_json_matches_frame_number_not_array_index(self):
        k0, k138 = [[100., 200., .9] for _ in range(26)], [[300., 400., .9] for _ in range(26)]
        records = [{"frame_number": 0, "keypoints": k0}, {"frame_number": 138, "keypoints": k138}]
        with patch("Src.analysis.stride_workflow.read_json", return_value=records):
            self.assertEqual(pose_heel(Path("sparse.json"), 138, "right"), [300., 400.])
            with self.assertRaisesRegex(ValueError, "frame 95"):
                pose_heel(Path("sparse.json"), 95, "right")

    def test_ambiguous_people_are_not_silently_selected(self):
        records = [{"frame_number": 0, "keypoints": [[1., 2., .9]]*26}]*2
        with patch("Src.analysis.stride_workflow.read_json", return_value=records):
            with self.assertRaisesRegex(ValueError, "exactly one person"):
                pose_heel(Path("people.json"), 0, "right")

    def test_bad_correspondence_is_rejected(self):
        document = self.annotations({"reference": [0, 0, 5000], "heel": [650, 0, 5000]})
        document["points"]["heel"]["cs"][1] += 80
        with self.assertRaisesRegex(ValueError, "Unreliable"):
            measure_annotations(document, self.cameras, self.R, self.t)

    def test_resized_annotations_are_rejected(self):
        document = self.annotations({"reference": [0, 0, 5000], "heel": [650, 0, 5000]})
        document["image_sizes"]["cf"] = [960, 542]
        with self.assertRaisesRegex(ValueError, "dimensions"):
            measure_annotations(document, self.cameras, self.R, self.t)

    def test_null_template_points_are_rejected(self):
        document = self.annotations({"reference": [0, 0, 5000], "heel": [650, 0, 5000]})
        document["points"]["heel"]["cf"] = None
        with self.assertRaisesRegex(ValueError, "Missing/invalid"):
            measure_annotations(document, self.cameras, self.R, self.t)

    def test_board_size_means_seven_inner_corners_six_intervals(self):
        self.assertEqual(self.obj.shape, (49, 3))
        np.testing.assert_array_equal(self.obj[-1], [600., 600., 0.])
        np.testing.assert_allclose(board_edges(self.obj, (7, 7)), 100.)


if __name__ == "__main__":
    unittest.main()
