"""Real main-window hand synchronization with models/camera capture mocked."""
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"


class PitchHandUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import Src.UI_Control.main as main
        from Src.cameras import pitch_profiles
        cls.main, cls.profiles = main, pitch_profiles
        cls.app = main.QApplication.instance() or main.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.object(self.profiles.calib_store, "CALIB_DIR", self.temp.name)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.profiles.save_profile("right", "front", "side-R")
        self.profiles.save_profile("left", "front", "side-L")
        with patch.object(self.main, "Model", return_value=SimpleNamespace(setImageSize=Mock())):
            self.window = self.main.Main()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.close()
        self.app.processEvents()

    def test_switch_from_each_tab_synchronizes_camera_and_analysis_hand(self):
        self.window.pitch_tab.ui.pitchInput_L.setChecked(True)
        self.assertEqual(self.window.pitch_hand, "left")
        for _, camera in self.window.camera_sources():
            self.assertEqual(camera.pitch_hand, "left")
        self.assertEqual(self.window.video_tab.heel_combo.currentData(), 24)
        self.assertEqual(self.window.video_tab.pose_estimater.pitch_hand_id, 9)
        self.window.video_tab.calibration_combo.setCurrentIndex(0)
        self.assertIn("left", self.window.video_tab.calibration_path.text())
        self.window.video_tab.heel_combo.setCurrentIndex(0)
        self.assertEqual(self.window.pitch_hand, "right")
        self.assertTrue(self.window.pitch_tab.ui.pitchInput.isChecked())
        self.assertEqual(self.window.pitch_tab.pose_estimater_2.pitch_hand_id, 10)
        self.assertEqual(self.window.pitch_hand_combo.currentData(), "right")

    def test_live_camera_reconnects_with_corresponding_serials_and_blocks_during_recording(self):
        camera = self.window.camera_tab.camera
        calls = []

        def toggle(enabled):
            calls.append((enabled, self.profiles.profile_serials(camera.pitch_hand)))
            camera.is_opened = enabled
            return 1920, 1080, 179

        with patch.object(camera, "toggleCamera", side_effect=toggle):
            self.window.camera_tab.ui.cameraCheckBox.setChecked(True)
            self.window.set_pitch_hand("left")
            self.assertEqual(calls, [(True, ("front", "side-R")), (False, ("front", "side-R")),
                                     (True, ("front", "side-L"))])
            camera.is_auto_recording = True
            with patch.object(self.main.QMessageBox, "warning"):
                self.assertFalse(self.window.set_pitch_hand("right"))
            self.assertEqual(self.window.pitch_hand, "left")
            self.assertEqual(self.window.pitch_hand_combo.currentData(), "left")
            self.assertEqual(len(calls), 3)
            camera.is_auto_recording = False
            self.window.camera_tab.ui.cameraCheckBox.setChecked(False)


if __name__ == "__main__":
    unittest.main()
