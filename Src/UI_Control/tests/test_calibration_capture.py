"""Regression tests for corner layouts and camera rescan feedback."""
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np


class CalibrationCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sdk_patch = patch.dict(sys.modules, {"PySpin": MagicMock()})
        sdk_patch.start()
        cls.addClassCleanup(sdk_patch.stop)
        import Src.calibration.calib as calib
        import Src.calibration.calibrate as calibrate
        cls.calib = calib
        cls.gui = calibrate
        cls.app = calibrate.QApplication.instance() or calibrate.QApplication([])

    def collector(self):
        with patch.object(self.calib, "list_cameras", return_value=[]), \
             patch.object(self.calib.calib_store, "load_roles", return_value=None), \
             patch.object(self.calib.calib_store, "load_intrinsics", return_value={}):
            window = self.calib.CalibWindow()
        self.addCleanup(window.close)
        window.timer.stop()
        window.camera = Mock()
        window.last_frame = np.zeros((160, 120, 3), dtype=np.uint8)
        window.image_size = (120, 160)
        return window

    def test_capture_accepts_2d_and_3d_corners_and_rejects_duplicates(self):
        points = np.mgrid[0:8, 0:11].T.reshape(-1, 2).astype(np.float32) * 10
        for first_shape in ((88, 2), (88, 1, 2)):
            for next_shape in ((88, 2), (88, 1, 2)):
                with self.subTest(first=first_shape, next=next_shape):
                    window = self.collector()
                    samples = [points.reshape(first_shape),
                               points.reshape(next_shape),
                               (points + [6, 8]).astype(np.float32).reshape(next_shape)]
                    with patch.object(self.calib.cv2, "findChessboardCorners",
                                      return_value=(True, points.reshape(-1, 1, 2))), \
                         patch.object(self.calib.cv2, "cornerSubPix", side_effect=samples):
                        window.capture_sample()
                        window.capture_sample()
                        self.assertEqual(window.captured_count, 1)
                        window.capture_sample()
                    self.assertEqual(window.captured_count, 2)
                    self.assertEqual(len(window.objpoints), 2)
                    self.assertEqual(window.imgpoints[-1].shape, (88, 1, 2))
                    np.testing.assert_array_equal(window.imgpoints[-1].reshape(-1, 2), points + [6, 8])

    def test_capture_rejects_changed_board_size_without_corrupting_samples(self):
        window = self.collector()
        window.imgpoints = [np.zeros((88, 2), dtype=np.float32)]
        window.objpoints = [np.zeros((88, 3), dtype=np.float32)]
        window.captured_count = 1
        window.spn_cols.setValue(7)
        corners = np.zeros((77, 1, 2), dtype=np.float32)
        with patch.object(self.calib.cv2, "findChessboardCorners", return_value=(True, corners)), \
             patch.object(self.calib.cv2, "cornerSubPix", return_value=corners):
            window.capture_sample()
        self.assertEqual(window.captured_count, 1)
        self.assertEqual(len(window.objpoints), 1)
        self.assertIn("清空樣本", window.log_area.toPlainText())

    def test_rescan_updates_live_serials_and_keeps_connected_role(self):
        with patch.object(self.calib, "list_cameras", return_value=[("B", "model"), ("A", "model")]) as scan, \
             patch.object(self.gui.calib_store, "load_roles", return_value=("oldF", "oldS")), \
             patch.object(self.gui.calib_store, "load_intrinsics", return_value={"oldF": {}, "oldS": {}}):
            window = self.gui.CalibrationWindow()
            self.addCleanup(window.close)
            self.assertEqual(window.front_combo.currentText(), "A")
            self.assertEqual(window.side_combo.currentText(), "B")
            self.assertIn("2 台", window.scan_status.text())
            self.assertIn("A", window.scan_status.text())
            self.assertIn("oldF", [window.front_combo.itemText(i) for i in range(window.front_combo.count())])
            scan.return_value = [("B", "model"), ("C", "model")]
            window.scan_button.click()
            self.assertEqual(scan.call_count, 2)
            self.assertEqual(window.front_combo.currentText(), "C")
            self.assertEqual(window.side_combo.currentText(), "B")
            self.assertIn("C", window.scan_status.text())

    def test_rescan_reports_no_devices_and_errors_and_allows_retry(self):
        with patch.object(self.calib, "list_cameras", return_value=[]) as scan, \
             patch.object(self.gui.calib_store, "load_roles", return_value=("oldF", "oldS")), \
             patch.object(self.gui.calib_store, "load_intrinsics", return_value={}):
            window = self.gui.CalibrationWindow()
            self.addCleanup(window.close)
            self.assertIn("未偵測", window.scan_status.text())
            self.assertEqual(window.front_combo.currentText(), "oldF")
            scan.side_effect = RuntimeError("SDK unavailable")
            window.scan_button.click()
            self.assertIn("SDK unavailable", window.scan_status.text())
            self.assertTrue(window.scan_button.isEnabled())
            scan.side_effect = None
            scan.return_value = [("A", "model"), ("B", "model")]
            window.scan_button.click()
            self.assertIn("2 台", window.scan_status.text())
            self.assertEqual(window.front_combo.currentText(), "A")
            self.assertEqual(window.side_combo.currentText(), "B")

    def test_rescan_preserves_offline_selection_when_reusing_intrinsics(self):
        with patch.object(self.calib, "list_cameras", return_value=[("A", "model"), ("B", "model")]), \
             patch.object(self.gui.calib_store, "load_roles", return_value=None), \
             patch.object(self.gui.calib_store, "load_intrinsics", return_value={"oldF": {}, "oldS": {}}):
            window = self.gui.CalibrationWindow()
            self.addCleanup(window.close)
            window.reuse.setChecked(True)
            window.front_combo.setCurrentText("oldF")
            window.side_combo.setCurrentText("oldS")
            window.scan_button.click()
            self.assertEqual(window.front_combo.currentText(), "oldF")
            self.assertEqual(window.side_combo.currentText(), "oldS")


if __name__ == "__main__":
    unittest.main()
