"""Offline checks for hotplug, camera limits, cleanup and Qt layouts."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import threading
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
from Src.cameras.flir_preview import CameraStream, PreviewService
from Src.UI_Control.main_flir_preview import PreviewWindow
from PyQt5.QtWidgets import QApplication


class FakeStream:
    def __init__(self, camera, sdk):
        self.camera = camera
        self.stop_event = threading.Event()
        self.alive = False
    def start(self): self.alive = True
    def is_alive(self): return self.alive
    def join(self): self.alive = False
    def snapshot(self): return None, 0, "waiting"


class PreviewTests(unittest.TestCase):
    def test_hotplug_limit_preserves_active_streams(self):
        service = PreviewService(sdk=object())
        with patch("Src.cameras.flir_preview.CameraStream", FakeStream):
            service.reconcile({str(i): object() for i in range(6)})
            self.assertEqual(len(service.streams), 5)
            first = service.streams["0"]
            removed = service.streams["1"]
            service.reconcile({str(i): object() for i in (0, 2, 3, 4, 5)})
            self.assertIs(service.streams["0"], first)
            self.assertTrue(removed.stop_event.is_set())
            self.assertIn("5", service.streams)
            service.reconcile({})
            self.assertEqual(service.snapshot()[0], {})

    def test_release_image_and_camera_on_conversion_failure(self):
        sdk, camera = MagicMock(), MagicMock()
        image = camera.GetNextImage.return_value
        image.IsIncomplete.return_value = False
        sdk.ImageProcessor.return_value.Convert.side_effect = RuntimeError("conversion failed")
        worker = CameraStream(camera, sdk)
        worker.run()
        self.assertEqual(image.Release.call_count, 3)
        camera.EndAcquisition.assert_called_once()
        camera.DeInit.assert_called_once()
        self.assertIsNone(worker.camera)
        self.assertIn("conversion failed", worker.snapshot()[2])

    def test_frame_copied_before_sdk_buffer_release(self):
        sdk, camera = MagicMock(), MagicMock()
        image = camera.GetNextImage.return_value
        image.IsIncomplete.return_value = False
        source = np.ones((12, 16, 3), dtype=np.uint8)
        sdk.ImageProcessor.return_value.Convert.return_value.GetNDArray.return_value = source
        worker = CameraStream(camera, sdk)
        def release():
            source[:] = 0
            worker.stop_event.set()
        image.Release.side_effect = release
        worker.run()
        self.assertTrue(worker.snapshot()[0].all())
        camera.TriggerMode.SetValue.assert_called_once_with(sdk.TriggerMode_Off)

    def test_layouts_and_removal(self):
        app = QApplication.instance() or QApplication([])
        service = MagicMock()
        service.is_alive.return_value = False
        service.snapshot.return_value = ({}, "empty")
        window = PreviewWindow(service)
        window.show()
        for count in (0, 1, 2, 3, 4, 5, 1, 0):
            frame = np.zeros((108, 192, 3), dtype=np.uint8)
            service.snapshot.return_value = ({str(i): (frame, 25, "live") for i in range(count)}, "ok")
            window.refresh()
            app.processEvents()
            self.assertEqual(len(window.tiles), count)
            self.assertEqual(window.grid.count(), count)
            for tile in window.tiles.values():
                self.assertGreater(tile.width(), 160)
                self.assertFalse(tile.image.pixmap().isNull())
        window.close()


if __name__ == "__main__":
    unittest.main()
