"""校正中心：各方法送出的指令與參數（不實際執行計算）。"""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication, QMessageBox  # noqa: E402

from Src.calibration import calibration_center as center  # noqa: E402


class CalibrationCenterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = center.CalibrationCenter()
        self.sent = []
        self.window.start = lambda commands: self.sent.extend(commands)

    def test_every_method_has_a_page(self):
        self.assertEqual(self.window.methods.count(), self.window.pages.count())
        titles = [self.window.methods.item(i).text().strip() for i in range(self.window.methods.count())]
        for keyword in ('RoMa', '校正桿手點', '3D 驗證', '棋盤格', '木框', '雙棒', '手動座標', '即時拍攝', '照片資料夾'):
            self.assertTrue(any(keyword in t for t in titles), keyword)

    def test_roma_runs_in_roma_env_with_options(self):
        self.window.roma_folders.addItem('D:/rec/20261020_Pitcher01')
        self.window.roma_frames.setText('0, 45')
        self.window.roma_save.setChecked(True)
        self.window.run_roma()
        program, args = self.sent[0]
        self.assertEqual(args[:4], ['run', '-n', 'Roma', '--no-capture-output'])
        self.assertTrue(args[6].endswith('roma_extrinsic.py'))
        self.assertIn('D:/rec/20261020_Pitcher01', args)
        self.assertEqual(args[args.index('--frames') + 1], '0,45')
        self.assertIn('--save', args)
        self.assertNotIn('--snapshot', args)

    def test_roma_without_folder_does_not_run(self):
        with patch.object(QMessageBox, 'warning') as warning:
            self.window.run_roma()
        warning.assert_called_once()
        self.assertEqual(self.sent, [])

    def test_chessboard_runs_intrinsics_then_extrinsics(self):
        with tempfile.TemporaryDirectory() as folder:
            for camera in ('cf', 'cs'):
                Path(folder, camera).mkdir()
            self.window.cb_images.edit.setText(folder)
            self.window.cb_method.setCurrentIndex(self.window.cb_method.findData('pnp'))
            self.window.run_chessboard()
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[0][1][1].endswith('calibrate_chessboard.py'))
        self.assertTrue(self.sent[1][1][1].endswith('calibrate_chessboard_extrinsic.py'))
        self.assertEqual(self.sent[1][1][-1], 'pnp')

    def test_cross_tool_removed(self):
        titles = [self.window.methods.item(i).text() for i in range(self.window.methods.count())]
        self.assertFalse(any('十字架' in t for t in titles))

    def test_window_tools_exist(self):
        for script in (center.CAL / 'calibrate.py', center.CAL / 'calib.py',
                       center.ROOT_DIR / 'Src' / 'legacy' / 'calibration' / '03_3d.py',
                       center.CAL / 'roma' / 'roma_extrinsic.py', center.CAL / 'calibrate_wood_frame.py',
                       center.CAL / 'calibrate_two_rods.py', center.CAL / 'calibrate_manual_coordinates.py'):
            self.assertTrue(script.is_file(), script)


if __name__ == '__main__':
    unittest.main()
