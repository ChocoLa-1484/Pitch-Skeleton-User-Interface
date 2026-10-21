"""Round-trip, invalid-annotation and real Qt pixel-mapping checks."""
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from Src.calibration.manual_coordinates import (
    load_document, make_document, save_document, validate_document,
)


def sample_views(path):
    image = {"path": str(path), "width": 640, "height": 480}
    return {role: {
        "axes_image": dict(image), "origin_px": [100, 300],
        "axes_px": {"X": [250, 300], "Y": [200, 200], "Z": [100, 100]},
        "scale": {
            "image": dict(image), "endpoints_px": [[20, 20], [320, 420]],
            "length": 25, "unit": "cm",
        },
    } for role in ("CF", "CS")}


class ManualCoordinateDataTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / "照片" / "原始影像.png"
        self.document = make_document(sample_views(self.source), "Z 向上")

    def test_export_round_trip_relative_paths_and_recomputed_scale(self):
        destination = self.root / "results" / "標記.json"
        original = copy.deepcopy(self.document)
        save_document(destination, self.document)
        self.assertEqual(original, self.document)
        saved = json.loads(destination.read_text(encoding="utf-8"))
        self.assertFalse(Path(saved["views"]["CF"]["axes_image"]["path"]).is_absolute())
        loaded = load_document(destination)
        view = loaded["views"]["CF"]
        self.assertEqual(view["axes_image"]["path"], str(self.source.resolve()))
        self.assertEqual(view["scale"]["distance_px"], 500)
        self.assertEqual(view["scale"]["length_mm"], 250)
        self.assertEqual(view["scale"]["mm_per_pixel"], 0.5)
        self.assertEqual(view["axis_unit_directions_px"]["Z"], [0, -1])
        self.assertFalse(loaded["is_metric_3d_calibration"])
        self.assertNotIn("R", view)
        saved["views"]["CF"]["scale"]["mm_per_pixel"] = 123456
        destination.write_text(json.dumps(saved), encoding="utf-8")
        self.assertEqual(load_document(destination)["views"]["CF"]["scale"]["mm_per_pixel"], 0.5)

    def test_invalid_points_lengths_units_and_schema_rejected(self):
        mutations = [
            lambda d: d["views"]["CF"].update(origin_px=[float("nan"), 1]),
            lambda d: d["views"]["CF"].update(origin_px=[640, 1]),
            lambda d: d["views"]["CF"].update(origin_px=[-1, 1]),
            lambda d: d["views"]["CF"]["axes_px"].update(X=[100, 300]),
            lambda d: d["views"]["CF"]["axes_px"].pop("Y"),
            lambda d: d["views"]["CF"]["scale"].update(endpoints_px=[[2, 2], [2, 2]]),
            lambda d: d["views"]["CF"]["scale"].update(length=0),
            lambda d: d["views"]["CF"]["scale"].update(length=-1),
            lambda d: d["views"]["CF"]["scale"].update(length=float("inf")),
            lambda d: d["views"]["CF"]["scale"].update(length=True),
            lambda d: d["views"]["CF"]["scale"].update(unit="inch"),
            lambda d: d["views"]["CF"]["axes_image"].update(width=640.0),
            lambda d: d["views"].pop("CS"),
            lambda d: d.update(schema_version=True),
            lambda d: d.update(schema_version=2),
            lambda d: d.update(is_metric_3d_calibration=True),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                document = copy.deepcopy(self.document)
                mutate(document)
                with self.assertRaises(ValueError):
                    validate_document(document)

    def test_failed_write_preserves_existing_file(self):
        destination = self.root / "existing.json"
        destination.write_text("original", encoding="utf-8")
        with patch("Src.calibration.manual_coordinates.os.replace", side_effect=OSError("disk error")):
            with self.assertRaises(OSError):
                save_document(destination, self.document)
        self.assertEqual(destination.read_text(encoding="utf-8"), "original")
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_metre_and_millimetre_units(self):
        document = copy.deepcopy(self.document)
        document["views"]["CF"]["scale"].update(length=0.25, unit="m")
        document["views"]["CS"]["scale"].update(length=250, unit="mm")
        result = validate_document(document)
        for view in result["views"].values():
            self.assertEqual(view["scale"]["mm_per_pixel"], 0.5)


class ManualCoordinateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        from Src.UI_Control import manual_coordinates as ui
        cls.ui = ui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PyQt5.QtGui import QImage, QColor
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / "中文影像.png"
        image = QImage(640, 480, QImage.Format_RGB32)
        image.fill(QColor("#34485b"))
        self.assertTrue(image.save(str(self.source)))
        self.window = self.ui.ManualCoordinatesWindow()
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window._dirty = False
        self.window.close()

    def fill_window(self):
        for panels in self.window.panels.values():
            axes, scale = panels["axes"], panels["scale"]
            axes.set_image(self.source)
            scale.set_image(self.source)
            for point in ([100, 300], [250, 300], [200, 200], [100, 100]):
                axes.mark_point(*point)
            scale.mark_point(20, 20)
            scale.mark_point(320, 420)
            scale.length.setValue(25)
            scale.unit.setCurrentText("cm")

    def test_full_ui_export_import_and_corrected_point(self):
        self.fill_window()
        axes = self.window.panels["CF"]["axes"]
        axes.target.setCurrentIndex(1)
        axes.mark_point(260, 300)
        self.assertEqual(axes.points["X"], [260, 300])
        self.window.notes.setText("原點位於木框左下角")
        destination = self.root / "標記.json"
        self.window.save_to(destination)
        axes.clear_points()
        self.assertEqual(self.window.load_from(destination), [])
        document = self.window.document()
        self.assertEqual(document["views"]["CF"]["axes_px"]["X"], [260, 300])
        self.assertEqual(document["notes"], "原點位於木框左下角")
        self.assertEqual(document["views"]["CS"]["scale"]["mm_per_pixel"], 0.5)

    def test_canvas_clicks_use_source_pixels_after_zoom_and_pan(self):
        from PyQt5.QtCore import QPointF, Qt
        from PyQt5.QtTest import QTest
        canvas = self.window.panels["CF"]["axes"].canvas
        canvas.set_image(self.source)
        canvas.scale(2.5, 2.5)
        canvas.centerOn(250, 200)
        self.app.processEvents()
        observed = []
        canvas.clicked.connect(lambda x, y: observed.append((x, y)))
        position = canvas.mapFromScene(QPointF(250, 200))
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=position)
        self.assertEqual(len(observed), 1)
        self.assertLess(math.hypot(observed[0][0] - 250, observed[0][1] - 200), 2)
        canvas.fit_image()
        self.app.processEvents()
        # Point outside source image must not produce a new observation.
        position = canvas.mapFromScene(QPointF(-10, -10))
        QTest.mouseClick(canvas.viewport(), Qt.LeftButton, pos=position)
        self.assertEqual(len(observed), 1)

    def test_invalid_import_does_not_replace_work(self):
        self.fill_window()
        original = self.window.document()["views"]
        destination = self.root / "invalid.json"
        destination.write_text('{"schema": "not supported"}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.window.load_from(destination)
        self.assertEqual(self.window.document()["views"], original)

    def test_missing_image_can_relink_without_losing_points(self):
        self.fill_window()
        destination = self.root / "標記.json"
        self.window.save_to(destination)
        moved = self.root / "搬移後.png"
        self.source.rename(moved)
        self.assertEqual(len(self.window.load_from(destination)), 4)
        axes = self.window.panels["CF"]["axes"]
        original = dict(axes.points)
        axes.set_image(moved, preserve_missing=True)
        self.assertEqual(axes.points, original)
        self.assertTrue(axes.image_available)

    def test_changed_image_clears_marks_and_missing_image_size_must_match(self):
        from PyQt5.QtGui import QImage
        self.fill_window()
        axes = self.window.panels["CF"]["axes"]
        axes.set_image(self.source)
        self.assertEqual(axes.points, {})
        small = self.root / "small.png"
        QImage(100, 100, QImage.Format_RGB32).save(str(small))
        axes.image_available = False
        with self.assertRaises(ValueError):
            axes.set_image(small, preserve_missing=True)
        self.assertEqual(axes.image["width"], 640)

    def test_import_preserves_small_decimal_length(self):
        document = make_document(sample_views(self.source))
        document["views"]["CF"]["scale"]["length"] = 0.000000123456789
        destination = self.root / "小數.json"
        save_document(destination, document)
        self.window.load_from(destination)
        self.assertEqual(self.window.document()["views"]["CF"]["scale"]["length"],
                         0.000000123456789)


if __name__ == "__main__":
    unittest.main()
