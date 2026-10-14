"""
tools/video_player.py — Rapsodo Pitching Video Player: Offline Data Extraction Tool

Process pre-recorded Rapsodo iPad screen videos to extract pitching metrics.
Unit preferences (mph/kph, in/cm) are selected via dialogs at startup.
No CLI arguments required; video file is selected through the UI.

Usage:
    python tools/video_player.py
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))


import logging
import os
import queue
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Event, Lock

import cv2
import numpy as np
import pytesseract
from PIL import Image as PILImage
from PyQt5.QtCore import Qt, QThread, pyqtSignal

# Tesseract binary location — set once at module import so every
# PitchCounterReader.read() call does not re-assign this attribute.
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

import Src.rapsodo.RapsodoMetricsOCRV2.config as config
from Src.rapsodo.RapsodoMetricsOCRV2.core.data_writer import DataWriter
from Src.rapsodo.RapsodoMetricsOCRV2.core.session_manager import SessionManager
from Src.rapsodo.RapsodoMetricsOCRV2.modes.base_ocr_reader import _get_easyocr_reader
from Src.rapsodo.RapsodoMetricsOCRV2.modes.pitching import PitchMetrics, PitchingOCRReader
from Src.rapsodo.RapsodoMetricsOCRV2.models import BaseMetrics, RelativeROI

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

VIDEO_PANEL_WIDTH = 640
VIDEO_PANEL_HEIGHT = 480

# Playback modes for the Commit 3b UI speed toggle. "max" drops the
# real-time msleep entirely (the producer reads frames as fast as
# cv2.VideoCapture allows), while "realtime" keeps the pre-3b behaviour
# of throttling to the source video's FPS. Default is "max" because the
# primary use case is offline batch extraction where speed matters more
# than a smooth 1× preview; demo/recording scenarios can flip the UI
# toggle to "realtime".
PLAYBACK_MODE_MAX = "max"
PLAYBACK_MODE_REALTIME = "realtime"

# When this env var is set to a truthy value, VideoProcessor saves the raw
# frame passed to _trigger_ocr() as debug/trigger_frame_{frame_index}.png.
# Purpose: investigate OCR failures against the exact frame that was OCR'd,
# without relying on timestamp-to-frame estimation (which is unreliable —
# see CHANGELOG known issue about _VideoDataWriter timestamp decoupling).
_DEBUG_DUMP_ENV_VAR = "RAPSODO_DEBUG_DUMP_FRAMES"
_DEBUG_DIR = Path(__file__).parent.parent / "debug"


def _clean_debug_artifacts() -> None:
    """
    Remove PNGs left over from the previous run (Commit 3b).

    Called at the start of every VideoProcessor session so a fresh run
    always begins with an empty debug/ directory, while still letting the
    user inspect files from the most recent run between sessions (the
    cleanup happens on the NEXT start, not the current finish). Honours
    _DEBUG_DUMP_ENV_VAR as a kill-switch: if the user has opted in to
    frame dumping for an investigation, we do not wipe the prior artefacts
    they may still be analysing.
    """
    if os.environ.get(_DEBUG_DUMP_ENV_VAR):
        return
    if not _DEBUG_DIR.is_dir():
        return
    for png in _DEBUG_DIR.glob("*.png"):
        try:
            png.unlink()
        except OSError as exc:
            logger.debug("Could not remove debug artifact %s: %s", png, exc)

# ── InvalidDetector ───────────────────────────────────────────────────────────

class InvalidDetector:
    """
    Detect frames where Rapsodo shows no ball data (fields display '---' or '-').

    Uses OpenCV pixel analysis instead of Tesseract:
      1. Crop each pitching ROI
      2. Binarise with Otsu threshold
      3. Compute ratio of dark (non-white) pixels
      4. If ALL ROIs have a dark-pixel ratio below
         config.INVALID_DARK_PIXEL_THRESHOLD, the frame is invalid.
    """

    def __init__(self) -> None:
        self._roi_definitions: dict[str, RelativeROI] = {
            name: RelativeROI(*coords)
            for name, coords in config.PITCHING_ROI.items()
        }

    def is_invalid(self, screen_crop: np.ndarray) -> bool:
        """Return True if all ROIs have very few dark pixels."""
        h, w = screen_crop.shape[:2]
        for roi in self._roi_definitions.values():
            x1 = int(roi.x * w)
            y1 = int(roi.y * h)
            x2 = int((roi.x + roi.w) * w)
            y2 = int((roi.y + roi.h) * h)
            crop = screen_crop[y1:y2, x1:x2]
            if crop.size == 0:
                return False
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            dark_ratio = np.sum(binary == 0) / binary.size
            if dark_ratio >= config.INVALID_DARK_PIXEL_THRESHOLD:
                # This ROI has enough dark pixels → not an invalid/loading frame
                return False
        return True


# ── Counter fast-path sanity guard (Fix 32) ───────────────────────────────────

def _fast_path_plausible(value: int, baseline: int | None, max_jump: int) -> bool:
    """Return True if *value* is consistent with the current *baseline*.

    The fast-path only runs one (blk, C, PSM) combination, so a single
    misread (e.g. "11"→"1" truncation, "13"→"135" noise) would be accepted
    and poison the counter baseline. Mirror the accept criteria used in
    `_accept_counter_reading` so implausible fast-path readings fall
    through to the full-sweep vote instead.
    """
    if baseline is None:
        return True  # no baseline yet — first read establishes it via full sweep anyway
    if value < baseline:
        return False
    if value > baseline + max_jump:
        return False
    return True


class PitchCounterReader:
    """
    Read the integer pitch counter from the pitching UI.

    Uses config.PITCH_COUNTER_ROI. The pitching counter shows
    "N PITCH" / "N PITCHES".

    Fix 14 — dual-engine counter reader:
    1. Global voting across ALL (blk,C) × PSM combinations (fixes
       two-digit truncation: "11"→"1", "15"→"1").
    2. EasyOCR cross-validation: the counter digit(s) appear at the
       start of EasyOCR's text (e.g. "3818" → 3, "1588" → 15).
       EasyOCR is always correct for the leading digits but appends
       OCR noise from the "PITCHES" label. Extract the leading 1–2
       digit prefix and use it to resolve Tesseract ties or override
       systematic Tesseract misreads (e.g. 3→5).
    """

    # Known Tesseract misread pair on Rapsodo counter font.
    _AMBIGUOUS_DIGITS = frozenset({3, 5})

    def read(self, frame: np.ndarray, baseline: int | None = None) -> int | None:
        """
        Return the current pitch counter integer, or None on failure.

        Fix 16 — two-phase early-exit architecture for speed.
        Fix 17 — Phase 1 skips ambiguous 3/5 values; falls through to
        Phase 2 (full voting + EasyOCR) for reliable disambiguation.
        Fix 32 — Phase 1 also falls through when the reading is
        implausible vs. *baseline* (e.g. "11"→"1" truncation reads
        below baseline, "13"→"135" reads beyond MAX_JUMP). Without
        this guard, a single fast-path misread poisons the recovery
        state machine and subsequent +1 increments are missed.
        """
        h, w = frame.shape[:2]
        x, y, rw, rh = config.PITCH_COUNTER_ROI
        x1 = int(x * w);  y1 = int(y * h)
        x2 = int((x + rw) * w);  y2 = int((y + rh) * h)

        if x2 <= x1 or y2 <= y1:
            return None

        crop = frame[y1:y2, x1:x2]
        if len(crop.shape) == 3:
            crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        new_w = crop.shape[1] * config.OCR_UPSCALE_FACTOR
        new_h = crop.shape[0] * config.OCR_UPSCALE_FACTOR
        crop = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)

        # ── Phase 1: single-shot fast read (PSM 7, best blk/C) ──────
        # Fix 17: skip ambiguous 3/5 and fall through to Phase 2 for
        # EasyOCR cross-validation (Tesseract systematically confuses
        # 3 ↔ 5 on the Rapsodo counter font).
        fast_blk, fast_C = config.OCR_ADAPTIVE_ATTEMPTS[0]
        binarised = cv2.adaptiveThreshold(
            crop, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            fast_blk, fast_C,
        )
        fast_cfg = config.build_tesseract_config(7)  # PSM 7
        raw = pytesseract.image_to_string(
            PILImage.fromarray(binarised), config=fast_cfg
        )
        match = re.search(r'\d+', raw)
        if match:
            value = int(match.group())
            if (
                0 <= value <= 999
                and value not in self._AMBIGUOUS_DIGITS
                and _fast_path_plausible(value, baseline, config.PITCH_COUNTER_MAX_JUMP)
            ):
                logger.debug(
                    "PitchCounterReader fast-path: %d (blk=%d C=%d psm=7)",
                    value, fast_blk, fast_C,
                )
                return value

        # ── Phase 2: full sweep (only on fast-path failure) ──────────
        return self._full_sweep(crop)

    def _full_sweep(self, crop: np.ndarray) -> int | None:
        """
        Full (blk,C) × PSM global voting + lazy EasyOCR.
        Preserves Fix 14 accuracy for difficult frames.
        """
        global_votes: dict[int, int] = {}
        total_votes = 0

        for blk, C in config.OCR_ADAPTIVE_ATTEMPTS:
            binarised = cv2.adaptiveThreshold(
                crop, 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                blk, C,
            )
            pil_img = PILImage.fromarray(binarised)

            for psm in config.OCR_PSM_FALLBACK_MODES:
                tess_cfg = config.build_tesseract_config(psm)
                raw_text: str = pytesseract.image_to_string(pil_img, config=tess_cfg)
                match = re.search(r'\d+', raw_text)
                if not match:
                    continue
                value = int(match.group())
                if not (0 <= value <= 999):
                    continue
                global_votes[value] = global_votes.get(value, 0) + 1
                total_votes += 1

        if not global_votes:
            return None

        winner, winner_cnt = max(
            global_votes.items(), key=lambda kv: kv[1]
        )

        if len(global_votes) == 1:
            if winner not in (3, 5):
                return winner

        # ── Lazy EasyOCR: only when Tesseract is ambiguous ───────────
        easy_candidates = self._easyocr_counter(crop)

        if not easy_candidates:
            if total_votes >= 2 and winner_cnt / total_votes > 0.50:
                return winner
            return None

        return self._cross_validate_counter(
            global_votes, winner, winner_cnt, total_votes,
            easy_candidates,
        )

    def _cross_validate_counter(
        self,
        global_votes: dict[int, int],
        winner: int,
        winner_cnt: int,
        total_votes: int,
        easy_candidates: list[int],
    ) -> int:
        """
        Resolve disagreement between Tesseract votes and EasyOCR
        candidates for the pitch counter value.
        """
        # Find EasyOCR candidates that appear in Tesseract votes.
        matched: list[tuple[int, int]] = []  # (candidate, vote_count)
        for ec in easy_candidates:
            if ec in global_votes:
                matched.append((ec, global_votes[ec]))

        if matched:
            # Prefer the candidate with the most Tesseract votes.
            # On ties, prefer the larger (more specific) value — e.g.
            # "11" over "1" when both have 9 votes.
            best_match = max(matched, key=lambda m: (m[1], m[0]))
            logger.debug(
                "PitchCounterReader: tess winner=%d votes=%s "
                "easy=%s matched=%s → pick %d",
                winner, global_votes, easy_candidates,
                matched, best_match[0],
            )
            return best_match[0]

        # EasyOCR candidate not in Tesseract votes at all.
        # Trust EasyOCR's single-digit candidate (last in list) — its
        # leading-digit extraction is immune to Tesseract's systematic
        # misreads on this font (e.g. 3→5).
        chosen = easy_candidates[-1]
        logger.debug(
            "PitchCounterReader: tess=%d (%.0f%%), easy=%s "
            "→ trust EasyOCR=%d",
            winner, winner_cnt / total_votes * 100 if total_votes else 0,
            easy_candidates, chosen,
        )
        return chosen

    @staticmethod
    def _easyocr_counter(gray_up: np.ndarray) -> list[int]:
        """
        Extract counter integer candidates from EasyOCR on the counter
        crop.

        EasyOCR reads the full ROI as a digit string like "3818"
        (counter = 3, "PITCHES" label → "818"). The counter value is
        always the leading 1–2 digits. Returns a list of plausible
        candidates: [2-digit prefix, 1-digit prefix] so the caller
        can pick the one that matches Tesseract votes.
        """
        try:
            reader = _get_easyocr_reader()
        except Exception:
            return []
        results = reader.readtext(
            gray_up, allowlist="0123456789", detail=1
        )
        if not results:
            return []
        # Pick highest confidence detection
        best = max(results, key=lambda r: r[2])
        text = best[1].strip()
        if not text or best[2] < config.PITCH_COUNTER_EASYOCR_MIN_CONF:
            return []

        max_val = config.PITCH_COUNTER_MAX_VALUE
        candidates: list[int] = []
        # Order: 2-digit prefix first (higher priority when matched
        # in Tesseract votes), then 1-digit as fallback.
        # 2-digit prefix (covers 10–max_val). Requires text length ≥ 3
        # so there is at least 1 noise digit after the prefix (e.g.
        # "118" → prefix "11", noise "8").
        if len(text) >= 3:
            two_digit = int(text[:2])
            if 10 <= two_digit <= max_val:
                candidates.append(two_digit)
        # 1-digit prefix (covers 1–9)
        if len(text) >= 2:
            one_digit = int(text[0])
            if 1 <= one_digit <= 9:
                candidates.append(one_digit)
        # Very short text (1–2 chars) — try full parse
        if not candidates and len(text) <= 2:
            try:
                val = int(text)
                if 0 <= val <= max_val:
                    candidates.append(val)
            except ValueError:
                pass
        return candidates


# ── OCRTask / OCRWorker ───────────────────────────────────────────────────────

@dataclass
class OCRTask:
    """
    A unit of work handed from VideoProcessor (producer) to OCRWorker (consumer).

    ``kind`` values:
      * ``"counter"`` — run PitchCounterReader on ``frame``. On an accepted
        +1 increment, the worker calls ``_schedule_trigger`` to request a
        follow-up "trigger" task NEW_HIT_WAIT_MS later.
      * ``"trigger"`` — run PitchingOCRReader + InvalidDetector on ``frame``
        and emit the appropriate signal.
      * ``"stop"`` — sentinel that makes the worker exit its run loop.
    """

    kind: str
    frame: np.ndarray | None
    frame_index: int
    # Fix 25: additional frames for multi-frame trigger vote.
    # Only used for "trigger" tasks; None for counter/stop tasks.
    extra_frames: list[tuple[int, np.ndarray]] | None = None


class OCRWorker(QThread):
    """
    Dedicated OCR thread (Commit 3a scaffold for Fix 3).

    Owns the hit-counter baseline and all OCR readers. Receives ``OCRTask``
    instances through a bounded ``queue.Queue`` fed by ``VideoProcessor``,
    and emits hit / invalid-hit signals for the UI exactly like the pre-3a
    VideoProcessor did.

    Cross-thread state contract with the producer:
      * ``_task_queue`` — producer pushes, worker pops. Bounded so the
        producer blocks briefly when the worker falls behind (backpressure).
      * ``_next_trigger_frame`` — protected by ``_trigger_lock``. Worker
        sets it when a counter-increment is accepted; producer reads it
        every frame to decide when to push a ``"trigger"`` task using the
        post-wait frame. ``None`` means "no trigger pending".

    This split keeps the counter state machine atomic inside the worker —
    the producer never observes ``_last_hit_count`` directly, which is
    exactly what Fix 2's sanity-check machinery needs.
    """

    new_hit = pyqtSignal(object, int)   # PitchMetrics, hit_id
    invalid_hit = pyqtSignal(int)       # hit_id

    def __init__(
        self,
        player_name: str,
        data_writer: DataWriter,
        task_queue: "queue.Queue[OCRTask]",
        trigger_lock: Lock,
        mode: str = "pitching",
        velocity_source_unit: str = "mph",
        break_source_unit: str = "in",
    ) -> None:
        super().__init__()
        self.player_name = player_name
        self.data_writer = data_writer
        self._mode = mode
        self._velocity_source_unit = velocity_source_unit
        self._break_source_unit = break_source_unit

        # Fix 34 — PitchingOCRReader 只吃 source（決定 range 守門）；
        # output 單位在 DataWriter 層做換算。
        self.ocr_reader = PitchingOCRReader(
            velocity_source_unit=velocity_source_unit,
            break_source_unit=break_source_unit,
        )
        self.counter_reader = PitchCounterReader()
        self.invalid_detector = InvalidDetector()

        self._task_queue = task_queue
        self._trigger_lock = trigger_lock
        self._next_trigger_frame: int | None = None
        self._last_hit_count: int | None = None

        # Fix 17 — recovery state: tracks consecutive identical readings
        # that were rejected (backward or forward-outlier) so we can
        # recover from counter misreads that corrupt the baseline.
        self._recovery_value: int | None = None
        self._recovery_streak: int = 0
        # Fix 17 — trigger retry: if first trigger hits a transition,
        # reschedule once before writing invalid.
        self._trigger_is_retry: bool = False
        # Fix 18 — dedup: skip writing if metrics match previous entry.
        self._last_metrics_values: dict | None = None
        # Fix 18 — sync: Producer waits on this event after pushing a
        # trigger task. Worker sets it when trigger processing finishes.
        self._trigger_done = Event()
        self._trigger_done.set()  # initially "done" (no trigger pending)
        # Fix 25 — suppress counter processing during trigger cycle.
        # Set by Producer before stability wait, cleared after trigger
        # completes. Prevents queued counter tasks from corrupting the
        # baseline via recovery while a trigger is in progress.
        self._ignore_counter = False
        # Fix 27 — set by worker after EasyOCR warm-up finishes, waited
        # on by the producer before it starts reading frames. Prevents
        # the "first-run misses balls" issue caused by EasyOCR model
        # loading blocking the worker while the producer fills the queue.
        self._ready = Event()

    # ------------------------------------------------------------------
    # Producer-side API (called from VideoProcessor.run())
    # ------------------------------------------------------------------

    def take_pending_trigger(self, current_frame_index: int) -> int | None:
        """
        If a trigger has been scheduled and the producer has now reached
        (or passed) the target frame, clear and return it. Otherwise return
        None. Called by the producer on every frame — must stay cheap.
        """
        with self._trigger_lock:
            target = self._next_trigger_frame
            if target is None or current_frame_index < target:
                return None
            self._next_trigger_frame = None
            return target

    def change_player(self, player_name: str) -> None:
        """Switch player and reset counter baseline. Safe to call from UI thread."""
        with self._trigger_lock:
            self.player_name = player_name
            self._last_hit_count = None
            self._next_trigger_frame = None
            self._recovery_value = None
            self._recovery_streak = 0
            self._trigger_is_retry = False
        logger.debug("Player changed to: %s", player_name)

    def _warm_up_pipeline(self) -> None:
        """
        Fix 29 — exercise the full OCR chain on a dummy frame so every
        codepath (Tesseract subprocess spawn, PSM 7/11/4 sweep, per-field
        ThreadPoolExecutor, EasyOCR cross-validation, counter reader) is
        hot before the producer starts. Eliminates the cold-start latency
        spike that caused first-run pitches to be read from wrong frames.

        Best-effort: any failure is logged at debug level and ignored —
        warm-up must never block startup.
        """
        import time
        t0 = time.perf_counter()
        # 1080×1920 matches a typical Rapsodo screen crop. Pure noise so
        # adaptive threshold / OCR engines have to actually run, not
        # short-circuit on a uniform image.
        dummy = np.random.randint(
            0, 256, size=(1080, 1920, 3), dtype=np.uint8
        )
        try:
            self.counter_reader.read(dummy)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Counter reader warm-up failed (non-fatal): %s", exc)
        try:
            self.ocr_reader.read(dummy, frame_index=-1)
        except Exception as exc:  # noqa: BLE001
            logger.debug("OCR reader warm-up failed (non-fatal): %s", exc)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        logger.info(
            "Fix 29 pipeline warm-up complete (%.0f ms, mode=%s)",
            elapsed_ms, self._mode,
        )

    # ------------------------------------------------------------------
    # Worker thread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        # Fix 27: warm up EasyOCR before signalling the producer to start.
        # Without this the first _get_easyocr_reader() call pays 2–8 s of
        # model loading inside the first counter/trigger task, during which
        # producer-side counter samples overflow the bounded queue and get
        # silently dropped (root cause of "first run misses balls, second
        # run is fine" on the pitcher video).
        from Src.rapsodo.RapsodoMetricsOCRV2.modes.base_ocr_reader import warm_up_easyocr
        warm_up_easyocr()

        # Fix 29: full-pipeline warm-up. EasyOCR alone isn't enough — the
        # first real trigger still pays ~1–2 s extra for Tesseract subprocess
        # spawn (PSM 7/11/4 sweep), the per-field ThreadPoolExecutor, and
        # PIL/cv2 codepaths that haven't been JIT'd. On the pitcher V1 video
        # this delay caused the first run to read entry 4 from the next ball's
        # frame (counter 3→4 stability point landed 692 frames late, after
        # Rapsodo UI had already swapped to ball 5). Run the entire OCR chain
        # once on a dummy frame so every Python/native codepath is hot before
        # the producer starts feeding real frames.
        self._warm_up_pipeline()

        self._ready.set()

        while True:
            task = self._task_queue.get()
            if task.kind == "stop":
                self._trigger_done.set()  # unblock Producer if waiting
                return
            try:
                if task.kind == "counter":
                    self._handle_counter_task(task)
                elif task.kind == "trigger":
                    try:
                        self._handle_trigger_task(task)
                    finally:
                        # Fix 18: always signal completion so Producer
                        # never deadlocks waiting for trigger to finish.
                        self._trigger_done.set()
                else:
                    logger.warning("OCRWorker: unknown task kind %r", task.kind)
            except Exception:  # pragma: no cover — log and keep worker alive
                logger.exception("OCRWorker: task %r failed", task.kind)

    # ------------------------------------------------------------------
    # Task handlers
    # ------------------------------------------------------------------

    def _handle_counter_task(self, task: OCRTask) -> None:
        """OCR the hit/pitch counter and, on +1 increment, schedule a trigger."""
        # Fix 25: skip counter processing while a trigger cycle is active.
        # Queued counter tasks from before the trigger can read stale/wrong
        # values (e.g. Tesseract 3→5 misread) and corrupt the baseline via
        # recovery, causing subsequent real transitions to be missed.
        if self._ignore_counter:
            return
        assert task.frame is not None
        # Fix 32: pass current baseline so the fast-path can reject
        # implausible one-shot readings (truncation / trailing-noise).
        count = self.counter_reader.read(task.frame, baseline=self._last_hit_count)
        logger.debug("frame %d — counter OCR: %s", task.frame_index, count)

        accepted = self._accept_counter_reading(count, task.frame_index)
        if accepted is None:
            return

        previous = self._last_hit_count
        if previous is None:
            self._last_hit_count = accepted
            logger.debug(
                "frame %d — hit counter baseline set to %d",
                task.frame_index, accepted,
            )
            # If the video already shows data at startup, fire an immediate
            # trigger for the very first frame (preserves pre-3a behaviour
            # for videos that start mid-session).
            if task.frame_index == 0 and accepted > 0:
                logger.debug(
                    "frame 0 — existing data detected, triggering immediate OCR"
                )
                self._handle_trigger_task(task)
            return

        if accepted > previous:
            # Positive increment: schedule a trigger NEW_HIT_WAIT_MS into the
            # future. The pitching counter OCR is less stable — intermediate
            # reads get missed, so jumps up to MAX_JUMP are accepted. Only
            # ONE trigger is scheduled (for the latest frame) because the
            # metrics screen shows the most recent entry; past entries that
            # were missed cannot be recovered.
            self._last_hit_count = accepted
            target_frame = task.frame_index + self._wait_frames()
            with self._trigger_lock:
                self._next_trigger_frame = target_frame
            logger.debug(
                "frame %d — counter %d→%d (+%d), scheduling trigger at frame %d (%dms wait)",
                task.frame_index, previous, accepted, accepted - previous,
                target_frame, config.NEW_HIT_WAIT_MS,
            )

    def _handle_trigger_task(self, task: OCRTask) -> None:
        """
        Run metrics OCR on *task.frame* and emit the appropriate signal.

        Fix 17: if the frame is invalid (transition animation) and this
        is the first attempt, reschedule the trigger instead of writing
        an invalid entry. Only write invalid on the retry to handle
        genuinely invalid balls (Rapsodo shows '---').

        Fix 25: if extra_frames is provided, OCR each frame independently
        and use per-field majority vote to counter Tesseract's frame-level
        digit confusion (e.g. "7" ↔ "1" on different compressed frames).
        """
        assert task.frame is not None
        self._maybe_dump_trigger_frame(task.frame, task.frame_index)

        # Fix 25: multi-frame OCR + per-field majority vote.
        frames_to_ocr = (
            task.extra_frames
            if task.extra_frames
            else [(task.frame_index, task.frame)]
        )
        metrics = self._vote_metrics(frames_to_ocr)
        is_invalid = metrics is None or self.invalid_detector.is_invalid(task.frame)

        if is_invalid and not self._trigger_is_retry:
            # First attempt hit a transition — reschedule once.
            retry_frames = self._wait_frames()
            target = task.frame_index + retry_frames
            with self._trigger_lock:
                self._next_trigger_frame = target
            self._trigger_is_retry = True
            logger.debug(
                "frame %d — invalid/None on first attempt, rescheduling "
                "retry at frame %d (+%d frames)",
                task.frame_index, target, retry_frames,
            )
            return

        # Reset retry flag for next trigger cycle.
        self._trigger_is_retry = False

        if is_invalid:
            logger.debug(
                "frame %d — OCR returned None or invalid (retry exhausted)",
                task.frame_index,
            )
            hit_id = self.data_writer.write_invalid_entry(self.player_name)
            self.invalid_hit.emit(hit_id)
        else:
            # Fix 18 — dedup: skip if metrics identical to previous entry.
            current_values = metrics.field_values()
            if self._last_metrics_values == current_values:
                logger.debug(
                    "frame %d — duplicate metrics, skipping write",
                    task.frame_index,
                )
                return
            self._last_metrics_values = current_values

            if self._mode == "pitching":
                logger.debug(
                    "frame %d — NEW PITCH vel=%s spin=%s dir=%s eff=%s v=%s h=%s",
                    task.frame_index,
                    metrics.velocity_mph, metrics.total_spin_rpm,
                    metrics.spin_direction, metrics.spin_efficiency_pct,
                    metrics.v_break_in, metrics.h_break_in,
                )
            else:
                logger.debug(
                    "frame %d — NEW HIT ev=%.1f la=%.1f dist=%s",
                    task.frame_index,
                    metrics.exit_velocity if metrics.exit_velocity is not None else float("nan"),
                    metrics.launch_angle if metrics.launch_angle is not None else float("nan"),
                    metrics.distance,
                )
            hit_id = self.data_writer.write_entry(self.player_name, metrics)
            self.new_hit.emit(metrics, hit_id)

    def _vote_metrics(
        self,
        frames: list[tuple[int, np.ndarray]],
    ) -> BaseMetrics | None:
        """
        Fix 25 — OCR each frame independently, then per-field majority vote.

        For each non-timestamp field, collect the value from each frame's
        OCR result and pick the most common non-None value. This counters
        Tesseract's frame-level digit confusion where different compressed
        frames produce different misreads (e.g. "7" ↔ "1").

        Falls back to single-frame OCR when only one frame is provided.

        Fix 27 — parallelise the per-frame OCR calls with a ThreadPoolExecutor.
        Each `ocr_reader.read()` spends >95 % of its time inside Tesseract's
        subprocess invocation (pytesseract forks `tesseract.exe`, which
        releases the GIL) or EasyOCR's PyTorch CPU inference (also GIL-free).
        Three frames run concurrently in threads, cutting trigger-cycle OCR
        wall time roughly 3×. Thread-safety: the readers themselves hold no mutable per-call
        state; the EasyOCR singleton's `readtext` is reentrant.
        """
        if len(frames) == 1:
            idx, frame = frames[0]
            m = self.ocr_reader.read(frame, frame_index=idx)
            return m

        from concurrent.futures import ThreadPoolExecutor

        def _ocr_one(idx_frame: tuple[int, np.ndarray]) -> BaseMetrics | None:
            idx, frame = idx_frame
            return self.ocr_reader.read(frame, frame_index=idx)

        results: list[BaseMetrics] = []
        with ThreadPoolExecutor(max_workers=len(frames)) as pool:
            for m in pool.map(_ocr_one, frames):
                if m is not None:
                    results.append(m)

        if not results:
            return None

        if len(results) == 1:
            return results[0]

        # Per-field majority vote across all valid results.
        voted = results[0]  # start from first result, override fields
        fields = voted.field_values()
        for field_name in fields:
            votes: dict = {}
            for m in results:
                val = getattr(m, field_name, None)
                if val is not None:
                    votes[val] = votes.get(val, 0) + 1
            if votes:
                winner = max(votes, key=votes.get)
                setattr(voted, field_name, winner)
                if len(votes) > 1:
                    logger.debug(
                        "vote %s: %s → winner=%s",
                        field_name, votes, winner,
                    )
        return voted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _wait_frames() -> int:
        """
        Number of frames equivalent to NEW_HIT_WAIT_MS at the playback
        rate used by VideoProcessor. Assumes 30 fps (the producer falls
        back to 30 fps when cap reports an invalid FPS). Kept as a method
        so Commit 3b can swap in the actual producer FPS if needed.
        """
        return max(1, int(config.NEW_HIT_WAIT_MS * 30 / 1000))

    def _accept_counter_reading(
        self, count: int | None, frame_index: int
    ) -> int | None:
        """
        Sanity-filter a raw counter reader value before it is allowed to
        update ``_last_hit_count``.

        Rejects (returns None) when:
          * ``count`` is None (reader failed entirely);
          * ``count`` is lower than the current baseline (counter cannot
            legally decrement within a session);
          * ``count`` exceeds the baseline by more than
            ``config.PITCH_COUNTER_MAX_JUMP`` (outlier — almost certainly
            an OCR misread).
        """
        if count is None:
            return None

        previous = self._last_hit_count
        if previous is None:
            # Fix 24: accept any non-negative initial counter value.
            # Videos may start mid-session (e.g. counter=25 in V2).
            # The old check (count > MAX_JUMP * 10) rejected anything
            # above 10, blocking the entire video from detecting balls.
            if count < 0:
                logger.warning(
                    "frame %d — rejecting negative initial counter %d",
                    frame_index, count,
                )
                return None
            return count

        max_jump = config.PITCH_COUNTER_MAX_JUMP
        is_backward = count < previous
        is_outlier = count > previous + max_jump

        if is_backward or is_outlier:
            # Fix 17 — recovery: track consecutive identical "rejected"
            # readings. After COUNTER_BACKWARD_RECOVERY_STREAK consecutive
            # identical values, trust the reading and update baseline.
            #
            # Backward case: baseline was inflated by a misread.
            #   → Reset baseline, no trigger (ball already passed).
            # Forward-outlier case: real counter jumped > max_jump
            #   (e.g. two balls in quick succession) or we recovered from
            #   a depressed misread. → Accept the value (triggers in
            #   _handle_counter_task if count > previous).
            if count == self._recovery_value:
                self._recovery_streak += 1
            else:
                self._recovery_value = count
                self._recovery_streak = 1

            if self._recovery_streak >= config.COUNTER_BACKWARD_RECOVERY_STREAK:
                kind = "backward" if is_backward else "forward-outlier"
                logger.warning(
                    "frame %d — %s recovery: baseline %d→%d "
                    "(%d consecutive readings)",
                    frame_index, kind, previous, count,
                    self._recovery_streak,
                )
                self._recovery_value = None
                self._recovery_streak = 0
                # In BOTH cases, silently correct the baseline without
                # triggering. Backward: baseline was inflated by misread.
                # Forward-outlier: baseline was depressed or counter
                # genuinely jumped — either way, the next normal +1
                # increment from this corrected baseline will trigger.
                # Triggering on recovery itself causes false entries when
                # counter OCR oscillates (e.g. 3↔5 confusion).
                self._last_hit_count = count
                return None

            logger.debug(
                "frame %d — rejected counter %d→%d (%s, streak %d/%d)",
                frame_index, previous, count,
                "backward" if is_backward else "outlier +%d" % (count - previous),
                self._recovery_streak,
                config.COUNTER_BACKWARD_RECOVERY_STREAK,
            )
            return None

        # Valid forward reading within max_jump — reset recovery state.
        self._recovery_value = None
        self._recovery_streak = 0
        return count

    def _maybe_dump_trigger_frame(
        self, frame: np.ndarray, frame_index: int
    ) -> None:
        """
        If _DEBUG_DUMP_ENV_VAR is set, save *frame* (the exact image about
        to be OCR'd) to debug/trigger_frame_{frame_index}.png for post-hoc
        analysis. Silent no-op otherwise.
        """
        if not os.environ.get(_DEBUG_DUMP_ENV_VAR):
            return
        try:
            _DEBUG_DIR.mkdir(exist_ok=True)
            dump_path = _DEBUG_DIR / f"trigger_frame_{frame_index}.png"
            cv2.imwrite(str(dump_path), frame)
            logger.debug("Dumped trigger frame: %s", dump_path)
        except OSError as exc:
            logger.debug("Could not dump trigger frame: %s", exc)


# ── VideoProcessor ────────────────────────────────────────────────────────────

class VideoProcessor(QThread):
    """
    Frame producer (Commit 3a — Fix 3 scaffold).

    Reads frames sequentially from a video file, emits ``frame_ready`` for
    every frame, and pushes ``OCRTask`` instances to an ``OCRWorker`` for
    all OCR work. Owns no OCR state — the counter baseline and sanity
    check machinery live in ``OCRWorker``.

    Signals:
        frame_ready(QImage)  — every frame, for video display
        finished()           — when video ends or stop() is called

    Hit / invalid-hit signals are emitted by the worker itself and
    forwarded to the UI by ``VideoPlayerWindow._start_processing()``.
    """

    frame_ready = pyqtSignal(QImage)
    finished = pyqtSignal()

    def __init__(
        self,
        video_path: str,
        player_name: str,
        data_writer: DataWriter,
        playback_mode: str = PLAYBACK_MODE_MAX,
        mode: str = "pitching",
        velocity_source_unit: str = "mph",
        break_source_unit: str = "in",
    ) -> None:
        super().__init__()
        self.video_path = video_path

        # Fix 16: queue size increased from 2 to 4. Counter tasks use
        # put_nowait (skipped when full), so only trigger tasks block.
        # A larger queue prevents trigger tasks from being delayed by
        # a counter task that just started processing.
        self._task_queue: queue.Queue[OCRTask] = queue.Queue(maxsize=4)
        self._trigger_lock = Lock()

        self.worker = OCRWorker(
            player_name=player_name,
            data_writer=data_writer,
            task_queue=self._task_queue,
            trigger_lock=self._trigger_lock,
            mode=mode,
            velocity_source_unit=velocity_source_unit,
            break_source_unit=break_source_unit,
        )

        self._running = False
        # Protected by _trigger_lock — shared between the run loop and
        # set_playback_mode() called from the UI thread. Stored as a plain
        # attribute because assignment of a short string is atomic enough
        # for this single-flag use.
        self._playback_mode = playback_mode

    def run(self) -> None:
        self._running = True
        _clean_debug_artifacts()
        self.worker.start()
        # Fix 27: wait for worker to finish EasyOCR warm-up before we
        # start reading frames. Without this, the first ~2–8 s of video
        # playback race against cold EasyOCR initialisation; the producer
        # fills the bounded queue with counter samples that the blocked
        # worker cannot consume, and once the queue overflows counter
        # samples get dropped (put_nowait skips). That was the real root
        # cause of "first run of the pitcher video misses balls".
        if not self.worker._ready.wait(timeout=30.0):
            logger.warning(
                "OCRWorker warm-up did not complete within 30 s; "
                "proceeding anyway (first-ball drop risk elevated)."
            )
        cap = cv2.VideoCapture(self.video_path)

        if not cap.isOpened():
            logger.error("Cannot open video: %s", self.video_path)
            self._shutdown_worker()
            self.finished.emit()
            return

        fps = cap.get(cv2.CAP_PROP_FPS)
        if fps <= 0:
            fps = 30.0
        frame_delay_ms = max(1, int(1000 / fps))
        frame_index = 0

        try:
            while self._running:
                loop_start = time.perf_counter()

                ret, frame = cap.read()
                if not ret:
                    break

                # 1. Display frame (every frame — smooth playback).
                self.frame_ready.emit(self._to_qimage(frame))

                # 2. Sample the hit counter every N frames (frame 0 included
                #    so the worker sees a baseline ASAP).
                #    Fix 16: use put_nowait — if the worker is still busy
                #    with the previous counter task, skip this sample rather
                #    than stalling the entire video. Counter increments are
                #    persistent on screen, so missing one sample is harmless;
                #    the next sample will catch the same counter value.
                if frame_index % config.OCR_SAMPLE_EVERY_N_FRAMES == 0:
                    try:
                        self._task_queue.put_nowait(
                            OCRTask(kind="counter", frame=frame.copy(), frame_index=frame_index)
                        )
                    except queue.Full:
                        pass  # worker busy — skip, next sample will retry

                # 3. Fire a trigger task once we reach the frame the worker
                #    scheduled after a +1 counter increment.
                #    Fix 19: after reaching the minimum wait, enter a
                #    stability loop — keep reading frames until the metrics
                #    ROI stops changing (N consecutive stable frames).
                #    This replaces the old fixed-delay approach and avoids
                #    triggering OCR during Rapsodo transition animations.
                target = self.worker.take_pending_trigger(frame_index)
                if target is not None:
                    # Fix 25: suppress counter processing during the
                    # entire trigger cycle (stability wait + OCR).
                    # Drain any queued counter tasks first, then set
                    # the flag so late arrivals are also ignored.
                    self._drain_counter_tasks()
                    self.worker._ignore_counter = True

                    stable_frames = self._wait_for_stability(
                        cap, frame_index,
                    )
                    if stable_frames is not None:
                        # Fix 25: send all stable frames as a single
                        # trigger task. Worker will OCR each and vote.
                        last_idx = stable_frames[-1][0]
                        self.worker._trigger_done.clear()
                        self._task_queue.put(
                            OCRTask(kind="trigger",
                                    frame=stable_frames[0][1],
                                    frame_index=stable_frames[0][0],
                                    extra_frames=stable_frames)
                        )
                        self.worker._trigger_done.wait()
                        frame_index = last_idx + 1

                    # Re-enable counter processing after trigger completes.
                    self.worker._ignore_counter = False
                    if stable_frames is not None:
                        continue  # skip the frame_index += 1 below

                frame_index += 1

                # 4. Throttle to source FPS only in realtime playback mode.
                #    Fix 22: delta time compensation — subtract processing
                #    time from the sleep duration so total loop time matches
                #    the source video's FPS (eliminates slow-motion effect).
                if self._playback_mode == PLAYBACK_MODE_REALTIME:
                    elapsed_ms = (time.perf_counter() - loop_start) * 1000
                    sleep_ms = max(0, int(frame_delay_ms - elapsed_ms))
                    if sleep_ms > 0:
                        self.msleep(sleep_ms)
        finally:
            cap.release()
            self._shutdown_worker()
            self.finished.emit()

    def stop(self) -> None:
        """Signal the run loop to exit on the next iteration."""
        self._running = False

    def change_player(self, player_name: str) -> None:
        """Switch to a new player and reset hit counter baseline."""
        self.worker.change_player(player_name)

    def set_playback_mode(self, mode: str) -> None:
        """Switch between max-speed and realtime playback mid-run."""
        if mode not in (PLAYBACK_MODE_MAX, PLAYBACK_MODE_REALTIME):
            logger.warning("Ignoring unknown playback mode %r", mode)
            return
        self._playback_mode = mode
        logger.debug("Playback mode set to %s", mode)

    def _drain_counter_tasks(self) -> None:
        """Remove all pending counter tasks from the queue.

        Called at the start of a trigger cycle (Fix 25) to prevent
        stale counter readings from corrupting the baseline via
        recovery while a trigger is in progress.
        """
        drained = 0
        while True:
            try:
                task = self._task_queue.get_nowait()
                if task.kind != "counter":
                    # Put non-counter tasks back (shouldn't happen in
                    # practice, but safety first).
                    self._task_queue.put(task)
                    break
                drained += 1
            except queue.Empty:
                break
        if drained:
            logger.debug("Drained %d counter tasks before trigger cycle", drained)

    def _shutdown_worker(self) -> None:
        """Drain any remaining tasks, send the stop sentinel, and join."""
        try:
            self._task_queue.put(OCRTask(kind="stop", frame=None, frame_index=-1))
        except Exception:
            logger.exception("Failed to enqueue stop sentinel for OCRWorker")
        self.worker.wait()

    def _wait_for_stability(
        self,
        cap: cv2.VideoCapture,
        start_frame_index: int,
    ) -> list[tuple[int, np.ndarray]] | None:
        """
        Fix 19 — read frames until the metrics ROI stabilises.
        Fix 25 — collect multiple stable frames for majority-vote OCR.

        Continuously reads frames from *cap*, emitting each to the UI.
        Compares the metrics ROI between consecutive frames using
        cv2.absdiff variance. When N consecutive frame pairs are below
        the threshold, collect TRIGGER_VOTE_FRAMES additional stable
        frames and return them as a list of (frame_index, frame) tuples.

        Falls back after STABILITY_MAX_WAIT_FRAMES to prevent infinite
        wait on noisy video. Returns None if video ends or stop is called.
        """
        # Build the stability ROI from the metrics ROIs.
        rois = list(config.PITCHING_ROI.values())
        x_min = min(r[0] for r in rois)
        y_min = min(r[1] for r in rois)
        x_max = max(r[0] + r[2] for r in rois)
        y_max = max(r[1] + r[3] for r in rois)

        prev_gray_roi = None
        stable_count = 0
        waited = 0
        frame_index = start_frame_index
        last_frame = None

        while self._running and waited < config.STABILITY_MAX_WAIT_FRAMES:
            ret, frame = cap.read()
            if not ret:
                return None

            self.frame_ready.emit(self._to_qimage(frame))
            frame_index += 1
            waited += 1
            last_frame = frame

            h, w = frame.shape[:2]
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            x1 = int(x_min * w); y1 = int(y_min * h)
            x2 = int(x_max * w); y2 = int(y_max * h)
            roi_crop = gray[y1:y2, x1:x2]

            if prev_gray_roi is not None and roi_crop.shape == prev_gray_roi.shape:
                diff = cv2.absdiff(roi_crop, prev_gray_roi)
                variance = float(diff.var())
                if variance < config.STABILITY_VARIANCE_THRESHOLD:
                    stable_count += 1
                else:
                    stable_count = 0

                if stable_count >= config.STABILITY_REQUIRED_FRAMES:
                    logger.debug(
                        "frame %d — stability reached after %d frames "
                        "(variance=%.1f, threshold=%.1f)",
                        frame_index, waited, variance,
                        config.STABILITY_VARIANCE_THRESHOLD,
                    )
                    # Fix 25: collect multiple stable frames for vote.
                    frames = [(frame_index, frame.copy())]
                    for _ in range(config.TRIGGER_VOTE_FRAMES - 1):
                        ret2, frame2 = cap.read()
                        if not ret2:
                            break
                        self.frame_ready.emit(self._to_qimage(frame2))
                        frame_index += 1
                        frames.append((frame_index, frame2.copy()))
                    return frames

            prev_gray_roi = roi_crop.copy()

        # Fallback: max wait exceeded, use last frame.
        if last_frame is not None:
            logger.debug(
                "frame %d — stability timeout after %d frames, "
                "triggering anyway",
                frame_index, waited,
            )
            return [(frame_index, last_frame.copy())]
        return None

    @staticmethod
    def _to_qimage(frame: np.ndarray) -> QImage:
        """Convert a BGR OpenCV frame to a QImage (RGB888)."""
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        return QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888).copy()


# ── VideoPlayerWindow ─────────────────────────────────────────────────────────

class VideoPlayerWindow(QMainWindow):
    """
    Main window: left side shows video frames; right side shows live hit data
    and a history list of all hits in the session.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Rapsodo Video Data Extractor")
        self._processor: VideoProcessor | None = None
        self._data_writer: DataWriter | None = None
        self._session_manager: SessionManager | None = None
        self._hit_count = 0
        self._current_mode = "pitching"
        # Default playback mode for new sessions; toggled by the speed
        # button in the top bar. Sticks between sessions within a single
        # process lifetime so a user who flips to realtime for a demo
        # doesn't have to re-flip on every new video.
        self._playback_mode = PLAYBACK_MODE_MAX
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(6)

        # ── Top bar ──────────────────────────────────────────────────
        top_bar = QHBoxLayout()
        title = QLabel("Rapsodo Video Data Extractor")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")

        # Playback speed toggle (Commit 3b). Live-switchable mid-run so a
        # user doing a demo can slow the preview to 1× without restarting.
        self._speed_btn = QPushButton(self._speed_button_text())
        self._speed_btn.setFixedWidth(110)
        self._speed_btn.clicked.connect(self._on_speed_toggle_clicked)

        self._open_btn = QPushButton("開啟影片")
        self._open_btn.setFixedWidth(100)
        self._open_btn.clicked.connect(self._on_open_clicked)

        top_bar.addWidget(title)
        top_bar.addStretch()
        top_bar.addWidget(self._speed_btn)
        top_bar.addWidget(self._open_btn)
        root_layout.addLayout(top_bar)

        # ── Main content (video | data) ───────────────────────────────
        content = QHBoxLayout()
        content.setSpacing(8)

        # Left: video panel
        self._video_label = QLabel()
        self._video_label.setFixedSize(VIDEO_PANEL_WIDTH, VIDEO_PANEL_HEIGHT)
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.setStyleSheet("background-color: #111; color: #aaa;")
        self._video_label.setText("影片尚未載入")
        content.addWidget(self._video_label)

        # Right: data panel
        right_panel = QVBoxLayout()
        right_panel.setSpacing(4)

        self._player_label = QLabel("Player: —")
        self._player_label.setStyleSheet("font-size: 13px; font-weight: bold;")
        self._hit_count_label = QLabel("")
        right_panel.addWidget(self._player_label)
        right_panel.addWidget(self._hit_count_label)

        right_panel.addSpacing(8)

        self._ev_label = QLabel("VELOCITY\n—")
        self._la_label = QLabel("TOTAL SPIN\n—")
        self._dist_label = QLabel("SPIN DIR\n—")
        self._eff_label = QLabel("SPIN EFF\n—")
        self._vbreak_label = QLabel("V-BREAK\n—")
        self._hbreak_label = QLabel("H-BREAK\n—")
        for lbl in (
            self._ev_label, self._la_label, self._dist_label,
            self._eff_label, self._vbreak_label, self._hbreak_label,
        ):
            lbl.setStyleSheet("font-size: 12px;")
            right_panel.addWidget(lbl)
            right_panel.addSpacing(4)

        right_panel.addStretch()

        history_title = QLabel("History")
        history_title.setStyleSheet("font-weight: bold;")
        right_panel.addWidget(history_title)

        self._history_list = QListWidget()
        self._history_list.setFixedHeight(160)
        right_panel.addWidget(self._history_list)

        content.addLayout(right_panel)
        root_layout.addLayout(content)

        self.adjustSize()

    # ------------------------------------------------------------------
    # Button handler
    # ------------------------------------------------------------------

    def _on_open_clicked(self) -> None:
        # Fix 34 — 拆 source（Rapsodo UI 顯示單位、OCR 讀到的原始數字）與
        # output（JSON 寫出單位）兩層。source 錯會被 range 守門擋掉，output
        # 省略則 = source（不換算）。使用者可在 dialog 選「與 Rapsodo 顯示
        # 相同」或「換算成另一單位」。
        unit_choice, ok = QInputDialog.getItem(
            self,
            "Rapsodo 顯示的球速單位",
            "請選擇 Rapsodo 畫面上顯示的球速單位\n(這是 OCR 實際讀到的數字單位):",
            ["kph (台灣常用)", "mph"],
            0,
            False,
        )
        if not ok:
            return
        vel_src = "kph" if "kph" in unit_choice else "mph"

        vel_out_choice, ok = QInputDialog.getItem(
            self,
            "JSON 輸出的球速單位",
            f"目前 source = {vel_src}。JSON 要寫出哪個單位？\n(選相同代表不換算)",
            [f"{vel_src} (不換算)", "mph" if vel_src == "kph" else "kph"],
            0,
            False,
        )
        if not ok:
            return
        vel_out = vel_src if "不換算" in vel_out_choice else ("mph" if vel_src == "kph" else "kph")

        brk_choice, ok = QInputDialog.getItem(
            self,
            "Rapsodo 顯示的 V/H 位移單位",
            "請選擇 Rapsodo 畫面上顯示的 V/H 位移單位:",
            ["cm (台灣常用)", "inch"],
            0,
            False,
        )
        if not ok:
            return
        brk_src = "cm" if "cm" in brk_choice else "in"

        brk_out_choice, ok = QInputDialog.getItem(
            self,
            "JSON 輸出的 V/H 位移單位",
            f"目前 source = {brk_src}。JSON 要寫出哪個單位？\n(選相同代表不換算)",
            [f"{brk_src} (不換算)", "inch" if brk_src == "cm" else "cm"],
            0,
            False,
        )
        if not ok:
            return
        if "不換算" in brk_out_choice:
            brk_out = brk_src
        else:
            brk_out = "in" if brk_src == "cm" else "cm"

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "選擇影片檔案",
            "",
            "Video Files (*.mp4 *.MP4)",
        )
        if not file_path:
            return

        player_name, ok = QInputDialog.getText(
            self, "輸入球員名稱", "球員名稱:"
        )
        while ok and not player_name.strip():
            QMessageBox.warning(self, "錯誤", "球員名稱不能為空白。")
            player_name, ok = QInputDialog.getText(
                self, "輸入球員名稱", "球員名稱:"
            )
        if not ok:
            return

        player_name = player_name.strip()
        self._start_processing(
            file_path, player_name,
            vel_src, vel_out, brk_src, brk_out,
        )

    # ------------------------------------------------------------------
    # Processing lifecycle
    # ------------------------------------------------------------------

    def _start_processing(
        self,
        video_path: str,
        player_name: str,
        velocity_source_unit: str = "mph",
        velocity_output_unit: str = "mph",
        break_source_unit: str = "in",
        break_output_unit: str = "in",
    ) -> None:
        mode = "pitching"
        # Fix 20: if a previous session exists, end it first so its JSON
        # is backed up before the new session overwrites it.
        self._end_current_session()

        self._open_btn.setEnabled(False)
        self._hit_count = 0
        self._current_mode = mode
        self._velocity_source_unit = velocity_source_unit
        self._velocity_output_unit = velocity_output_unit
        self._break_source_unit = break_source_unit
        self._break_output_unit = break_output_unit
        self._history_list.clear()
        self._player_label.setText(f"Player: {player_name}")
        self._hit_count_label.setText("")
        self._setup_data_labels()

        self._session_manager = SessionManager()
        video_filename = Path(video_path).name
        output_path = config.OUTPUT_JSON_PITCHING
        session_id = self._session_manager.start_session(
            mode, player_name, json_path=output_path
        )

        self._data_writer = _VideoDataWriter(
            output_path=output_path,
            mode=mode,
            session_id=session_id,
            video_file=video_filename,
            velocity_source_unit=velocity_source_unit,
            velocity_output_unit=velocity_output_unit,
            break_source_unit=break_source_unit,
            break_output_unit=break_output_unit,
        )

        self._processor = VideoProcessor(
            video_path=video_path,
            player_name=player_name,
            data_writer=self._data_writer,
            playback_mode=self._playback_mode,
            mode=mode,
            velocity_source_unit=velocity_source_unit,
            break_source_unit=break_source_unit,
        )
        self._processor.frame_ready.connect(self.on_frame_ready)
        self._processor.worker.new_hit.connect(self.on_new_hit)
        self._processor.worker.invalid_hit.connect(self.on_invalid_hit)
        self._processor.finished.connect(self.on_finished)
        self._processor.start()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def on_frame_ready(self, image: QImage) -> None:
        pixmap = QPixmap.fromImage(image).scaled(
            VIDEO_PANEL_WIDTH,
            VIDEO_PANEL_HEIGHT,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self._video_label.setPixmap(pixmap)

    def on_new_hit(self, metrics, hit_id: int) -> None:
        self._hit_count += 1
        self._hit_count_label.setText(f"Entry #{hit_id} of session")

        # Fix 34 — metrics stores source-unit raw numbers; UI must render
        # in OUTPUT unit so it matches what the JSON records.
        vel_out = getattr(self, "_velocity_output_unit", "mph")
        brk_out = getattr(self, "_break_output_unit", "in")
        vel_val = self._data_writer._convert_velocity(metrics.velocity_mph) if self._data_writer else metrics.velocity_mph
        vbr_val = self._data_writer._convert_break(metrics.v_break_in) if self._data_writer else metrics.v_break_in
        hbr_val = self._data_writer._convert_break(metrics.h_break_in) if self._data_writer else metrics.h_break_in
        vel = f"{vel_val:.1f} {vel_out.upper()}" if vel_val is not None else "—"
        spin = f"{metrics.total_spin_rpm} RPM" if metrics.total_spin_rpm is not None else "—"
        sdir = metrics.spin_direction if metrics.spin_direction is not None else "—"
        eff = f"{metrics.spin_efficiency_pct:.1f}%" if metrics.spin_efficiency_pct is not None else "—"
        brk_suffix = "cm" if brk_out == "cm" else '"'
        vbr = f"{vbr_val:.1f}{brk_suffix}" if vbr_val is not None else "—"
        hbr = f"{hbr_val:.1f}{brk_suffix}" if hbr_val is not None else "—"

        self._ev_label.setText(f"VELOCITY\n{vel}")
        self._la_label.setText(f"TOTAL SPIN\n{spin}")
        self._dist_label.setText(f"SPIN DIR\n{sdir}")
        self._eff_label.setText(f"SPIN EFF\n{eff}")
        self._vbreak_label.setText(f"V-BREAK\n{vbr}")
        self._hbreak_label.setText(f"H-BREAK\n{hbr}")

        item = QListWidgetItem(
            f"#{hit_id}  {vel}  {spin}  {sdir}  {eff}  V:{vbr}  H:{hbr}"
        )
        self._history_list.addItem(item)
        self._history_list.scrollToBottom()

    def on_invalid_hit(self, hit_id: int) -> None:
        self._hit_count_label.setText(f"Entry #{hit_id} of session")
        item = QListWidgetItem(f"#{hit_id}  --- (invalid)")
        self._history_list.addItem(item)
        self._history_list.scrollToBottom()

    def on_finished(self) -> None:
        # Flush any pending cross-thread signals (new_hit / invalid_hit)
        # that were queued but not yet dispatched before we show the
        # summary dialog. Without this, the modal QMessageBox blocks the
        # event loop and the hit count displayed may be stale (0).
        QApplication.processEvents()
        self._open_btn.setEnabled(True)

        # Fix 20: backup immediately when video processing ends, not on
        # window close. This ensures each video's JSON is saved to backup/
        # even if the user opens another video without closing the window.
        self._end_current_session()

        QMessageBox.information(
            self,
            "完成",
            f"影片處理完成。共記錄 {self._hit_count} 筆有效資料。",
        )

    # ------------------------------------------------------------------
    # Session lifecycle (Fix 20)
    # ------------------------------------------------------------------

    def _end_current_session(self) -> None:
        """End the current session and back up its JSON file.

        Idempotent: sets ``_session_manager`` to None after ending, so
        calling twice (e.g. on_finished + closeEvent) is safe.
        """
        if self._session_manager is not None:
            self._session_manager.end_session()
            self._session_manager = None

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        if self._processor is not None and self._processor.isRunning():
            self._processor.stop()
            self._processor.wait()
        # Fix 20: end_current_session is idempotent — if on_finished
        # already called it, this is a no-op.
        self._end_current_session()
        event.accept()

    # ------------------------------------------------------------------
    # Keyboard shortcuts
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Q:
            self.close()
        elif event.key() == Qt.Key_N:
            self._change_player()
        else:
            super().keyPressEvent(event)

    def _change_player(self) -> None:
        """Prompt for a new player name and reset change detection."""
        if self._processor is None or not self._processor.isRunning():
            return
        player_name, ok = QInputDialog.getText(self, "換球員", "新球員名稱:")
        while ok and not player_name.strip():
            QMessageBox.warning(self, "錯誤", "球員名稱不能為空白。")
            player_name, ok = QInputDialog.getText(self, "換球員", "新球員名稱:")
        if not ok:
            return
        player_name = player_name.strip()
        self._processor.change_player(player_name)
        self._player_label.setText(f"Player: {player_name}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _setup_data_labels(self) -> None:
        self._ev_label.setText("VELOCITY\n—")
        self._la_label.setText("TOTAL SPIN\n—")
        self._dist_label.setText("SPIN DIR\n—")
        self._eff_label.setText("SPIN EFF\n—")
        self._eff_label.setVisible(True)
        self._vbreak_label.setText("V-BREAK\n—")
        self._hbreak_label.setText("H-BREAK\n—")
        self._vbreak_label.setVisible(True)
        self._hbreak_label.setVisible(True)

    def _speed_button_text(self) -> str:
        """Label the speed button with the mode the click will switch TO."""
        if self._playback_mode == PLAYBACK_MODE_MAX:
            return "速度: 最快"
        return "速度: 1×"

    def _on_speed_toggle_clicked(self) -> None:
        """
        Flip between max-speed and realtime playback. Updates the running
        processor too, so a demo viewer can drop to 1× without restarting.
        """
        self._playback_mode = (
            PLAYBACK_MODE_REALTIME
            if self._playback_mode == PLAYBACK_MODE_MAX
            else PLAYBACK_MODE_MAX
        )
        self._speed_btn.setText(self._speed_button_text())
        if self._processor is not None and self._processor.isRunning():
            self._processor.set_playback_mode(self._playback_mode)


# ── _VideoDataWriter ──────────────────────────────────────────────────────────

class _VideoDataWriter(DataWriter):
    """
    DataWriter subclass that adds `source` and `video_file` fields to the
    JSON payload, as required by SDD v1.1 §3.6.
    """

    def __init__(
        self,
        output_path: str,
        mode: str,
        session_id: str,
        video_file: str,
        velocity_source_unit: str = "mph",
        velocity_output_unit: str | None = None,
        break_source_unit: str = "in",
        break_output_unit: str | None = None,
    ) -> None:
        super().__init__(
            output_path=output_path,
            mode=mode,
            session_id=session_id,
            velocity_source_unit=velocity_source_unit,
            velocity_output_unit=velocity_output_unit,
            break_source_unit=break_source_unit,
            break_output_unit=break_output_unit,
        )
        self._video_file = video_file

    def _build_payload(self) -> dict:
        return {
            "session_id": self._session_id,
            "mode": self._mode,
            "source": "video",
            "video_file": self._video_file,
            "last_updated": datetime.now().isoformat(timespec="seconds"),
            "current_player": self._current_player,
            "entries": self._entries,
        }

    # Override write_entry to inject extra fields into payload
    def write_entry(self, player: str, metrics) -> int:
        self._current_player = player
        entry_id = len(self._entries) + 1
        entry = self._build_entry(entry_id, player, metrics)
        self._entries.append(entry)
        self._atomic_write(self._build_payload())
        return entry_id

    # Override write_invalid_entry to inject extra fields into payload
    def write_invalid_entry(self, player: str) -> int:
        self._current_player = player
        entry_id = len(self._entries) + 1
        base = {
            "entry_id": entry_id,
            "player": player,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        if self._mode == "pitching":
            base[self._velocity_key] = None
            base["total_spin_rpm(旋轉數)"] = None
            base["spin_direction(旋轉方向)"] = None
            base["spin_efficiency_pct(旋轉效率)"] = None
            base["v_break_in(垂直位移)"] = None
            base["h_break_in(水平位移)"] = None
        else:
            base["exit_velocity_mph(出棒速度)"] = None
            base["launch_angle_deg(擊球角度)"] = None
            base["distance_ft(飛行距離)"] = None
        base["note"] = "invalid — no data detected"
        self._entries.append(base)
        self._atomic_write(self._build_payload())
        return entry_id


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Log to file for investigation (debug/video_player_log.txt)
    _log_dir = Path(__file__).parent.parent / "debug"
    _log_dir.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(str(_log_dir / "video_player_log.txt"), mode="w", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    app = QApplication(sys.argv)
    window = VideoPlayerWindow()
    window.show()
    sys.exit(app.exec_())
