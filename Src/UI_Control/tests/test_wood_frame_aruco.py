"""Real ArUco decoding, rotation-aware identities, and both GUI workflows."""
import copy
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import cv2
import numpy as np

from Src.calibration.wood_frame import wood_frame_target
from Src.calibration.wood_frame_aruco import detect_frame_markers


def marker(ident):
    aruco = cv2.aruco
    dictionary = aruco.getPredefinedDictionary(aruco.DICT_4X4_50)
    if hasattr(aruco, "generateImageMarker"):
        return aruco.generateImageMarker(dictionary, ident, 240)
    return aruco.drawMarker(dictionary, ident, 240)


def scene():
    image = np.full((600, 1000), 255, np.uint8)
    image[150:390, 100:340] = np.rot90(marker(0))
    image[150:390, 600:840] = marker(1)
    points = {"cf": np.array([[100., 150.], [339., 150.], [339., 389.], [100., 389.]]),
              "cs": np.array([[600., 150.], [839., 150.], [839., 389.], [600., 389.]])}
    return image, points


class ArucoTests(unittest.TestCase):
    def test_decoded_corners_follow_physical_ids_for_all_image_rotations(self):
        target = wood_frame_target()
        for turns in range(4):
            image, expected = scene()
            for _ in range(turns):
                expected = {role: np.c_[xy[:, 1], image.shape[1] - 1 - xy[:, 0]] for role, xy in expected.items()}
                image = np.rot90(image).copy()
            detected = detect_frame_markers(image, target)
            for role in ("cf", "cs"):
                actual = [detected[role]["points"][p] for p in target["view_point_ids"][role]]
                np.testing.assert_allclose(actual, expected[role], atol=1.)
                self.assertEqual(detected[role]["marker_id"], 0 if role == "cf" else 1)

    def test_perspective_distortion_preserves_corner_order(self):
        image, expected = scene()
        H = cv2.getPerspectiveTransform(np.float32([[0,0],[999,0],[999,599],[0,599]]),
                                       np.float32([[80,20],[950,90],[990,550],[10,590]]))
        warped = cv2.warpPerspective(image, H, (1000, 600), borderValue=255)
        detected = detect_frame_markers(warped, wood_frame_target())
        for role, ids in wood_frame_target()["view_point_ids"].items():
            truth = cv2.perspectiveTransform(expected[role].reshape(-1, 1, 2), H).reshape(-1, 2)
            np.testing.assert_allclose([detected[role]["points"][p] for p in ids], truth, atol=1.2)

    def test_missing_and_duplicate_ids_never_substitute_other_face(self):
        image, _ = scene()
        image[150:390, 100:340] = 255
        detected = detect_frame_markers(image, wood_frame_target())
        self.assertEqual(detected["cf"]["points"], {})
        self.assertEqual(len(detected["cs"]["points"]), 4)
        image[150:390, 100:340] = marker(1)
        self.assertEqual(detect_frame_markers(image, wood_frame_target())["cs"]["status"], "duplicate")

    def test_legacy_target_is_not_silently_assigned_new_marker_mapping(self):
        target = wood_frame_target()
        del target["aruco"]
        with self.assertRaises(ValueError):
            detect_frame_markers(scene()[0], target)


class ArucoUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from Src.calibration import calibrate_wood_frame as wood_gui
        from Src.calibration import calibrate_wood_frame_geometry as geometry_gui
        cls.wood, cls.geometry = wood_gui, geometry_gui
        cls.app = wood_gui.QApplication.instance() or wood_gui.QApplication([])

    def wait_job(self, window):
        deadline = time.monotonic() + 10
        while window.job is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(window.job)

    def camera(self):
        return {"intrinsic_matrix": [[1000.,0.,500.],[0.,1000.,300.],[0.,0.,1.]],
                "distortion_coefficients": [0.] * 5, "image_size": [1000,600]}

    def test_capture_and_background_redetect_persist_physical_corners(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(self.wood.calib_store, "CALIB_DIR", folder), \
                patch.object(self.wood.WoodFrameWindow, "_load_defaults"):
            window = self.wood.WoodFrameWindow()
            window.fail = Mock()
            image = cv2.cvtColor(scene()[0], cv2.COLOR_GRAY2BGR)
            window.set_intrinsics({"front": self.camera(), "side": self.camera()}, "test.json")
            window.start_session()
            window.capture = Mock(latest=(time.monotonic(), image, image))
            window.capture_pair()
            window.capture = None
            self.assertEqual(len(window.pairs), 1)
            self.assertEqual(set(window.pairs[0]["cf"]["points"]), {"F1","F2","F3","F4"})
            self.assertEqual(set(window.pairs[0]["cs"]["points"]), {"S1","S2","S3","S4"})
            source = Path(folder) / "import"
            for role in ("cf", "cs"):
                (source / role).mkdir(parents=True)
                cv2.imencode(".png", image)[1].tofile(str(source / role / "pair.png"))
            window.import_pairs(str(source))
            self.wait_job(window)
            self.assertEqual(len(window.pairs), 2)
            for role in ("cf", "cs"):
                self.assertEqual(len(window.pairs[1][role]["points"]), 4)
                self.assertEqual(window.pairs[1][role]["aruco_detection"]["status"], "detected")
            window.pairs[0]["cf"]["points"] = {"F1": [42., 42.]}
            window.detect_aruco_pairs([0])
            self.wait_job(window)
            self.assertEqual(window.pairs[0]["cf"]["points"], {"F1": [42., 42.]})
            before = copy.deepcopy(window.pairs)
            with patch.object(window, "persist", side_effect=OSError("disk full")):
                window.detect_aruco_pairs([0], overwrite=True)
                self.wait_job(window)
            self.assertEqual(window.pairs, before)
            window.close()

    def test_geometry_photo_import_automatically_fills_all_eight_points(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(self.geometry.WoodFrameGeometryWindow, "set_intrinsics"):
            path = Path(folder) / "both_faces.png"
            cv2.imencode(".png", scene()[0])[1].tofile(str(path))
            window = self.geometry.WoodFrameGeometryWindow()
            window.fail = Mock()
            window.entries = {"camera": self.camera()}
            window.intrinsics.addItem("camera")
            with patch.object(self.geometry.QFileDialog, "getOpenFileNames", return_value=([str(path)], "")):
                window.import_photos()
            self.wait_job(window)
            self.assertEqual(len(window.views[0]["points"]), 8)
            window.fail.assert_not_called()
            window.close()


if __name__ == "__main__":
    unittest.main()
