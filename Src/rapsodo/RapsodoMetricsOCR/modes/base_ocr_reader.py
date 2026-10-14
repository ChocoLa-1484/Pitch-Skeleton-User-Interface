from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from PIL import Image

import Src.rapsodo.RapsodoMetricsOCR.config as config

_DEBUG_DIR = Path(__file__).parent.parent / "debug"

# Ensure Tesseract binary is found on Windows regardless of PATH in threads
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
from Src.rapsodo.RapsodoMetricsOCR.models import BaseMetrics, RelativeROI

logger = logging.getLogger(__name__)


# ── EasyOCR lazy singleton ────────────────────────────────────────────────────
# Shared by both hitting and pitching modes. Loaded once on first call,
# reused for the entire process lifetime. GPU is used if available.
_easyocr_reader = None


def _get_easyocr_reader():
    """Return the module-level EasyOCR reader, creating it on first call."""
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        logger.info("Initialising EasyOCR reader (one-time, GPU if available)…")
        _easyocr_reader = easyocr.Reader(["en"], gpu=True, verbose=False)
        logger.info("EasyOCR reader ready (device: %s)", _easyocr_reader.device)
    return _easyocr_reader


def warm_up_easyocr() -> None:
    """
    Eager-initialise EasyOCR so the first OCR call in the hot path isn't
    blocked by 1–3 s of model loading.

    Measured effect: on a cold start the first `_get_easyocr_reader()` call
    takes ~2 s (CPU) to ~8 s (mixed). During that wait the OCRWorker thread
    blocks while the VideoProcessor keeps feeding frames into the bounded
    task queue. The queue fills, the producer blocks on `put()`, and counter
    samples get silently dropped — which is how the first run of the
    pitcher video lost balls while the second run (same Python process,
    EasyOCR already warm) picked up more entries.

    Call this from the UI startup path (e.g. `VideoPlayerWindow.__init__`)
    or from any headless driver before starting `VideoProcessor`. Safe to
    call multiple times — the singleton guard in `_get_easyocr_reader()`
    makes subsequent calls no-ops.

    Runs a tiny dummy inference on a 32×32 grayscale image so the PyTorch
    graph is fully compiled. Without this, the first real call still pays
    ~300–500 ms of kernel-warmup even after the singleton is constructed.
    """
    reader = _get_easyocr_reader()
    dummy = np.zeros((32, 32), dtype=np.uint8)
    try:
        reader.readtext(dummy, allowlist="0123456789.")
    except Exception as exc:  # noqa: BLE001 — warm-up is best-effort
        logger.debug("EasyOCR warm-up inference failed (non-fatal): %s", exc)


class BaseOCRReader:
    """
    Shared OCR preprocessing pipeline.

    Subclasses must define:
        ROI_DEFINITIONS  : dict[str, RelativeROI]
        VALIDATION_RANGES: dict[str, tuple[float, float]]

    and override:
        read(screen_crop) -> BaseMetrics | None
    """

    ROI_DEFINITIONS: dict[str, RelativeROI] = {}
    VALIDATION_RANGES: dict[str, tuple[float, float]] = {}

    # Weighted-vote threshold (Fix 4d frac60_min3). Override in subclass
    # to raise the bar for specific modes whose font or layout produces
    # noisier PSM ties. Pitching uses frac70_min4 because the italic-
    # bold '7'/'8'/'9' causes leading-digit eats that a 60 % winner can
    # still sneak past. Hitting stays at 60/3 — that rule is pinned by
    # the Fix 4d simulation table in _read_field() below.
    VOTE_MIN_FRACTION: float = 0.60
    VOTE_MIN_TOTAL: int = 3

    # Per-field white-border padding. When set to a mapping from field
    # name → border width in pixels, _read_field() will pad the
    # binarised crop with a white border before handing it to Tesseract.
    # This rescues cases where the italic-bold ascender of a leading
    # '7' / '9' reaches the top edge of the crop and gets merged with
    # the image border during layout analysis (e.g. pitching velocity
    # 74.6 reads as "46.0"). Padding with white gives Tesseract
    # breathing room and lets it see the full ascender.
    #
    # Default: empty (no padding) — hitting mode doesn't need it because
    # HITTING_ROI already leaves ~3 % top margin. Pitching overrides
    # this per-field because adding padding to total_spin_rpm hurts
    # that field (see Fix 10 measure notes in pitching.py).
    BINARISED_BORDER_PX: dict[str, int] = {}

    def _read_field(
        self,
        screen_crop: np.ndarray,
        roi: RelativeROI,
        frame_index: int = -1,
    ) -> float | None:
        """Convenience wrapper — returns only the winner value."""
        winner, _ = self._read_field_full(screen_crop, roi, frame_index)
        return winner

    def _read_field_full(
        self,
        screen_crop: np.ndarray,
        roi: RelativeROI,
        frame_index: int = -1,
    ) -> tuple[float | None, dict[float, int]]:
        """
        Extract a single numeric field from the screen crop.

        Pipeline:
            1. Crop ROI using relative coordinates
            2. Convert to grayscale
            3. Upscale by OCR_UPSCALE_FACTOR with INTER_LANCZOS4
            4. Adaptive threshold binarisation (replaces contrast enhancement)
            5. Run Tesseract with digit/dot/minus whitelist
            6. Parse to float; validate against VALIDATION_RANGES

        On complete failure (all attempts exhausted), saves the preprocessed
        crop to debug/{field}_{frame_index}.png for post-hoc diagnosis.
        """
        h, w = screen_crop.shape[:2]

        x1 = int(roi.x * w)
        y1 = int(roi.y * h)
        x2 = int((roi.x + roi.w) * w)
        y2 = int((roi.y + roi.h) * h)

        # Guard against degenerate crops
        if x2 <= x1 or y2 <= y1:
            logger.debug("Degenerate ROI crop: (%d,%d,%d,%d)", x1, y1, x2, y2)
            return None

        crop = screen_crop[y1:y2, x1:x2]

        # Step 2 – grayscale
        if len(crop.shape) == 3:
            crop = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

        # Step 3 – upscale
        new_w = crop.shape[1] * config.OCR_UPSCALE_FACTOR
        new_h = crop.shape[0] * config.OCR_UPSCALE_FACTOR
        crop = cv2.resize(crop, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)

        # Steps 4–6 – adaptive threshold + Tesseract with full PSM × (blk, C)
        # sweep + psm7_2x weighted vote rule (Fix 4d) + 3x3 morphological
        # closing (Fix 4f).
        #
        # Video-compressed frames show per-frame pixel variation that makes a
        # single fixed (block_size, C) pair unreliable, and Tesseract's layout
        # analysis is sensitive to tiny crop-size differences. Prior behaviour
        # was "first parse + in-range wins" — empirically this produced 4 out
        # of 30 silent-wrong cells on Rapsodo_GT.json even after ROI width was
        # tuned in Fix 4b (0.22 → 0.165 to trim unit glyphs).
        #
        # Root cause: the first in-range candidate is often a minority
        # misread. Fix 4c collected ALL in-range readings across all 21
        # attempts and applied frac60_min3 (unweighted majority ≥60 % of
        # ≥3 readings). That held the red line (0 silent-wrong) but
        # dropped 5 baseline-correct cells to null.
        #
        # Inspect-nulls analysis (Fix 4d diagnostic) revealed PSM 7 is
        # systematically the most reliable segmenter for the Rapsodo digit
        # font — for 5 of the 11 null cells, PSM 7 already had a correct
        # majority but was being diluted to below-60 % by PSM 11/4 misreads.
        # Example: #1 distance — PSM7 reads 7×131 (correct), PSM11/4 read
        # 3/31 (garbage). Unweighted = 7/14 = 50 % → frac60_min3 rejects.
        # Weighted (PSM7=2×) = 14/21 = 67 % → winner stands.
        #
        # Rule chosen: psm7_2x + frac60_min3 threshold. Simulation
        # (Fix 4d measure-rules) over all 30 numeric cells:
        #
        #     rule            CORRECT  SILENT_WRONG  NULL
        #     frac60_min3     19       0             11
        #     psm7_2x         21       0             9     ← chosen
        #     psm7_3x         23       2             5     ← red line broken
        #     psm7_only_min3  24       1             5     ← red line broken
        #
        # Higher PSM 7 weights (3× or PSM7-only) bring back silent-wrong
        # via #4 distance (PSM7 consistently misreads as 207 when GT=227).
        # At 2×, the PSM 11/4 dilution still keeps #4 distance's bad
        # winner below the 60 % threshold → null (honest failure).
        #
        # The user's red line is (乙): NO silent-wrong values may leak
        # through. psm7_2x preserves it while recovering #1 dist and
        # #10 launch_angle relative to frac60_min3.
        #
        # Fix 4f: morphological 3×3 CLOSING applied after adaptive
        # thresholding. Motivation: the Rapsodo font's decimal dot is
        # only ~3 binarised pixels wide at the current upscale factor,
        # and Tesseract occasionally drops it (e.g. #8 EV "77.6" → "776",
        # #7 LA "8.5" → "85"). Closing = dilate → erode, which on a
        # WHITE-BG / BLACK-TEXT binarised image fills small gaps inside
        # character strokes without inflating strokes overall. Simulation
        # across morph variants (measure-morph):
        #
        #     variant      CORRECT  SILENT_WRONG  NULL
        #     none         21       0             9
        #     erode_2x2    23       2             5    ← red line broken
        #     erode_3x3    21       2             7    ← red line broken
        #     dilate_2x2   20       2             8    ← red line broken
        #     close_3x3    22       0             8    ← chosen
        #
        # close_3x3 is the only variant that stays on the red line AND
        # gains a cell (#7 launch_angle 8.5). Pure erode variants merge
        # adjacent digits under extreme thresholds and create silent
        # misreads like #4 LA 27.6 → 21.6.
        last_binarised = crop  # fallback for debug save
        _CLOSE_KERNEL = np.ones((3, 3), np.uint8)
        # Resolve per-field white-border width (0 = no padding).
        field_name_for_border = next(
            (name for name, r in self.ROI_DEFINITIONS.items() if r == roi),
            "",
        )
        border_px = self.BINARISED_BORDER_PX.get(field_name_for_border, 0)
        # Weighted tally of rounded-to-1-decimal in-range readings.
        # PSM 7 contributes 2 per hit, PSM 11 & 4 contribute 1 per hit.
        votes: dict[float, int] = {}
        for psm in config.OCR_PSM_FALLBACK_MODES:
            tess_cfg = config.build_tesseract_config(psm)
            psm_weight = 2 if psm == 7 else 1
            for blk, C in config.OCR_ADAPTIVE_ATTEMPTS:
                binarised = cv2.adaptiveThreshold(
                    crop, 255,
                    cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                    cv2.THRESH_BINARY,
                    blk, C,
                )
                # Fix 4f: 3×3 closing to heal broken strokes / tiny
                # decimal dots before handing to Tesseract.
                binarised = cv2.morphologyEx(
                    binarised, cv2.MORPH_CLOSE, _CLOSE_KERNEL
                )
                # Fix 10a: optional white border to give Tesseract
                # layout analysis breathing room for italic-bold
                # ascenders that touch the crop edge. Per-field via
                # BINARISED_BORDER_PX.
                if border_px > 0:
                    binarised = cv2.copyMakeBorder(
                        binarised,
                        border_px, border_px, border_px, border_px,
                        cv2.BORDER_CONSTANT,
                        value=255,
                    )
                last_binarised = binarised
                raw_text: str = pytesseract.image_to_string(
                    Image.fromarray(binarised), config=tess_cfg
                )
                cleaned = raw_text.strip().replace(" ", "")
                try:
                    value = float(cleaned)
                except ValueError:
                    logger.debug(
                        "OCR parse failure (psm=%d blk=%d C=%d): %r",
                        psm, blk, C, raw_text,
                    )
                    continue
                if not self._in_range(value, roi):
                    logger.debug(
                        "Value %.2f out of range for ROI (psm=%d blk=%d C=%d)",
                        value, psm, blk, C,
                    )
                    continue
                key = round(value, 1)
                votes[key] = votes.get(key, 0) + psm_weight

        # Apply psm7_2x + weighted vote rule. `votes` holds PSM-weighted
        # totals. Threshold values come from the subclass (hitting uses
        # the Fix 4d frac60_min3 default, pitching overrides to
        # frac70_min4 to counter italic-digit leading-eats).
        if votes:
            sorted_votes = sorted(votes.items(), key=lambda kv: -kv[1])
            winner_val, winner_cnt = sorted_votes[0]
            total = sum(votes.values())
            if (
                total >= self.VOTE_MIN_TOTAL
                and winner_cnt / total >= self.VOTE_MIN_FRACTION
            ):
                logger.debug(
                    "Weighted vote winner %.1f (%d/%d = %.1f%%)",
                    winner_val, winner_cnt, total, winner_cnt / total * 100,
                )
                return winner_val, votes
            logger.debug(
                "Weighted vote rejected: winner=%.1f %d/%d (%.1f%%) — returning None",
                winner_val, winner_cnt, total, winner_cnt / total * 100,
            )

        # All attempts exhausted or vote rule rejected — save debug crop
        field_name = next(
            (name for name, r in self.ROI_DEFINITIONS.items() if r == roi),
            "unknown",
        )
        try:
            _DEBUG_DIR.mkdir(exist_ok=True)
            debug_path = _DEBUG_DIR / f"debug_{field_name}_{frame_index}.png"
            cv2.imwrite(str(debug_path), last_binarised)
            logger.debug("Saved OCR failure crop: %s", debug_path)
        except OSError as exc:
            logger.debug("Could not save debug crop: %s", exc)

        return None, votes

    def _in_range(self, value: float, roi: RelativeROI) -> bool:
        """Validate value against the VALIDATION_RANGES entry matching this ROI."""
        for field_name, field_roi in self.ROI_DEFINITIONS.items():
            if field_roi == roi:
                lo, hi = self.VALIDATION_RANGES[field_name]
                in_range = lo <= value <= hi
                if not in_range:
                    logger.debug(
                        "Value %.2f out of range [%.2f, %.2f] for field '%s'",
                        value, lo, hi, field_name,
                    )
                return in_range
        return True  # Unknown ROI – pass through

    # ── EasyOCR rescue (Fix 15 — hitting dual-engine) ───────────────────

    def _easyocr_rescue(
        self,
        screen_crop: np.ndarray,
        field_name: str,
        tess_votes: dict[float, int],
    ) -> float | None:
        """
        EasyOCR fallback when Tesseract voting fails to produce a winner.

        Strategy (Fix 15):
          1. Run EasyOCR on grayscale upscaled ROI (no binarisation).
          2. Extract in-range candidates via:
             a) Direct parse of OCR text
             b) Decimal point insertion at each position (rescues
                "711" → 71.1 when the decimal dot is dropped)
             c) Trailing digit truncation (rescues "2638" → 263)
          3. Cross-validate against Tesseract's non-winning votes:
             - If a candidate matches a Tesseract vote, prefer it
             - Single unambiguous candidate is accepted without cross-check
             - Multiple ambiguous candidates with no Tesseract match → null

        Returns the rescued value or None.
        """
        roi = self.ROI_DEFINITIONS[field_name]
        lo, hi = self.VALIDATION_RANGES[field_name]

        h, w = screen_crop.shape[:2]
        x1 = int(roi.x * w)
        y1 = int(roi.y * h)
        x2 = int((roi.x + roi.w) * w)
        y2 = int((roi.y + roi.h) * h)
        crop = screen_crop[y1:y2, x1:x2]

        if len(crop.shape) == 3:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        else:
            gray = crop
        up = cv2.resize(
            gray,
            (gray.shape[1] * config.OCR_UPSCALE_FACTOR,
             gray.shape[0] * config.OCR_UPSCALE_FACTOR),
            interpolation=cv2.INTER_LANCZOS4,
        )

        reader = _get_easyocr_reader()
        results = reader.readtext(up, allowlist="0123456789.-")

        # Build candidate list: (value, confidence, method)
        candidates: list[tuple[float, float, str]] = []
        for _bbox, text, conf in results:
            cleaned = text.strip().replace(" ", "").strip(".-")
            if not cleaned:
                continue
            # a) Direct parse
            try:
                v = float(cleaned)
                if lo <= v <= hi:
                    candidates.append((round(v, 1), conf, "direct"))
            except ValueError:
                pass
            # b) Decimal point insertion for digit-only strings
            digits = cleaned.replace(".", "").replace("-", "")
            if len(digits) >= 3 and "." not in cleaned:
                for pos in range(1, len(digits)):
                    try:
                        v = float(digits[:pos] + "." + digits[pos:])
                        if lo <= v <= hi:
                            candidates.append((round(v, 1), conf * 0.7, f"dot@{pos}"))
                    except ValueError:
                        pass
                # c) Trailing digit truncation
                try:
                    v = float(digits[:-1])
                    if lo <= v <= hi:
                        candidates.append((round(v, 1), conf * 0.6, "trunc"))
                except ValueError:
                    pass

        if not candidates:
            return None

        # Selection with Tesseract cross-validation
        directs = [(v, c, m) for v, c, m in candidates if m == "direct"]
        derived = [(v, c, m) for v, c, m in candidates if m != "direct"]

        # Priority 1: direct candidate matching Tesseract vote
        if directs and tess_votes:
            matched = [(v, c) for v, c, _ in directs if v in tess_votes]
            if matched:
                best = max(matched, key=lambda x: (tess_votes.get(x[0], 0), x[1]))
                logger.debug(
                    "%s EasyOCR rescue: direct+tess_match %.1f", field_name, best[0]
                )
                return best[0]

        # Priority 2: single direct candidate
        if len(directs) == 1:
            logger.debug(
                "%s EasyOCR rescue: single direct %.1f", field_name, directs[0][0]
            )
            return directs[0][0]

        # Priority 3: derived candidate matching Tesseract vote
        if derived and tess_votes:
            matched = [(v, c) for v, c, _ in derived if v in tess_votes]
            if matched:
                best = max(matched, key=lambda x: (tess_votes.get(x[0], 0), x[1]))
                logger.debug(
                    "%s EasyOCR rescue: derived+tess_match %.1f", field_name, best[0]
                )
                return best[0]

        # Priority 4: single derived candidate (no directs available)
        if len(derived) == 1 and not directs:
            logger.debug(
                "%s EasyOCR rescue: single derived %.1f", field_name, derived[0][0]
            )
            return derived[0][0]

        # Multiple ambiguous candidates, no Tesseract corroboration → null
        logger.debug(
            "%s EasyOCR rescue: ambiguous (%d candidates), returning null",
            field_name, len(candidates),
        )
        return None

    def read(self, screen_crop: np.ndarray) -> BaseMetrics | None:
        """Override in subclasses to return a typed metrics object."""
        raise NotImplementedError
