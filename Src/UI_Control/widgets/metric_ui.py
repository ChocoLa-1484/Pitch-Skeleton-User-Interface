"""Qt interface for metric heel measurements, independent of the legacy scale factors."""
from pathlib import Path
from math import sqrt

import cv2
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPen, QPixmap
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget)

from Src.calibration.chessboard_metric import read_json
from Src.analysis.metric_session import MetricSession, infer_annotations
from Src.analysis.stride_workflow import default_contact_frame


class PixelView(QGraphicsView):
    """The scene is always original distorted pixels; zoom does not change points."""
    pixelClicked = pyqtSignal(float, float)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setMinimumSize(300, 210)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.image_size = None
        self.markers = []
        self.press_position = None

    def set_image(self, bgr):
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        pixmap = QPixmap.fromImage(QImage(rgb.data, width, height, rgb.strides[0], QImage.Format_RGB888).copy())
        self.scene().clear()
        self.markers = []
        self.scene().addPixmap(pixmap)
        self.setSceneRect(0, 0, width, height)
        self.image_size = (width, height)
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)

    def show_points(self, points):
        for item in self.markers:
            self.scene().removeItem(item)
        self.markers = []
        for name, point in points.items():
            if point is None:
                continue
            x, y = point
            pen = QPen(QColor("#fff03b"), 2)
            pen.setCosmetic(True)
            dot = self.scene().addEllipse(x - 4, y - 4, 8, 8, pen)
            text = self.scene().addText(name)
            text.setDefaultTextColor(QColor("#fff03b"))
            text.setPos(x + 6, y - 25)
            self.markers.extend((dot, text))

    def wheelEvent(self, event):
        factor = 1.25 if event.angleDelta().y() > 0 else .8
        if .08 <= self.transform().m11() * factor <= 12:
            self.scale(factor, factor)
        event.accept()

    def mousePressEvent(self, event):
        self.press_position = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if (event.button() == Qt.LeftButton and self.press_position is not None
                and (event.pos() - self.press_position).manhattanLength() <= 3 and self.image_size):
            point = self.mapToScene(event.pos())
            if 0 <= point.x() < self.image_size[0] and 0 <= point.y() < self.image_size[1]:
                self.pixelClicked.emit(point.x(), point.y())
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
        event.accept()


class InferenceWorker(QThread):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, session, model):
        super().__init__()
        self.session, self.model = session, model

    def run(self):
        try:
            self.completed.emit(infer_annotations(self.session, self.model))
        except Exception as exc:
            self.failed.emit(str(exc))


class MetricPanel(QWidget):
    def __init__(self, options, model=None, import_context=None, before_infer=None):
        super().__init__()
        self.model, self.import_context, self.before_infer = model, import_context, before_infer
        self.session = self.document = self.result = self.worker = None
        self.loading = True
        layout = QVBoxLayout(self)
        note = QLabel("校正量測：初始後腳腳跟 → 前腳落地時，同一後腳腳跟。相機位置、鏡頭設定須與校正時一致。")
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        self.rig_path = QLineEdit(str(options.calibration))
        self.folder = QLineEdit(str(options.video_dir))
        for title, edit, action in (("校正 stereo.json", self.rig_path, self.choose_rig),
                                     ("CF／CS 影片資料夾", self.folder, self.choose_folder)):
            row = QHBoxLayout()
            row.addWidget(edit)
            button = QPushButton("選擇…")
            button.clicked.connect(action)
            row.addWidget(button)
            form.addRow(title, row)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.reference = self.spin(options.reference_frame, 0)
        frame = options.frame if options.frame is not None else default_contact_frame(options.video_dir)[0]
        self.contact = self.spin(frame, 0)
        self.offset = self.spin(options.cs_frame_offset, -100000)
        self.heel = QComboBox()
        self.heel.addItem("右後腳", "right")
        self.heel.addItem("左後腳", "left")
        self.heel.setCurrentIndex(0 if options.heel == "right" else 1)
        for title, widget in (("初始 CF 幀", self.reference), ("落地 CF 幀", self.contact),
                              ("CS = CF +", self.offset), ("腳跟", self.heel)):
            row.addWidget(QLabel(title))
            row.addWidget(widget)
        self.ground = QCheckBox("另選 3 個地面點，量水平距離")
        self.ground.setChecked(True)
        row.addWidget(self.ground)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.contact_verified = QCheckBox("已目視確認前腳落地幀")
        self.sync_verified = QCheckBox("已目視核對雙相機同步")
        row.addWidget(self.contact_verified)
        row.addWidget(self.sync_verified)
        row.addWidget(QLabel("幀號由 0 開始；既有 FC 僅供定位，需逐幀確認。"))
        layout.addLayout(row)
        row = QHBoxLayout()
        actions = [("載入／重設選點", self.load_session), ("讀取腳跟 JSON", self.load_pose),
                   ("重新辨識 4 張影像", self.infer), ("載入選點…", self.load_annotations)]
        if import_context:
            actions.append(("帶入上一個分析頁影片／幀", self.use_context))
        for title, action in actions:
            button = QPushButton(title)
            button.clicked.connect(action)
            if title.startswith("重新辨識"):
                button.setEnabled(model is not None)
                button.setToolTip("使用主程式模型辨識原始影像；不改寫原骨架檔。輕量模式請人工選點。")
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.point = QComboBox()
        self.point.addItems(["reference", "heel", "ground0", "ground1", "ground2"])
        row.addWidget(QLabel("選點位置"))
        row.addWidget(self.point)
        self.point_help = QLabel()
        row.addWidget(self.point_help, 1)
        for title, delta in (("−10 幀", -10), ("−1 幀", -1), ("+1 幀", 1), ("+10 幀", 10)):
            button = QPushButton(title)
            button.clicked.connect(lambda checked=False, d=delta: self.step_frame(d))
            row.addWidget(button)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.views, self.frame_labels = {}, {}
        for camera in ("cf", "cs"):
            column = QVBoxLayout()
            label = QLabel(camera.upper())
            view = PixelView()
            view.pixelClicked.connect(lambda x, y, c=camera: self.pick(c, x, y))
            column.addWidget(label)
            column.addWidget(view, 1)
            row.addLayout(column, 1)
            self.views[camera], self.frame_labels[camera] = view, label
        layout.addLayout(row, 1)
        layout.addWidget(QLabel("兩張圖各點同一實體位置。滾輪放大、拖曳平移、雙擊還原；地面點須在同一水平面且分散、不共線。"))
        row = QHBoxLayout()
        self.measure_button = QPushButton("計算後腳位移")
        self.measure_button.clicked.connect(self.measure)
        self.compare_button = QPushButton("用另一份校正比較…")
        self.compare_button.clicked.connect(self.compare)
        self.export_button = QPushButton("匯出結果、選點與影像")
        self.export_button.clicked.connect(self.export)
        self.export_button.setEnabled(False)
        for button in (self.measure_button, self.compare_button, self.export_button):
            row.addWidget(button)
        layout.addLayout(row)
        self.distance = QLabel("尚未量測")
        self.distance.setStyleSheet("font-size: 20px; font-weight: bold; padding: 5px;")
        self.distance.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.distance)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(125)
        layout.addWidget(self.details)
        for spin in (self.reference, self.contact, self.offset):
            spin.valueChanged.connect(self.invalidate)
        self.heel.currentIndexChanged.connect(self.invalidate)
        self.ground.toggled.connect(self.invalidate)
        self.folder.textChanged.connect(self.invalidate)
        self.rig_path.textChanged.connect(self.invalidate)
        self.point.currentIndexChanged.connect(self.preview)
        self.loading = False
        self.load_session()
        if options.annotations:
            self.load_annotations(path=options.annotations)

    @staticmethod
    def spin(value, minimum):
        spin = QSpinBox()
        spin.setRange(minimum, 1000000)
        spin.setValue(value)
        return spin

    def error(self, message):
        self.result = None
        self.export_button.setEnabled(False)
        self.distance.setText("無法量測／尚未完成")
        self.details.setPlainText(str(message))

    def invalidate(self, *args):
        if self.loading:
            return
        self.session = self.document = self.result = None
        self.contact_verified.setChecked(False)
        self.sync_verified.setChecked(False)
        self.export_button.setEnabled(False)
        self.distance.setText("設定已改變，請載入／重設選點")
        self.details.clear()
        for view in self.views.values():
            view.scene().clear()
            view.markers = []
            view.image_size = None

    def make_session(self):
        return MetricSession(self.rig_path.text(), self.folder.text(), self.reference.value(),
            self.contact.value(), self.offset.value(), self.heel.currentData())

    def load_session(self):
        self.result = self.document = self.session = None
        self.export_button.setEnabled(False)
        try:
            self.session = self.make_session()
            self.document = self.session.template(self.ground.isChecked())
            self.distance.setText("等待腳跟對應點" + ("與地面點" if self.ground.isChecked() else ""))
            self.details.setPlainText(self.calibration_note())
            self.preview()
        except Exception as exc:
            self.error(exc)

    def calibration_note(self):
        rig = self.session.rig
        warnings = "\n".join(rig.get("warnings", [])) or "校正報告未列警告。"
        baseline = sqrt(sum(float(x) ** 2 for x in self.session.t))
        return (f"{rig['method']}；基線 {baseline:.1f} mm\n"
                "精度尚未經獨立實尺驗證。重投影誤差不是公分誤差上限。\n" + warnings)

    def choose_rig(self):
        path, _ = QFileDialog.getOpenFileName(self, "選擇公制 stereo.json", self.rig_path.text(), "JSON (*.json)")
        if path:
            self.rig_path.setText(path)
            self.load_session()

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self, "選擇一組 CF／CS 影片", self.folder.text())
        if path:
            self.folder.setText(path)
            self.contact.setValue(default_contact_frame(path)[0])
            self.reference.setValue(0)
            self.offset.setValue(0)
            self.load_session()

    def use_context(self):
        try:
            folder, frame, offset = self.import_context()
            self.folder.setText(str(folder))
            self.reference.setValue(max(0, -offset))
            self.contact.setValue(frame)
            self.offset.setValue(offset)
            self.load_session()
        except Exception as exc:
            self.error(exc)

    def preview(self):
        if self.loading or not self.session or not self.document:
            return
        name = self.point.currentText()
        stage = "reference" if name == "reference" else "heel"
        descriptions = {"reference": "初始站立時的後腳腳跟", "heel": "前腳落地時，同一後腳腳跟",
                        "ground0": "地面點 1", "ground1": "地面點 2", "ground2": "地面點 3"}
        self.point_help.setText(descriptions[name])
        try:
            for camera, view in self.views.items():
                view.set_image(self.session.frame(camera, stage))
                field = f"reference_frame_{camera}" if stage == "reference" else f"frame_{camera}"
                self.frame_labels[camera].setText(f"{camera.upper()}　幀 {self.document[field]}　{descriptions[name]}")
            self.markers()
        except Exception as exc:
            self.error(exc)

    def markers(self):
        is_reference = self.point.currentText() == "reference"
        for camera, view in self.views.items():
            view.show_points({n: p[camera] for n, p in self.document["points"].items()
                              if (n == "reference") == is_reference})

    def pick(self, camera, x, y):
        if not self.session or not self.document:
            return
        name = self.point.currentText()
        if name not in self.document["points"]:
            self.error("要選地面點，請先勾選地面距離並載入／重設選點。")
            return
        self.document["points"][name][camera] = [x, y]
        self.document["point_source"] = "manual original-video pixel annotations (may include reviewed pose points)"
        self.result = None
        self.export_button.setEnabled(False)
        missing = [f"{n}/{c}" for n, p in self.document["points"].items() for c in ("cf", "cs") if p[c] is None]
        self.distance.setText("尚待選點：" + ", ".join(missing) if missing else "選點完成，可計算")
        self.markers()

    def step_frame(self, delta):
        spin = self.reference if self.point.currentText() == "reference" else self.contact
        spin.setValue(spin.value() + delta)
        self.load_session()

    def adopt_annotations(self, document):
        self.session.validate_annotations(document)
        if self.ground.isChecked():
            for name in ("ground0", "ground1", "ground2"):
                document["points"].setdefault(name, {"cf": None, "cs": None})
        self.document = document
        self.result = None
        self.export_button.setEnabled(False)
        self.preview()
        self.distance.setText("選點已載入，請核對圖上位置後計算")

    def load_pose(self):
        try:
            if not self.session:
                self.load_session()
            if self.session:
                self.adopt_annotations(self.session.pose_annotations())
        except Exception as exc:
            self.error(exc)

    def load_annotations(self, checked=False, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "載入原始像素選點", self.folder.text(), "JSON (*.json)")
        if not path:
            return
        try:
            if not self.session:
                self.load_session()
            if self.session:
                self.adopt_annotations(read_json(path))
        except Exception as exc:
            self.error(exc)

    def infer(self):
        if self.model is None or self.worker is not None:
            return
        if not self.session:
            self.load_session()
        if self.session is None:
            return
        if self.before_infer:
            self.before_infer()
        self.distance.setText("正在辨識初始／落地的 CF、CS 四張原始影像…")
        self.window().setEnabled(False)
        self.worker = InferenceWorker(self.session, self.model)
        self.worker.completed.connect(self.inference_done)
        self.worker.failed.connect(self.error)
        self.worker.finished.connect(self.inference_finished)
        self.worker.start()

    def inference_done(self, document):
        try:
            self.adopt_annotations(document)
        except Exception as exc:
            self.error(exc)

    def inference_finished(self):
        self.window().setEnabled(True)
        self.worker.deleteLater()
        self.worker = None

    def measure(self):
        try:
            if self.session is None or self.document is None:
                raise ValueError("請先載入影片與校正，再選點。")
            self.result = self.session.measure(self.document)
            self.show_result()
        except Exception as exc:
            self.error(exc)

    def show_result(self):
        result = self.result
        ground = result["ground_distance_cm"]
        self.distance.setText(f"同一後腳：三維位移 {result['distance_3d_cm']:.2f} cm　｜　" +
                             (f"地面投影 {ground:.2f} cm" if ground is not None else "地面距離：未量測"))
        text = (f"最大重投影 {result['max_reprojection_px']:.2f} px；最小射線夾角 {result['min_ray_angle_deg']:.2f}°\n"
                + self.calibration_note())
        if result.get("comparison"):
            comp = result["comparison"]
            text += f"\n另一份 {comp['calibration']['method']}：三維 {comp['distance_3d_cm']:.2f} cm；兩模型相差 {comp['difference_3d_cm']:.2f} cm。"
            if comp["ground_distance_cm"] is not None:
                text += f" 地面 {comp['ground_distance_cm']:.2f} cm；相差 {comp['difference_ground_cm']:.2f} cm。"
            text += "\n模型相近不等於實尺驗證；比較只用於觀察校正敏感度。"
        self.details.setPlainText(text)
        self.export_button.setEnabled(True)

    def compare(self):
        if self.result is None:
            self.measure()
        if self.result is None:
            return
        path, _ = QFileDialog.getOpenFileName(self, "用相同選點比較另一份 stereo.json", str(Path(self.rig_path.text()).parent.parent), "JSON (*.json)")
        if not path:
            return
        try:
            self.result["comparison"] = self.session.compare(self.document, path)
            self.show_result()
        except Exception as exc:
            self.details.appendPlainText("另一份校正未通過量測檢查：" + str(exc))

    def export(self):
        try:
            if self.result is None:
                raise ValueError("請先完成有效量測。")
            self.result["contact_frame_visually_verified"] = self.contact_verified.isChecked()
            self.result["synchronization"]["visually_verified"] = self.sync_verified.isChecked()
            output = self.session.export(self.result)
            self.details.appendPlainText("已匯出：" + str(output))
        except Exception as exc:
            self.error(exc)
