"""Catch broken entry points and resource paths after package moves."""
import ast
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


class ProjectLayoutTests(unittest.TestCase):
    def test_resources_stay_in_repository_and_current_data_directory(self):
        from Src.paths import CAMERA_CONFIG_DIR, DB_DIR, UI_DIR
        from Src.calibration import calib_store, chessboard_metric
        self.assertEqual(DB_DIR, ROOT / 'Db')
        self.assertEqual(Path(calib_store.CALIB_DIR), DB_DIR / 'Calibration')
        self.assertEqual(chessboard_metric.DEFAULT_CALIBRATION, DB_DIR / 'Calibration/chessboard')
        for name in ('GH3_camera_config.yaml', 'GH3_camera_config_2.yaml'):
            self.assertTrue((CAMERA_CONFIG_DIR / name).is_file())
        self.assertTrue((UI_DIR / 'rendering/R-PMingLiU-TW-2.ttf').is_file())

    def test_active_packages_never_import_archived_or_missing_local_modules(self):
        for folder in ('calibration', 'cameras', 'analysis', 'pose', 'common', 'UI_Control', 'tools', 'rapsodo'):
            for path in (ROOT / 'Src' / folder).rglob('*.py'):
                if 'tests' in path.parts: continue
                for node in ast.walk(ast.parse(path.read_text(encoding='utf-8-sig'))):
                    if isinstance(node, ast.ImportFrom):
                        names = [node.module or ''] if not node.level else []
                    elif isinstance(node, ast.Import):
                        names = [n.name for n in node.names]
                    else:
                        continue
                    for name in names:
                        if not name.startswith('Src.'): continue
                        with self.subTest(file=str(path), module=name):
                            self.assertFalse(name.startswith('Src.legacy'))
                            target = ROOT.joinpath(*name.split('.'))
                            self.assertTrue(target.is_dir() or target.with_suffix('.py').is_file(), name)

    def test_cli_entrypoints_work_from_an_unrelated_working_directory(self):
        commands = [
            ['Src/calibration/calibrate_chessboard.py', '--help'],
            ['Src/calibration/calibrate_chessboard_extrinsic.py', '--help'],
            ['Src/calibration/extract_fluorescent_balls.py', '--help'],
            ['Src/tools/measure_stride_pnp.py', '--help'],
            ['Src/tools/measure_stride_8point.py', '--help'],
            ['Src/tools/extract_frame.py', '--help'],
            ['Src/UI_Control/main_metric.py', '--help'],
            ['Src/UI_Control/calibrate_wood_frame.py', '--help'],
            ['Src/UI_Control/calibrate_two_rods.py', '--help'],
            ['Src/UI_Control/calibrate.py', '--two-rods', '--help'],
            ['Src/UI_Control/manual_coordinates.py', '--help'],
            ['Src/UI_Control/calibrate.py', '--wood-frame', '--help'],
            ['Src/UI_Control/calibrate.py', '--manual-coordinates', '--help'],
        ]
        with tempfile.TemporaryDirectory() as cwd:
            for command in commands:
                with self.subTest(entry=command[0]):
                    result = subprocess.run([sys.executable, str(ROOT / command[0])] + command[1:],
                        cwd=cwd, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage:', result.stdout)


if __name__ == '__main__':
    unittest.main()
