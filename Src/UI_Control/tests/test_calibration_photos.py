"""Photo-based calibration without camera capture or writes to real calibration files."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np


class CalibrationPhotoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(sys.modules, {'PySpin': MagicMock()}):
            import calib
        cls.calib = calib
        cls.app = calib.QApplication.instance() or calib.QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / '棋盤格照片'
        self.folder.mkdir()
        with patch.object(self.calib, 'list_cameras', return_value=[]), \
             patch.object(self.calib.calib_store, 'load_roles', return_value=None), \
             patch.object(self.calib.calib_store, 'load_intrinsics', return_value={}):
            self.window = self.calib.CalibWindow()
        self.addCleanup(self.window.close)
        self.window.timer.stop()

    def board(self, dx=0, dy=0):
        image = np.full((480, 640, 3), 255, np.uint8)
        for row in range(12):
            for col in range(9):
                x, y = 70 + dx + col * 30, 40 + dy + row * 30
                image[y:y + 30, x:x + 30] = 0 if (row + col) % 2 == 0 else 255
        return image

    def save_image(self, name, image):
        path = self.folder / name
        ok, data = cv2.imencode(path.suffix, image)
        self.assertTrue(ok)
        data.tofile(str(path))

    def test_import_detects_real_corners_and_skips_invalid_or_duplicate_photos(self):
        self.save_image('01.png', self.board())
        self.save_image('02.png', self.board())
        self.save_image('03.png', self.board(20, 20))
        self.save_image('04.png', np.zeros((480, 640, 3), np.uint8))
        self.save_image('05.png', np.zeros((100, 100, 3), np.uint8))
        (self.folder / '06.jpg').write_bytes(b'broken image')
        self.assertTrue(self.window.load_photo_folder(self.folder, '123456'))
        self.assertIsNone(self.window.camera)
        self.assertEqual(self.window.connected_sn, '123456')
        self.assertEqual(self.window.captured_count, 2)
        self.assertEqual(self.window.image_size, (640, 480))
        self.assertEqual(self.window.imgpoints[0].shape, (88, 1, 2))
        self.assertEqual([Path(p).name for p in self.window._photo_paths], ['01.png', '03.png'])
        np.testing.assert_allclose(self.window.objpoints[0][1], [10, 0, 0])
        log = self.window.log_area.toPlainText()
        for reason in ('幾乎相同', '影像尺寸', '未完整偵測', '無法解碼'):
            self.assertIn(reason, log)

    def test_cancel_and_empty_or_invalid_batch_preserve_previous_samples(self):
        self.save_image('01.png', self.board())
        self.save_image('02.png', self.board(20, 20))
        self.assertTrue(self.window.load_photo_folder(self.folder, '123456'))
        original = self.window.imgpoints
        progress = Mock()
        progress.wasCanceled.side_effect = [False, True]
        with patch.object(self.calib, 'QProgressDialog', return_value=progress):
            self.assertFalse(self.window.load_photo_folder(self.folder, '999999'))
        self.assertIs(self.window.imgpoints, original)
        self.assertEqual(self.window.connected_sn, '123456')
        empty = self.folder / 'empty'
        empty.mkdir()
        self.assertFalse(self.window.load_photo_folder(empty, '999999'))
        (empty / 'broken.jpg').write_bytes(b'broken')
        self.assertFalse(self.window.load_photo_folder(empty, '999999'))
        self.assertIs(self.window.imgpoints, original)

    def test_import_cannot_mix_live_camera_or_unsafe_serial(self):
        self.save_image('01.png', self.board())
        self.window.camera = Mock()
        self.assertFalse(self.window.load_photo_folder(self.folder, '123456'))
        self.window.camera = None
        for serial in ('', '../other', '12/34'):
            self.assertFalse(self.window.load_photo_folder(self.folder, serial))
        self.assertEqual(self.window.captured_count, 0)

    def test_changed_square_size_requires_reloading_photos(self):
        self.save_image('01.png', self.board())
        self.window.load_photo_folder(self.folder, '123456')
        self.window.spn_square.setValue(100)
        with patch.object(self.window, '_calibrate') as solve:
            self.window.run_calibration()
            solve.assert_not_called()
        self.assertIn('規格與樣本不符', self.window.log_area.toPlainText())

    def test_offline_calibration_saves_under_imported_serial_with_source_paths(self):
        self.save_image('01.png', self.board())
        self.window.load_photo_folder(self.folder, '123456')
        self.window.objpoints *= 10
        self.window.imgpoints *= 10
        self.window._photo_paths *= 10
        self.window.captured_count = 10
        K = np.array([[800., 0, 320], [0, 800, 240], [0, 0, 1]])
        dist = np.zeros((1, 5))
        output = Path(self.temp.name) / 'output'
        with patch.object(self.window, '_calibrate', return_value=(
                0.2, K, dist, np.full(10, 0.2), np.full(10, 25.))), \
             patch.object(self.window, '_assess', return_value=('良好', [], [])), \
             patch.object(self.calib.calib_store, 'INTRINSIC_DIR', str(output)), \
             patch.object(self.calib.calib_store, 'INTRINSICS_JSON', str(output / 'intrinsics_all.json')):
            self.window.run_calibration()
            entry = self.calib.calib_store.load_intrinsics()['123456']
        self.assertEqual(entry['sample_count'], 10)
        self.assertEqual(entry['source_images'], self.window._photo_paths)
        with np.load(str(output / 'calib_SN123456.npz'), allow_pickle=False) as data:
            np.testing.assert_array_equal(data['mtx'], K)
            self.assertEqual(data['serial_number'].item(), '123456')
            self.assertEqual(data['image_size'].tolist(), [640, 480])

    def test_reset_clears_photo_geometry_before_live_capture(self):
        self.save_image('01.png', self.board())
        self.window.load_photo_folder(self.folder, '123456')
        self.window.reset_samples()
        self.assertIsNone(self.window.image_size)
        self.assertIsNone(self.window._sample_spec)
        self.assertEqual(self.window._photo_paths, [])
        self.assertEqual(self.window.captured_count, 0)


if __name__ == '__main__':
    unittest.main()
