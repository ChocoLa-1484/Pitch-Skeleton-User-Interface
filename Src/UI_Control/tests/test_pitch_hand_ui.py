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

    def test_camera_fields_follow_selected_hand_and_keep_unsaved_edits(self):
        from PyQt5.QtCore import QPoint
        panel = self.window.pitch_camera_assignments
        self.window.ui.Two_d_Tab.setCurrentIndex(0)
        self.window.show()
        self.window.resize(1500, 960)
        for _ in range(4):
            self.app.processEvents()

        def geometry(hand):
            return [(edit.mapTo(panel, QPoint(0, 0)), edit.size())
                    for role in ('front', 'side') for edit in [panel.fields[hand, role]]]

        original = geometry('right')
        panel.fields['right', 'front'].setText('edited-front')
        self.window.set_pitch_hand('left')
        for _ in range(4):
            self.app.processEvents()
        self.assertEqual(geometry('left'), original)
        for hand in ('left', 'right'):
            for role in ('front', 'side'):
                self.assertEqual(panel.fields[hand, role].isHidden(), hand == 'right')
        self.assertEqual(self.window.pitch_tab.pitching_analyzer.hand, 'left')
        self.window.set_pitch_hand('right')
        for _ in range(4):
            self.app.processEvents()
        self.assertEqual(geometry('right'), original)
        self.assertEqual(panel.fields['right', 'front'].text(), 'edited-front')

    def test_2d_3d_video_cards_and_sliders_have_same_size(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtWidgets import QLabel
        self.window.show()
        for width, height in ((1500, 960), (980, 800)):
            self.window.resize(width, height)
            sizes = []
            heading_fonts = []
            for index in (1, 2):
                self.window.ui.Two_d_Tab.setCurrentIndex(index)
                for _ in range(4):
                    self.app.processEvents()
                ui = (self.window.pitch_tab if index == 1 else self.window.video_tab).ui
                sizes.append([(getattr(ui, name).width(), getattr(ui, name).height())
                              for name in ('FrameView', 'FrameView_2', 'fcView', 'merView', 'brView', 'frameSlider')])
                sizes[-1].extend([(getattr(ui, name).width(), getattr(ui, name).height())
                                  for name in ('footContact_ptn', 'maxER_ptn', 'release_ptn')])
                self.assertFalse(ui.frameSlider.isHidden())
                self.assertTrue(ui.resetBRBtn.isHidden())
                headings = self.window.responsive_pages[index].findChildren(QLabel, 'viewTitle')
                self.assertEqual([label.text() for label in headings], ['正面 CF', '側面 CS'])
                heading_fonts.append([(label.font().weight(), label.font().pointSize()) for label in headings])
                if index == 1:
                    self.assertTrue(ui.line.isHidden())
                    skeleton_row = ui.gridLayout.getItemPosition(ui.gridLayout.indexOf(ui.showSkeletonCheckBox))
                    release_row = ui.gridLayout.getItemPosition(ui.gridLayout.indexOf(ui.showBR_checkBox))
                    self.assertEqual(release_row[:2], (skeleton_row[0] + 1, skeleton_row[1]))
                else:
                    self.assertTrue(ui.selectCheckBox.isHidden())
                    self.assertEqual(ui.FC_label_unit.text(), '°\ncm')
                    self.assertEqual(ui.MER_label_unit.text(), '°\n')
                    self.assertEqual(ui.BR_label_unit.text(), 'm/s\ncm')
                    for metric in (ui.MER_label_2, ui.MER_label_3, ui.MER_label_unit):
                        self.assertTrue(metric.alignment() & Qt.AlignVCenter)
            self.assertEqual(sizes[0], sizes[1], (width, height, sizes))
            self.assertEqual(heading_fonts[0], heading_fonts[1])

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
