from __future__ import annotations

# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))


import argparse
import os
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
from PyQt5.QtCore import QMutex, QThread, QWaitCondition, pyqtSignal


WINDOW_NAME = "Tablet Capture"
from Src.paths import RAPSODO_DIR as ROOT_DIR
OCR_DIR = ROOT_DIR / "RapsodoMetricsOCR"

if str(OCR_DIR) not in sys.path:
    sys.path.insert(0, str(OCR_DIR))

import Src.rapsodo.RapsodoMetricsOCR.config as ocr_config
from Src.rapsodo.RapsodoMetricsOCR.core.change_detector import ChangeDetector
from Src.rapsodo.RapsodoMetricsOCR.core.data_writer import DataWriter
from Src.rapsodo.RapsodoMetricsOCR.core.screen_detector import ScreenDetector
from Src.rapsodo.RapsodoMetricsOCR.core.session_manager import SessionManager
from Src.rapsodo.RapsodoMetricsOCR.modes.pitching import PitchingOCRReader


def parse_source(value: str):
    if value.isdigit():
        return int(value)
    return value


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def clamp_roi(roi, width, height):
    x, y, w, h = roi
    x = max(0, min(int(x), width - 1))
    y = max(0, min(int(y), height - 1))
    w = max(1, min(int(w), width - x))
    h = max(1, min(int(h), height - y))
    return x, y, w, h


def draw_hud(frame, capture_roi, save_count, frame_idx, stage, ocr_count):
    """Draw HUD with capture ROI and status.

    Stages:
      'select_capture': waiting for capture ROI selection
      'running': capture ROI fixed, auto-saving and OCR active
    """
    vis = frame.copy()

    if capture_roi is not None:
        x, y, w, h = capture_roi
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)
        cv2.putText(vis, "CAPTURE ROI", (x, max(y - 5, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    hud_lines = []
    if stage == "select_capture":
        hud_lines = [
            "[STAGE 1: SELECT CAPTURE ROI]",
            "Drag mouse to select ROI | c: confirm | r: reset | q: quit",
        ]
    elif stage == "running":
        hud_lines = [
            "[STAGE 2: AUTO CAPTURING + FULL-FRAME OCR]",
            "q: quit",
            f"Saved: {save_count} | Frame: {frame_idx}",
            f"OCR entries: {ocr_count}",
        ]

    y0 = 24
    for text in hud_lines:
        cv2.putText(vis, text, (10, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (30, 220, 30), 2, cv2.LINE_AA)
        y0 += 24

    return vis


def draw_ocr_filter_boxes(frame):
    """Draw projected OCR field boxes used by PitchingOCRReader.

    The reader uses fractional ROIs from config.PITCHING_ROI on the OCR base
    image. Since our OCR base is the full frame resized to 1200x900, the same
    fractional coordinates can be projected directly onto the current preview.
    """
    vis = frame.copy()
    h, w = vis.shape[:2]

    for field_name, (rx, ry, rw, rh) in ocr_config.PITCHING_ROI.items():
        x1 = int(rx * w)
        y1 = int(ry * h)
        x2 = int((rx + rw) * w)
        y2 = int((ry + rh) * h)
        cv2.rectangle(vis, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(
            vis,
            field_name,
            (x1, max(20, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 0, 255),
            1,
            cv2.LINE_AA,
        )

    return vis


class TabletImageCaptureThread(QThread):
    image_saved = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, output_dir, prefix="tablet", queue_size=256, jpeg_quality=98, save_ext="jpg", parent=None):
        super().__init__(parent)
        self.output_dir = ensure_dir(output_dir)
        self.prefix = prefix
        self.queue_size = max(1, int(queue_size))
        self.jpeg_quality = max(1, min(100, int(jpeg_quality)))
        self.save_ext = str(save_ext).lower().lstrip(".")
        if self.save_ext not in ("jpg", "jpeg", "png"):
            self.save_ext = "jpg"

        self._mutex = QMutex()
        self._wait_cond = QWaitCondition()
        self._queue = deque()
        self._running = True
        self._save_index = 0

    def submit(self, frame, roi):
        if frame is None or roi is None:
            return False

        h, w = frame.shape[:2]
        x, y, rw, rh = clamp_roi(roi, w, h)

        self._mutex.lock()
        try:
            if not self._running:
                return False
            if len(self._queue) >= self.queue_size:
                self._queue.popleft()
            self._queue.append((frame.copy(), (x, y, rw, rh)))
            self._wait_cond.wakeOne()
            return True
        finally:
            self._mutex.unlock()

    def stop(self):
        self._mutex.lock()
        try:
            self._running = False
            self._wait_cond.wakeAll()
        finally:
            self._mutex.unlock()
        self.wait()

    def run(self):
        while True:
            self._mutex.lock()
            try:
                while self._running and len(self._queue) == 0:
                    self._wait_cond.wait(self._mutex)

                if not self._running and len(self._queue) == 0:
                    return

                frame, roi = self._queue.popleft()
            finally:
                self._mutex.unlock()

            ok, path_or_err = self._save_crop(frame, roi)
            if ok:
                self.image_saved.emit(path_or_err)
            else:
                self.error.emit(path_or_err)

    def _save_crop(self, frame, roi):
        x, y, w, h = roi
        crop = frame[y : y + h, x : x + w]
        if crop.size == 0:
            return False, "empty crop"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        file_name = f"{self.prefix}_{ts}_{self._save_index:06d}.{self.save_ext}"
        self._save_index += 1
        out_path = os.path.join(self.output_dir, file_name)

        if self.save_ext == "png":
            ok = cv2.imwrite(out_path, crop, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
        else:
            ok = cv2.imwrite(out_path, crop, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            return False, f"save failed: {out_path}"
        return True, out_path


def _create_ocr_pipeline(output_json: Path, player: str, use_screen_detection: bool):
    session_manager = SessionManager()
    session_id = session_manager.start_session("pitching", player, json_path=str(output_json))
    writer = DataWriter(str(output_json), "pitching", session_id)
    writer.set_player(player)
    screen_detector = None
    ocr_reader = PitchingOCRReader()
    change_detector = ChangeDetector()
    return session_manager, writer, screen_detector, ocr_reader, change_detector


def _build_ocr_base(frame):
    """Return the image that PitchingOCRReader should treat as the screen crop.

    OCR input is always the full frame normalized to the calibrated size.
    """
    return cv2.resize(
        frame,
        (ocr_config.SCREEN_WARP_WIDTH, ocr_config.SCREEN_WARP_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )


def _process_ocr_frame(
    frame,
    frame_index: int,
    screen_detector,
    ocr_reader,
    writer: DataWriter,
    session_manager: SessionManager,
    change_detector: ChangeDetector,
    last_written_signature: dict,
) -> None:
    """Process OCR using full-frame input only."""
    screen_crop = _build_ocr_base(frame)

    metrics = ocr_reader.read(screen_crop, frame_index=frame_index)
    if metrics is None:
        print(f"[OCR] frame {frame_index}: no metrics")
        return

    signature = (
        metrics.velocity_mph,
        metrics.total_spin_rpm,
        metrics.spin_direction,
        metrics.spin_efficiency_pct,
    )

    if all(v is None for v in signature):
        print(f"[OCR] frame {frame_index}: all fields null")
        return

    if last_written_signature.get("value") == signature:
        print(f"[OCR] frame {frame_index}: duplicate metrics skipped")
        return

    entry_id = writer.write_entry(session_manager.current_player, metrics)
    last_written_signature["value"] = signature
    print(f"[OCR] entry #{entry_id}: {metrics}")


def main():
    parser = argparse.ArgumentParser(description="Capture tablet ROI in background thread and optionally export OCR JSON.")
    parser.add_argument("--source", default="0", help="Camera index (e.g. 0/1) or video path.")
    parser.add_argument("--output-dir", default="output/roi_crops", help="Directory to save crops.")
    parser.add_argument("--prefix", default="tablet", help="Saved file name prefix.")
    parser.add_argument("--auto-every", type=int, default=30, help="Auto-save every N frames when auto mode is enabled.")
    parser.add_argument("--width", type=int, default=1920, help="Requested capture width for camera source.")
    parser.add_argument("--height", type=int, default=1080, help="Requested capture height for camera source.")
    parser.add_argument("--jpeg-quality", type=int, default=98, help="JPEG quality (1-100).")
    parser.add_argument("--save-ext", default="jpg", choices=["jpg", "jpeg", "png"], help="Output image format.")
    parser.add_argument("--window-normal", action="store_true", help="Use resizable window. Default uses autosize.")

    parser.add_argument("--skip-ocr", action="store_true", help="Disable OCR / JSON export.")
    parser.add_argument("--json-output", type=Path, default=Path(ocr_config.OUTPUT_JSON_PITCHING), help="OCR JSON output path.")
    parser.add_argument("--player", default="1", help="Player name stored in JSON session.")
    parser.add_argument(
        "--ocr-sample-every-n-frames",
        type=int,
        default=ocr_config.OCR_SAMPLE_EVERY_N_FRAMES,
        help="Run OCR every N frames.",
    )
    parser.add_argument(
        "--ocr-screen-detect",
        action="store_true",
        default=True,
        help="Detect and warp iPad screen before OCR (default).",
    )
    parser.add_argument(
        "--no-ocr-screen-detect",
        dest="ocr_screen_detect",
        action="store_false",
        help="Skip screen detection and OCR full frame.",
    )
    args = parser.parse_args()

    if args.width <= 0 or args.height <= 0:
        raise ValueError("--width and --height must be > 0")
    if args.auto_every <= 0:
        raise ValueError("--auto-every must be > 0")
    if args.ocr_sample_every_n_frames <= 0:
        raise ValueError("--ocr-sample-every-n-frames must be > 0")

    source = parse_source(str(args.source))
    cap = cv2.VideoCapture(source)

    if isinstance(source, int):
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(args.width))
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(args.height))

    if not cap.isOpened():
        print(f"[ERROR] Cannot open source: {args.source}")
        return

    actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"[INFO] Source opened: {actual_w}x{actual_h}, FPS={actual_fps:.2f}")

    worker = TabletImageCaptureThread(
        args.output_dir,
        prefix=args.prefix,
        jpeg_quality=args.jpeg_quality,
        save_ext=args.save_ext,
    )
    state = {
        "dragging": False,
        "start": None,
        "capture_roi": None,
        "pending_roi": None,
        "save_count": 0,
        "ocr_count": 0,
        "frame_idx": 0,
        "stage": "select_capture",  # select_capture -> running
        "last_ocr_signature": {"value": None},
    }

    ocr_state = None
    if not args.skip_ocr:
        ocr_state = _create_ocr_pipeline(args.json_output, args.player, args.ocr_screen_detect)

    def on_saved(path):
        state["save_count"] += 1
        print(f"[SAVED] {path}")

    def on_error(msg):
        print(f"[WARN] {msg}")

    worker.image_saved.connect(on_saved)
    worker.error.connect(on_error)
    worker.start()

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            state["dragging"] = True
            state["start"] = (x, y)
        elif event == cv2.EVENT_MOUSEMOVE and state["dragging"] and state["start"] is not None:
            x0, y0 = state["start"]
            rx = min(x0, x)
            ry = min(y0, y)
            rw = abs(x - x0)
            rh = abs(y - y0)
            if rw > 0 and rh > 0:
                state["pending_roi"] = (rx, ry, rw, rh)
        elif event == cv2.EVENT_LBUTTONUP and state["start"] is not None:
            state["dragging"] = False
            x0, y0 = state["start"]
            rx = min(x0, x)
            ry = min(y0, y)
            rw = abs(x - x0)
            rh = abs(y - y0)
            if rw > 0 and rh > 0:
                state["pending_roi"] = (rx, ry, rw, rh)
            state["start"] = None

    window_mode = cv2.WINDOW_NORMAL if args.window_normal else cv2.WINDOW_AUTOSIZE
    cv2.namedWindow(WINDOW_NAME, window_mode)
    cv2.setMouseCallback(WINDOW_NAME, on_mouse)

    auto_every = max(1, int(args.auto_every))

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("[INFO] End of stream or failed to read frame.")
                break

            state["frame_idx"] += 1
            frame_idx = state["frame_idx"]
            h, w = frame.shape[:2]

            if state["capture_roi"] is not None:
                state["capture_roi"] = clamp_roi(state["capture_roi"], w, h)

            if state["pending_roi"] is not None:
                state["pending_roi"] = clamp_roi(state["pending_roi"], w, h)

            if state["stage"] == "running" and state["capture_roi"] is not None and frame_idx % auto_every == 0:
                worker.submit(frame, state["capture_roi"])

            if state["stage"] == "running" and ocr_state is not None and frame_idx % args.ocr_sample_every_n_frames == 0:
                session_manager, writer, screen_detector, ocr_reader, change_detector = ocr_state
                _process_ocr_frame(
                    frame,
                    frame_idx,
                    screen_detector,
                    ocr_reader,
                    writer,
                    session_manager,
                    change_detector,
                    state["last_ocr_signature"],
                )
                state["ocr_count"] += 1

            frame_with_ocr_boxes = draw_ocr_filter_boxes(frame)

            vis = draw_hud(
                frame_with_ocr_boxes,
                state["capture_roi"] if state["stage"] != "select_capture" else state["pending_roi"],
                state["save_count"],
                frame_idx,
                state["stage"],
                state["ocr_count"],
            )
            cv2.imshow(WINDOW_NAME, vis)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("r"):
                if state["stage"] == "select_capture":
                    state["pending_roi"] = None
            if key == ord("c"):
                if state["stage"] == "select_capture" and state["pending_roi"] is not None:
                    state["capture_roi"] = state["pending_roi"]
                    state["pending_roi"] = None
                    state["stage"] = "running"
                    print(f"[INFO] Capture ROI confirmed: {state['capture_roi']}")
                    print("[INFO] Starting auto-save and OCR using the selected ROI as the base.")
                    if ocr_state is not None:
                        session_manager, writer, screen_detector, ocr_reader, change_detector = ocr_state
                        _process_ocr_frame(
                            frame,
                            frame_idx,
                            screen_detector,
                            ocr_reader,
                            writer,
                            session_manager,
                            change_detector,
                            state["last_ocr_signature"],
                        )
                        state["ocr_count"] += 1
    finally:
        cap.release()
        cv2.destroyAllWindows()
        worker.stop()
        if ocr_state is not None:
            session_manager, _, _, _, _ = ocr_state
            session_manager.end_session()
        print(f"[DONE] Total saved: {state['save_count']} | Output: {os.path.abspath(args.output_dir)}")


if __name__ == "__main__":
    main()
