
# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import argparse
import os
import sys

import cv2


def extract_frame(video_path: str, frame_index: int, output_path: str) -> None:
    if frame_index < 0:
        raise ValueError("frame_index must be >= 0")

    if not os.path.isfile(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames > 0 and frame_index >= total_frames:
        cap.release()
        raise ValueError(f"frame_index {frame_index} out of range (0..{total_frames - 1})")

    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
    success, frame = cap.read()
    cap.release()

    if not success or frame is None:
        raise RuntimeError(f"Failed to read frame {frame_index} from {video_path}")

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    if not cv2.imwrite(output_path, frame):
        raise RuntimeError(f"Failed to write image: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract a specific frame from a video.")
    parser.add_argument("--video", required=True, help="Path to input video file")
    parser.add_argument("--frame", type=int, required=True, help="Frame index (0-based)")
    parser.add_argument("--out", required=True, help="Path to output image (png/jpg)")
    args = parser.parse_args()

    try:
        extract_frame(args.video, args.frame, args.out)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Saved frame {args.frame} to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
