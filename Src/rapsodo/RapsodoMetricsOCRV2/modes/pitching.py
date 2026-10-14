from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image

import Src.rapsodo.RapsodoMetricsOCRV2.config as config
from Src.rapsodo.RapsodoMetricsOCRV2.models import BaseMetrics, RelativeROI
from Src.rapsodo.RapsodoMetricsOCRV2.modes.base_ocr_reader import BaseOCRReader, _get_easyocr_reader

logger = logging.getLogger(__name__)

_DEBUG_DIR = Path(__file__).parent.parent / "debug"


@dataclass
class PitchMetrics(BaseMetrics):
    """
    Pitching-mode metrics. Field names match Rapsodo_Pitcher_GT.json keys
    exactly so that JSON write/compare paths can use direct dict access.

    spin_direction is a STRING in "HH:MM" clock format (e.g. "12:58"),
    NOT a numeric angle. When Rapsodo cannot compute it, the UI still
    displays "12:00" as a fallback — that is treated as a valid read,
    not a null.
    """
    velocity_mph: float | None = None         # MPH
    total_spin_rpm: int | None = None         # RPM
    spin_direction: str | None = None         # "HH:MM"
    spin_efficiency_pct: float | None = None  # percent
    v_break_in: float | None = None           # vertical break, inches (signed)
    h_break_in: float | None = None           # horizontal break, inches (signed)


# Minimum dark-pixel fraction under Otsu threshold for a ROI crop to
# count as "populated" (i.e. the UI is displaying a number, not the
# single "-" placeholder that Rapsodo shows when a field is incomplete).
# Shared with InvalidDetector in tools/video_player.py via the same
# config constant, but here we apply it PER FIELD (Fix: pitching mode
# must null out only the incomplete spin fields, never the whole entry,
# because velocity is still valid in those cases).
_DASH_DARK_PIXEL_THRESHOLD = config.INVALID_DARK_PIXEL_THRESHOLD


def _is_dash_field(crop_bgr: np.ndarray) -> bool:
    """
    Return True if the ROI crop is showing the single "-" placeholder
    that Rapsodo uses for incomplete data. Detected via Otsu binarised
    dark pixel ratio — a single dash occupies far fewer dark pixels
    than any 2+ digit number.
    """
    if crop_bgr.size == 0:
        return True
    gray = (
        cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        if len(crop_bgr.shape) == 3
        else crop_bgr
    )
    _, binarised = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    dark_ratio = float(np.count_nonzero(binarised)) / binarised.size
    return dark_ratio < _DASH_DARK_PIXEL_THRESHOLD


class PitchingOCRReader(BaseOCRReader):
    """
    Extract the four pitching metrics from a perspective-corrected screen
    crop. Inherits the full Fix 4d+4f preprocessing pipeline from
    BaseOCRReader (close_3x3 morph + psm7_2x weighted voting + frac60_min3
    threshold) for the three numeric fields. spin_direction takes a
    separate path because it is a string-typed "HH:MM" clock value.
    """

    # NUMERIC fields go into ROI_DEFINITIONS / VALIDATION_RANGES because
    # BaseOCRReader._read_field() assumes a numeric validation range.
    # spin_direction is handled by _read_spin_direction() below.
    # V/H break 同樣是 numeric float field，需註冊以走完整 OCR pipeline
    # (Tesseract voting + EasyOCR rescue)。Fix 30。
    ROI_DEFINITIONS: dict[str, RelativeROI] = {
        name: RelativeROI(*config.PITCHING_ROI[name])
        for name in (
            "velocity_mph", "total_spin_rpm", "spin_efficiency_pct",
            "v_break_in", "h_break_in",
        )
    }
    VALIDATION_RANGES: dict[str, tuple[float, float]] = config.PITCHING_VALIDATION

    # frac70_min4 — pitching's italic-bold digits produce noisy PSM
    # ties: leading '7'/'8'/'9' regularly get eaten by PSM 11/4 under
    # some (block, C) thresholds, so a 60 % winner can still carry a
    # silent-wrong read past the vote. Requiring 70 % of ≥4 total
    # weighted votes forces those marginal wins to fall through to null.
    VOTE_MIN_FRACTION: float = 0.70
    VOTE_MIN_TOTAL: int = 4

    # Per-field white-border padding (Fix 10a). The 18-entry A/B test
    # shows border=20px gives velocity_mph 12→13 CORRECT and 2→0
    # SILENT_WRONG (fully clean), but regresses total_spin_rpm 8→6
    # CORRECT because the '1' vs '4' disambiguation it provides on
    # velocity actually HURTS on total_spin (the '8' gets eaten by
    # extra whitespace around it). spin_efficiency_pct is unchanged
    # either way. So: enable border for velocity only.
    #
    # Measured on debug/pitcher_gt/entry_{01..18}_frame_*_full.png:
    #                        border=OFF          border=ON
    #   velocity_mph         C=12 SW=2          C=13 SW=0
    #   total_spin_rpm       C=8  SW=2          C=6  SW=2
    #   spin_efficiency_pct  C=2  SW=3          C=2  SW=3
    BINARISED_BORDER_PX: dict[str, int] = {
        "velocity_mph": 20,
    }

    # Pre-compiled regex for clock-format validation.
    _CLOCK_RE = re.compile(config.PITCHING_CLOCK_REGEX)

    def __init__(
        self,
        velocity_source_unit: str = "mph",
        break_source_unit: str = "in",
    ) -> None:
        """
        velocity_source_unit / break_source_unit — Rapsodo iPad UI 實際顯示
        的單位，也就是 OCR 會從畫面讀到的原始數字單位。Reader 只關心
        source，因為 VALIDATION range 必須符合讀到的數字（例如 UI 顯示 cm
        時值可能到 41.0，in range ±35 會擋掉）。

        Reader 讀到的值維持 source 單位原始數字不換算；若下游要 output 不同
        單位（例如使用者要 cm 輸出但 Rapsodo 顯示 in），在 DataWriter 寫入
        JSON 前做換算（Fix 34）。dataclass 欄位名 velocity_mph / v_break_in /
        h_break_in 純為型別容器、不隱含單位。
        """
        super().__init__()
        if velocity_source_unit not in ("mph", "kph"):
            raise ValueError(
                f"velocity_source_unit must be 'mph' or 'kph', got {velocity_source_unit!r}"
            )
        if break_source_unit not in ("in", "cm"):
            raise ValueError(
                f"break_source_unit must be 'in' or 'cm', got {break_source_unit!r}"
            )
        self.velocity_source_unit = velocity_source_unit
        self.break_source_unit = break_source_unit
        vel_range = (
            config.PITCHING_VELOCITY_RANGE_KPH if velocity_source_unit == "kph"
            else config.PITCHING_VELOCITY_RANGE_MPH
        )
        brk_range = (
            config.PITCHING_BREAK_RANGE_CM if break_source_unit == "cm"
            else config.PITCHING_BREAK_RANGE_INCH
        )
        # Per-instance override of the class-level VALIDATION_RANGES so
        # BaseOCRReader._read_field() picks up the correct range without
        # touching other fields.
        self.VALIDATION_RANGES = {
            **config.PITCHING_VALIDATION,
            "velocity_mph": vel_range,
            "v_break_in": brk_range,
            "h_break_in": brk_range,
        }

    def read(
        self, screen_crop: np.ndarray, frame_index: int = -1
    ) -> PitchMetrics | None:
        """
        Run OCR on all four fields. Each numeric field independently
        falls back to None if it is either (a) showing the "-" dash
        placeholder or (b) OCR weighted-vote rule rejects the reading.
        spin_direction has its own string-typed path.

        Returns None only if EVERY field failed — i.e. this frame is
        not a valid pitch entry at all (which should be rare because
        pitching-mode InvalidDetector already filtered by velocity).
        """
        # Fix 27 — parallelise the four per-field OCR calls. Each field
        # spends >95 % of its wall time inside Tesseract subprocess calls
        # (GIL released) and EasyOCR inference (also GIL-free). The four
        # fields are independent (disjoint ROIs, stateless readers, no
        # shared mutable state), so parallelisation is a pure latency
        # win with zero correctness risk. Combined with the 3-frame
        # parallel vote in video_player.py, trigger-cycle OCR time drops
        # ~7× versus the Fix 25 serial baseline.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=6) as pool:
            vel_future = pool.submit(
                self._read_numeric_field, screen_crop, "velocity_mph", frame_index
            )
            spin_future = pool.submit(
                self._read_numeric_field, screen_crop, "total_spin_rpm", frame_index
            )
            dir_future = pool.submit(
                self._read_spin_direction, screen_crop, frame_index
            )
            eff_future = pool.submit(
                self._read_numeric_field, screen_crop, "spin_efficiency_pct", frame_index
            )
            v_future = pool.submit(
                self._read_numeric_field, screen_crop, "v_break_in", frame_index
            )
            h_future = pool.submit(
                self._read_numeric_field, screen_crop, "h_break_in", frame_index
            )
            velocity_mph = vel_future.result()
            total_spin_raw = spin_future.result()
            spin_direction = dir_future.result()
            spin_efficiency_pct = eff_future.result()
            v_break_in = v_future.result()
            h_break_in = h_future.result()
        total_spin_rpm = int(total_spin_raw) if total_spin_raw is not None else None

        if (
            velocity_mph is None
            and total_spin_rpm is None
            and spin_direction is None
            and spin_efficiency_pct is None
            and v_break_in is None
            and h_break_in is None
        ):
            logger.debug("All pitching fields failed OCR; skipping frame.")
            return None

        return PitchMetrics(
            velocity_mph=velocity_mph,
            total_spin_rpm=total_spin_rpm,
            spin_direction=spin_direction,
            spin_efficiency_pct=spin_efficiency_pct,
            v_break_in=v_break_in,
            h_break_in=h_break_in,
        )

    # ── internal helpers ──────────────────────────────────────────────────

    def _crop_field(
        self, screen_crop: np.ndarray, field_name: str
    ) -> np.ndarray:
        """Crop the ROI for a pitching field from the full screen crop."""
        roi = config.PITCHING_ROI[field_name]
        h, w = screen_crop.shape[:2]
        x1 = int(roi[0] * w)
        y1 = int(roi[1] * h)
        x2 = int((roi[0] + roi[2]) * w)
        y2 = int((roi[1] + roi[3]) * h)
        return screen_crop[y1:y2, x1:x2]

    # ── Cross-validation tolerance per field ────────────────────────────
    # How close Tesseract and EasyOCR values must be to count as
    # "agreeing". For float fields (velocity, spin_efficiency) we
    # compare at 1-decimal resolution. For int fields (total_spin)
    # we allow ±5 because EasyOCR sometimes appends a trailing digit
    # from the next line which gets truncated during parsing.
    _CROSS_VALIDATE_TOL: dict[str, float] = {
        "velocity_mph": 0.15,
        "total_spin_rpm": 5.0,
        "spin_efficiency_pct": 0.15,
        "v_break_in": 0.15,
        "h_break_in": 0.15,
    }

    def _read_numeric_field(
        self, screen_crop: np.ndarray, field_name: str, frame_index: int
    ) -> float | None:
        """
        Read a numeric pitching field with dual-engine cross-validation.

        Fix 11 + Fix 13 — dual-engine architecture (Tesseract + EasyOCR):
          1. Dash detection first — if the ROI shows "-", return None.
          2. Tesseract track: full Fix 4 pipeline (binarised + close_3x3
             + psm7_2x weighted voting) via BaseOCRReader._read_field().
          3. EasyOCR (CNN) track: grayscale upscaled image (NO binarisation,
             per Gemini's advice — CNN models need grey-level gradients).
          4. Cross-validation:
             - Both agree within tolerance → return Tesseract value.
             - Disagree → trust EasyOCR (Fix 13). Diagnostic on 16-entry
               pitcher sample shows every disagreement is a Tesseract
               misread (leading digit eaten / 8↔6 confusion) where
               EasyOCR is correct. EasyOCR value must be in-range.
             - One null → trust whichever has a reading.
             - Both null → null.
        """
        crop = self._crop_field(screen_crop, field_name)
        if _is_dash_field(crop):
            logger.debug(
                "%s shows dash placeholder (frame %d) — field → null",
                field_name, frame_index,
            )
            return None

        # ── Track 1: Tesseract (binarised pipeline) ──────────────────
        tess_val = self._read_field(
            screen_crop, self.ROI_DEFINITIONS[field_name], frame_index
        )

        # ── Track 2: EasyOCR (grayscale pipeline) ────────────────────
        easy_val = self._read_easyocr(crop, field_name)

        # ── Cross-validation ─────────────────────────────────────────
        tol = self._CROSS_VALIDATE_TOL.get(field_name, 0.15)

        # Fix 30 — V/H 嚴格模式：兩引擎一致才收，否則 null（防止 SILENT_WRONG）。
        # 動機：實測發現 V/H 的 ROI 容易被 'V:'/'H:' label 與 '"' 引號干擾，
        # 單引擎的 fallback 會把例如 0.0 / 1.0 / 29.2 / 4.6 等錯讀寫入。
        # V/H 不像 velocity/spin 有清楚的物理下限可以靠 validation range 過濾
        # （±35" 範圍涵蓋所有合理值），所以紅線唯一防線是雙引擎一致。
        if field_name in ("v_break_in", "h_break_in"):
            if tess_val is not None and easy_val is not None:
                if abs(tess_val - easy_val) <= tol:
                    logger.debug(
                        "%s strict agree: tess=%.1f easy=%.1f",
                        field_name, tess_val, easy_val,
                    )
                    return tess_val
                logger.info(
                    "%s STRICT MODE disagree (frame %d): "
                    "tess=%.1f easy=%.1f → null",
                    field_name, frame_index, tess_val, easy_val,
                )
                return None
            # 任一引擎 null → null
            logger.debug(
                "%s strict mode: tess=%s easy=%s → null",
                field_name, tess_val, easy_val,
            )
            return None

        if tess_val is not None and easy_val is not None:
            if abs(tess_val - easy_val) <= tol:
                # Both engines agree — high confidence
                logger.debug(
                    "%s cross-validated: tess=%.1f easy=%.1f (agree)",
                    field_name, tess_val, easy_val,
                )
                return tess_val
            else:
                # Engines disagree — trust EasyOCR (Fix 13).
                # Diagnostic evidence: Tesseract's italic-bold failure
                # modes (leading-digit eat, 8↔6) cause every observed
                # disagreement; EasyOCR (CNN) is immune to these.
                logger.info(
                    "%s CROSS-VALIDATION DISAGREE (frame %d): "
                    "tess=%.1f easy=%.1f → trust EasyOCR",
                    field_name, frame_index, tess_val, easy_val,
                )
                return easy_val

        if tess_val is not None and easy_val is None:
            # EasyOCR failed but Tesseract has a value. Since Tesseract
            # already passed strict voting (frac70_min4), trust it.
            logger.debug(
                "%s tess=%.1f, easy=null → trust Tesseract vote",
                field_name, tess_val,
            )
            return tess_val

        if easy_val is not None and tess_val is None:
            # Tesseract failed voting but EasyOCR has a reading.
            # Return EasyOCR value — it may rescue null cells.
            logger.debug(
                "%s tess=null, easy=%.1f → use EasyOCR fallback",
                field_name, easy_val,
            )
            return easy_val

        # Both null
        return None

    def _read_easyocr(
        self, crop_bgr: np.ndarray, field_name: str
    ) -> float | None:
        """
        Run EasyOCR on a ROI crop. Uses grayscale upscaled image
        (NO binarisation) per Gemini's advice — CNN models need
        grey-level gradients.

        Fix 13: Select the best IN-RANGE detection, not just the
        highest-confidence one. The ROI often includes label text
        (e.g. "08" from "SPIN EFFICIENCY" row above) which EasyOCR
        reads with higher confidence than the italic-bold value.
        Selecting by highest-confidence-among-valid-values fixes this.

        Returns a validated float, or None on failure.
        """
        reader = _get_easyocr_reader()

        # Grayscale + upscale (same factor as Tesseract track)
        if len(crop_bgr.shape) == 3:
            gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        else:
            gray = crop_bgr
        new_w = gray.shape[1] * config.OCR_UPSCALE_FACTOR
        new_h = gray.shape[0] * config.OCR_UPSCALE_FACTOR
        gray_up = cv2.resize(
            gray, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4
        )

        # Fix 30 — V/H 需完整 charset 才能正確辨識
        # 加 allowlist 會讓 EasyOCR 把 "V"→"1"、"H"→"4"、"""→數字污染結果
        # (e.g. "H: 21.4"" 用 allowlist 讀出 "421.45")
        # 改為不加 allowlist，從 raw text regex 抽取「(可選-)數字.數字」
        if field_name in ("v_break_in", "h_break_in"):
            results = reader.readtext(gray_up, detail=1)
        elif field_name == "total_spin_rpm":
            results = reader.readtext(gray_up, allowlist="0123456789", detail=1)
        else:
            results = reader.readtext(gray_up, allowlist="0123456789.", detail=1)

        if not results:
            logger.debug("EasyOCR %s: no results", field_name)
            return None

        # Fix 33 — 用 instance-level VALIDATION_RANGES，讓 velocity mph/kph
        # 與 V/H inch/cm 的單位切換都能在 EasyOCR 軌道被套用。
        lo, hi = self.VALIDATION_RANGES.get(
            field_name, (float("-inf"), float("inf"))
        )

        # Parse all detections and collect in-range values
        valid_candidates: list[tuple[float, float]] = []  # (value, conf)
        for det in results:
            text, conf = det[1], det[2]

            if field_name in ("v_break_in", "h_break_in"):
                # 從 raw text (含 "V:", "H:", 引號 ' / ", "cm" 尾綴) 抽出數值。
                # Fix 33 — EasyOCR 系統性把數字 0 讀成字母 O/o（V3 影片
                # "41.0cm" → "41.Ocm"，regex 原本抓不到）。在 regex 前
                # 先把 O/o 還原成 0，再抽取「-?\d+\.\d+」。
                normalised = text.replace("O", "0").replace("o", "0")
                m = re.search(r"-?\d+\.\d+", normalised)
                if not m:
                    continue
                try:
                    value = float(m.group())
                except ValueError:
                    continue
            else:
                try:
                    value = float(text)
                except ValueError:
                    continue

            # For total_spin_rpm, truncate 5-digit reads to 4 digits
            if field_name == "total_spin_rpm" and value > 9999:
                value = float(str(int(value))[:4])

            if lo <= value <= hi:
                valid_candidates.append((round(value, 1), conf))

        if not valid_candidates:
            logger.debug(
                "EasyOCR %s: no in-range candidates from %d detections",
                field_name, len(results),
            )
            return None

        # Pick highest-confidence in-range candidate
        best_val, best_conf = max(valid_candidates, key=lambda vc: vc[1])
        logger.debug(
            "EasyOCR %s: %.1f (conf=%.3f, %d candidates)",
            field_name, best_val, best_conf, len(valid_candidates),
        )
        return best_val

    # Regex for 3-5 digit strings that could be a clock value without colon.
    # E.g. "1244" → "12:44", "0158" → "01:58", "822" → "08:22"
    _COLONLESS_CLOCK_RE = re.compile(r"^(\d{3,5})$")

    def _parse_clock_candidates(self, digits: str) -> list[str]:
        """
        Reconstruct "HH:MM" candidates from a colon-less digit string.
        Returns a list of all valid interpretations, best-first.

        3 digits: "822" → ["08:22"]
        4 digits: "1244" → ["12:44"]  (deterministic)
        5 digits: "08422" → ["08:42", "08:22"]  (ambiguous — try
                  dropping each middle digit to see which 4-digit
                  subset yields valid HH:MM)
        """
        results: list[str] = []

        def _try_4(d4: str) -> str | None:
            candidate = f"{d4[:2]}:{d4[2:]}"
            if self._CLOCK_RE.match(candidate):
                hh, mm = candidate.split(":")
                return f"{int(hh):02d}:{mm}"
            return None

        if len(digits) == 4:
            c = _try_4(digits)
            if c:
                results.append(c)

        elif len(digits) == 3:
            candidate = f"0{digits[0]}:{digits[1:]}"
            if self._CLOCK_RE.match(candidate):
                hh, mm = candidate.split(":")
                results.append(f"{int(hh):02d}:{mm}")

        elif len(digits) == 5:
            # Try dropping each of the 5 positions to get 4-digit subsets.
            # Most common pattern: EasyOCR doubles a digit in the middle,
            # e.g. "08422" = "08:22" with extra '4', or "04422" = "04:22"
            # with extra '4'. First 4 digits go first (original heuristic).
            seen: set[str] = set()
            for drop_pos in range(len(digits)):
                subset = digits[:drop_pos] + digits[drop_pos + 1:]
                c = _try_4(subset)
                if c and c not in seen:
                    results.append(c)
                    seen.add(c)

        return results

    def _read_spin_direction(
        self, screen_crop: np.ndarray, frame_index: int
    ) -> str | None:
        """
        Read spin_direction as a clock-format "HH:MM" string.

        Fix 13 — EasyOCR-primary with Tesseract cross-check:
          1. EasyOCR on grayscale upscaled crop (high accuracy, 14/16 on
             pitcher sample). Filter noise detections by confidence > 0.1.
          2. If EasyOCR produces "HH:MM" directly, use it.
          3. If EasyOCR produces digits without colon (e.g. "1244"),
             try reconstructing "HH:MM" by inserting colon.
          4. Tesseract voting (PSM 11 only — PSM 7 returns empty, PSM 4
             returns label garbage) as cross-check. Relaxed threshold
             (frac50_min2) since PSM 11 produces fewer valid votes.
          5. Both agree → return. Disagree → trust EasyOCR (it has
             demonstrated higher accuracy on this italic-bold font).
             Either alone → return that value.

        Note: Rapsodo's fallback display for incomplete direction data
        is "12:00", not a dash. That case is treated as a VALID read
        and returned as the literal string "12:00", not as None.
        """
        crop_bgr = self._crop_field(screen_crop, "spin_direction")
        if crop_bgr.size == 0:
            return None

        # Grayscale + upscale
        if len(crop_bgr.shape) == 3:
            gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        else:
            gray = crop_bgr
        new_w = gray.shape[1] * config.OCR_UPSCALE_FACTOR
        new_h = gray.shape[0] * config.OCR_UPSCALE_FACTOR
        gray_up = cv2.resize(
            gray, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4
        )

        # ── Track 1: EasyOCR ─────────────────────────────────────────
        easy_val, easy_had_colon, easy_digit_count, easy_candidates = (
            self._read_spin_direction_easyocr(gray_up)
        )

        # ── Track 2: Tesseract (PSM 11 only, relaxed voting) ─────────
        tess_val, tess_votes = self._read_spin_direction_tesseract(
            gray_up, frame_index
        )

        # ── Decision ─────────────────────────────────────────────────
        if easy_val is not None and tess_val is not None:
            if easy_val == tess_val:
                logger.debug(
                    "spin_direction cross-validated: easy=%s tess=%s (agree)",
                    easy_val, tess_val,
                )
                return easy_val
            else:
                # Disagree. Decision depends on EasyOCR's read quality:
                # - Native colon (e.g. "01:46"): trust EasyOCR (high
                #   quality read, Tesseract's leading-digit-eat is the
                #   known failure mode).
                # - 4-digit reconstructed (e.g. "1252" → "12:52"):
                #   trust EasyOCR — 4 digits have only one valid
                #   HH:MM split, so reconstruction is deterministic.
                # - 5-digit reconstructed (e.g. "08422" → "08:42"):
                #   the extra digit means we had to drop one, which
                #   may be wrong. Trust Tesseract since it passed
                #   its own voting threshold.
                if easy_had_colon or easy_digit_count == 4:
                    logger.info(
                        "spin_direction DISAGREE (frame %d): "
                        "easy=%s tess=%s → trust EasyOCR",
                        frame_index, easy_val, tess_val,
                    )
                    return easy_val
                else:
                    logger.info(
                        "spin_direction DISAGREE (frame %d): "
                        "easy=%s (5-digit reconstructed) tess=%s "
                        "→ trust Tesseract",
                        frame_index, easy_val, tess_val,
                    )
                    return tess_val

        if easy_val is not None:
            # EasyOCR has a value but Tesseract didn't reach threshold.
            # For 5-digit reconstructions, cross-check EasyOCR's
            # candidate list against Tesseract's raw votes (even if
            # they didn't reach the voting threshold).
            if (not easy_had_colon and easy_digit_count == 5
                    and tess_votes and len(easy_candidates) > 1):
                for cand in easy_candidates:
                    if cand in tess_votes:
                        logger.info(
                            "spin_direction 5-digit rescue (frame %d): "
                            "EasyOCR candidates=%s, tess_votes=%s "
                            "→ matched %s",
                            frame_index, easy_candidates,
                            tess_votes, cand,
                        )
                        return cand
            logger.debug(
                "spin_direction easy=%s, tess=null → use EasyOCR",
                easy_val,
            )
            return easy_val

        if tess_val is not None:
            logger.debug(
                "spin_direction easy=null, tess=%s → use Tesseract",
                tess_val,
            )
            return tess_val

        # Both null — save debug crop
        try:
            _DEBUG_DIR.mkdir(exist_ok=True)
            debug_path = _DEBUG_DIR / f"debug_spin_direction_{frame_index}.png"
            cv2.imwrite(str(debug_path), gray_up)
            logger.debug("Saved spin_direction failure crop: %s", debug_path)
        except OSError as exc:
            logger.debug("Could not save debug crop: %s", exc)

        return None

    def _read_spin_direction_easyocr(
        self, gray_up: np.ndarray
    ) -> tuple[str | None, bool, int, list[str]]:
        """
        Run EasyOCR on the spin_direction crop. Returns a tuple of
        (normalised "HH:MM" string or None, had_native_colon,
        digit_count, all_candidates).
        - had_native_colon: True if EasyOCR's text already contained
          a colon (high confidence read).
        - digit_count: number of raw digits in the detection (3, 4,
          or 5). Relevant when had_native_colon is False — 4-digit
          reconstructions are deterministic; 5-digit ones lose a digit
          and may be wrong.
        - all_candidates: list of all valid HH:MM candidates from
          digit reconstruction (useful for 5-digit cross-validation).
        """
        reader = _get_easyocr_reader()
        results = reader.readtext(
            gray_up, allowlist="0123456789:", detail=1
        )
        if not results:
            return None, False, 0, []

        # Filter low-confidence noise (the "DIRECTION" label reads as
        # "9:04" with conf ~0.04). Keep only conf > 0.1.
        ocr_candidates = [r for r in results if r[2] > 0.1]
        if not ocr_candidates:
            return None, False, 0, []

        best = max(ocr_candidates, key=lambda r: r[2])
        text = best[1].strip().replace(" ", "")

        # Case 1: already has colon and passes clock regex
        if self._CLOCK_RE.match(text):
            hh, mm = text.split(":")
            digits_only = text.replace(":", "")
            normalised = f"{int(hh):02d}:{mm}"
            return normalised, True, len(digits_only), [normalised]

        # Case 2: digits only — try inserting colon
        if self._COLONLESS_CLOCK_RE.match(text):
            clock_candidates = self._parse_clock_candidates(text)
            if clock_candidates:
                # For 3/4-digit strings there's only one candidate.
                # For 5-digit strings there may be multiple — return
                # the first (best positional match) and let cross-
                # validation in _read_spin_direction pick from the
                # full list if needed.
                chosen = clock_candidates[0]
                logger.debug(
                    "spin_direction EasyOCR reconstructed: %r → %s "
                    "(candidates=%s)",
                    text, chosen, clock_candidates,
                )
                return chosen, False, len(text), clock_candidates

        logger.debug(
            "spin_direction EasyOCR: cannot parse %r (conf=%.3f)",
            text, best[2],
        )
        return None, False, 0, []

    def _read_spin_direction_tesseract(
        self, gray_up: np.ndarray, frame_index: int
    ) -> tuple[str | None, dict[str, int]]:
        """
        Tesseract voting for spin_direction. Uses only PSM 11
        (PSM 7 returns empty for most entries, PSM 4 returns label
        garbage). Relaxed threshold: frac50_min2 (PSM 11 alone
        produces fewer valid votes than the full PSM sweep).

        Returns (winner_or_None, raw_votes_dict). The raw votes are
        returned so that cross-validation with EasyOCR candidates can
        check for partial matches even when no single Tesseract value
        reached the voting threshold.
        """
        close_kernel = np.ones((3, 3), np.uint8)
        votes: dict[str, int] = {}

        for blk, C in config.OCR_ADAPTIVE_ATTEMPTS:
            binarised = cv2.adaptiveThreshold(
                gray_up, 255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                blk, C,
            )
            binarised = cv2.morphologyEx(
                binarised, cv2.MORPH_CLOSE, close_kernel
            )
            tess_cfg = config.build_tesseract_config_clock(11)
            raw_text: str = pytesseract.image_to_string(
                Image.fromarray(binarised), config=tess_cfg
            )
            cleaned = raw_text.strip().replace(" ", "")
            if not self._CLOCK_RE.match(cleaned):
                continue
            hh, mm = cleaned.split(":")
            key = f"{int(hh):02d}:{mm}"
            votes[key] = votes.get(key, 0) + 1

        if votes:
            sorted_votes = sorted(votes.items(), key=lambda kv: -kv[1])
            winner_val, winner_cnt = sorted_votes[0]
            total = sum(votes.values())
            # Relaxed threshold: 50% of ≥2 votes
            if total >= 2 and winner_cnt / total >= 0.50:
                logger.debug(
                    "spin_direction tess vote winner %s (%d/%d = %.1f%%)",
                    winner_val, winner_cnt, total,
                    winner_cnt / total * 100,
                )
                return winner_val, votes
            logger.debug(
                "spin_direction tess vote rejected: %s %d/%d (%.1f%%)",
                winner_val, winner_cnt, total,
                winner_cnt / total * 100,
            )

        return None, votes
