
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import argparse
import os
from Src.paths import DB_DIR
import sys
from collections import deque
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PyQt5.QtCore import QMutex, QThread, QWaitCondition, pyqtSignal


WINDOW_NAME = "Tablet Capture"
ROOT_DIR = Path(__file__).resolve().parent
OCR_DIR = ROOT_DIR / "RapsodoMetricsOCRV2"

if str(OCR_DIR) not in sys.path:
    sys.path.insert(0, str(OCR_DIR))


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


def parse_roi(roi_text: str):
    parts = [p.strip() for p in str(roi_text).split(",")]
    if len(parts) != 4:
        raise ValueError("ROI must be 'x,y,w,h'")
    try:
        x, y, w, h = (int(v) for v in parts)
    except ValueError as exc:
        raise ValueError("ROI must be integer values: 'x,y,w,h'") from exc
    return x, y, w, h


def draw_hud(frame, roi, save_count, auto_enabled, frame_idx):
    vis = frame.copy()
    if roi is not None:
        x, y, w, h = roi
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 255, 0), 2)

    hud = [
        "Fixed ROI mode",
        "s: queue save | a: toggle auto save | q: quit",
        f"Saved: {save_count} | Auto: {'ON' if auto_enabled else 'OFF'} | Frame: {frame_idx}",
    ]

    y0 = 24
    for text in hud:
        cv2.putText(vis, text, (10, y0), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (30, 220, 30), 2, cv2.LINE_AA)
        y0 += 24

    return vis


def select_roi_from_frame(frame, window_name="Select ROI", show_crosshair=True, from_center=False):
    x, y, w, h = cv2.selectROI(window_name, frame, show_crosshair, from_center)
    cv2.destroyWindow(window_name)
    if w <= 0 or h <= 0:
        return None
    fh, fw = frame.shape[:2]
    return clamp_roi((x, y, w, h), fw, fh)


class TabletImageCaptureThread(QThread):
    image_saved = pyqtSignal(str)
    roi_frame_ready = pyqtSignal(object)
    ocr_data_ready = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(
        self,
        output_dir,
        prefix="tablet",
        queue_size=256,
        jpeg_quality=98,
        save_ext="jpg",
        enable_ocr=False,
        ocr_json_output=None,
        ocr_player="1",
        ocr_sample_every_n_frames=15,
        parent=None,
    ):
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

        self.enable_ocr = bool(enable_ocr)
        self.ocr_json_output = ocr_json_output
        self.ocr_player = str(ocr_player)
        self.ocr_sample_every_n_frames = max(1, int(ocr_sample_every_n_frames))
        self._last_ocr_signature = None

        self._ocr_inited = False
        self._ocr_config = None
        self._session_manager = None
        self._writer = None
        self._ocr_reader = None

        self._source = None
        self._source_cap = None
        self._source_roi = None
        self._source_every_n_frames = 1
        self._source_width = 1920
        self._source_height = 1080
        self._source_frame_index = 0

    def submit(self, frame, roi, frame_index=None):
        if frame is None or roi is None:
            return False

        h, w = frame.shape[:2]
        rois = self._normalize_rois(roi, w, h)
        if not rois:
            return False
        if frame_index is None:
            frame_index = -1
        else:
            try:
                frame_index = int(frame_index)
            except (TypeError, ValueError):
                frame_index = -1

        self._mutex.lock()
        try:
            if not self._running:
                return False
            if len(self._queue) >= self.queue_size:
                self._queue.popleft()
            self._queue.append((frame.copy(), rois, frame_index))
            self._wait_cond.wakeOne()
            return True
        finally:
            self._mutex.unlock()

    def configure_source(self, source, roi, capture_every_n_frames=15, width=1920, height=1080):
        self._mutex.lock()
        try:
            self._source = parse_source(str(source))
            self._source_roi = tuple(roi) if roi is not None else None
            self._source_every_n_frames = max(1, int(capture_every_n_frames))
            self._source_width = int(width)
            self._source_height = int(height)
            self._source_frame_index = 0

            if self._source_cap is not None:
                try:
                    self._source_cap.release()
                except Exception:
                    pass
                self._source_cap = None
        finally:
            self._mutex.unlock()

    def poll_source_once(self):
        if self._source is None or self._source_roi is None:
            return False
        if not self._ensure_source_opened():
            return False

        ok, frame = self._source_cap.read()
        if not ok or frame is None:
            return False

        self._source_frame_index += 1
        if self._source_frame_index % self._source_every_n_frames != 0:
            return False

        h, w = frame.shape[:2]
        rois = self._normalize_rois(self._source_roi, w, h, scale_from_source_config=True)
        if not rois:
            return False

        return self.submit(frame, rois, frame_index=self._source_frame_index)

    def select_source_roi(
        self,
        window_name="Select ROI",
        show_crosshair=True,
        from_center=False,
        max_read_tries=30,
        return_frame=False,
    ):
        """Open a source frame for manual ROI selection and return the ROI.

        If return_frame is True, also return the source frame used for selection.
        """
        self._mutex.lock()
        try:
            source = self._source
            width = self._source_width
            height = self._source_height
        finally:
            self._mutex.unlock()

        if source is None:
            self.error.emit("source is not configured")
            return None

        cap = cv2.VideoCapture(source)
        if isinstance(source, int):
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(width))
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(height))

        if not cap.isOpened():
            self.error.emit(f"source open failed: {source}")
            return None

        ok = False
        frame = None
        try:
            for _ in range(max(1, int(max_read_tries))):
                ok, frame = cap.read()
                if ok and frame is not None:
                    break
        finally:
            cap.release()

        if not ok or frame is None:
            self.error.emit("failed to read frame for ROI selection")
            return None

        try:
            x, y, w, h = cv2.selectROI(window_name, frame, show_crosshair, from_center)
            cv2.destroyWindow(window_name)
        except Exception as exc:
            self.error.emit(f"ROI selection failed: {exc}")
            return None

        if w <= 0 or h <= 0:
            return None

        fh, fw = frame.shape[:2]
        roi = clamp_roi((x, y, w, h), fw, fh)
        if return_frame:
            return roi, frame.copy()
        return roi

    def stop(self):
        self._mutex.lock()
        try:
            self._running = False
            self._wait_cond.wakeAll()
        finally:
            self._mutex.unlock()
        self.wait()
        self._release_source()

    def run(self):
        if self.enable_ocr:
            self._init_ocr_pipeline()

        while True:
            self._mutex.lock()
            try:
                has_queued_frame = len(self._queue) > 0
                if has_queued_frame:
                    frame, rois, frame_index = self._queue.popleft()
                else:
                    frame = None
                    rois = None
                    frame_index = -1

                running = self._running
                source = self._source
                source_roi = self._source_roi
                source_every_n_frames = self._source_every_n_frames
            finally:
                self._mutex.unlock()

            if not has_queued_frame:
                if not running:
                    self._shutdown_ocr_pipeline()
                    return

                if source is None or source_roi is None:
                    self.msleep(10)
                    continue

                if not self._ensure_source_opened():
                    self.msleep(200)
                    continue

                ok, source_frame = self._source_cap.read()
                if not ok or source_frame is None:
                    self.msleep(5)
                    continue

                self._source_frame_index += 1
                source_frame_index = self._source_frame_index

                if self._ocr_inited and source_frame_index % self.ocr_sample_every_n_frames == 0:
                    self._process_ocr_frame(source_frame, source_frame_index)

                if source_frame_index % source_every_n_frames != 0:
                    continue

                h, w = source_frame.shape[:2]
                auto_rois = self._normalize_rois(source_roi, w, h, scale_from_source_config=True)
                if not auto_rois:
                    continue
                self._process_capture_frame(source_frame, auto_rois, source_frame_index, process_ocr=False)
                continue

            self._process_capture_frame(frame, rois, frame_index, process_ocr=True)

    def _process_capture_frame(self, frame, rois, frame_index, process_ocr=True):
        if rois is None:
            return

        total_rois = len(rois)
        for idx, roi in enumerate(rois):
            file_suffix = None if total_rois == 1 else idx
            # ok, path_or_err = self._save_crop(frame, roi, roi_index=file_suffix)
            # if ok:
            #     # self.image_saved.emit(path_or_err)
            x, y, w, h = roi
            roi_crop = frame[y:y + h, x:x + w]
            if roi_crop.size > 0:
                if total_rois == 1:
                    self.roi_frame_ready.emit(roi_crop.copy())
                else:
                    self.roi_frame_ready.emit({
                        "roi_index": idx,
                        "roi": roi,
                        "frame": roi_crop.copy(),
                        "ball_center": self.find_ball_center(roi_crop),
                    })

        if process_ocr and self._ocr_inited and frame_index >= 0 and frame_index % self.ocr_sample_every_n_frames == 0:
            self._process_ocr_frame(frame, frame_index)

    def _save_crop(self, frame, roi, roi_index=None):
        x, y, w, h = roi
        crop = frame[y:y + h, x:x + w]
        if crop.size == 0:
            return False, "empty crop"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        roi_tag = "" if roi_index is None else f"_r{int(roi_index)}"
        file_name = f"{self.prefix}{roi_tag}_{ts}_{self._save_index:06d}.{self.save_ext}"
        self._save_index += 1
        out_path = os.path.join(self.output_dir, file_name)

        if self.save_ext == "png":
            ok = cv2.imwrite(out_path, crop, [int(cv2.IMWRITE_PNG_COMPRESSION), 3])
        else:
            ok = cv2.imwrite(out_path, crop, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            return False, f"save failed: {out_path}"
        return True, out_path

    def _scale_roi_to_frame(self, roi, width, height):
        """Scale ROI from configured source resolution to current frame size."""
        if roi is None or len(roi) != 4:
            return roi

        ref_w = float(self._source_width) if self._source_width else 0.0
        ref_h = float(self._source_height) if self._source_height else 0.0
        if ref_w <= 0.0 or ref_h <= 0.0:
            return roi

        # No scaling needed if source size matches configured reference.
        if int(ref_w) == int(width) and int(ref_h) == int(height):
            return roi

        x, y, w, h = roi
        sx = float(width) / ref_w
        sy = float(height) / ref_h
        return (
            int(round(float(x) * sx)),
            int(round(float(y) * sy)),
            int(round(float(w) * sx)),
            int(round(float(h) * sy)),
        )

    def _normalize_rois(self, roi, width, height, scale_from_source_config=False):
        if roi is None:
            return []

        if isinstance(roi, (tuple, list)) and len(roi) == 4 and all(isinstance(v, (int, float)) for v in roi):
            if scale_from_source_config:
                roi = self._scale_roi_to_frame(roi, width, height)
            return [clamp_roi(roi, width, height)]

        normalized = []
        if isinstance(roi, (tuple, list)):
            for item in roi:
                if not isinstance(item, (tuple, list)) or len(item) != 4:
                    continue
                if not all(isinstance(v, (int, float)) for v in item):
                    continue
                if scale_from_source_config:
                    item = self._scale_roi_to_frame(item, width, height)
                normalized.append(clamp_roi(item, width, height))
        return normalized

    def _init_ocr_pipeline(self):
        try:
            import Src.rapsodo.RapsodoMetricsOCRV2.config as ocr_config
            from Src.rapsodo.RapsodoMetricsOCRV2.core.data_writer import DataWriter
            from Src.rapsodo.RapsodoMetricsOCRV2.core.session_manager import SessionManager
            from Src.rapsodo.RapsodoMetricsOCRV2.modes.pitching import PitchingOCRReader

            self._ocr_config = ocr_config
            output_json = self.ocr_json_output or ocr_config.OUTPUT_JSON_PITCHING

            self._session_manager = SessionManager()
            session_id = self._session_manager.start_session("pitching", self.ocr_player, json_path=str(output_json))

            self._writer = DataWriter(str(output_json), "pitching", session_id)
            self._writer.set_player(self.ocr_player)
            self._ocr_reader = PitchingOCRReader("kph","cm")
            self._ocr_inited = True
        except Exception as exc:
            self._ocr_inited = False
            self.error.emit(f"OCR init failed: {exc}")

    def _shutdown_ocr_pipeline(self):
        if self._session_manager is not None:
            try:
                self._session_manager.end_session()
            except Exception:
                pass
        self._session_manager = None
        self._writer = None
        self._ocr_reader = None
        self._ocr_inited = False

    def _build_ocr_base(self, frame):
        return cv2.resize(
            frame,
            (self._ocr_config.SCREEN_WARP_WIDTH, self._ocr_config.SCREEN_WARP_HEIGHT),
            interpolation=cv2.INTER_AREA,
        )

    def _ensure_source_opened(self):
        if self._source_cap is not None and self._source_cap.isOpened():
            return True

        self._source_cap = cv2.VideoCapture(self._source)
        if not self._source_cap.isOpened():
            self.error.emit(f"source open failed: {self._source}")
            self._source_cap = None
            return False

        if isinstance(self._source, int):
            self._source_cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            self._source_cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._source_width)
            self._source_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._source_height)
        return True

    def _release_source(self):
        if self._source_cap is None:
            return
        try:
            self._source_cap.release()
        except Exception:
            pass
        self._source_cap = None

    def _process_ocr_frame(self, frame, frame_index):
        try:
            screen_crop = self._build_ocr_base(frame)
            metrics = self._ocr_reader.read(screen_crop, frame_index=frame_index)
            if metrics is None:
                return

            signature = (
                metrics.velocity_mph,
                metrics.total_spin_rpm,
                metrics.spin_direction,
                metrics.spin_efficiency_pct,
                metrics.v_break_in,
                metrics.h_break_in,
            )
            if all(v is None for v in signature):
                return
            if self._last_ocr_signature == signature:
                return

            entry_id = self._writer.write_entry(self._session_manager.current_player, metrics)
            self._last_ocr_signature = signature

            payload = {
                "entry_id": entry_id,
                "frame_index": frame_index,
                "velocity_mph": metrics.velocity_mph,
                "total_spin_rpm": metrics.total_spin_rpm,
                "spin_direction": metrics.spin_direction,
                "spin_efficiency_pct": metrics.spin_efficiency_pct,
                "vB": metrics.v_break_in,
                "hB": metrics.h_break_in,
            }
            self.ocr_data_ready.emit(payload)
        except Exception as exc:
            self.error.emit(f"OCR process failed: {exc}")

    @staticmethod
    def find_ball_center(roi_frame):
        if roi_frame is None:
            return None

        # 1. 轉換到 HSV 空間，方便過濾顏色
        hsv = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
        
        # 2. 定義「白色」的範圍 (球體通常是亮度最高的部分)
        # 低飽和度 (S), 高亮度 (V) 是白色的特徵
        lower_white = np.array([0, 0, 200])   # 亮度高於 200
        upper_white = np.array([180, 50, 255]) # 飽和度低於 50
        
        mask = cv2.inRange(hsv, lower_white, upper_white)
        
        # 3. 去除雜訊 (侵蝕與膨脹)
        kernel = np.ones((3,3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

        # 4. 尋找輪廓
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        best_center = None
        max_circularity = 0
        
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < 50: # 過濾太小的雜訊
                continue
                
            # 計算圓形度 (Circularity) = 4 * PI * Area / Perimeter^2
            # 愈接近 1 代表愈像圓形
            perimeter = cv2.arcLength(cnt, True)
            if perimeter == 0: continue
            circularity = 4 * np.pi * area / (perimeter * perimeter)
            
            if circularity > 0.6: # 如果這個輪廓夠圓
                # 計算質心 (Centroid)
                M = cv2.moments(cnt)
                if M["m00"] != 0:
                    cX = int(M["m10"] / M["m00"])
                    cY = int(M["m01"] / M["m00"])
                    
                    # 在此應用中，通常我們要找的是「最上方」或「最新」的球
                    # 這裡我們先回傳圓形度最高的一個
                    if circularity > max_circularity:
                        max_circularity = circularity
                        best_center = (cX, cY)

        return best_center


def main():
    parser = argparse.ArgumentParser(description="Capture tablet screen crops from camera/video in background thread.")
    parser.add_argument("--source", default="0", help="Camera index (e.g. 0/1) or video path.")
    parser.add_argument("--output-dir", default=str(DB_DIR / "Record/TabletCapture"), help="Directory to save crops.")
    parser.add_argument("--prefix", default="tablet", help="Saved file name prefix.")
    parser.add_argument("--auto-every", type=int, default=30, help="Auto-save every N frames when auto mode is enabled.")
    parser.add_argument("--width", type=int, default=1920, help="Requested capture width for camera source.")
    parser.add_argument("--height", type=int, default=1080, help="Requested capture height for camera source.")
    parser.add_argument("--jpeg-quality", type=int, default=98, help="JPEG quality (1-100).")
    parser.add_argument("--save-ext", default="jpg", choices=["jpg", "jpeg", "png"], help="Output image format.")
    parser.add_argument("--roi", default="868,536,146,97", help="Fixed ROI as x,y,w,h")
    parser.add_argument("--detect-roi", default="", help="Ball-detection ROI as x,y,w,h. If empty, use --roi.")
    parser.add_argument("--select-roi", action="store_true", help="Interactively select ROI on startup.")
    parser.add_argument("--window-normal", action="store_true", help="Use resizable window. Default uses autosize to avoid misleading scaling.")
    args = parser.parse_args()

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

    if args.select_roi:
        ok, first_frame = cap.read()
        if not ok or first_frame is None:
            print("[ERROR] Failed to read first frame for ROI selection.")
            cap.release()
            return
        selected_roi = select_roi_from_frame(first_frame, window_name="Select ROI")
        if selected_roi is None:
            print("[INFO] ROI selection cancelled.")
            cap.release()
            return
        configured_roi = selected_roi
        print(f"[INFO] Selected ROI: {configured_roi}")
    else:
        try:
            configured_roi = parse_roi(args.roi)
        except ValueError as exc:
            print(f"[ERROR] Invalid --roi: {exc}")
            cap.release()
            return

    if args.detect_roi:
        try:
            detect_roi = parse_roi(args.detect_roi)
        except ValueError as exc:
            print(f"[ERROR] Invalid --detect-roi: {exc}")
            cap.release()
            return
    else:
        detect_roi = configured_roi

    worker = TabletImageCaptureThread(
        args.output_dir,
        prefix=args.prefix,
        jpeg_quality=args.jpeg_quality,
        save_ext=args.save_ext,
    )
    state = {
        "roi": configured_roi,
        "detect_roi": detect_roi,
        "save_count": 0,
        "frame_idx": 0,
        "auto_enabled": False,
        "ball_center": None,
    }

    def on_saved(path):
        state["save_count"] += 1
        print(f"[SAVED] {path}")

    def on_error(msg):
        print(f"[WARN] {msg}")

    worker.image_saved.connect(on_saved)
    worker.error.connect(on_error)
    worker.start()

    window_mode = cv2.WINDOW_NORMAL if args.window_normal else cv2.WINDOW_AUTOSIZE
    cv2.namedWindow(WINDOW_NAME, window_mode)

    auto_every = max(1, int(args.auto_every))

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("[INFO] End of stream or failed to read frame.")
                break

            state["frame_idx"] += 1
            h, w = frame.shape[:2]

            state["roi"] = clamp_roi(state["roi"], w, h)
            state["detect_roi"] = clamp_roi(state["detect_roi"], w, h)
            rx, ry, rw, rh = state["roi"]
            dx, dy, dw, dh = state["detect_roi"]

            roi_crop = frame[dy:dy + dh, dx:dx + dw]
            roi_center = TabletImageCaptureThread.find_ball_center(roi_crop)
            if roi_center is not None:
                cx, cy = roi_center
                state["ball_center"] = (dx + cx, dy + cy)
            else:
                state["ball_center"] = None

            if state["auto_enabled"] and state["frame_idx"] % auto_every == 0:
                worker.submit(frame, state["roi"])

            vis = draw_hud(frame, state["roi"], state["save_count"], state["auto_enabled"], state["frame_idx"])
            cv2.rectangle(vis, (dx, dy), (dx + dw, dy + dh), (255, 180, 0), 2)

            if state["ball_center"] is not None:
                cx, cy = state["ball_center"]
                cv2.circle(vis, (int(cx), int(cy)), 7, (0, 0, 255), -1)
                cv2.circle(vis, (int(cx), int(cy)), 12, (255, 255, 255), 2)
                center_text = f"Ball center: ({int(cx)}, {int(cy)})"
            else:
                center_text = "Ball center: N/A"
            cv2.putText(vis, center_text, (10, 98), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (30, 220, 220), 2, cv2.LINE_AA)
            cv2.putText(vis, f"Detect ROI: ({dx},{dy},{dw},{dh})", (10, 122), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 180, 0), 2, cv2.LINE_AA)

            cv2.imshow(WINDOW_NAME, vis)

            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("a"):
                state["auto_enabled"] = not state["auto_enabled"]
            if key == ord("s"):
                rx, ry, rw, rh = state["roi"]
                print(f"[INFO] Queue save ROI: {rw}x{rh}")
                worker.submit(frame, state["roi"])
    finally:
        cap.release()
        cv2.destroyAllWindows()
        worker.stop()
        print(f"[DONE] Total saved: {state['save_count']} | Output: {os.path.abspath(args.output_dir)}")


if __name__ == "__main__":
    main()
