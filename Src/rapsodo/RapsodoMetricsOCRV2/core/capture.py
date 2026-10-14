from __future__ import annotations

import logging
import time

import cv2
import numpy as np

import Src.rapsodo.RapsodoMetricsOCRV2.config as config

logger = logging.getLogger(__name__)


class FrameCapture:
    """
    Continuously read frames from the HDMI capture card.

    The AVerMedia BU110 registers as a standard UVC device; no driver needed.
    Captures at CAPTURE_FPS (default 1 FPS).  Always returns the latest frame;
    frames are not buffered.  Returns None on transient capture failure.
    """

    def __init__(
        self,
        device_index: int = config.CAMERA_DEVICE_INDEX,
        fps: int = config.CAPTURE_FPS,
    ) -> None:
        self._fps = fps
        self._cap = cv2.VideoCapture(device_index)

        if not self._cap.isOpened():
            available = self._list_available_devices()
            logger.error(
                "Could not open capture device %d. "
                "Available device indices: %s. "
                "Update CAMERA_DEVICE_INDEX in config.py.",
                device_index,
                available,
            )
            raise RuntimeError(
                f"Capture device {device_index} not found. "
                f"Available: {available}. Update CAMERA_DEVICE_INDEX in config.py."
            )

        logger.info("Capture device %d opened at %d FPS.", device_index, fps)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_frame(self) -> np.ndarray | None:
        """
        Grab and return the current frame, or None on failure.

        Sleeps 1/fps seconds to throttle capture rate.
        """
        time.sleep(1.0 / self._fps)

        ret, frame = self._cap.read()
        if not ret or frame is None:
            logger.warning("Frame capture failed (cap.read() returned False).")
            return None

        return frame

    def release(self) -> None:
        """Release the capture device."""
        if self._cap.isOpened():
            self._cap.release()
            logger.info("Capture device released.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _list_available_devices(max_check: int = 5) -> list[int]:
        """Probe device indices 0–max_check and return those that open."""
        available: list[int] = []
        for idx in range(max_check):
            cap = cv2.VideoCapture(idx)
            if cap.isOpened():
                available.append(idx)
            cap.release()
        return available
