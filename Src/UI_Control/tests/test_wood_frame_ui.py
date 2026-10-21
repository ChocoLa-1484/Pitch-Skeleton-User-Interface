"""Guided capture/session regression tests; only temporary images/calibrations."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import cv2
import numpy as np

from Src.UI_Control import calibrate_wood_frame as gui
from Src.calibration.wood_frame import wood_frame_target, load_wood_frame_rig


class WoodFrameUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = gui.QApplication.instance() or gui.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        camera = {"intrinsic_matrix": [[1200., 0, 640], [0, 1200., 480], [0, 0, 1]],
                  "distortion_coefficients": [0.] * 5, "image_size": [1280, 960]}
        self.intrinsics = {"123": copy.deepcopy(camera), "456": copy.deepcopy(camera)}
        self.store_patch = patch.object(gui.calib_store, "CALIB_DIR", str(self.root))
        self.store_patch.start()
        self.addCleanup(self.store_patch.stop)
        with patch.object(gui.WoodFrameWindow, "_load_defaults"):
            self.window = gui.WoodFrameWindow()
        self.addCleanup(self.close_window)
        self.window.fail = Mock()
        self.window.set_intrinsics(self.intrinsics, self.root / "intrinsics.json")
        self.window.start_session()
        self.assertEqual(self.window.phase, "capture")
        self.image = np.zeros((960, 1280, 3), np.uint8)

    def close_window(self):
        self.window.capture = None
        self.window.close()

    def fill_pairs(self, count=6):
        target = wood_frame_target()
        xyz = {p["id"]: p["xyz_mm"] for p in target["points"]}
        Rrelative = cv2.Rodrigues(np.array([.02, -.9, .01]))[0]
        trelative = np.array([1500., 20., 700.])
        rng = np.random.default_rng(60)
        pairs = []
        for i in range(count):
            Rf = cv2.Rodrigues(np.array([.1, .3, -.05]) + rng.uniform(-.15, .15, 3))[0]
            tf = np.array([-200., -200., 2200.]) + rng.uniform(-100, 100, 3)
            pair = {"id": str(i)}
            for role in gui.ROLES:
                R, t = (Rf, tf) if role == "cf" else (Rrelative @ Rf, Rrelative @ tf + trelative)
                ids = gui.POINT_IDS[role]
                points = cv2.projectPoints(np.array([xyz[key] for key in ids], float),
                                          cv2.Rodrigues(R)[0], t,
                                          np.array(self.intrinsics["123"]["intrinsic_matrix"]),
                                          np.zeros(5))[0].reshape(-1, 2)
                path = self.window.session / role / (str(i) + ".png")
                gui.write_image(path, self.image)
                pair[role] = {"path": str(path), "image_size": [1280, 960],
                              "points": dict(zip(ids, points.tolist()))}
            pairs.append(pair)
        self.window.pairs = pairs
        self.window.persist()
        return pairs

    def test_capture_fresh_pair_duplicate_stale_and_failed_write(self):
        self.window.capture = Mock(latest=(time.monotonic(), self.image, self.image))
        self.window.capture_pair()
        self.assertEqual(len(self.window.pairs), 1)
        self.assertTrue(Path(self.window.pairs[0]["cf"]["path"]).is_file())
        self.window.capture_pair()
        self.assertEqual(len(self.window.pairs), 1)
        self.window.capture.latest = (time.monotonic() - 10, self.image, self.image)
        self.window.capture_pair()
        self.assertEqual(len(self.window.pairs), 1)
        self.assertIn("新的雙機影像", self.window.fail.call_args[0][0])
        self.window.capture.latest = (time.monotonic(), self.image, self.image)
        before = sorted(self.window.session.rglob("*.png"))
        with patch.object(gui, "write_image", side_effect=OSError("disk full")):
            self.window.capture_pair()
        self.assertEqual(sorted(self.window.session.rglob("*.png")), before)
        self.assertEqual(len(self.window.pairs), 1)

    def test_capture_failure_remains_visible_after_worker_finishes(self):
        self.window.capture = Mock()
        self.window.capture_failed("相機連線失敗：GPIO")
        self.window.capture_stopped()
        self.assertEqual(self.window.status.text(), "相機連線失敗：GPIO")

    def test_annotation_skip_calculate_export_and_resume(self):
        pairs = self.fill_pairs()
        pairs[-1]["cf"]["points"] = {}
        self.window.begin_annotation()
        self.window.index = len(pairs) - 1
        self.window.show_pair()
        self.assertFalse(self.window.calculate_button.isEnabled())
        self.window.include_pair.setChecked(False)
        self.assertTrue(self.window.calculate_button.isEnabled())
        self.window.calculate()
        deadline = time.monotonic() + 30
        while self.window.job is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(self.window.job)
        self.assertEqual(self.window.phase, "result", self.window.fail.call_args)
        self.assertEqual(self.window.result["manually_excluded_pairs"], ["5"])
        self.assertEqual(self.window.result_table.rowCount(), 5)
        path = self.root / "export" / "木框.json"
        with patch.object(gui.QFileDialog, "getSaveFileName", return_value=(str(path), "")), \
                patch.object(gui.QMessageBox, "information"):
            self.window.export_result()
        self.assertEqual(load_wood_frame_rig(path)["target_type"], "wood_frame")
        session = self.window.session / "session.json"
        expected = copy.deepcopy(self.window.pairs)
        self.window.reset()
        with patch.object(gui.QFileDialog, "getOpenFileName", return_value=(str(session), "")):
            self.window.resume_session()
        self.assertEqual(self.window.phase, "annotate")
        self.assertEqual(self.window.pairs, expected)
        self.window.index = 0
        self.window.show_pair()
        old_points = copy.deepcopy(self.window.pairs[0]["cf"]["points"])
        with patch.object(self.window, "persist", side_effect=OSError("full")):
            self.window.mark_point("cf", 200, 300)
        self.assertEqual(self.window.pairs[0]["cf"]["points"], old_points)

    def test_bad_session_and_intrinsic_formats_leave_current_work_unchanged(self):
        self.fill_pairs()
        before = copy.deepcopy(self.window.pairs)
        bad = self.root / "bad.json"
        for value in ([], {"cameras": []}, None):
            bad.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(ValueError):
                gui.load_intrinsic_choices(bad)
        data = json.loads((self.window.session / "session.json").read_text(encoding="utf-8"))
        data["pairs"][0]["cf"]["points"] = []
        bad.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(gui.QFileDialog, "getOpenFileName", return_value=(str(bad), "")):
            self.window.resume_session()
        self.assertEqual(self.window.pairs, before)
        self.window.fail.assert_called()

    def test_exif_import_uses_same_source_pixels_as_canvas(self):
        from PIL import Image
        path = self.root / "rotated.jpg"
        image = Image.new("RGB", (120, 80), "red")
        exif = Image.Exif()
        exif[274] = 6
        image.save(path, exif=exif)
        source = gui.read_image(path)
        size = self.window.views["cf"].set_image(path)
        self.assertEqual(size, (source.shape[1], source.shape[0]))
        self.assertEqual(size, (120, 80))


if __name__ == "__main__":
    unittest.main()
