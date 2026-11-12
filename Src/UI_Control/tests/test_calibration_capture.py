"""Regression tests for corner layouts and camera rescan feedback."""
import os
from pathlib import Path
import sys
import tempfile
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
        window.connected_sn = "TEST"
        window.last_frame = np.zeros((160, 120, 3), dtype=np.uint8)
        window.image_size = (120, 160)
        # Live captures save PNGs; keep them out of the real Db folder.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        intrinsic = patch.object(self.calib.calib_store, "INTRINSIC_DIR", temp.name)
        intrinsic.start()
        self.addCleanup(intrinsic.stop)
        return window

    def test_capture_accepts_2d_and_3d_corners_and_rejects_duplicates(self):
        points = np.mgrid[0:8, 0:7].T.reshape(-1, 2).astype(np.float32) * 10
        for first_shape in ((56, 2), (56, 1, 2)):
            for next_shape in ((56, 2), (56, 1, 2)):
                with self.subTest(first=first_shape, next=next_shape):
                    window = self.collector()
                    samples = [points.reshape(first_shape),
                               points.reshape(next_shape),
                               (points + [6, 8]).astype(np.float32).reshape(next_shape)]
                    with patch.object(self.calib, "find_chessboard_corners", side_effect=samples):
                        window.capture_sample()
                        window.capture_sample()
                        self.assertEqual(window.captured_count, 1)
                        window.capture_sample()
                    self.assertEqual(window.captured_count, 2)
                    self.assertEqual(len(window.objpoints), 2)
                    self.assertEqual(window.imgpoints[-1].shape, (56, 1, 2))
                    np.testing.assert_array_equal(window.imgpoints[-1].reshape(-1, 2), points + [6, 8])

    def test_capture_rejects_changed_board_size_without_corrupting_samples(self):
        window = self.collector()
        window.imgpoints = [np.zeros((56, 2), dtype=np.float32)]
        window.objpoints = [np.zeros((56, 3), dtype=np.float32)]
        window.captured_count = 1
        window.spn_cols.setValue(7)
        corners = np.zeros((49, 1, 2), dtype=np.float32)
        with patch.object(self.calib, "find_chessboard_corners", return_value=corners):
            window.capture_sample()
        self.assertEqual(window.captured_count, 1)
        self.assertEqual(len(window.objpoints), 1)
        self.assertIn("清空樣本", window.log_area.toPlainText())

    def test_live_capture_saves_photo_under_hand_and_view(self):
        from datetime import datetime
        window = self.collector()
        window.hand_buttons["left"].setChecked(True)
        window.view_buttons["CS"].setChecked(True)
        points = (np.mgrid[0:8, 0:7].T.reshape(-1, 2).astype(np.float32) * 10).reshape(-1, 1, 2)
        with patch.object(self.calib, "find_chessboard_corners", return_value=points):
            self.assertTrue(window.capture_sample())
        saved = Path(window._photo_paths[0])
        today = datetime.now().strftime("%Y%m%d")
        self.assertEqual(saved.parent.parts[-3:-1], ("Intrinsic_" + today, "Left_CS"))
        self.assertRegex(saved.parent.name, r"^TEST_{}_[0-9a-f]{{6}}$".format(today))
        self.assertRegex(saved.name, r"^001_\d{6}_\d{3}\.png$")
        self.assertTrue(saved.exists())
        # A second capture in the same session uses the same capture folder.
        moved = points + 40
        with patch.object(self.calib, "find_chessboard_corners", return_value=moved):
            self.assertTrue(window.capture_sample())
        self.assertEqual(Path(window._photo_paths[1]).parent, saved.parent)
        # Output location is locked while samples exist.
        self.assertFalse(window.target_card.isEnabled())
        with patch.object(self.calib.QMessageBox, "question",
                          return_value=self.calib.QMessageBox.Yes) as ask:
            window.reset_samples()
        ask.assert_called_once()
        self.assertFalse(saved.parent.exists())   # user chose to delete; the empty capture folder goes too
        self.assertTrue(window.target_card.isEnabled())
        # The next session after reset gets a new capture folder.
        with patch.object(self.calib, "find_chessboard_corners", return_value=points):
            self.assertTrue(window.capture_sample())
        self.assertNotEqual(Path(window._photo_paths[0]).parent, saved.parent)

    def test_auto_capture_waits_for_steady_board_then_accepts_in_background(self):
        window = self.collector()
        points = (np.mgrid[0:8, 0:7].T.reshape(-1, 2).astype(np.float32) * 10).reshape(-1, 1, 2)
        window._preview_found, window._preview_stable = True, False
        with patch.object(self.calib, "find_chessboard_corners", return_value=points):
            window.btn_auto.setChecked(True)
            window._next_capture_at = 0
            window._auto_tick()
            self.assertIsNone(window._pending)          # board still moving
            self.assertIn("拿穩", window.lbl_status.text())
            window._preview_stable = True
            window._auto_tick()
            window._pending[0].result(timeout=5)
            window._auto_tick()
        self.assertEqual(window.captured_count, 1)
        self.assertGreater(window._next_capture_at, 0)
        window.btn_auto.setChecked(False)
        self.assertFalse(window.auto_running)

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
