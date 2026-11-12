"""Import/annotate monocular photos to calibrate non-square wood-frame geometry.

GUI: python -m Src.calibration.calibrate_wood_frame_geometry
CLI: python -m Src.calibration.calibrate_wood_frame_geometry --session session.json --output target.json
"""
import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path
import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PyQt5.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from Src.calibration import calib_store
from Src.calibration.calibrate import Job
from Src.calibration.calibrate_manual_coordinates import AnnotationCanvas
from Src.calibration.calibrate_wood_frame import load_intrinsic_choices, read_image
from Src.calibration.calibration_workflow import save_json, validate_intrinsic
from Src.calibration.wood_frame import validate_target, wood_frame_target
from Src.calibration.wood_frame_geometry import POINT_IDS, SESSION_SCHEMA, fit_wood_frame_geometry
from Src.calibration.wood_frame_aruco import detect_frame_markers, marker_config


def fit_session(document):
    if not isinstance(document, dict) or document.get("schema") != SESSION_SCHEMA:
        raise ValueError("需要 wood_frame_geometry_session_v1 工作階段。")
    return fit_wood_frame_geometry(document["camera"], document["views"],
                                   target=document.get("target"), min_views=5,
                                   max_rms_px=document.get("max_rms_px", 3.))


class WoodFrameGeometryWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("木框雙面幾何／非 90° 校正")
        self.resize(1150, 900)
        self.entries, self.views = {}, []
        self.index = 0
        self.target = wood_frame_target()
        self.result = self.job = self.session_path = None
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        note = QLabel(
            "用同一台已完成內參的相機拍 12–20 張（至少 5 張）。每張必須同時看見 F1–F4 和 S1–S4 八點。\n"
            "保持木框剛性、焦距、解析度及裁切不變；改變俯仰、左右傾角、距離與位置，不能只重複同一姿態。\n"
            "以正面實測座標固定尺度與軸向，估計側面相對位置及角度；不預設木框為 90°。")
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.intrinsics = QComboBox()
        self.intrinsics.currentTextChanged.connect(self.invalidate_result)
        row.addWidget(QLabel("拍照相機內參／序號"))
        row.addWidget(self.intrinsics, 1)
        self.add_button("選擇內參 JSON…", self.choose_intrinsics, row)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.add_button("匯入同一相機的照片…", self.import_photos, row)
        self.add_button("載入標記工作階段…", self.load_session, row)
        self.add_button("儲存標記工作階段…", self.save_session, row)
        layout.addLayout(row)
        geometry = QLabel(
            "實體編號（不是螢幕左右）：正面 F1 左上 (0,232,0)、F2 右上 (232,232,0)、F3 右下、F4 左下。\n"
            "側面 S1 上方靠接縫 (363,232,106)、S2 上方遠端 (363,232,338)、S3 下方遠端、S4 下方靠接縫；單位 mm，厚度已包含。\n"
            "DICT_4X4_50：正面 ID 0（逆時針 90°）、側面 ID 1（原方向）。匯入後自動辨識八個黑色外框角點。\n"
            "滑鼠滾輪放大，中鍵拖曳；請核對疊圖，選編號後可手動修正。")
        geometry.setWordWrap(True)
        layout.addWidget(geometry)
        self.photo_label = QLabel("尚未匯入照片")
        layout.addWidget(self.photo_label)
        self.canvas = AnnotationCanvas()
        self.canvas.clicked.connect(self.mark_point)
        layout.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        self.add_button("上一張", lambda: self.move(-1), row)
        self.add_button("下一張", lambda: self.move(1), row)
        self.point = QComboBox()
        self.point.addItems(POINT_IDS)
        row.addWidget(QLabel("下一個標記"))
        row.addWidget(self.point)
        self.add_button("清除此張標記", self.clear_points, row)
        self.add_button("移除此張", self.remove_photo, row)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.add_button("重新偵測本張 ArUco", lambda: self.detect_aruco_views([self.index], overwrite=True), row)
        self.add_button("自動補齊全部 ArUco", lambda: self.detect_aruco_views(list(range(len(self.views)))), row)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.add_button("計算木框幾何", self.calculate, row)
        self.export_button = self.add_button("匯出修正後 target.json…", self.export_target, row)
        self.export_button.setEnabled(False)
        layout.addLayout(row)
        self.summary = QTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(145)
        layout.addWidget(self.summary)
        try:
            self.set_intrinsics(calib_store.latest_intrinsics_json())
        except (OSError, ValueError, KeyError):
            self.summary.setPlainText("請先選擇該拍照相機的內參 JSON。")

    @staticmethod
    def add_button(text, callback, layout):
        button = QPushButton(text)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def fail(self, message):
        QMessageBox.warning(self, "木框幾何校正", str(message))

    def invalidate_result(self, *_):
        self.result = None
        if hasattr(self, "export_button"):
            self.export_button.setEnabled(False)

    def set_intrinsics(self, path):
        entries = load_intrinsic_choices(path)
        self.entries = entries
        self.intrinsics.clear()
        self.intrinsics.addItems(sorted(entries))

    def choose_intrinsics(self):
        path, _ = QFileDialog.getOpenFileName(self, "拍照相機的內參", calib_store.latest_intrinsics_json(), "JSON (*.json)")
        if path:
            try:
                self.set_intrinsics(path)
            except (OSError, ValueError, KeyError) as exc:
                self.fail(exc)

    def import_photos(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "同一相機的雙面同框照片", "", "照片 (*.png *.jpg *.jpeg *.bmp *.tif *.tiff)")
        if not paths:
            return
        try:
            camera = self.entries[self.intrinsics.currentText()]
            from Src.calibration.calibrate_manual_coordinates import read_qimage
            known = {v["path"] for v in self.views}
            added = []
            for path in paths:
                path = str(Path(path).resolve())
                if path in known:
                    continue
                image = read_qimage(path)
                size = [image.width(), image.height()]
                if size != list(camera["image_size"]):
                    raise ValueError(Path(path).name + "：影像尺寸與內參不同，不能縮放或裁切後套用。")
                added.append({"id": path, "path": path, "image_size": size, "points": {}})
                known.add(path)
            first_new = len(self.views)
            self.views.extend(added)
            self.invalidate_result()
            self.show_view()
            self.detect_aruco_views(list(range(first_new, len(self.views))))
        except (OSError, ValueError, KeyError) as exc:
            self.fail("請先確認內參及照片。\n" + str(exc))

    def detect_aruco_views(self, indices, overwrite=False):
        if self.job is not None or not indices or not self.views:
            return
        if "aruco" not in self.target:
            self.summary.setPlainText("此工作階段沒有 ArUco 角點對應，保留人工標記。")
            return
        views, target = copy.deepcopy(self.views), copy.deepcopy(self.target)

        def work():
            results = {}
            for i in indices:
                try:
                    results[i] = detect_frame_markers(read_image(views[i]["path"]), target)
                except (ValueError, OSError) as exc:
                    results[i] = {role: {"points": {}, "status": "error", "note": str(exc)} for role in ("cf", "cs")}
            return results

        self.centralWidget().setEnabled(False)
        self.summary.setPlainText("正在背景偵測 ArUco，依標記編碼方向對應 F1–F4／S1–S4…")
        self.job = Job(work, self)

        def done():
            job, self.job = self.job, None
            self.centralWidget().setEnabled(True)
            try:
                if job.error:
                    raise ValueError(job.error)
                previous = copy.deepcopy(self.views)
                notes = []
                for i, detections in job.result.items():
                    view = self.views[i]
                    view["aruco_detection"] = detections
                    for role, detection in detections.items():
                        ids = target["view_point_ids"][role]
                        if any(key in view["points"] for key in ids) and not overwrite:
                            continue
                        if detection["points"]:
                            view["points"].update(detection["points"])
                        else:
                            notes.append("{} {}：{}".format(Path(view["path"]).name, role.upper(), detection["note"]))
                if self.session_path is not None:
                    try:
                        save_json(self.session_path, self.document())
                    except (ValueError, OSError):
                        self.views = previous
                        raise
                self.invalidate_result()
                self.show_view()
                self.summary.setPlainText("偵測完成，請核對八點疊圖；缺少的角點可補拍或手動標記。\n" + "\n".join(notes))
            except (ValueError, OSError) as exc:
                self.fail(exc)
            finally:
                job.deleteLater()
        self.job.finished.connect(done)
        self.job.start()

    def show_view(self):
        self.canvas.clear_image()
        if not self.views:
            self.photo_label.setText("尚未匯入照片")
            return
        view = self.views[self.index]
        try:
            self.canvas.set_image(view["path"])
            self.canvas.set_points(view["points"])
        except (OSError, ValueError) as exc:
            self.canvas.clear_image()
            self.fail(exc)
            return
        missing = [p for p in POINT_IDS if p not in view["points"]]
        self.point.setCurrentText(missing[0] if missing else POINT_IDS[0])
        self.update_progress()

    def update_progress(self):
        view = self.views[self.index]
        complete = sum(set(v["points"]) == set(POINT_IDS) for v in self.views)
        self.photo_label.setText("{}/{} · {} · 此張 {}/8 點 · 完整照片 {} 張".format(
            self.index + 1, len(self.views), Path(view["path"]).name, len(view["points"]), complete))

    def mark_point(self, x, y):
        if self.job is not None or not self.views or self.canvas.image_size is None:
            return
        width, height = self.canvas.image_size
        if not np.isfinite([x, y]).all() or not (0 <= x < width and 0 <= y < height):
            return
        view = self.views[self.index]
        view["points"][self.point.currentText()] = [x, y]
        self.canvas.set_points(view["points"])
        missing = [p for p in POINT_IDS if p not in view["points"]]
        if missing:
            self.point.setCurrentText(missing[0])
        self.invalidate_result()
        self.update_progress()
        self.autosave()

    def move(self, offset):
        if self.views:
            self.index = min(len(self.views) - 1, max(0, self.index + offset))
            self.show_view()

    def clear_points(self):
        if self.views:
            self.views[self.index]["points"] = {}
            self.invalidate_result()
            self.show_view()
            self.autosave()

    def remove_photo(self):
        if self.views:
            self.views.pop(self.index)
            self.index = max(0, min(self.index, len(self.views) - 1))
            self.invalidate_result()
            self.show_view()
            self.autosave()

    def document(self):
        key = self.intrinsics.currentText()
        if key not in self.entries:
            raise ValueError("請先選擇拍照相機內參。")
        camera = copy.deepcopy(self.entries[key])
        validate_intrinsic(camera)
        for view in self.views:
            if view["image_size"] != list(camera["image_size"]):
                raise ValueError("照片尺寸與所選內參不符。")
        return {"schema": SESSION_SCHEMA, "camera": camera, "camera_key": key,
                "target": copy.deepcopy(self.target), "views": copy.deepcopy(self.views), "max_rms_px": 3.}

    def autosave(self):
        if self.session_path is not None:
            try:
                save_json(self.session_path, self.document())
            except (OSError, ValueError) as exc:
                self.fail("儲存工作階段失敗：" + str(exc))

    def save_session(self):
        default = self.session_path or Path(calib_store.CALIB_DIR) / "wood_frame" / "geometry_session.json"
        path, _ = QFileDialog.getSaveFileName(self, "儲存標記工作階段", str(default), "JSON (*.json)")
        if path:
            try:
                save_json(path, self.document())
                self.session_path = Path(path)
            except (OSError, ValueError) as exc:
                self.fail(exc)

    def load_session(self):
        path, _ = QFileDialog.getOpenFileName(self, "載入標記工作階段", "", "JSON (*.json)")
        if not path:
            return
        try:
            from Src.calibration.calibrate_manual_coordinates import read_qimage
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("schema") != SESSION_SCHEMA:
                raise ValueError("不是木框雙面幾何工作階段。")
            validate_intrinsic(data["camera"])
            ids, _ = validate_target(data["target"])
            if "aruco" in data["target"]:
                marker_config(data["target"])
            if set(ids) != set(POINT_IDS) or not isinstance(data["views"], list):
                raise ValueError("木框幾何需包含 F1–F4、S1–S4；views 必須為照片陣列。")
            names = set()
            for view in data["views"]:
                if (not isinstance(view, dict) or not isinstance(view.get("id"), str)
                        or not view["id"] or view["id"] in names
                        or not isinstance(view.get("points"), dict)):
                    raise ValueError("照片 ID 缺漏／重複，或標記 points 不是物件。")
                names.add(view["id"])
                image = read_qimage(view["path"])
                if [image.width(), image.height()] != view["image_size"] or view["image_size"] != list(data["camera"]["image_size"]):
                    raise ValueError("照片尺寸已改變或與內參不符。")
                for name, point in view["points"].items():
                    xy = np.asarray(point, float)
                    if (name not in POINT_IDS or xy.shape != (2,) or not np.isfinite(xy).all()
                            or np.any(xy < 0) or np.any(xy >= view["image_size"])):
                        raise ValueError("照片包含無效的控制點或像素座標。")
            key = str(data.get("camera_key", "camera"))
            self.entries = {key: data["camera"]}
            self.intrinsics.clear()
            self.intrinsics.addItem(key)
            self.views, self.target = data["views"], data["target"]
            self.index, self.session_path = 0, Path(path)
            self.invalidate_result()
            self.show_view()
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self.fail(exc)

    def calculate(self):
        if self.job is not None:
            return
        try:
            document = self.document()
            if len(self.views) < 5 or any(set(v["points"]) != set(POINT_IDS) for v in self.views):
                raise ValueError("至少需要 5 張，每張需標記完整八點；建議 12–20 張。")
            if self.session_path is None:
                self.session_path = Path(calib_store.CALIB_DIR) / "wood_frame" / ("geometry_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json")
            save_json(self.session_path, document)
            self.invalidate_result()
            self.centralWidget().setEnabled(False)
            self.summary.setPlainText("正在比較平面姿態解並共同最佳化兩面幾何…")
            self.job = Job(lambda: fit_session(document), self)
            self.job.finished.connect(self.calculation_finished)
            self.job.start()
        except (OSError, ValueError) as exc:
            self.fail(exc)

    def calculation_finished(self):
        job, self.job = self.job, None
        self.centralWidget().setEnabled(True)
        if job.error:
            self.fail(job.error)
            self.summary.setPlainText(job.error)
        else:
            self.result = job.result
            fit = self.result["metadata"]["geometry_calibration"]
            self.result["metadata"]["geometry_source_session"] = str(self.session_path.resolve())
            self.summary.setPlainText(
                "完成 {} 張。依實體點序定義的兩面法向角 {:.3f}°；重投影 RMS {:.3f} px，最差照片 {:.3f} px。\n"
                "請匯出 target.json，回到「木框 PnP」載入此幾何，再拍雙機外參照片。\n"
                "正面座標／兩面各自尺寸保持實測值；此誤差是擬合結果，另用未參與校正的已知長度驗證。\n工作階段：{}".format(
                    fit["view_count"], fit["normal_angle_deg"], fit["rms_px"], fit["max_view_rms_px"], self.session_path))
            self.export_button.setEnabled(True)
        job.deleteLater()

    def export_target(self):
        if self.result is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "匯出修正後木框幾何", str(Path(calib_store.CALIB_DIR) / "wood_frame" / "target.json"), "JSON (*.json)")
        if path:
            try:
                save_json(path, self.result)
                self.summary.append("已匯出：" + path)
            except OSError as exc:
                self.fail(exc)

    def closeEvent(self, event):
        if self.job is not None:
            event.ignore()
        else:
            event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description="木框雙面同框照片幾何校正，不預設 90°")
    parser.add_argument("--session", type=Path, help="已標記八點的 geometry session JSON；無此參數則開 GUI")
    parser.add_argument("--output", type=Path, help="修正後 target.json 路徑（CLI 必填）")
    args = parser.parse_args(argv)
    if args.session:
        if not args.output:
            parser.error("--session 需要 --output")
        result = fit_session(json.loads(args.session.read_text(encoding="utf-8")))
        result["metadata"]["geometry_source_session"] = str(args.session.resolve())
        save_json(args.output, result)
        print(str(args.output))
        return 0
    if args.output:
        parser.error("--output 需要 --session")
    app = QApplication.instance() or QApplication(sys.argv)
    window = WoodFrameGeometryWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
