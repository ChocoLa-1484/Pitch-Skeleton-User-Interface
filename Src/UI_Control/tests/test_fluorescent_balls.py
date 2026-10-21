"""Standalone extraction: no calibration imports, pairing, or fixed ball count."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

UI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_DIR.parents[1]))

import cv2
import numpy as np

import Src.calibration.extract_fluorescent_balls as extraction
from Src.calibration.fluorescent_balls import DetectionConfig, detect_balls, read_image, write_image


def three_balls():
    hsv = np.zeros((160, 240, 3), np.uint8)
    for point in [(45, 40), (115, 80), (195, 120)]:
        cv2.circle(hsv, point, 10, (153, 170, 200), -1)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


class ExtractionTests(unittest.TestCase):
    def test_imports_without_pnp_calibration_and_validates_detector_config(self):
        code = """
import sys
from Src.calibration import extract_fluorescent_balls
assert 'Src.calibration.wood_frame' not in sys.modules
assert 'Src.calibration.calibration_workflow' not in sys.modules
assert 'PyQt5' not in sys.modules
assert 'PySpin' not in sys.modules
"""
        completed = subprocess.run([sys.executable, "-c", code], cwd=str(UI_DIR.parents[1]),
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        config = DetectionConfig(135, 168, 105, 50, 3, 20, 2500)
        config.validate()
        candidates, _ = detect_balls(three_balls(), config)
        self.assertEqual(len(candidates), 3)

    def test_single_unicode_image_roi_coordinates_and_foreground(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "中文照片 space.png"
            write_image(source, three_balls())
            args = extraction.build_parser().parse_args(
                ["--input", str(source), "--output", str(root / "out"),
                 "--roi", "80", "50", "150", "100", "--expected-count", "2"])
            with contextlib.redirect_stdout(io.StringIO()):
                output, report = extraction.run(args)
            frame = report["frames"][source.name]
            self.assertEqual(frame["count"], 2)
            self.assertTrue(frame["count_matches_expected"])
            np.testing.assert_allclose([c["center_px"] for c in frame["candidates"]],
                                       [[115, 80], [195, 120]], atol=0.2)
            self.assertEqual([c["candidate_id"] for c in frame["candidates"]], [1, 2])
            foreground = read_image(output / frame["artifacts"]["foreground"])
            np.testing.assert_array_equal(foreground[40, 45], [0, 0, 0])
            self.assertTrue(np.any(foreground[80, 115]))
            saved = json.loads((output / (source.name+".centers.json")).read_text(encoding="utf-8"))
            self.assertEqual(saved["candidates"], frame["candidates"])
            with self.assertRaises(FileExistsError):
                extraction.run(args)

    def test_recursive_unpaired_inputs_zero_balls_and_read_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "inputs"
            write_image(source / "front/same.png", three_balls())
            write_image(source / "front/same.jpg", three_balls())
            write_image(source / "different_name/blank.png", np.zeros((100, 100, 3), np.uint8))
            (source / "bad.jpg").write_bytes(b"not an image")
            args = extraction.build_parser().parse_args(["--input", str(source), "--output", str(root / "out")])
            with contextlib.redirect_stdout(io.StringIO()):
                output, report = extraction.run(args)
            self.assertEqual(report["summary"], {"total_images": 4, "processed_images": 3,
                                                 "count_mismatch_images": 3, "failed_images": 1})
            self.assertEqual(report["status"], "partial_failure")
            self.assertTrue((output / "front/same.png.mask.png").is_file())
            self.assertTrue((output / "front/same.jpg.mask.png").is_file())
            self.assertEqual(report["frames"]["front/same.png"]["count"], 3)
            self.assertEqual(report["frames"]["different_name/blank.png"]["candidates"], [])
            self.assertIsNotNone(report["frames"]["bad.jpg"]["error"])
            self.assertEqual(json.loads((output / "detections.json").read_text(encoding="utf-8"))["status"], "partial_failure")

    def test_zero_expected_count_and_output_inside_source_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "inputs"
            write_image(source / "single.png", three_balls())
            args = extraction.build_parser().parse_args(["--input", str(source), "--output", str(source / "out")])
            with self.assertRaisesRegex(ValueError, "outside"):
                extraction.run(args)
            self.assertFalse((source / "out").exists())
            args.output, args.expected_count = root / "out", 0
            with contextlib.redirect_stdout(io.StringIO()):
                _, report = extraction.run(args)
            self.assertIsNone(report["frames"]["single.png"]["count_matches_expected"])
            self.assertEqual(report["frames"]["single.png"]["count"], 3)


if __name__ == "__main__":
    unittest.main()
