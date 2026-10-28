"""Known-angle recovery, observability, and field-origin regression checks."""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import cv2
import numpy as np

from Src.calibration.wood_frame import wood_frame_target, validate_target, fit_wood_frame_rig, load_wood_frame_rig, save_wood_frame_rig
from Src.calibration.wood_frame_geometry import fit_wood_frame_geometry, POINT_IDS
from Src.calibration.board_defaults import DEFAULT_PATTERN, DEFAULT_SQUARE_MM
from Src.calibration.chessboard_cross.chessboard_metric import object_points


def geometry_fixture(noise=0., count=8):
    target = wood_frame_target()
    _, xyz = validate_target(target)
    true_xyz = xyz.copy()
    rotation = cv2.Rodrigues(np.array([0., np.deg2rad(12.), 0.]))[0]
    true_xyz[4:] = (xyz[4:] - xyz[4:].mean(axis=0)) @ rotation.T + xyz[4:].mean(axis=0) + [8., -4., 6.]
    camera = {"intrinsic_matrix": [[1400., 0., 960.], [0., 1410., 540.], [0., 0., 1.]],
              "distortion_coefficients": [.02, -.003, .001, -.001, 0.], "image_size": [1920, 1080]}
    rng = np.random.default_rng(812)
    views = []
    for i in range(count):
        rvec = np.array([.1, -.5, 0.]) + rng.uniform(-.3, .3, 3)
        tvec = np.array([-220., -120., 1350.]) + rng.uniform(-100., 100., 3)
        pixels = cv2.projectPoints(true_xyz, rvec, tvec, np.array(camera["intrinsic_matrix"]),
                                  np.array(camera["distortion_coefficients"]))[0].reshape(-1, 2)
        pixels += rng.normal(0., noise, pixels.shape)
        views.append({"id": str(i), "image_size": camera["image_size"], "points": dict(zip(POINT_IDS, pixels.tolist()))})
    return camera, views, true_xyz


class WoodGeometryTests(unittest.TestCase):
    def test_shared_board_defaults_have_correct_metric_extent(self):
        self.assertEqual(DEFAULT_PATTERN, (8, 7))
        self.assertEqual(DEFAULT_SQUARE_MM, 100.)
        points = object_points()
        self.assertEqual(points.shape, (56, 3))
        np.testing.assert_array_equal(points.max(axis=0), [700., 600., 0.])

    def test_recovers_non_orthogonal_geometry_and_preserves_measured_faces(self):
        camera, views, truth = geometry_fixture()
        original = copy.deepcopy((camera, views))
        result = fit_wood_frame_geometry(camera, views)
        _, actual = validate_target(result)
        np.testing.assert_allclose(actual, truth, atol=.05)
        fit = result["metadata"]["geometry_calibration"]
        self.assertAlmostEqual(fit["acute_plane_angle_deg"], 78., places=2)
        self.assertLess(fit["rms_px"], .001)
        np.testing.assert_array_equal(actual[:4], truth[:4])
        self.assertEqual((camera, views), original)

    def test_noise_does_not_force_ninety_degrees(self):
        camera, views, truth = geometry_fixture(.12)
        result = fit_wood_frame_geometry(camera, views)
        _, actual = validate_target(result)
        self.assertLess(np.max(np.linalg.norm(actual - truth, axis=1)), 5.)
        self.assertLess(abs(result["metadata"]["geometry_calibration"]["acute_plane_angle_deg"] - 78.), 1.)

    def test_translation_only_photos_do_not_establish_orientation_diversity(self):
        camera, views, xyz = geometry_fixture()
        for i, view in enumerate(views):
            pixels = cv2.projectPoints(xyz, np.array([.1, -.5, 0.]),
                                      np.array([-250. + i * 12., -120., 1300.]),
                                      np.array(camera["intrinsic_matrix"]),
                                      np.array(camera["distortion_coefficients"]))[0].reshape(-1, 2)
            view["points"] = dict(zip(POINT_IDS, pixels.tolist()))
        with self.assertRaisesRegex(ValueError, "傾角"):
            fit_wood_frame_geometry(camera, views)

    def test_rejects_missing_face_wrong_resolution_duplicates_and_too_few_views(self):
        camera, views, _ = geometry_fixture()
        cases = [views[:4], copy.deepcopy(views), copy.deepcopy(views), copy.deepcopy(views)]
        del cases[1][0]["points"]["S4"]
        cases[2][0]["image_size"] = [960, 540]
        cases[3][1]["points"] = copy.deepcopy(cases[3][0]["points"])
        for case in cases:
            with self.subTest(case=case[0]["id"]), self.assertRaises(ValueError):
                fit_wood_frame_geometry(camera, case)

    def test_world_reference_does_not_change_stereo_rig(self):
        from Src.UI_Control.tests.test_wood_frame import WoodFrameTests
        source = WoodFrameTests()
        source.setUp()
        default = fit_wood_frame_rig(source.cameras, source.target, source.pairs)
        reference = next(p["id"] for p in source.pairs if p["id"] != default["best_pair_id"])
        result = fit_wood_frame_rig(source.cameras, source.target, source.pairs, world_pair_id=reference)
        np.testing.assert_array_equal(result["R_cs_from_cf"], default["R_cs_from_cf"])
        np.testing.assert_array_equal(result["t_cs_from_cf_mm"], default["t_cs_from_cf_mm"])
        R, t = source.truth[reference]
        pose = result["world"]["cameras"]["cf"]
        np.testing.assert_allclose(pose["R_camera_from_world"], R, atol=1e-5)
        np.testing.assert_allclose(pose["t_camera_from_world_mm"], t, atol=.01)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "stereo.json"
            save_wood_frame_rig(path, result)
            self.assertEqual(load_wood_frame_rig(path)["world"]["source_pair_id"], reference)
        with self.assertRaises(ValueError):
            fit_wood_frame_rig(source.cameras, source.target, source.pairs, world_pair_id="missing")


class WoodGeometryUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from Src.calibration import calibrate_wood_frame_geometry as gui
        cls.gui = gui
        cls.app = gui.QApplication.instance() or gui.QApplication([])

    def test_import_annotation_session_resume_and_invalid_data_preserve_work(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            photo = root / "frame.png"
            cv2.imencode(".png", np.zeros((1080, 1920, 3), np.uint8))[1].tofile(str(photo))
            camera, _, _ = geometry_fixture()
            with patch.object(self.gui.WoodFrameGeometryWindow, "set_intrinsics"):
                window = self.gui.WoodFrameGeometryWindow()
            self.addCleanup(window.close)
            window.fail = Mock()
            window.entries = {"camera": camera}
            window.intrinsics.addItem("camera")
            with patch.object(self.gui.QFileDialog, "getOpenFileNames", return_value=([str(photo)], "")):
                window.import_photos()
            deadline = time.monotonic() + 10
            while window.job is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertIsNone(window.job)
            window.mark_point(200., 300.)
            self.assertEqual(window.views[0]["points"], {"F1": [200., 300.]})
            session = root / "session.json"
            with patch.object(self.gui.QFileDialog, "getSaveFileName", return_value=(str(session), "")):
                window.save_session()
            original = copy.deepcopy(window.views)
            window.views = []
            with patch.object(self.gui.QFileDialog, "getOpenFileName", return_value=(str(session), "")):
                window.load_session()
            self.assertEqual(window.views, original)
            data = json.loads(session.read_text(encoding="utf-8"))
            data["views"][0]["points"]["F1"] = [float("nan"), 100.]
            session.write_text(json.dumps(data), encoding="utf-8")
            with patch.object(self.gui.QFileDialog, "getOpenFileName", return_value=(str(session), "")):
                window.load_session()
            self.assertEqual(window.views, original)
            window.fail.assert_called()


if __name__ == "__main__":
    unittest.main()
