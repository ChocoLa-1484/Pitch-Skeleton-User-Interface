"""Photo import transactions and shared capture lifecycle; no camera or Db access."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import numpy as np
from PyQt5.QtCore import QTimer

from Src.UI_Control import calibrate_wood_frame as gui
from Src.UI_Control.controllers.calibration_import import ImportCancelled, stage_pairs


class CalibrationImportReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = gui.QApplication.instance() or gui.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = patch.object(gui.calib_store, "CALIB_DIR", str(self.root))
        self.store.start()
        with patch.object(gui.WoodFrameWindow, "_load_defaults"):
            self.window = gui.WoodFrameWindow()
        self.window.fail = Mock()
        camera = {"intrinsic_matrix": [[100., 0, 32], [0, 100., 24], [0, 0, 1]],
                  "distortion_coefficients": [0.] * 5, "image_size": [64, 48]}
        self.window.set_intrinsics({"111": camera, "222": copy.deepcopy(camera)}, "test.json")
        self.window.start_session()
        self.image = np.zeros((48, 64, 3), np.uint8)
        self.source = self.root / "輸入照片"
        for role in gui.ROLES:
            (self.source / role).mkdir(parents=True)
            gui.write_image(self.source / role / "一.png", self.image)

    def tearDown(self):
        self.window.capture = None
        if self.window.job is not None:
            self.window.job.requestInterruption()
            self.wait_finished()
        self.window.close()
        self.app.processEvents()
        self.store.stop()
        self.temp.cleanup()

    def wait_finished(self):
        deadline = time.monotonic() + 10
        while self.window.job is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.window.job)

    def images(self):
        return sorted(self.window.session.rglob("*.png"))

    def test_single_pair_supplement_import_commits_then_calls_hook(self):
        w = self.window
        w.on_pairs_imported = Mock()
        w.import_pairs(str(self.source))
        self.wait_finished()
        self.assertEqual(len(w.pairs), 1)
        self.assertEqual(len(self.images()), 2)
        self.assertEqual(w.pairs[0]["original_name"], "一.png")
        saved = json.loads((w.session / "session.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["pairs"], w.pairs)
        w.on_pairs_imported.assert_called_once_with([0])
        w.fail.assert_not_called()
        self.assertTrue(w.pages.isEnabled())

    def test_persist_failure_removes_staged_images_and_preserves_existing_work(self):
        w = self.window
        w.import_pairs(str(self.source))
        self.wait_finished()
        before, paths = copy.deepcopy(w.pairs), self.images()
        w.on_pairs_imported = Mock()
        with patch.object(w, "persist", side_effect=OSError("disk full")):
            w.import_pairs(str(self.source))
            self.wait_finished()
        self.assertEqual(w.pairs, before)
        self.assertEqual(self.images(), paths)
        self.assertEqual(json.loads((w.session / "session.json").read_text(encoding="utf-8"))["pairs"], before)
        w.on_pairs_imported.assert_not_called()
        w.fail.assert_called_once()

    def test_bad_second_view_rolls_back_both_staged_files(self):
        (self.source / "cs" / "一.png").write_bytes(b"invalid image")
        self.window.import_pairs(str(self.source))
        self.wait_finished()
        self.assertEqual(self.images(), [])
        self.assertEqual(self.window.pairs, [])
        self.window.fail.assert_called_once()

    def test_wrong_resolution_rolls_back_and_leaves_sources(self):
        gui.write_image(self.source / "cs" / "一.png", self.image[:20])
        self.window.import_pairs(str(self.source))
        self.wait_finished()
        self.assertEqual(self.images(), [])
        self.assertTrue((self.source / "cs" / "一.png").is_file())
        self.window.fail.assert_called_once()

    def test_cancel_after_first_image_cleans_staging(self):
        cancelled = threading.Event()
        with self.assertRaises(ImportCancelled):
            stage_pairs(self.source, self.window.session, self.window.cameras, gui.read_image,
                        cancelled.is_set, lambda *_: cancelled.set())
        self.assertEqual(self.images(), [])

    def test_partial_copy_write_failure_removes_partial_file(self):
        original = Path.open

        class FailingWriter:
            def __init__(self, stream):
                self.stream = stream

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.stream.close()

            def write(self, content):
                self.stream.write(content[:10])
                raise OSError("partial write")

        def open_file(path, mode="r", *args, **kwargs):
            stream = original(path, mode, *args, **kwargs)
            return FailingWriter(stream) if mode == "xb" else stream

        with patch.object(Path, "open", open_file), self.assertRaisesRegex(OSError, "partial write"):
            stage_pairs(self.source, self.window.session, self.window.cameras, gui.read_image)
        self.assertEqual(self.images(), [])

    def test_background_import_keeps_events_live_and_close_cancels_safely(self):
        started, release = threading.Event(), threading.Event()
        reader = gui.read_image

        def blocked_reader(path):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test worker was not released")
            return reader(path)

        w = self.window
        w.show()
        tick = Mock()
        with patch.object(gui, "read_image", blocked_reader):
            try:
                w.import_pairs(str(self.source))
                self.assertTrue(started.wait(2))
                QTimer.singleShot(0, tick)
                self.app.processEvents()
                tick.assert_called_once()
                self.assertEqual(w.pairs, [])
                self.assertFalse(w.pages.isEnabled())
                w.close()
                self.assertTrue(w.close_after_import)
                self.assertTrue(w.isVisible())
            finally:
                release.set()
                self.wait_finished()
        self.assertEqual(self.images(), [])
        self.assertEqual(w.pairs, [])
        self.assertFalse(w.isVisible())
        w.fail.assert_not_called()

    def test_cancel_button_does_not_commit_or_invoke_detector(self):
        started, release = threading.Event(), threading.Event()
        reader = gui.read_image

        def blocked_reader(path):
            started.set()
            release.wait(5)
            return reader(path)

        w = self.window
        w.on_pairs_imported = Mock()
        with patch.object(gui, "read_image", blocked_reader):
            try:
                w.import_pairs(str(self.source))
                self.assertTrue(started.wait(2))
                w.import_dialog.findChild(gui.QPushButton).click()
            finally:
                release.set()
                self.wait_finished()
        self.assertEqual(w.pairs, [])
        self.assertEqual(self.images(), [])
        w.on_pairs_imported.assert_not_called()
        w.fail.assert_not_called()

    def test_missing_photo_clears_both_canvases_and_cannot_accept_points(self):
        w = self.window
        w.import_pairs(str(self.source))
        self.wait_finished()
        w.begin_annotation()
        self.assertTrue(w.mark_point("cf", 10, 15))
        before = copy.deepcopy(w.pairs)
        Path(w.pairs[0]["cs"]["path"]).unlink()
        w.show_pair()
        self.assertTrue(all(view.image_size is None for view in w.views.values()))
        self.assertFalse(w.mark_point("cf", 20, 30))
        self.assertEqual(w.pairs, before)

    def test_cancel_after_worker_finishes_before_gui_commit_is_honored(self):
        w = self.window
        w.on_pairs_imported = Mock()
        w.import_pairs(str(self.source))
        self.assertTrue(w.job.wait(5000))
        self.assertTrue(w.job.result)
        # The GUI has not received the queued finished signal yet.
        w.import_dialog.findChild(gui.QPushButton).click()
        self.wait_finished()
        self.assertEqual(w.pairs, [])
        self.assertEqual(self.images(), [])
        w.on_pairs_imported.assert_not_called()

    def test_preview_skips_same_frame_and_capture_cleans_partial_write(self):
        w = self.window
        w.capture = Mock(latest=(time.monotonic(), self.image, self.image))
        for view in w.views.values():
            view.update_preview_image = Mock()
        w.update_preview()
        w.update_preview()
        for view in w.views.values():
            view.update_preview_image.assert_called_once()
        w.capture.latest = (w.capture.latest[0] + .001, self.image, self.image)
        w.update_preview()
        for view in w.views.values():
            self.assertEqual(view.update_preview_image.call_count, 2)

        def partial_write(path, image):
            Path(path).write_bytes(b"partial")
            raise OSError("disk full")

        with patch.object(gui, "write_image", partial_write):
            w.capture_pair()
        self.assertEqual(w.pairs, [])
        self.assertEqual(self.images(), [])


if __name__ == "__main__":
    unittest.main()
