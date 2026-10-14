"""
Rapsodo Pitching Real-Time OCR Extraction System
================================================

Usage:
    python main.py                                                 # mph + in
    python main.py --source-velocity-unit kph --source-break-unit cm
    python main.py --source-velocity-unit kph --source-break-unit cm \\
                   --output-velocity-unit mph --output-break-unit in

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

import Src.rapsodo.RapsodoMetricsOCRV2.config as config
from Src.rapsodo.RapsodoMetricsOCRV2.core.capture import FrameCapture
from Src.rapsodo.RapsodoMetricsOCRV2.core.change_detector import ChangeDetector
from Src.rapsodo.RapsodoMetricsOCRV2.core.data_writer import DataWriter
from Src.rapsodo.RapsodoMetricsOCRV2.core.screen_detector import ScreenDetector
from Src.rapsodo.RapsodoMetricsOCRV2.core.session_manager import SessionManager
from Src.rapsodo.RapsodoMetricsOCRV2.modes.pitching import PitchingOCRReader

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
    # Fix 34 — 投手單位拆 source（Rapsodo UI 顯示、OCR 讀到的原始數字）與
    # output（JSON 寫出單位）。--source-* 必須和 Rapsodo iPad 顯示一致，否則
    # 會被 range 守門擋掉或誤寫入；--output-* 省略時 = source（不換算）。
    parser.add_argument(
        "--source-velocity-unit",
        choices=("mph", "kph"),
        default="mph",
        help="Velocity unit displayed in Rapsodo iPad UI (default: mph)",
    )
    parser.add_argument(
        "--output-velocity-unit",
        choices=("mph", "kph"),
        default=None,
        help="Velocity unit to write into JSON (default: same as --source-velocity-unit)",
    )
    parser.add_argument(
        "--source-break-unit",
        choices=("in", "cm"),
        default="in",
        help="V/H break unit displayed in Rapsodo iPad UI (default: in)",
    )
    parser.add_argument(
        "--output-break-unit",
        choices=("in", "cm"),
        default=None,
        help="V/H break unit to write into JSON (default: same as --source-break-unit)",
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
    mode = "pitching"
    vel_src: str = args.source_velocity_unit
    vel_out: str = args.output_velocity_unit or vel_src
    brk_src: str = args.source_break_unit
    brk_out: str = args.output_break_unit or brk_src

    vel_lbl = vel_src.upper() if vel_src == vel_out else f"{vel_src.upper()}→{vel_out.upper()}"
    brk_lbl = brk_src if brk_src == brk_out else f"{brk_src}→{brk_out}"
    print(f"\n=== Rapsodo Pitching OCR  |  velocity: {vel_lbl}, break: {brk_lbl} ===")

    # Prompt for the first player
    first_player = _prompt_player("Enter first player name: ")

    # Initialise components
    session = SessionManager()
    session_id = session.start_session(mode, first_player)

    capture = FrameCapture()
    detector = ScreenDetector()
    # Fix 34 — OCR reader 只吃 source 單位（決定 VALIDATION range）。
    ocr_reader = PitchingOCRReader(
        velocity_source_unit=vel_src, break_source_unit=brk_src
    )
    change_detector = ChangeDetector()
    writer = DataWriter(
        config.OUTPUT_JSON_PATH, mode, session_id,
        velocity_source_unit=vel_src, velocity_output_unit=vel_out,
        break_source_unit=brk_src, break_output_unit=brk_out,
    )
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
            frame = capture.get_frame()
            if frame is None:
                key = cv2.waitKey(config.KEYBOARD_CHECK_TIMEOUT_MS) & 0xFF
                if key == ord("q"):
                    running = False
                    break
                continue

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

            screen = detector.detect(frame)
            if screen is None:
                continue

            metrics = ocr_reader.read(screen)
            print(f"[OCR] Read metrics- spin direction: {metrics.spin_direction}")
            if metrics is None:
                continue

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
