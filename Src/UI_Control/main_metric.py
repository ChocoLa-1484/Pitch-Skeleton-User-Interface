"""main.py's existing application plus a calibrated, auditable measurement page.

Run from the repository root with an activated Pitcher environment:
    python Src/UI_Control/main_metric.py
    python Src/UI_Control/main_metric.py --measurement-only
    python Src/UI_Control/main_metric.py --check --frame 115

The 3D page can use PnP/8-point metric_stereo_v1 calibration; the additional
measurement page provides manual pixel checks and auditable metric reports.
"""

# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import argparse
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def parse_options(argv=None):
    parser = argparse.ArgumentParser(description="保留 main.py 功能，新增棋盤校正的同一後腳位移量測")
    parser.add_argument("--calibration", type=Path, default=ROOT / "Db/Calibration/chessboard/pnp/stereo.json")
    parser.add_argument("--video-dir", type=Path, default=ROOT / "Db/Record/20261008_Pitcher01/20261008_1130_P05")
    parser.add_argument("--frame", type=int, help="落地 CF 幀（0 起算）；預設讀取既有 FC 候選")
    parser.add_argument("--reference-frame", type=int, default=0)
    parser.add_argument("--cs-frame-offset", type=int, default=0, help="CS frame = CF frame + offset")
    parser.add_argument("--heel", choices=("left", "right"), default="right")
    parser.add_argument("--annotations", type=Path, help="載入原始像素的腳跟／地面選點")
    parser.add_argument("--measurement-only", action="store_true", help="輕量量測介面，不載入相機 SDK／GPU 模型")
    parser.add_argument("--check", action="store_true", help="無介面量測／檢查；缺資料或品質不合格時回傳 2")
    parser.add_argument("--infer", action="store_true", help="搭配 --check：用模型重新辨識四張原圖")
    parser.add_argument("--output", type=Path, help="搭配 --check：成功時匯出至新的資料夾")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if options.infer and (not options.check or options.annotations):
        parser.error("--infer 需搭配 --check，且不可同時指定 --annotations")
    for name in ("calibration", "video_dir", "annotations", "output", "screenshot"):
        value = getattr(options, name)
        if value is not None:
            setattr(options, name, value.resolve())
    return options


def configure_runtime():
    # Existing model/config/icon paths are relative to Src/UI_Control.
    os.chdir(HERE)
    os.environ.setdefault("CONDA_PREFIX", sys.prefix)
    platform_path = Path(os.environ["CONDA_PREFIX"]) / "Library/plugins/platforms"
    if platform_path.is_dir():
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platform_path)
    # Model has its own parsers; never forward this entry point's flags to them.
    sys.argv = [str(HERE / "main_metric.py")]


def check(options):
    # Inference uses the same dependency/DLL initialization order as main.py.
    # Pure geometry checks intentionally need neither Qt nor the camera SDK.
    if options.infer:
        import vispy
        vispy.use("pyqt5")
        from Src.UI_Control.main import Model
    from Src.analysis.metric_session import MetricSession, infer_annotations
    from Src.calibration.chessboard_metric import read_json
    from Src.analysis.stride_workflow import default_contact_frame
    try:
        frame = options.frame if options.frame is not None else default_contact_frame(options.video_dir)[0]
        session = MetricSession(options.calibration, options.video_dir, options.reference_frame,
                                frame, options.cs_frame_offset, options.heel)
        if options.annotations:
            annotations = read_json(options.annotations)
        elif options.infer:
            annotations = infer_annotations(session, Model())
        else:
            annotations = session.pose_annotations()
        result = session.measure(annotations)
        if options.output:
            result["output_directory"] = str(session.export(result, options.output))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, default=lambda x: x.tolist(), indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "unavailable", "distance_3d_cm": None, "ground_distance_cm": None,
                          "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 2


def run_ui(options):
    from PyQt5.QtCore import QTimer
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication, QLabel, QMainWindow, QToolBar
    # Select Qt before importing main's camera SDK and other third-party DLLs.
    if not options.measurement_only:
        import vispy
        vispy.use("pyqt5")
        from Src.UI_Control.main import Main
    from Src.UI_Control.widgets.metric_ui import MetricPanel

    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft JhengHei", 10))
    if options.measurement_only:
        window = QMainWindow()
        window.setCentralWidget(MetricPanel(options))
        window.setWindowTitle("棋盤校正｜同一後腳位移")
        window.resize(1420, 980)
    else:
        class MetricMain(Main):
            def init_tabs(self):
                super().init_tabs()
                self.last_analysis_tab = self.pitch_tab
                tabs = self.ui.Two_d_Tab
                for index, name in ((1, "2D 原分析"), (2, "3D 分析／校正"), (3, "原回放比較")):
                    tabs.setTabText(index, name)
                self.metric_panel = MetricPanel(options, self.model, self.current_video_context, self.pause_analysis)
                self.metric_index = tabs.addTab(self.metric_panel, "校正量測（後腳）")
                tabs.currentChanged.connect(self.tab_changed)
                toolbar = QToolBar("校正量測", self)
                toolbar.setMovable(False)
                button = toolbar.addAction("量測目前影片")
                button.triggered.connect(self.measure_current_video)
                self.scope_label = QLabel()
                self.scope_label.setWordWrap(True)
                toolbar.addWidget(self.scope_label)
                self.addToolBar(toolbar)
                tabs.setCurrentIndex(self.metric_index)
                self.tab_changed(self.metric_index)

            def tab_changed(self, index):
                tab = self.ui.Two_d_Tab.widget(index)
                if tab in (self.pitch_tab, self.video_tab, self.video_compare_tab):
                    self.last_analysis_tab = tab
                if index == self.metric_index:
                    message = "  校正量測使用棋盤毫米尺度；結果另存 metric_measurements，尚需實尺驗證精度。"
                elif tab is self.video_tab:
                    message = "  3D 頁可選 PnP／8Points 棋盤外參；請確認校正、相機同步與關鍵幀。"
                else:
                    message = "  此頁保留原分析：距離、速度與 3D 尺度未套用棋盤校正。公制後腳位移請使用「校正量測」。"
                self.scope_label.setText(message)

            def current_video_context(self):
                tab = self.last_analysis_tab
                loader = tab.video_loader
                first = Path(loader.video_path or "")
                second = Path(getattr(loader, "video_path_2", None) or "")
                if not first.is_file() or not second.is_file() or first.parent != second.parent:
                    raise ValueError("請先在 2D／3D 頁載入同資料夾的 CF、CS 兩段影片。")
                names = (first.name[:3].lower(), second.name[:3].lower())
                if set(names) != {"cf_", "cs_"}:
                    raise ValueError("影片須為一組 CF_／CS_，單影片不能做雙目公制量測。")
                slider = getattr(tab.ui, "frameSlider", None)
                if slider is None:
                    slider = getattr(tab.ui, "frameSlider_2", None)
                if slider is None:
                    raise ValueError("此分析頁沒有可用的影片幀，請在量測頁選擇影片資料夾。")
                first_frame = slider.value()
                second_frame = first_frame - int(getattr(loader, "frame_offset", 0))
                frames = {names[0][:2]: first_frame, names[1][:2]: second_frame}
                if min(frames.values()) < 0:
                    raise ValueError("原頁的偏移會把幀截到影片邊界，請移到有效雙目幀。")
                return first.parent, frames["cf"], frames["cs"] - frames["cf"]

            def pause_analysis(self):
                for tab in (self.pitch_tab, self.video_tab, self.video_compare_tab):
                    for name in ("timer", "_play_timer"):
                        timer = getattr(tab, name, None)
                        if timer is not None:
                            timer.stop()
                    if hasattr(tab, "is_play"):
                        tab.is_play = False

            def measure_current_video(self):
                self.pause_analysis()
                self.ui.Two_d_Tab.setCurrentIndex(self.metric_index)
                self.metric_panel.use_context()

            def closeEvent(self, event):
                if self.metric_panel.worker is not None:
                    self.statusBar().showMessage("正在辨識四張影像，完成後可關閉。")
                    event.ignore()
                    return
                self.pause_analysis()
                super().closeEvent(event)

        window = MetricMain()
        window.setWindowTitle("Pitch Skeleton｜棋盤公制量測")
        window.resize(1450, 1050)
    window.show()
    if options.smoke_test:
        def finish():
            if options.screenshot:
                options.screenshot.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(options.screenshot))
            app.quit()
        QTimer.singleShot(1800, finish)
    return app.exec_()


def main(argv=None):
    options = parse_options(argv)
    configure_runtime()
    return check(options) if options.check else run_ui(options)


if __name__ == "__main__":
    sys.exit(main())
