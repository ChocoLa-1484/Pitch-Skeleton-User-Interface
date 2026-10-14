"""Metric geometry and failure-mode tests; no physical cameras or Qt needed."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import cv2
import numpy as np

import Src.calibration.calibrate_cross as cli
from Src.calibration import cross_calibration as cross


def camera_at(center):
    center = np.array(center, float)
    forward = -center / np.linalg.norm(center)
    right = np.cross(forward, [0, 0, 1])
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    R = np.array([right, down, forward])
    return cv2.Rodrigues(R)[0], -R @ center


class CrossTests(unittest.TestCase):
    def setUp(self):
        self.world = cross.object_points()
        self.K = np.array([[1450., 0, 960], [0, 1430, 542], [0, 0, 1]])
        self.dist = np.array([-0.16, 0.08, 0.0002, -0.0001, -0.02])
        self.rv, self.tv = camera_at([700, -2600, 650])
        self.pixels = cv2.projectPoints(self.world, self.rv, self.tv, self.K, self.dist)[0].reshape(-1, 2)

    def test_world_units_and_depth_not_height(self):
        np.testing.assert_array_equal(self.world[:5, 2], [150, 100, 0, -150, -350])
        np.testing.assert_array_equal(self.world[5:], [[-350, 15, 0], [-150, 15, 0], [100, 15, 0]])
        np.testing.assert_array_equal(cross.object_points(-15)[5:, 1], [-15]*3)

    def test_metric_pose_and_ball_order_with_clutter_and_rotated_image(self):
        # A rotated image must not cause image-left/right numbering assumptions.
        angle = 0.9
        rotation = np.array([[np.cos(angle), -np.sin(angle), 0],
                             [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])
        rv = cv2.Rodrigues(rotation @ cv2.Rodrigues(self.rv)[0])[0]
        tv = rotation @ self.tv
        pixels = cv2.projectPoints(self.world, rv, tv, self.K, self.dist)[0].reshape(-1, 2)
        all_pixels = np.vstack((pixels, [[100, 100], [1700, 900]]))
        shuffle = np.random.RandomState(7).permutation(len(all_pixels))
        candidates = [{"center_px": p.tolist()} for p in all_pixels[shuffle]]
        fit = cross.label_and_solve(candidates, self.world, self.K, self.dist)
        self.assertTrue(fit["accepted"])
        self.assertLess(fit["rms_px"], 1e-5)
        np.testing.assert_allclose(fit["image_points_px"], pixels, atol=1e-5)
        np.testing.assert_allclose(fit["camera_center_world_mm"], [700, -2600, 650], atol=1e-3)
        json.dumps(fit, allow_nan=False)  # Includes labels/alternative poses.

    def test_hsv_touching_balls_and_wrapped_hue(self):
        hsv = np.zeros((160, 240, 3), np.uint8)
        locations = [(45, 35), (45, 56), (45, 100), (45, 135),
                     (90, 100), (125, 100), (160, 100), (200, 100)]
        for center in locations:
            cv2.circle(hsv, center, 11, (153, 170, 200), -1)
        # First two balls overlap; green circle is not a pink marker.
        cv2.circle(hsv, (150, 30), 11, (60, 255, 255), -1)
        image = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        candidates, mask = cross.detect_balls(image)
        self.assertEqual(len(candidates), 8)
        found = np.array([c["center_px"] for c in candidates])
        for center in locations:
            self.assertLess(np.min(np.linalg.norm(found-center, axis=1)), 1)
        self.assertTrue(any(c["split_component"] for c in candidates))
        self.assertEqual(mask[30, 150], 0)
        red = np.zeros((60, 60, 3), np.uint8)
        cv2.circle(red, (30, 30), 10, (0, 0, 255), -1)
        detected, _ = cross.detect_balls(red, cross.DetectionConfig(hue_min=170, hue_max=10))
        self.assertEqual(len(detected), 1)

    def test_static_fit_rejects_moved_frame_and_recovers_metric_scale(self):
        rng = np.random.RandomState(11)
        poses = {}
        for i in range(6):
            observations = self.pixels + rng.normal(0, 0.08, (8, 2))
            if i == 5:
                observations += [15, -5]
            fit = cross.solve_pose(self.world, observations, self.K, self.dist)
            fit.update(accepted=True, image_points_px=observations.tolist())
            poses[str(i)] = fit
        joint = cross.fit_static_camera(poses, self.world, self.K, self.dist)
        self.assertEqual(joint["excluded_motion_frames"], ["5"])
        self.assertLess(joint["rms_px"], 0.15)
        np.testing.assert_allclose(joint["camera_center_world_mm"], [700, -2600, 650], atol=8)
        with self.assertRaisesRegex(ValueError, "stationary"):
            for i, pose in enumerate(poses.values()):
                pose["image_points_px"] = (self.pixels+[i*20, 0]).tolist()
            cross.fit_static_camera(poses, self.world, self.K, self.dist)

    def test_stereo_triangulation_ground_and_transform_direction(self):
        rv_side, tv_side = camera_at([-2500, -600, 900])
        side_pixels = cv2.projectPoints(self.world, rv_side, tv_side, self.K, self.dist)[0].reshape(-1, 2)
        front = cross.solve_pose(self.world, self.pixels, self.K, self.dist)
        side = cross.solve_pose(self.world, side_pixels, self.K, self.dist)
        reconstructed = cross.triangulate_world(self.pixels, side_pixels, front, side,
                                               self.K, self.dist, self.K, self.dist)
        np.testing.assert_allclose(reconstructed, self.world, atol=1e-4)
        relative = cross.stereo_geometry(front, side, self.K, self.K)
        uf = cv2.undistortPoints(self.pixels.reshape(-1, 1, 2), self.K, self.dist, P=self.K).reshape(-1, 2)
        us = cv2.undistortPoints(side_pixels.reshape(-1, 1, 2), self.K, self.dist, P=self.K).reshape(-1, 2)
        residuals = np.sum(np.c_[uf, np.ones(8)] * (np.c_[us, np.ones(8)] @ np.array(relative["F_undistorted_side_to_front"]).T), axis=1)
        np.testing.assert_allclose(residuals, 0, atol=1e-7)
        ground = np.array([[0, 0, -500], [300, 400, -500]], float)
        projected = cv2.projectPoints(ground, self.rv, self.tv, self.K, self.dist)[0].reshape(-1, 2)
        for point, pixel in zip(ground, projected):
            np.testing.assert_allclose(cross.pixel_to_plane(pixel, front, self.K, self.dist, -500), point, atol=1e-4)
        self.assertAlmostEqual(cross.ground_distance_mm(*ground), 500)

    def test_rejects_incomplete_bad_geometry_and_wrong_resolution(self):
        entry = {"intrinsic_matrix": self.K.tolist(), "distortion_coefficients": self.dist.tolist(),
                 "image_size": [1920, 1084]}
        with self.assertRaisesRegex(ValueError, "size"):
            cross.camera_parameters(entry, (960, 542))
        with self.assertRaisesRegex(ValueError, "candidates"):
            cross.label_and_solve([{"center_px": p} for p in self.pixels[:7]], self.world, self.K, self.dist)
        bad = self.pixels.copy()
        bad[3] += [60, 40]
        with self.assertRaisesRegex(ValueError, "geometry"):
            cross.label_and_solve([{"center_px": p} for p in bad], self.world, self.K, self.dist)
        with self.assertRaises(ValueError):
            cross.solve_pose(np.zeros((8, 3)), self.pixels, self.K, self.dist)
        with self.assertRaises(ValueError):
            cross.detect_balls(np.zeros((30, 30, 3), np.uint8), roi=(20, 20, 20, 20))

    def test_batch_output_serialization_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            intrinsics = {}
            for role, center, serial in [("cf", [700, -2600, 650], "front"),
                                          ("cs", [-1800, -1900, 650], "side")]:
                (root / "images" / role).mkdir(parents=True)
                rv, tv = camera_at(center)
                pixels = cv2.projectPoints(self.world, rv, tv, self.K, self.dist)[0].reshape(-1, 2)
                hsv = np.zeros((1084, 1920, 3), np.uint8)
                for point in np.rint(pixels).astype(int):
                    cv2.circle(hsv, tuple(point), 6, (153, 170, 200), -1)
                image = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
                for i in range(3):
                    cli.write_image(root / "images" / role / (str(i)+".png"), image)
                intrinsics[serial] = {"intrinsic_matrix": self.K.tolist(),
                                      "distortion_coefficients": self.dist.tolist(), "image_size": [1920, 1084]}
            cli.save_json(root / "intrinsics.json", intrinsics)
            args = cli.build_parser().parse_args(["--images", str(root / "images"), "--intrinsics", str(root / "intrinsics.json"),
                                                 "--front-sn", "front", "--side-sn", "side", "--output", str(root / "output")])
            with contextlib.redirect_stdout(io.StringIO()):
                output, report = cli.run(args)
            self.assertEqual(set(report["cameras"]), {"cf", "cs"})
            self.assertEqual(report["status"], "review_required")
            self.assertFalse(report["independently_validated_for_measurement"])
            loaded = json.loads((output / "extrinsics.json").read_text(encoding="utf-8"))
            self.assertEqual(loaded["world"]["unit"], "mm")
            self.assertTrue((output / "cf/0.png.detail.jpg").is_file())
            with self.assertRaises(FileExistsError):
                cli.run(args)


if __name__ == "__main__":
    unittest.main()
