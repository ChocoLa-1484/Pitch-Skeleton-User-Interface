"""Run with: python -m unittest discover -s Src/UI_Control/tests -v"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
for _folder in ("main", "chessboard_cross"):   # 校正工具搬到 calibration/ 底下
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "calibration" / _folder))

import cv2
import numpy as np
from cv_utils import calibration_workflow as workflow


def sample_data(outliers=False):
    rng = np.random.RandomState(42)
    K = np.array([[950., 0, 640], [0, 960, 480], [0, 0, 1]])
    rotation = np.array([0.04, -0.13, 0.02])
    translation = np.array([550., 30., 50.])
    world = rng.uniform([-700, -500, 2200], [700, 500, 4500], (60, 3))
    side = cv2.projectPoints(world, np.zeros(3), np.zeros(3), K, None)[0].reshape(-1, 2)
    front = cv2.projectPoints(world, rotation, translation, K, None)[0].reshape(-1, 2)
    front += rng.normal(0, 0.12, front.shape)
    side += rng.normal(0, 0.12, side.shape)
    if outliers:
        front[::6] += [100, -100]
    front_dict = {"{:02d}.jpg".format(i+1): front[i*6:(i+1)*6].tolist() for i in range(10)}
    side_dict = {"{:02d}.jpg".format(i+1): side[i*6:(i+1)*6].tolist() for i in range(10)}
    entry = {"intrinsic_matrix": K.tolist(), "distortion_coefficients": [[0.] * 5],
             "image_size": [1280, 960]}
    return front_dict, side_dict, entry


class GeometryTests(unittest.TestCase):
    def test_recovers_direction_and_rejects_outliers(self):
        front, side, _ = sample_data(True)
        result = workflow.estimate_wand_fundamental(front, side, 1.0)
        self.assertGreaterEqual(result["inliers"], 48)
        self.assertLessEqual(result["inliers"], 50)
        F = np.array(result["F"])
        x_f = np.vstack(list(front.values()))
        x_s = np.vstack(list(side.values()))
        good = np.arange(len(x_f)) % 6 != 0
        self.assertLess(np.median(workflow.epipolar_errors(F, x_s[good], x_f[good])), 0.5)
        self.assertGreater(np.median(workflow.epipolar_errors(F.T, x_s[good], x_f[good])), 5)
        self.assertEqual(np.linalg.matrix_rank(F), 2)
        repeated = workflow.estimate_wand_fundamental(front, side, 1.0)
        np.testing.assert_array_equal(result["F"], repeated["F"])
        self.assertEqual(result["inliers"], repeated["inliers"])

    def test_rejects_incomplete_nonfinite_and_collinear_input(self):
        front, side, _ = sample_data()
        with self.assertRaises(ValueError):
            workflow.estimate_wand_fundamental({"01.jpg": front["01.jpg"]}, side)
        front["01.jpg"][0][0] = float("nan")
        with self.assertRaises(ValueError):
            workflow.estimate_wand_fundamental(front, side)
        points = {str(i): [[j + i*6, 2*(j + i*6)] for j in range(6)] for i in range(4)}
        with self.assertRaises(ValueError):
            workflow.estimate_wand_fundamental(points, points)

    def test_pairing_requires_identical_names(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for role in ("cf", "cs"):
                (folder / role).mkdir()
                for name in ("01.jpg", "02.jpg", "03.jpg", "10.jpg"):
                    (folder / role / name).touch()
            self.assertEqual(workflow.match_image_pairs(folder), ["01.jpg", "02.jpg", "03.jpg", "10.jpg"])
            (folder / "cs/03.jpg").rename(folder / "cs/04.jpg")
            with self.assertRaises(ValueError):
                workflow.match_image_pairs(folder)

    def test_rejects_unsafe_roles_and_invalid_intrinsics(self):
        for roles in (("a", "a"), ("", "b"), ("../a", "b")):
            with self.assertRaises(ValueError):
                workflow.validate_roles(*roles)
        _, _, entry = sample_data()
        workflow.validate_intrinsic(entry)
        entry["image_size"] = [0, 960]
        with self.assertRaises(ValueError):
            workflow.validate_intrinsic(entry)


class StorageTests(unittest.TestCase):
    def test_publish_backup_compatibility_and_rollback(self):
        front, side, entry = sample_data()
        report = workflow.estimate_wand_fundamental(front, side)
        entries = {"front123": entry, "side456": entry}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "sessions/test"
            session.mkdir(parents=True)
            roles_path = root / "camera_roles.json"
            roles_path.write_text('{"front":"old","side":"rig"}', encoding="utf-8")
            before = roles_path.read_bytes()
            original_write = workflow.atomic_write

            def failing_write(file, data):
                if Path(file) == root / "extrinsic/fundamental.json":
                    raise OSError("simulated disk failure")
                original_write(file, data)

            with patch.object(workflow, "atomic_write", side_effect=failing_write):
                with self.assertRaises(OSError):
                    workflow.publish_calibration(root, session, tuple(entries), entries, report,
                                                 {"cf": front, "cs": side})
            self.assertEqual(roles_path.read_bytes(), before)
            self.assertFalse((root / "intrinsic/intrinsics_all.json").exists())
            workflow.publish_calibration(root, session, tuple(entries), entries, report,
                                         {"cf": front, "cs": side})
            self.assertEqual((session / "previous_calibration/camera_roles.json").read_bytes(), before)
            from cv_utils import calib_store
            with patch.multiple(calib_store, ROLES_JSON=str(roles_path),
                                INTRINSICS_JSON=str(root / "intrinsic/intrinsics_all.json"),
                                FUNDAMENTAL_JSON=str(root / "extrinsic/fundamental.json")):
                calibration, reason = calib_store.load_stereo_calibration()
                self.assertIsNone(reason)
                np.testing.assert_allclose(calibration["F"], report["F"])
                self.assertEqual(calibration["front"], "front123")


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import calibrate
        # The camera SDK is mocked; these tests exercise Qt/OpenCV and lifecycle,
        # not physical cameras or the vendor's native DLL loader.
        sdk_patch = patch.dict(sys.modules, {"PySpin": MagicMock()})
        sdk_patch.start()
        cls.addClassCleanup(sdk_patch.stop)
        import calib
        cls.gui = calibrate
        cls.app = calibrate.QApplication.instance() or calibrate.QApplication([])

    def test_point_mapping_zoom_and_undo(self):
        from PyQt5.QtCore import Qt, QPointF
        from PyQt5.QtTest import QTest
        view = self.gui.PointView()
        view.resize(600, 400)
        view.show()
        points = []
        view.set_image(np.zeros((960, 1280, 3), dtype=np.uint8), points, True)
        self.app.processEvents()
        for target in (QPointF(200, 200), QPointF(400, 400)):
            view.scale(1.2, 1.2)
            position = view.mapFromScene(target)
            QTest.mouseClick(view.viewport(), Qt.LeftButton, pos=position)
            np.testing.assert_allclose(points[-1], [target.x(), target.y()], atol=3)
        QTest.mouseClick(view.viewport(), Qt.RightButton, pos=view.mapFromScene(QPointF(400, 400)))
        self.assertEqual(len(points), 1)
        view.close()

    def test_existing_intrinsics_to_publication_and_cancel(self):
        front, side, entry = sample_data()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.gui.CalibrationWindow, "scan_cameras"), \
                 patch.object(self.gui.calib_store, "CALIB_DIR", directory), \
                 patch.object(self.gui.calib_store, "load_intrinsics", return_value={"F": entry, "S": entry}):
                window = self.gui.CalibrationWindow()
                failures = []
                window.fail = failures.append
                window.front_combo.setCurrentText("F")
                window.side_combo.setCurrentText("S")
                window.reuse.setChecked(True)
                window.start_session()
                self.assertEqual(window.state, "capture")
                window.cancel()
                self.assertFalse((Path(directory) / "camera_roles.json").exists())
                window.start_session()
                window.names = list(front)
                image = np.zeros((960, 1280, 3), dtype=np.uint8)
                for name in window.names:
                    for role in ("cf", "cs"):
                        self.gui.write_image(window.session / role / name, image)
                with self.assertRaises(ValueError):
                    window.check_image_size(image[:500], "cf")
                window.points = {"cf": front, "cs": side}
                window.begin_annotation()
                window.calculate_extrinsic()
                deadline = time.monotonic() + 10
                while window.job is not None and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.assertIsNone(window.job)
                self.assertEqual(failures, [])
                self.assertEqual(window.state, "done")
                report = json.loads((window.session / "report.json").read_text(encoding="utf-8"))
                self.assertEqual(report["front"], "F")
                self.assertTrue((Path(directory) / "extrinsic/fundamental.json").exists())
                window.close()

    def test_both_intrinsics_are_staged_before_publication(self):
        import calib
        from unittest.mock import Mock
        camera = Mock()
        camera.get_grayscale_image.return_value = (False, None)
        _, _, entry = sample_data()
        K = np.asarray(entry["intrinsic_matrix"])
        obj = np.zeros((8*11, 3), np.float32)
        obj[:, :2] = np.mgrid[0:8, 0:11].T.reshape(-1, 2) * 10
        rng = np.random.RandomState(8)
        objects, images = [], []
        for _ in range(15):
            rotation = rng.uniform(-0.5, 0.5, 3)
            translation = rng.uniform([-150, -100, 400], [50, 50, 800])
            corners = cv2.projectPoints(obj, rotation, translation, K, np.zeros(5))[0]
            objects.append(obj.copy())
            images.append(corners)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.gui.CalibrationWindow, "scan_cameras"), \
                 patch.object(self.gui.calib_store, "CALIB_DIR", directory), \
                 patch.object(calib, "list_cameras", return_value=[("F", "test"), ("S", "test")]), \
                 patch.object(calib, "FlirCameraSystem", return_value=camera):
                window = self.gui.CalibrationWindow()
                failures = []
                window.fail = failures.append
                window.front_combo.setCurrentText("F")
                window.side_combo.setCurrentText("S")
                window.start_session()
                for index, serial in enumerate(("F", "S")):
                    self.assertEqual(window.state, "intrinsic_{}".format(index))
                    window.panel.image_size = (1280, 960)
                    window.panel.objpoints = objects.copy()
                    window.panel.imgpoints = images.copy()
                    window.panel.captured_count = 15
                    window.calculate_intrinsic()
                    deadline = time.monotonic() + 10
                    while window.job is not None and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(0.01)
                    self.assertIsNone(window.job)
                    self.assertEqual(failures, [])
                    self.assertIn(serial, window.entries)
                    self.assertTrue((window.session / "intrinsic" / ("calib_SN" + serial + ".npz")).exists())
                    self.assertFalse((Path(directory) / "intrinsic/intrinsics_all.json").exists())
                self.assertEqual(window.state, "capture")
                self.assertEqual(camera.release.call_count, 2)
                window.close()


if __name__ == "__main__":
    unittest.main()
