from __future__ import annotations

import logging

import cv2
import numpy as np

import Src.rapsodo.RapsodoMetricsOCRV2.config as config

logger = logging.getLogger(__name__)


class ScreenDetector:
    """
    Given a raw camera frame, locate the iPad screen's four corners
    and return a perspective-corrected rectangular crop.

    Algorithm:
        1. Grayscale conversion
        2. Gaussian blur (kernel size from config.SCREEN_BLUR_KERNEL_SIZE)
        3. Canny edge detection
        4. Find contours; filter by area (>20 % of frame) and aspect ratio (0.6–0.9)
        5. Approximate contour to 4-point polygon
        6. Perspective transform to a fixed-size frontal crop
    """

    def __init__(self) -> None:
        self._consecutive_failures: int = 0

    def detect(self, frame: np.ndarray) -> np.ndarray | None:
        """
        Return the perspective-corrected iPad screen crop, or None if not found.

        Logs a warning after SCREEN_FAIL_WARN_THRESHOLD consecutive failures
        but does not raise.
        """
        screen = self._find_screen(frame)

        if screen is None:
            self._consecutive_failures += 1
            if self._consecutive_failures >= config.SCREEN_FAIL_WARN_THRESHOLD:
                logger.warning(
                    "iPad screen not detected for %d consecutive frames.",
                    self._consecutive_failures,
                )
        else:
            self._consecutive_failures = 0

        return screen

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _find_screen(self, frame: np.ndarray) -> np.ndarray | None:
        frame_area = frame.shape[0] * frame.shape[1]
        min_area = frame_area * config.SCREEN_MIN_AREA_FRACTION

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(
            gray,
            (config.SCREEN_BLUR_KERNEL_SIZE, config.SCREEN_BLUR_KERNEL_SIZE),
            0,
        )
        edges = cv2.Canny(
            blurred,
            config.CANNY_THRESHOLD_LOW,
            config.CANNY_THRESHOLD_HIGH,
        )

        contours, _ = cv2.findContours(
            edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        best_quad: np.ndarray | None = None
        best_area: float = 0.0

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue

            # Approximate to polygon; accept only quadrilaterals
            peri = cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(
                contour, config.SCREEN_CONTOUR_EPSILON_FACTOR * peri, True
            )
            if len(approx) != 4:
                continue

            # Check aspect ratio
            x, y, w, h = cv2.boundingRect(approx)
            if h == 0:
                continue
            aspect = w / h
            if not (
                config.SCREEN_ASPECT_RATIO_MIN <= aspect <= config.SCREEN_ASPECT_RATIO_MAX
            ):
                continue

            if area > best_area:
                best_area = area
                best_quad = approx

        if best_quad is None:
            logger.debug("No valid iPad screen contour found in frame.")
            return None

        return self._perspective_transform(frame, best_quad)

    @staticmethod
    def _perspective_transform(
        frame: np.ndarray, quad: np.ndarray
    ) -> np.ndarray:
        """Apply a perspective warp to produce a frontal rectangular crop."""
        pts = quad.reshape(4, 2).astype(np.float32)

        # Order points: top-left, top-right, bottom-right, bottom-left
        pts = _order_points(pts)

        dst = np.array(
            [
                [0, 0],
                [config.SCREEN_WARP_WIDTH - 1, 0],
                [config.SCREEN_WARP_WIDTH - 1, config.SCREEN_WARP_HEIGHT - 1],
                [0, config.SCREEN_WARP_HEIGHT - 1],
            ],
            dtype=np.float32,
        )

        matrix = cv2.getPerspectiveTransform(pts, dst)
        warped = cv2.warpPerspective(
            frame, matrix, (config.SCREEN_WARP_WIDTH, config.SCREEN_WARP_HEIGHT)
        )
        return warped


def _order_points(pts: np.ndarray) -> np.ndarray:
    """
    Order four points as: top-left, top-right, bottom-right, bottom-left.

    Uses the sum/diff trick:
        top-left     → smallest sum  (x+y)
        bottom-right → largest sum
        top-right    → smallest diff (x-y)
        bottom-left  → largest diff
    """
    ordered = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    ordered[0] = pts[np.argmin(s)]   # top-left
    ordered[2] = pts[np.argmax(s)]   # bottom-right
    diff = np.diff(pts, axis=1)
    ordered[1] = pts[np.argmin(diff)]  # top-right
    ordered[3] = pts[np.argmax(diff)]  # bottom-left
    return ordered
