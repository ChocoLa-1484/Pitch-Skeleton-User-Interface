"""
Rapsodo Pitching Real-Time OCR Extraction System
================================================

Usage:
    python main.py

Keyboard commands while running:
    n  – change current player
    q  – end session and exit
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))


import argparse
import logging
import sys

import cv2

import Src.rapsodo.RapsodoMetricsOCR.config as config
from Src.rapsodo.RapsodoMetricsOCR.core.capture import FrameCapture
from Src.rapsodo.RapsodoMetricsOCR.core.change_detector import ChangeDetector
from Src.rapsodo.RapsodoMetricsOCR.core.data_writer import DataWriter
from Src.rapsodo.RapsodoMetricsOCR.core.screen_detector import ScreenDetector
from Src.rapsodo.RapsodoMetricsOCR.core.session_manager import SessionManager
from Src.rapsodo.RapsodoMetricsOCR.modes.pitching import PitchingOCRReader

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rapsodo Pitching Real-Time OCR Extraction System"
    )
    parser.add_argument(
        "--mode",
        default="pitching",
        choices=["pitching"],
        help="Capture mode (only pitching in this delivery build)",
    )
    return parser.parse_args()


def _prompt_player(prompt: str = "Enter player name: ") -> str:
    while True:
        name = input(prompt).strip()
        if name:
            return name
        print("Player name cannot be empty.")


def main() -> None:
    args = _parse_args()
    mode: str = args.mode

    print(f"\n=== Rapsodo OCR System  |  mode: {mode.upper()} ===")

    # Prompt for the first player
    first_player = _prompt_player("Enter first player name: ")

    # Initialise components
    session = SessionManager()
    session_id = session.start_session(mode, first_player)

    capture = FrameCapture()
    detector = ScreenDetector()
    ocr_reader = PitchingOCRReader()
    change_detector = ChangeDetector()
    writer = DataWriter(config.OUTPUT_JSON_PATH, mode, session_id)
    writer.set_player(first_player)

    print(
        f"Session {session_id} started.  Player: {first_player}\n"
        f"Press 'n' to change player, 'q' to quit.\n"
    )

    running = True
    _PREVIEW_WINDOW = "Rapsodo Live (press q to quit, n to change player)"
    cv2.namedWindow(_PREVIEW_WINDOW, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(_PREVIEW_WINDOW, 640, 360)

    try:
        while running:
            # --- Capture ---
            frame = capture.get_frame()
            if frame is None:
                # Keep UI responsive even when no frame arrives
                key = cv2.waitKey(config.KEYBOARD_CHECK_TIMEOUT_MS) & 0xFF
                if key == ord("q"):
                    running = False
                    break
                continue

            # --- Preview (also enables cv2.waitKey to receive keystrokes) ---
            cv2.imshow(_PREVIEW_WINDOW, frame)
            key = cv2.waitKey(config.KEYBOARD_CHECK_TIMEOUT_MS) & 0xFF
            if key == ord("q"):
                print("Quit requested.")
                running = False
                break
            if key == ord("n"):
                new_player = _prompt_player("Enter new player name: ")
                session.change_player(new_player)
                writer.set_player(new_player)
                change_detector.reset()
                print(f"Player changed to: {new_player}")

            # --- Screen detection ---
            screen = detector.detect(frame)
            if screen is None:
                continue

            # --- OCR ---
            metrics = ocr_reader.read(screen)
            if metrics is None:
                continue

            # --- Change detection ---
            if change_detector.is_new_entry(metrics):
                entry_id = writer.write_entry(session.current_player, metrics)
                print(f"✓ [{mode}] Entry #{entry_id} | {metrics}")
                logger.info("[%s] Entry #%d written: %s", mode, entry_id, metrics)

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        capture.release()
        session.end_session()
        cv2.destroyAllWindows()
        print("Session ended. Goodbye.")


if __name__ == "__main__":
    main()
