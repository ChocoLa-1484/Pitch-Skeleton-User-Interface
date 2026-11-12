"""Photo-based calibration without camera capture or writes to real calibration files."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import cv2
import numpy as np


class CalibrationPhotoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(sys.modules, {'PySpin': MagicMock()}):
            import Src.calibration.calib as calib
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
        for row in range(8):
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
        self.assertEqual(self.window.imgpoints[0].shape, (56, 1, 2))
        self.assertEqual([Path(p).name for p in self.window._photo_paths], ['01.png', '03.png'])
        np.testing.assert_allclose(self.window.objpoints[0][1], [100, 0, 0])
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
        self.window.spn_square.setValue(150)
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
             patch.object(self.calib.calib_store, 'INTRINSIC_DIR', str(output)):
            self.window.run_calibration()
            entry = self.calib.calib_store.load_intrinsics()['123456']
        self.assertEqual(entry['sample_count'], 10)
        # A folder outside Intrinsic_<date>/<hand>/<view> is saved under today's date.
        dated = output / ('Intrinsic_' + self.window._session_date)
        # Photo paths are stored relative to the day folder (portable when Db moves) and resolve back.
        store = self.calib.calib_store
        self.assertTrue(all(not os.path.isabs(p) and '\\' not in p for p in entry['source_images']))
        self.assertEqual(entry['source_images'][0], '../../棋盤格照片/01.png')
        self.assertEqual([store.resolve_intrinsic_path(p, dated) for p in entry['source_images']],
                         self.window._photo_paths)
        daily = json.loads((dated / 'Intrinsics_all.json').read_text(encoding='utf-8'))
        self.assertEqual(daily['123456_Right_CF']['view'], 'CF')
        self.assertEqual(daily['123456_Right_CF']['detector'], 'findChessboardCornersSB')
        self.assertFalse((output / 'intrinsics_all.json').exists())   # no top-level table
        # Everything that used to be in the npz is in the JSON; no npz is written.
        self.assertEqual(list(output.rglob('*.npz')), [])
        np.testing.assert_array_equal(daily['123456_Right_CF']['intrinsic_matrix'], K)
        self.assertEqual(daily['123456_Right_CF']['image_size'], [640, 480])
        self.assertEqual(daily['123456_Right_CF']['per_image_rms'], [0.2] * 10)
        self.assertEqual(daily['123456_Right_CF']['tilt_deg'], [25.0] * 10)

    def test_dated_folder_sets_hand_view_and_output_date(self):
        folder = Path(self.temp.name) / 'Intrinsic_20261029' / 'Left_CS' / '999_20261029_abc123'
        folder.mkdir(parents=True)
        self.folder = folder
        self.save_image('01.png', self.board())
        self.save_image('02.png', self.board(20, 20))
        self.assertTrue(self.window.load_photo_folder(folder, '123456'))
        self.assertEqual((self.window.hand(), self.window.view()), ('left', 'CS'))
        self.assertIn('資料夾名稱的序號是 999', self.window.log_area.toPlainText())
        self.assertEqual(self.window._session_date, '20261029')
        with patch.object(self.calib.calib_store, 'INTRINSIC_DIR', str(Path(self.temp.name) / 'out')):
            self.assertEqual(Path(self.window.result_dir()).name, 'Intrinsic_20261029')
            # An existing lower-case day folder from before the rename is reused, not duplicated.
            (Path(self.temp.name) / 'out' / 'intrinsic_20261029').mkdir(parents=True)
            self.assertEqual(Path(self.window.result_dir()).name, 'intrinsic_20261029')

    def test_images_subfolder_is_recognised(self):
        parse = self.calib.calib_store.parse_intrinsic_photo_dir
        base = Path('Db') / 'Calibration' / 'Intrinsics' / 'Intrinsic_20261111' / 'Right_CF'
        self.assertEqual(parse(base / '24380115_20261111_a1b2c3'), ('20261111', 'right', 'CF', '24380115'))
        self.assertEqual(parse(base), ('20261111', 'right', 'CF', None))
        self.assertEqual(parse(base.parent / 'left_cs'), ('20261111', 'left', 'CS', None))
        self.assertIsNone(parse(Path('somewhere') / 'photos'))
        self.assertIsNone(parse(base.parent / 'Right' / 'CF'))   # old two-level layout is no longer used

    def test_load_intrinsics_merges_days_and_keeps_newest(self):
        store = self.calib.calib_store
        root = Path(self.temp.name) / 'Intrinsics'
        with patch.object(store, 'INTRINSIC_DIR', str(root)):
            self.assertEqual(store.load_intrinsics(), {})
            def entry(fx, at):
                return {'intrinsic_matrix': [[fx, 0, 0], [0, fx, 0], [0, 0, 1]],
                        'distortion_coefficients': [[0] * 5], 'fx': fx, 'calibrated_at': at}
            store.save_dated_intrinsic('20261029', 'A', entry(1, '2026-10-29T10:00:00'), 'right', 'CF')
            store.save_dated_intrinsic('20261029', 'B', entry(2, '2026-10-29T10:00:00'), 'right', 'CS')
            store.save_dated_intrinsic('20261111', 'A', entry(3, '2026-11-11T09:00:00'), 'right', 'CF')
            merged = store.load_intrinsics()
            self.assertEqual((merged['A']['fx'], merged['B']['fx']), (3, 2))
            self.assertTrue(store.latest_intrinsics_json().endswith(
                os.path.join('Intrinsic_20261111', 'Intrinsics_all.json')))
            first = store.new_capture_dir('20261111', 'left', 'CS', '24380115')
            self.assertRegex(Path(first).name, r'^24380115_20261111_[0-9a-f]{6}$')
            self.assertEqual(Path(first).parent.parts[-2:], ('Intrinsic_20261111', 'Left_CS'))
        self.assertFalse((root / 'intrinsics_all.json').exists())

    def test_live_photos_and_capture_dir_are_stored_relative_to_day_folder(self):
        store = self.calib.calib_store
        root = Path(self.temp.name) / 'Intrinsics'
        with patch.object(store, 'INTRINSIC_DIR', str(root)):
            capture = Path(store.new_capture_dir('20261112', 'left', 'CF', '24380115'))
            capture.mkdir(parents=True)
            photo = capture / '001_101500_123.png'
            photo.write_bytes(b'png')
            entry = {'intrinsic_matrix': [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                     'distortion_coefficients': [[0] * 5],
                     'source_images': [str(photo)], 'capture_dir': str(capture)}
            path, _ = store.save_dated_intrinsic('20261112', '24380115', entry, 'left', 'CF')
            saved = json.loads(Path(path).read_text(encoding='utf-8'))['24380115_Left_CF']
        self.assertEqual(saved['capture_dir'], 'Left_CF/' + capture.name)
        self.assertEqual(saved['source_images'], ['Left_CF/{}/001_101500_123.png'.format(capture.name)])
        self.assertEqual(store.resolve_intrinsic_path(saved['source_images'][0], Path(path).parent),
                         str(photo.resolve()))
        # Old absolute paths are still understood.
        self.assertEqual(store.resolve_intrinsic_path(str(photo.resolve()), Path(path).parent), str(photo.resolve()))

    def test_same_camera_as_cf_for_both_hands_keeps_both_entries(self):
        store = self.calib.calib_store
        root = Path(self.temp.name) / 'Intrinsics'

        def entry(fx, at):
            return {'intrinsic_matrix': [[fx, 0, 0], [0, fx, 0], [0, 0, 1]],
                    'distortion_coefficients': [[0] * 5], 'fx': fx, 'calibrated_at': at}
        with patch.object(store, 'INTRINSIC_DIR', str(root)):
            # Same day, same camera, CF for the right then the left rig (e.g. refocused).
            store.save_dated_intrinsic('20261112', '24380115', entry(1400, '2026-11-12T09:00:00'), 'right', 'CF')
            path, count = store.save_dated_intrinsic(
                '20261112', '24380115', entry(1500, '2026-11-12T10:00:00'), 'left', 'CF')
            self.assertEqual(count, 2)
            daily = json.loads(Path(path).read_text(encoding='utf-8'))
            self.assertEqual(set(daily), {'24380115_Right_CF', '24380115_Left_CF'})
            self.assertEqual(daily['24380115_Left_CF']['serial_number'], '24380115')
            # Each rig gets its own intrinsics; an unknown hand falls back to the newest.
            self.assertEqual(store.get_intrinsic('24380115', 'right', 'CF')[0][0, 0], 1400)
            self.assertEqual(store.get_intrinsic('24380115', 'left', 'CF')[0][0, 0], 1500)
            self.assertEqual(store.get_intrinsic('24380115')[0][0, 0], 1500)
            self.assertEqual(list(store.load_intrinsics()), ['24380115'])
            # Redoing the right rig the same day replaces only that entry.
            store.save_dated_intrinsic('20261112', '24380115', entry(1410, '2026-11-12T11:00:00'), 'right', 'CF')
            self.assertEqual(store.get_intrinsic('24380115', 'right', 'CF')[0][0, 0], 1410)
            self.assertEqual(store.get_intrinsic('24380115', 'left', 'CF')[0][0, 0], 1500)

    def test_old_serial_keys_are_rekeyed_not_overwritten(self):
        store = self.calib.calib_store
        day = Path(self.temp.name) / 'Intrinsics' / 'Intrinsic_20261111'
        day.mkdir(parents=True)
        old = {'intrinsic_matrix': [[1, 0, 0], [0, 1, 0], [0, 0, 1]], 'distortion_coefficients': [[0] * 5],
               'pitch_hand': 'right', 'view': 'CS', 'calibrated_at': '2026-11-11T23:47:00'}
        (day / 'Intrinsics_all.json').write_text(json.dumps({'21055356': old}), encoding='utf-8')
        with patch.object(store, 'INTRINSIC_DIR', str(day.parent)):
            self.assertIsNotNone(store.get_intrinsic('21055356', 'right', 'CS'))
            new = dict(old, intrinsic_matrix=[[2, 0, 0], [0, 2, 0], [0, 0, 1]], view='CF',
                       calibrated_at='2026-11-11T23:59:00')
            store.save_dated_intrinsic('20261111', '21055356', new, 'left', 'CF')
            daily = json.loads((day / 'Intrinsics_all.json').read_text(encoding='utf-8'))
        self.assertEqual(set(daily), {'21055356_Right_CS', '21055356_Left_CF'})

    def test_board_geometry_classifies_tilt_direction(self):
        pattern = (8, 7)
        grid = np.mgrid[0:8, 0:7].T.reshape(-1, 2).astype(np.float64)
        flat = grid * 40 + 100
        squeezed_y = flat.copy()
        squeezed_y[:, 1] = (grid[:, 1] * 40 * np.cos(np.radians(35))) + 100
        squeezed_x = flat.copy()
        squeezed_x[:, 0] = (grid[:, 0] * 40 * np.cos(np.radians(35))) + 100
        geometry = self.calib.board_geometry
        self.assertEqual(geometry(flat, pattern, (640, 480))['kind'], 'frontal')
        tilted = geometry(squeezed_y, pattern, (640, 480))
        self.assertEqual(tilted['kind'], 'vertical')
        self.assertAlmostEqual(tilted['tilt'], 35, delta=1)
        self.assertEqual(geometry(squeezed_x, pattern, (640, 480))['kind'], 'horizontal')
        # x 100–380 of 640 → left/centre columns; y 100–340 of 480 → all three rows.
        self.assertEqual(geometry(flat, pattern, (640, 480))['cells'], {0, 1, 3, 4, 6, 7})

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
