"""Camera pairing and cancellation must not mix separate pitching records."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5.QtWidgets import QApplication, QDialog
from Src.UI_Control.widgets.compare_video_dialog import CompareVideoDialog, validate_comparison_paths


class ComparisonSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.paths = []
        for pitch in ('A', 'B'):
            for role in ('CF', 'CS'):
                path = self.folder / f'{role}_{pitch}.mp4'
                path.touch()
                self.paths.append(str(path))

    def test_distinct_synchronized_pairs_are_accepted(self):
        self.assertEqual(validate_comparison_paths(self.paths), '')

    def test_duplicate_swapped_and_mismatched_pairs_are_rejected(self):
        self.assertTrue(validate_comparison_paths(self.paths[:2] * 2))
        self.assertTrue(validate_comparison_paths([self.paths[1], self.paths[0]] + self.paths[2:]))
        self.assertTrue(validate_comparison_paths([self.paths[i] for i in (0, 3, 2, 1)]))
        self.assertTrue(validate_comparison_paths(self.paths[:3] + ['']))

    def test_browse_pairs_matching_cameras_and_cancel_preserves_selection(self):
        dialog = CompareVideoDialog(paths=self.paths)
        self.addCleanup(dialog.close)
        with patch('Src.UI_Control.widgets.compare_video_dialog.QFileDialog.getOpenFileName',
                   return_value=(self.paths[2], '')):
            dialog.browse(0)
        self.assertEqual(tuple(map(Path, dialog.paths()[:2])), tuple(map(Path, self.paths[2:])))
        before = dialog.paths()
        with patch('Src.UI_Control.widgets.compare_video_dialog.QFileDialog.getOpenFileName',
                   return_value=('', '')):
            dialog.browse(0)
        self.assertEqual(dialog.paths(), before)
        dialog.accept()  # Duplicate top/bottom groups cannot be committed.
        self.assertNotEqual(dialog.result(), QDialog.Accepted)
        self.assertTrue(dialog.error.text())

    def test_missing_partner_clears_old_pitch_and_manual_names_can_be_paired(self):
        dialog = CompareVideoDialog(paths=self.paths)
        self.addCleanup(dialog.close)
        missing = self.folder / 'CF_C.mp4'
        missing.touch()
        with patch('Src.UI_Control.widgets.compare_video_dialog.QFileDialog.getOpenFileName',
                   return_value=(str(missing), '')):
            dialog.browse(0)
        self.assertEqual(dialog.paths()[1], '')
        for index, filename in enumerate(('front.mp4', 'side.mp4')):
            path = self.folder / filename
            path.touch()
            with patch('Src.UI_Control.widgets.compare_video_dialog.QFileDialog.getOpenFileName',
                       return_value=(str(path), '')):
                dialog.browse(index)
        self.assertEqual(validate_comparison_paths(dialog.paths()), '')
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.Accepted)


if __name__ == '__main__':
    unittest.main()
