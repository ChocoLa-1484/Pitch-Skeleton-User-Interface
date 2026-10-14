"""
Detect available VideoCapture device indices.

Usage:
    python tools/detect_devices.py

Scans indices 0–9 and reports which are accessible by OpenCV.
No dependencies beyond opencv-python.
"""

import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))


import sys
import os

# Allow imports from the project root when run as a script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import cv2


def detect_devices(max_index: int = 9) -> None:
    print(f"Scanning VideoCapture indices 0 – {max_index}...\n")

    available = []
    unavailable = []

    for idx in range(max_index + 1):
        cap = cv2.VideoCapture(idx)
        if cap.isOpened():
            width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps    = cap.get(cv2.CAP_PROP_FPS)
            available.append((idx, width, height, fps))
        else:
            unavailable.append(idx)
        cap.release()

    if available:
        print("Available devices:")
        for idx, w, h, fps in available:
            print(f"  [{idx}]  {w}x{h} @ {fps:.0f} fps")
    else:
        print("No available devices found.")

    if unavailable:
        print("\nUnavailable indices:")
        print(f"  {unavailable}")

    if available:
        print(
            f"\nSet CAMERA_DEVICE_INDEX = {available[0][0]} in config.py"
            " (or whichever index corresponds to your AVerMedia BU110)."
        )


if __name__ == "__main__":
    detect_devices()
