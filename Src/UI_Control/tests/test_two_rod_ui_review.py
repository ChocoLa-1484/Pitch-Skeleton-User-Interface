"""Regression coverage for saved annotations and the eight-ball confirmation gate."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import cv2
import numpy as np

from Src.calibration import calibrate_two_rods as gui


class TwoRodUiReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = gui.QApplication.instance() or gui.QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        store = patch.object(gui.calib_store, 'CALIB_DIR', str(self.root))
        store.start()
        self.addCleanup(store.stop)
        with patch.object(gui.TwoRodWindow, '_load_defaults'):
            self.window = gui.TwoRodWindow()
        self.addCleanup(self.close_window)
        self.window.fail = Mock()
        camera = {'intrinsic_matrix': [[500., 0, 160], [0, 500., 120], [0, 0, 1]],
                  'distortion_coefficients': [0.] * 5, 'image_size': [320, 240]}
        self.window.set_intrinsics({r: copy.deepcopy(camera) for r in gui.ROLES}, 'intrinsics.json')
        self.window.start_session()
        pair = {'id': 'one', 'confirmed': True}
        for role in gui.ROLES:
            path = self.window.session / role / 'one.png'
            cv2.imwrite(str(path), np.zeros((240, 320, 3), np.uint8))
            points = [[50 + 150 * (i // 4), 40 + 40 * (i % 4)] for i in range(8)]
            pair[role] = {'path': str(path), 'image_size': [320, 240],
                          'points': dict(zip(gui.IDS, points))}
        self.window.pairs = [pair]
        self.window.persist()
        self.window.begin_annotation()
        self.session_file = self.window.session / 'session.json'

    def close_window(self):
        self.wait_for_job()
        self.window.close()

    def wait_for_job(self):
        deadline = time.monotonic() + 10
        while self.window.job is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertIsNone(self.window.job)

    def test_failed_annotation_writes_preserve_points_confirmation_and_result(self):
        window = self.window
        expected = copy.deepcopy(window.pairs)
        saved = self.session_file.read_bytes()
        result = {'existing_result': True}
        window.result = result
        actions = (
            lambda: window.mark_point('cf', 35, 45),
            lambda: window.undo_point('cf'),
            lambda: window.reverse_rod('cf', 'A'),
            window.confirm_pair,
        )
        for action in actions:
            with self.subTest(action=action), patch.object(window, 'persist', side_effect=OSError('disk full')):
                action()
                self.assertEqual(window.pairs, expected)
                self.assertIs(window.result, result)
                self.assertEqual(self.session_file.read_bytes(), saved)
        self.assertEqual(window.fail.call_count, len(actions))

    def test_committed_edit_invalidates_confirmation_and_result(self):
        window = self.window
        window.result = {'existing_result': True}
        self.assertTrue(window.mark_point('cf', 35, 45))
        self.assertFalse(window.pairs[0]['confirmed'])
        self.assertIsNone(window.result)
        saved = json.loads(self.session_file.read_text(encoding='utf-8'))
        self.assertFalse(saved['pairs'][0]['confirmed'])
        self.assertEqual(saved['pairs'][0]['cf']['points']['A1'], [35., 45.])

    def test_busy_edit_does_not_invalidate_confirmed_pair(self):
        window = self.window
        before = copy.deepcopy(window.pairs)
        window.job = Mock()
        try:
            window.mark_point('cf', 35, 45)
            window.undo_point('cs')
            window.confirm_pair()
        finally:
            window.job = None
        self.assertEqual(window.pairs, before)

    def test_failed_detection_save_preserves_previous_manual_annotations(self):
        window = self.window
        before = copy.deepcopy(window.pairs)
        saved = self.session_file.read_bytes()
        result = {'existing_result': True}
        window.result = result
        detection = {'points': {}, 'candidate_count': 0, 'note': 'no candidates'}
        with patch.object(gui, 'suggest_points', return_value=detection), \
                patch.object(window, 'persist', side_effect=OSError('disk full')):
            window.redetect_current()
            self.wait_for_job()
        self.assertEqual(window.pairs, before)
        self.assertEqual(self.session_file.read_bytes(), saved)
        self.assertIs(window.result, result)
        self.assertTrue(window.pages.isEnabled())
        window.fail.assert_called_once()

    def test_failed_resume_does_not_replace_detector_settings(self):
        window = self.window
        before = copy.deepcopy(window.pairs)
        detector = copy.deepcopy(window.active_detector)
        data = json.loads(self.session_file.read_text(encoding='utf-8'))
        data['detector']['hue_min'] = 100
        data['pairs'][0]['cf']['path'] = str(self.root / 'missing.png')
        path = self.root / 'invalid.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        self.assertFalse(window.resume_session(str(path)))
        self.assertEqual(window.pairs, before)
        self.assertEqual(window.active_detector, detector)
        self.assertEqual(window.detector_fields['hue_min'].value(), detector['hue_min'])

    def test_resume_rejects_invalid_confirmation_and_detection_metadata(self):
        window = self.window
        original = json.loads(self.session_file.read_text(encoding='utf-8'))
        before = copy.deepcopy(window.pairs)
        path = self.root / 'invalid.json'
        cases = [('confirmed', 'yes'), ('detection', ['unexpected'])]
        for kind, value in cases:
            data = copy.deepcopy(original)
            if kind == 'confirmed':
                data['pairs'][0]['confirmed'] = value
            else:
                data['pairs'][0]['cf']['detection'] = value
            path.write_text(json.dumps(data), encoding='utf-8')
            with self.subTest(kind=kind):
                self.assertFalse(window.resume_session(str(path)))
                self.assertEqual(window.pairs, before)

    def test_duplicate_centers_cannot_be_confirmed(self):
        pair = self.window.pairs[0]
        pair['confirmed'] = False
        pair['cf']['points']['A2'] = pair['cf']['points']['A1'][:]
        self.window.confirm_pair()
        self.assertFalse(pair['confirmed'])
        self.window.fail.assert_called_once()

    def test_old_partial_detection_metadata_can_be_displayed(self):
        self.window.pairs[0]['cf']['detection'] = {'note': 'manual annotation'}
        self.window.update_annotation_status()
        self.assertIn('manual annotation', self.window.status.text())


if __name__ == '__main__':
    unittest.main()
