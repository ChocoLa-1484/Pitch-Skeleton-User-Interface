"""Profiles and recording copies must keep physical camera identities intact."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from Src.cameras import pitch_profiles as profiles


class PitchCameraProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.patch = patch.object(profiles.calib_store, 'CALIB_DIR', str(self.root))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        roles = patch.object(profiles.calib_store, 'load_roles', return_value=('old-front', 'old-side'))
        roles.start()
        self.addCleanup(roles.stop)

    def test_left_does_not_inherit_or_reverse_old_right_cameras(self):
        self.assertEqual(profiles.profile_serials('right'), ('old-front', 'old-side'))
        with self.assertRaisesRegex(ValueError, '尚未設定左投'):
            profiles.profile_serials('left')
        profiles.save_profile('left', 'front', 'left-side')
        profiles.save_profile('right', 'front', 'right-side')
        self.assertEqual(profiles.profile_serials('left'), ('front', 'left-side'))
        self.assertEqual(profiles.profile_serials('right'), ('front', 'right-side'))

    def test_profiles_and_calibration_paths_are_independent(self):
        left = self.root / 'left-stereo.json'
        profiles.save_profile('left', 'cf', 'csL', {'pnp': str(left)})
        profiles.save_profile('right', 'cf', 'csR')
        self.assertEqual(profiles.calibration_path('left', 'pnp'), left)
        self.assertNotEqual(profiles.calibration_path('left', '8point'), profiles.calibration_path('right', '8point'))
        for front, side in (('', 'x'), ('x', ''), ('x', 'x')):
            with self.assertRaises(ValueError):
                profiles.save_profile('left', front, side)

    def test_assignment_save_preserves_calibrations_and_all_four_serials(self):
        path = self.root / 'left-stereo.json'
        profiles.save_profile('left', 'old-front', 'old-side', {'pnp': str(path)})
        profiles.save_camera_assignments({
            'left': (' shared-front ', 'left-side'),
            'right': ('shared-front', 'right-side'),
        })
        self.assertEqual(profiles.profile_serials('left'), ('shared-front', 'left-side'))
        self.assertEqual(profiles.profile_serials('right'), ('shared-front', 'right-side'))
        self.assertEqual(profiles.calibration_path('left', 'pnp'), path)

    def test_invalid_second_hand_does_not_partially_save_first_hand(self):
        profiles.save_profile('left', 'front', 'side')
        before = profiles.profile_file().read_bytes()
        for right in (('same', 'same'), ('front', '')):
            with self.assertRaises(ValueError):
                profiles.save_camera_assignments({'left': ('new-front', 'new-side'), 'right': right})
            self.assertEqual(profiles.profile_file().read_bytes(), before)

    def rig(self, side='left-side'):
        camera = {'intrinsic_matrix': [[800., 0., 320.], [0., 800., 240.], [0., 0., 1.]],
                  'distortion_coefficients': [0.] * 5, 'image_size': [640, 480]}
        return {'schema': 'metric_stereo_v1', 'method': 'pnp', 'units': 'mm',
                'coordinate_convention': 'X_cs = R_cs_from_cf @ X_cf + t_cs_from_cf_mm',
                'R_cs_from_cf': [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]],
                't_cs_from_cf_mm': [-1000., 0., 0.],
                'intrinsics': {'cf': dict(camera, serial_number='front'),
                               'cs': dict(camera, serial_number=side)}}

    def test_recording_copies_rig_and_survives_later_profile_changes(self):
        source = self.root / 'left-stereo.json'
        original = self.rig()
        source.write_text(json.dumps(original), encoding='utf-8')
        profiles.save_profile('left', 'front', 'left-side', {'pnp': str(source)})
        folder = self.root / 'recording'
        context = profiles.save_recording_context(folder, 'left', 'front', 'left-side')
        self.assertEqual(context['warnings'], [])
        self.assertEqual(context['pitch_hand'], 'left')
        copied = profiles.recording_calibration(folder, 'pnp')
        self.assertEqual(json.loads(copied.read_text()), original)
        source.write_text('{}', encoding='utf-8')
        profiles.save_profile('left', 'different-front', 'different-side')
        self.assertEqual(json.loads(copied.read_text()), original)
        self.assertEqual(profiles.read_recording_context(folder)['side'], 'left-side')

    def test_wrong_camera_rig_is_not_attached_to_recording(self):
        source = self.root / 'wrong-stereo.json'
        source.write_text(json.dumps(self.rig('right-side')), encoding='utf-8')
        profiles.save_profile('left', 'front', 'left-side', {'pnp': str(source)})
        context = profiles.save_recording_context(self.root / 'record', 'left', 'front', 'left-side')
        self.assertNotIn('pnp', context['calibrations'])
        self.assertTrue(context['warnings'])
        self.assertIsNone(profiles.recording_calibration(self.root / 'record', 'pnp'))


class PitchCameraAssignmentsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PyQt5.QtWidgets import QApplication
        from Src.UI_Control.widgets.pitch_camera_settings import PitchCameraAssignmentsPanel
        cls.app = QApplication.instance() or QApplication([])
        cls.panel_type = PitchCameraAssignmentsPanel

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        for item in (patch.object(profiles.calib_store, 'CALIB_DIR', temp.name),
                     patch.object(profiles.calib_store, 'load_roles', return_value=('old-front', 'old-side'))):
            item.start()
            self.addCleanup(item.stop)
        self.can_save = Mock(return_value=True)
        self.panel = self.panel_type(self.can_save)
        self.addCleanup(self.panel.close)

    def test_save_and_reload_four_fields_with_legacy_right_defaults(self):
        self.assertEqual(self.panel.fields['right', 'front'].text(), 'old-front')
        self.assertEqual(self.panel.fields['left', 'front'].text(), '')
        saved = Mock()
        self.panel.saved.connect(saved)
        for hand in ('left', 'right'):
            for role in ('front', 'side'):
                self.panel.fields[hand, role].setText(f' {hand}-{role} ')
        self.panel.save_button.click()
        saved.assert_called_once_with()
        reopened = self.panel_type(self.can_save)
        self.addCleanup(reopened.close)
        for hand in ('left', 'right'):
            for role in ('front', 'side'):
                self.assertEqual(reopened.fields[hand, role].text(), f'{hand}-{role}')

    def test_open_camera_guard_prevents_writing(self):
        self.can_save.return_value = False
        self.panel.save_button.click()
        self.can_save.assert_called_once_with()
        self.assertFalse(profiles.profile_file().exists())

    def test_unconfigured_hand_can_stay_empty_but_partial_pair_cannot_save(self):
        self.panel.save_button.click()
        self.assertNotIn('left', profiles.load_profiles())
        before = profiles.profile_file().read_bytes()
        self.panel.fields['left', 'front'].setText('left-front')
        with patch('Src.UI_Control.widgets.pitch_camera_settings.QMessageBox.warning') as warning:
            self.panel.save_button.click()
        warning.assert_called_once()
        self.assertEqual(profiles.profile_file().read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
