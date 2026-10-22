"""Regressions for annotation metadata and reusable live-preview canvases."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from Src.calibration.manual_coordinates import load_document, make_document, save_document


class ManualCoordinatesReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        from Src.calibration import calibrate_manual_coordinates as ui
        cls.ui = ui
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)

    @staticmethod
    def image(width=640, height=480, color="red"):
        from PyQt5.QtGui import QColor, QImage
        image = QImage(width, height, QImage.Format_RGB32)
        image.fill(QColor(color))
        return image

    def canvas(self):
        canvas = self.ui.AnnotationCanvas()
        canvas.resize(800, 600)
        canvas.show()
        self.app.processEvents()
        self.addCleanup(canvas.close)
        return canvas

    def test_preview_reuses_pixmap_and_keeps_zoom_pan_and_overlay(self):
        canvas = self.canvas()
        source = self.root / "frame.png"
        self.assertTrue(self.image().save(str(source)))
        canvas.set_image(source)
        canvas.set_points({"O": [100, 100], "X": [200, 100]}, "O")
        canvas.scale(2.5, 2.5)
        canvas.centerOn(350, 250)
        self.app.processEvents()
        item = canvas._pixmap_item
        overlays = list(canvas._markers)
        transform = canvas.transform()
        scroll = (canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value())

        for color in ("green", "blue"):
            self.assertEqual(canvas.update_preview_image(self.image(color=color)), (640, 480))

        self.assertIs(canvas._pixmap_item, item)
        self.assertEqual(canvas._markers, overlays)
        self.assertEqual(canvas.transform(), transform)
        self.assertEqual((canvas.horizontalScrollBar().value(), canvas.verticalScrollBar().value()), scroll)
        self.assertEqual(item.pixmap().toImage().pixelColor(50, 50).name(), "#0000ff")
        self.assertIsNone(canvas.image_path)

    def test_new_resolution_or_still_image_resets_annotations(self):
        canvas = self.canvas()
        canvas.update_preview_image(self.image())
        canvas.set_points({"O": [100, 100]})
        old_item = canvas._pixmap_item
        canvas.update_preview_image(self.image(800, 600))
        self.assertIsNot(canvas._pixmap_item, old_item)
        self.assertEqual(canvas.image_size, (800, 600))
        self.assertEqual(canvas._points, {})
        self.assertEqual(canvas._markers, [])
        self.assertEqual(len(canvas.scene().items()), 1)

        canvas.set_points({"O": [100, 100]})
        canvas.set_image(self.image(800, 600))
        self.assertEqual(canvas._points, {})
        self.assertEqual(canvas._markers, [])
        self.assertEqual(len(canvas.scene().items()), 1)

    def test_preview_after_clear_and_invalid_frame(self):
        from PyQt5.QtGui import QImage
        canvas = self.canvas()
        canvas.update_preview_image(self.image())
        item = canvas._pixmap_item
        with self.assertRaises(ValueError):
            canvas.update_preview_image(QImage())
        self.assertIs(canvas._pixmap_item, item)
        canvas.clear_image()
        self.assertIsNone(canvas._pixmap_item)
        canvas.update_preview_image(self.image(color="blue"))
        self.assertEqual(len(canvas.scene().items()), 1)
        self.assertEqual(canvas._pixmap_item.pixmap().toImage().pixelColor(50, 50).name(), "#0000ff")

    def test_import_edit_export_preserves_document_creation_time(self):
        source = self.root / "reference.png"
        self.assertTrue(self.image().save(str(source)))
        image = {"path": str(source), "width": 640, "height": 480}
        views = {role: {
            "axes_image": dict(image), "origin_px": [100, 300],
            "axes_px": {"X": [250, 300], "Y": [200, 200], "Z": [100, 100]},
            "scale": {
                "image": dict(image), "endpoints_px": [[20, 20], [320, 420]],
                "length": 25, "unit": "cm",
            },
        } for role in ("CF", "CS")}
        original = make_document(views)
        original["created_at"] = "2026-10-01T03:20:00+00:00"
        saved = self.root / "original.json"
        save_document(saved, original)
        window = self.ui.ManualCoordinatesWindow()
        try:
            self.assertEqual(window.load_from(saved), [])
            window.notes.setText("Updated coordinate notes")
            updated = self.root / "updated.json"
            window.save_to(updated)
            result = load_document(updated)
            self.assertEqual(result["created_at"], original["created_at"])
            self.assertEqual(result["notes"], "Updated coordinate notes")
            self.assertEqual(result["views"], original["views"])
        finally:
            window._dirty = False
            window.close()


if __name__ == "__main__":
    unittest.main()
