"""Profiles and recording copies must keep physical camera identities intact."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == '__main__':
    unittest.main()
