from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path

import cv2

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover
    YOLO = None


@dataclass(frozen=True)
class Roi:
    name: str
    x: int
    y: int
    w: int
    h: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Strike zone tracker: left panel shows original frame, right panel shows YOLO ball detection + K_Zone overlays"
    )
    parser.add_argument("video", type=Path, help="Path to input MP4")
    parser.add_argument("--roi-file", type=Path, required=True, help="JSON file of named ROIs")
    parser.add_argument(
        "--search-zone-name",
        default="K_Zone_ROI",
        help="ROI name that contains all possible ball positions (default: K_Zone_ROI)",
    )
    parser.add_argument(
        "--strike-zone-name",
        default="K_Zone",
        help="ROI name for the strike zone region (default: K_Zone)",
    )
    parser.add_argument(
        "--display-scale",
        type=float,
        default=0.0,
        help="Scale preview window size. 0 = auto-fit so the side-by-side preview width stays around 1600 px (default: 0 / auto)",
    )
    parser.add_argument(
        "--max-display-width",
        type=int,
        default=1600,
        help="Target max width (px) for the side-by-side preview when --display-scale is auto (default: 1600)",
    )
    parser.add_argument(
        "--save-video",
        type=Path,
        help="Optional output path to save the side-by-side preview video",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        help="Optional CSV path to save detected ball coordinates",
    )
    parser.add_argument(
        "--yolo-model",
        default="yolov8n.pt",
        help="YOLO model path or model name (default: yolov8n.pt)",
    )
    parser.add_argument(
        "--yolo-conf",
        type=float,
        default=0.10,
        help="YOLO confidence threshold for sports ball detection (default: 0.10)",
    )
    return parser.parse_args()


def load_rois_from_file(roi_file: Path) -> dict[str, Roi]:
    if not roi_file.exists():
        raise FileNotFoundError(f"ROI file does not exist: {roi_file}")

    with roi_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError("ROI file format is invalid (expected JSON object)")

    rois: dict[str, Roi] = {}
    for name, roi_data in data.items():
        if not isinstance(roi_data, dict):
            raise ValueError(f"ROI entry for {name} is invalid")
        try:
            x = int(roi_data["x"])
            y = int(roi_data["y"])
            w = int(roi_data["w"])
            h = int(roi_data["h"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"ROI entry for {name} must contain integer x,y,w,h") from exc
        rois[name] = Roi(name=name, x=x, y=y, w=w, h=h)

    return rois


def detect_ball_yolo_in_search_roi(
    frame,
    search_roi: Roi,
    model,
    conf: float,
) -> tuple[tuple[int, int] | None, tuple[int, int, int, int] | None, float, str]:
    roi_bgr = frame[search_roi.y : search_roi.y + search_roi.h, search_roi.x : search_roi.x + search_roi.w]
    results = model.predict(source=roi_bgr, conf=conf, verbose=False)
    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return None, None, 0.0, "none"

    names = model.names
    best: tuple[float, int, int, int, int, str] | None = None
    for b in boxes:
        cls_id = int(b.cls.item())
        cls_name = str(names.get(cls_id, cls_id)).lower()
        if cls_name not in {"sports ball", "baseball", "ball"}:
            continue

        score = float(b.conf.item())
        x1, y1, x2, y2 = (int(v) for v in b.xyxy[0].cpu().numpy())
        gx1 = search_roi.x + x1
        gy1 = search_roi.y + y1
        gx2 = search_roi.x + x2
        gy2 = search_roi.y + y2
        if best is None or score > best[0]:
            best = (score, gx1, gy1, gx2, gy2, cls_name)

    if best is None:
        return None, None, 0.0, "none"

    score, gx1, gy1, gx2, gy2, cls_name = best
    cx = int((gx1 + gx2) / 2)
    cy = int((gy1 + gy2) / 2)
    return (cx, cy), (gx1, gy1, gx2, gy2), score, cls_name


def render_detection_panel(
    frame,
    search_roi: Roi,
    strike_roi: Roi,
    ball_xy: tuple[int, int] | None,
    ball_box: tuple[int, int, int, int] | None,
    score: float,
    cls_name: str,
):
    out = frame.copy()

    cv2.rectangle(out, (search_roi.x, search_roi.y), (search_roi.x + search_roi.w, search_roi.y + search_roi.h), (0, 255, 0), 2)
    cv2.putText(out, search_roi.name, (search_roi.x, max(20, search_roi.y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 0), 2, cv2.LINE_AA)

    cv2.rectangle(out, (strike_roi.x, strike_roi.y), (strike_roi.x + strike_roi.w, strike_roi.y + strike_roi.h), (255, 200, 0), 2)
    cv2.putText(out, strike_roi.name, (strike_roi.x, max(20, strike_roi.y - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 200, 0), 2, cv2.LINE_AA)

    if ball_xy is not None and ball_box is not None:
        x1, y1, x2, y2 = ball_box
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 200, 255), 2)
        cv2.circle(out, ball_xy, 3, (0, 0, 255), -1)
        cv2.putText(
            out,
            f"YOLO {cls_name} conf={score:.2f} ball=({ball_xy[0]},{ball_xy[1]})",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 200, 255),
            2,
            cv2.LINE_AA,
        )
    else:
        cv2.putText(out, "YOLO ball=not found", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 255), 2, cv2.LINE_AA)

    return out


def make_side_by_side(left, right):
    left_panel = left.copy()
    cv2.putText(left_panel, "Left: Original", (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)

    right_panel = right.copy()
    cv2.putText(right_panel, "Right: YOLO + K_Zone", (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA)
    return cv2.hconcat([left_panel, right_panel])


def main() -> None:
    args = parse_args()
    if not args.video.exists():
        raise FileNotFoundError(f"Video does not exist: {args.video}")
    if args.display_scale < 0:
        raise ValueError("--display-scale must be >= 0 (0 means auto-fit)")
    if args.max_display_width <= 0:
        raise ValueError("--max-display-width must be > 0")
    if not 0.0 <= args.yolo_conf <= 1.0:
        raise ValueError("--yolo-conf must be between 0 and 1")
    if YOLO is None:
        raise ImportError("ultralytics is required. Install with: uv add ultralytics")

    yolo_model = YOLO(args.yolo_model)

    rois = load_rois_from_file(args.roi_file)
    if args.search_zone_name not in rois:
        available = ", ".join(sorted(rois.keys()))
        raise ValueError(f"Search zone ROI not found: {args.search_zone_name}. Available: {available}")
    if args.strike_zone_name not in rois:
        available = ", ".join(sorted(rois.keys()))
        raise ValueError(f"Strike zone ROI not found: {args.strike_zone_name}. Available: {available}")

    search_roi = rois[args.search_zone_name]
    strike_roi = rois[args.strike_zone_name]

    cap = cv2.VideoCapture(str(args.video))
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {args.video}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    if search_roi.x + search_roi.w > width or search_roi.y + search_roi.h > height:
        raise ValueError(f"Search zone ROI out of bounds. frame={width}x{height}, roi={search_roi}")

    if strike_roi.x + strike_roi.w > width or strike_roi.y + strike_roi.h > height:
        raise ValueError(f"Strike zone ROI out of bounds. frame={width}x{height}, roi={strike_roi}")

    side_by_side_width = width * 2
    if args.display_scale == 0.0:
        display_scale = min(1.0, args.max_display_width / side_by_side_width)
    else:
        display_scale = args.display_scale

    preview_w = max(1, int(side_by_side_width * display_scale))
    preview_h = max(1, int(height * display_scale))

    window_name = "Strike Zone Tracker (press q to quit)"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, preview_w, preview_h)

    writer = None
    csv_f = None
    csv_writer = None

    if args.save_video:
        args.save_video.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(args.save_video), fourcc, fps, (preview_w, preview_h))
        if not writer.isOpened():
            raise RuntimeError(f"Failed to open output video for writing: {args.save_video}")

    if args.output_csv:
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        csv_f = args.output_csv.open("w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_f)
        csv_writer.writerow(["frame", "dot_x", "dot_y", "rel_x", "rel_y", "inside_zone"])

    frame_idx = 0

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                break

            ball_xy, ball_box, score, cls_name = detect_ball_yolo_in_search_roi(
                frame,
                search_roi,
                yolo_model,
                args.yolo_conf,
            )

            inside_zone = False
            rel_x = ""
            rel_y = ""
            if ball_xy is not None:
                inside_zone = (
                    strike_roi.x <= ball_xy[0] <= strike_roi.x + strike_roi.w
                    and strike_roi.y <= ball_xy[1] <= strike_roi.y + strike_roi.h
                )
                rel_x = (ball_xy[0] - strike_roi.x) / max(1, strike_roi.w)
                rel_y = (ball_xy[1] - strike_roi.y) / max(1, strike_roi.h)

            if csv_writer is not None:
                csv_writer.writerow(
                    [
                        frame_idx,
                        "" if ball_xy is None else ball_xy[0],
                        "" if ball_xy is None else ball_xy[1],
                        "" if ball_xy is None else f"{rel_x:.4f}",
                        "" if ball_xy is None else f"{rel_y:.4f}",
                        int(inside_zone),
                    ]
                )

            if ball_xy is not None:
                print(f"frame={frame_idx} ball=({ball_xy[0]},{ball_xy[1]}) cls={cls_name} conf={score:.3f} inside_zone={int(inside_zone)}")

            right_panel = render_detection_panel(frame, search_roi, strike_roi, ball_xy, ball_box, score, cls_name)
            side_by_side = make_side_by_side(frame, right_panel)

            if abs(display_scale - 1.0) > 1e-6:
                side_by_side = cv2.resize(
                    side_by_side,
                    (preview_w, preview_h),
                    interpolation=cv2.INTER_AREA,
                )

            if writer is not None:
                writer.write(side_by_side)

            cv2.imshow(window_name, side_by_side)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break

            frame_idx += 1
    finally:
        if writer is not None:
            writer.release()
        if csv_f is not None:
            csv_f.close()
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
