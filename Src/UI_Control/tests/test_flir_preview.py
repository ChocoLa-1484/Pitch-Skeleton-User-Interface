"""Offline checks for hotplug, camera limits, cleanup and Qt layouts."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import threading
import unittest
from unittest.mock import MagicMock, patch
import numpy as np
from Src.cameras.flir_preview import CameraStream, PreviewService, configure_preview_resolution
from Src.UI_Control.main_flir_preview import PreviewWindow
from PyQt5.QtWidgets import QApplication


class FakeStream:
    def __init__(self, camera, sdk):
        self.camera = camera
        self.stop_event = threading.Event()
        self.alive = False
        self.started = None
    def start(self): self.alive, self.started = True, 1.0
    def is_alive(self): return self.alive and not self.stop_event.is_set()
    def join(self): self.alive = False
    def snapshot(self): return None, 0, "waiting", {}
    def set_preview_enabled(self, enabled): self.preview_enabled = enabled


class PreviewTests(unittest.TestCase):
    def make_camera(self):
        sdk, camera = MagicMock(), MagicMock()
        for name in ("Width", "Height", "OffsetX", "OffsetY"):
            node = getattr(camera, name)
            node.GetMin.return_value = 0
            node.GetMax.return_value = 4096
            node.GetInc.return_value = 1
            node.GetValue.return_value = 0
            node.SetValue.side_effect = lambda value, node=node: setattr(node.GetValue, "return_value", value)
        return sdk, camera

    def test_exact_resolution_and_unsupported_size(self):
        sdk, camera = self.make_camera()
        configure_preview_resolution(camera, sdk)
        self.assertEqual(camera.Width.GetValue(), 1920)
        self.assertEqual(camera.Height.GetValue(), 1080)
        self.assertEqual(camera.OffsetX.GetValue(), 0)
        self.assertEqual(camera.OffsetY.GetValue(), 0)
        camera.Width.GetMax.return_value = 1280
        with self.assertRaisesRegex(RuntimeError, "不支援 Width"):
            configure_preview_resolution(camera, sdk)
        camera.Width.GetMax.return_value = 4096
        camera.Height.GetInc.return_value = 16
        with self.assertRaisesRegex(RuntimeError, "不支援 Height"):
            configure_preview_resolution(camera, sdk)

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
            service.streams["0"].alive = False
            service.reconcile({str(i): object() for i in (0, 2, 3, 4, 5)})
            self.assertEqual(service.snapshot()[0]["0"][3]["reconnects"], 1)
            self.assertEqual(service.snapshot()[0]["0"][3]["last_disconnect"], "waiting")
            service.reconcile({})
            self.assertEqual(service.snapshot()[0], {})

    def test_release_image_and_camera_on_conversion_failure(self):
        sdk, camera = self.make_camera()
        image = camera.GetNextImage.return_value
        image.IsIncomplete.return_value = False
        sdk.ImageProcessor.return_value.Convert.side_effect = RuntimeError("conversion failed")
        worker = CameraStream(camera, sdk)
        image.Release.side_effect = worker.stop_event.set
        worker.run()
        self.assertEqual(image.Release.call_count, 1)
        camera.EndAcquisition.assert_called_once()
        camera.DeInit.assert_called_once()
        self.assertIsNone(worker.camera)
        self.assertIn("conversion failed", worker.snapshot()[2])
        self.assertEqual(worker.snapshot()[3]["received"], 1)

    def test_frame_copied_before_sdk_buffer_release(self):
        sdk, camera = self.make_camera()
        image = camera.GetNextImage.return_value
        image.IsIncomplete.return_value = False
        source = np.ones((12, 16, 3), dtype=np.uint8)
        def deep_copy(image):
            sdk.ImageProcessor.return_value.Convert.return_value.GetNDArray.return_value = source.copy()
        sdk.Image.Create.return_value.DeepCopy.side_effect = deep_copy
        worker = CameraStream(camera, sdk)
        def release():
            source[:] = 0
            worker.stop_event.set()
        image.Release.side_effect = release
        worker.run()
        self.assertTrue(worker.snapshot()[0].all())
        camera.TriggerMode.SetValue.assert_called_once_with(sdk.TriggerMode_Off)

    def test_full_rate_receiving_samples_only_thirty_previews_per_second(self):
        sdk, camera = self.make_camera()
        worker = CameraStream(camera, sdk)
        clock = [100.0]
        count = [0]
        capture_finished = threading.Event()
        measured = []
        converted = MagicMock()
        converted.GetNDArray.return_value = np.zeros((12, 16, 3), dtype=np.uint8)
        def slow_conversion(*args):
            # Conversion cannot finish until all 360 images have been received.
            if not capture_finished.wait(2):
                raise RuntimeError("Preview blocked the receiver")
            return converted
        sdk.ImageProcessor.return_value.Convert.side_effect = slow_conversion
        image = MagicMock()
        image.IsIncomplete.return_value = False
        def receive(timeout):
            count[0] += 1
            clock[0] = 100 + count[0] / 180
            image.GetFrameID.return_value = count[0]
            return image
        def release():
            if count[0] == 360:
                measured.append(worker.snapshot())
                capture_finished.set()
                worker.stop_event.set()
        camera.GetNextImage.side_effect = receive
        image.Release.side_effect = release
        with patch("Src.cameras.flir_preview.time.monotonic", side_effect=lambda: clock[0]):
            worker.run()
            preview, stopped_fps, status, _ = worker.snapshot()
            _, fps, _, stats = measured[0]
            self.assertEqual(stopped_fps, 0)
            self.assertIsNotNone(preview)
            self.assertNotIn("失敗", status)
            self.assertAlmostEqual(fps, 180)
            self.assertAlmostEqual(stats["average"], 180)
            self.assertEqual(stats["received"], 360)
            self.assertEqual(stats["gaps"], 0)
            self.assertLessEqual(sdk.Image.Create.call_count, 60)
            self.assertGreater(sdk.Image.Create.call_count, 45)
            self.assertEqual(image.Release.call_count, 360)
            clock[0] += 3
            self.assertEqual(worker.snapshot()[1], 0)

    def test_five_connected_four_receiving_and_preferences_survive_hotplug(self):
        service = PreviewService(sdk=object())
        cameras = {str(i): object() for i in range(5)}
        with patch("Src.cameras.flir_preview.CameraStream", FakeStream):
            service.reconcile(cameras)
            others = {sn: service.streams[sn] for sn in ("0", "1", "2", "3")}
            disabled = service.streams["4"]
            service.set_enabled("4", acquisition=False, preview=False)
            for _ in range(3):
                service.reconcile(cameras)
            self.assertEqual(len(service.snapshot()[0]), 5)
            self.assertEqual(sum(stream.is_alive() for stream in service.streams.values()), 4)
            self.assertIs(service.streams["4"], disabled)
            self.assertFalse(service.snapshot()[0]["4"][3]["acquisition_enabled"])
            service.reconcile({sn: camera for sn, camera in cameras.items() if sn != "4"})
            service.reconcile(cameras)
            self.assertNotIn("4", service.streams)
            self.assertIn("4", service.snapshot()[0])
            service.set_enabled("4", acquisition=True)
            service.reconcile(cameras)
            self.assertTrue(service.streams["4"].is_alive())
            self.assertFalse(service.streams["4"].preview_enabled)
            for sn, stream in others.items():
                self.assertIs(service.streams[sn], stream)

    def test_hidden_preview_keeps_receiving_without_copy_or_conversion(self):
        sdk, camera = self.make_camera()
        worker = CameraStream(camera, sdk)
        worker.set_preview_enabled(False)
        image = camera.GetNextImage.return_value
        image.IsIncomplete.return_value = False
        count = [0]
        def release():
            count[0] += 1
            if count[0] == 10:
                worker.stop_event.set()
        image.Release.side_effect = release
        worker.run()
        self.assertEqual(worker.snapshot()[3]["received"], 10)
        self.assertIsNone(worker.snapshot()[0])
        sdk.Image.Create.assert_not_called()
        sdk.ImageProcessor.return_value.Convert.assert_not_called()
        camera.EndAcquisition.assert_called_once()

    def test_toggle_ui_targets_correct_camera(self):
        app = QApplication.instance() or QApplication([])
        service = MagicMock()
        service.is_alive.return_value = False
        service.snapshot.return_value = ({str(i): (None, 180, "live", {}) for i in range(5)}, "ok")
        window = PreviewWindow(service)
        window.refresh()
        tile = window.tiles["4"]
        tile.preview.click()
        service.set_enabled.assert_called_with("4", preview=False)
        tile.acquisition.click()
        service.set_enabled.assert_called_with("4", acquisition=False)
        service.set_enabled.reset_mock()
        tile.display(None, 0, "接收已關閉", {"acquisition_enabled": False, "preview_enabled": False})
        self.assertFalse(tile.acquisition.isChecked())
        self.assertFalse(tile.preview.isChecked())
        self.assertIn("接收已關閉", tile.image.text())
        service.set_enabled.assert_not_called()
        window.close()

    def test_preview_copy_failure_does_not_stop_acquisition(self):
        sdk, camera = self.make_camera()
        sdk.Image.Create.return_value.DeepCopy.side_effect = RuntimeError("out of memory")
        worker = CameraStream(camera, sdk)
        image = camera.GetNextImage.return_value
        image.IsIncomplete.return_value = False
        clock, count = [100.0], [0]
        def release():
            count[0] += 1
            clock[0] += 0.1  # every frame is due for a preview sample
            if count[0] == 10:
                worker.stop_event.set()
        image.Release.side_effect = release
        with patch("Src.cameras.flir_preview.time.monotonic", side_effect=lambda: clock[0]):
            worker.run()
        stats = worker.snapshot()[3]
        self.assertEqual(stats["received"], 10)
        self.assertEqual(stats["errors"], 0)
        self.assertGreaterEqual(sdk.Image.Create.call_count, 3)

    def test_failed_reopen_attempts_are_not_counted_as_reconnects(self):
        service = PreviewService(sdk=object())
        cameras = {"0": object()}
        with patch("Src.cameras.flir_preview.CameraStream", FakeStream):
            service.reconcile(cameras)
            for _ in range(3):  # Init() fails: the stream dies without ever streaming
                stream = service.streams["0"]
                stream.alive, stream.started = False, None
                service.reconcile(cameras)
            self.assertEqual(service.snapshot()[0]["0"][3]["reconnects"], 0)

    def test_reenabling_while_old_stream_is_stopping_restarts_immediately(self):
        service = PreviewService(sdk=object())
        cameras = {"0": object()}
        with patch("Src.cameras.flir_preview.CameraStream", FakeStream):
            service.reconcile(cameras)
            old = service.streams["0"]
            service.set_enabled("0", acquisition=False)
            old.is_alive = lambda: old.alive  # still shutting down
            service.set_enabled("0", acquisition=True)
            service.reconcile(cameras)
            self.assertIsNot(service.streams["0"], old)
            self.assertFalse(old.alive)
            self.assertEqual(service.snapshot()[0]["0"][3]["reconnects"], 0)

    def test_incomplete_frames_and_gaps_are_separate(self):
        sdk, camera = self.make_camera()
        worker = CameraStream(camera, sdk)
        images = [MagicMock() for _ in range(3)]
        for image, frame_id, incomplete in zip(images, (10, 11, 14), (False, True, False)):
            image.GetFrameID.return_value = frame_id
            image.IsIncomplete.return_value = incomplete
        images[-1].Release.side_effect = worker.stop_event.set
        camera.GetNextImage.side_effect = images
        worker.run()
        stats = worker.snapshot()[3]
        self.assertEqual(stats["received"], 2)
        self.assertEqual(stats["incomplete"], 1)
        self.assertEqual(stats["gaps"], 2)
        for image in images:
            image.Release.assert_called_once()

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
