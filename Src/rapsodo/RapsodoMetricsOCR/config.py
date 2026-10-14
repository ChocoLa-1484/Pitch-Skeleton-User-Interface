# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------
CAMERA_DEVICE_INDEX: int = 0
CAPTURE_FPS: int = 1

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
OUTPUT_JSON_PATH: str = r"C:\rapsodo-metrics-ocr\rapsodo_data.json"
OUTPUT_JSON_HITTING: str  = r"C:\rapsodo-metrics-ocr\rapsodo_data_hitting.json"
OUTPUT_JSON_PITCHING: str = r"E:\Pitch-Skeleton-User-Interface_demo_45090\Db\Record\TabletCapture\rapsodo_data_pitching.json"
BACKUP_DIR: str       = r"C:\rapsodo-metrics-ocr\backup"
LOG_FILE: str         = r"C:\rapsodo-metrics-ocr\rapsodo_ocr.log"

# ---------------------------------------------------------------------------
# Screen detection
# ---------------------------------------------------------------------------
# Canny edge detection thresholds
CANNY_THRESHOLD_LOW: int  = 50
CANNY_THRESHOLD_HIGH: int = 150

# Minimum contour area as a fraction of total frame area
SCREEN_MIN_AREA_FRACTION: float = 0.20

# Accepted aspect ratio range for the detected iPad screen (landscape)
SCREEN_ASPECT_RATIO_MIN: float = 0.6
SCREEN_ASPECT_RATIO_MAX: float = 0.9

# Consecutive frame failures before a warning is logged
SCREEN_FAIL_WARN_THRESHOLD: int = 3

# Gaussian blur kernel size (must be odd)
SCREEN_BLUR_KERNEL_SIZE: int = 5

# Contour approximation epsilon as a fraction of the contour perimeter
SCREEN_CONTOUR_EPSILON_FACTOR: float = 0.02

# Output size of the perspective-corrected screen crop (width × height, 4:3)
SCREEN_WARP_WIDTH: int  = 1200
SCREEN_WARP_HEIGHT: int = 900

# ---------------------------------------------------------------------------
# OCR preprocessing
# ---------------------------------------------------------------------------
OCR_UPSCALE_FACTOR: int = 2

# Adaptive threshold fallback sequence: list of (block_size, C) pairs tried
# in order until one yields a valid, in-range float.  Block size must be odd.
# Covers the per-frame pixel variation introduced by video compression.
OCR_ADAPTIVE_ATTEMPTS: list[tuple[int, int]] = [
    (31,  5),
    (31,  8),
    (41,  5),
    (41,  8),
    (41, 12),
    (51,  5),
    (51,  8),
]

# Tesseract page-segmentation / engine config shared across all OCR fields.
# This is the default single-PSM form, still used by HitCounterReader and
# the standalone tools (tools/test_ocr.py, tools/debug_ev_null.py).
# BaseOCRReader uses OCR_PSM_FALLBACK_MODES + build_tesseract_config() below
# to retry with alternative page-segmentation modes when the primary mode
# fails to read a valid, in-range value.
TESSERACT_CONFIG: str = "--psm 7 --oem 3 -c tessedit_char_whitelist=0123456789.-"

# Alternative Tesseract PSM (Page Segmentation Mode) values tried by
# BaseOCRReader in order. PSM 7 is the original single-line mode used in
# all prior versions; PSM 11 and PSM 4 were empirically verified to read
# the decimal point on the italic bold Rapsodo digit font in cases where
# PSM 7 drops it (e.g. "65.9" misread as "659" → out of range → field
# recorded as null). See CHANGELOG Fix 1 investigation for details.
OCR_PSM_FALLBACK_MODES: list[int] = [7, 11, 4]


def build_tesseract_config(psm: int) -> str:
    """
    Return a Tesseract config string for the given PSM, reusing the same
    OEM and digit whitelist as TESSERACT_CONFIG. Used by BaseOCRReader to
    sweep multiple PSM values within a single ROI read.
    """
    return f"--psm {psm} --oem 3 -c tessedit_char_whitelist=0123456789.-"

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
# cv2.waitKey poll interval in milliseconds (1 = effectively non-blocking)
KEYBOARD_CHECK_TIMEOUT_MS: int = 1

# ---------------------------------------------------------------------------
# Hitting ROIs  (x, y, w, h as fractions of perspective-corrected screen)
# ---------------------------------------------------------------------------
# Fix 4b — w reduced from 0.22 to 0.165 (= 0.22 × 0.75) so the right edge
# of the ROI lands just before the MPH / ° / ' unit glyphs. The unit
# characters are non-digit so the whitelist already filters them out, but
# their stroke geometry still breaks Tesseract's layout analysis and
# causes silent-wrong reads like "131'" → "3", "62.0 MPH" → "62.08".
# Measured across all 12 Rapsodo_GT.json frames via
# tools/evaluate_ocr.py measure: baseline 0.22 gives 9/30 numeric cells
# correct, 0.165 gives 25/30. See run_log4d.txt for the full sweep.
HITTING_ROI: dict[str, tuple[float, float, float, float]] = {
    "exit_velocity": (0.74, 0.32, 0.165, 0.12),
    "launch_angle":  (0.74, 0.52, 0.165, 0.12),
    "distance":      (0.74, 0.72, 0.165, 0.12),
}
HITTING_VALIDATION: dict[str, tuple[float, float]] = {
    "exit_velocity": (20.0, 130.0),
    "launch_angle":  (-90.0, 90.0),
    "distance":      (0.0, 600.0),
}

# ---------------------------------------------------------------------------
# Pitching ROIs  (PLACEHOLDER – calibrate before use, 階段 2)
# ---------------------------------------------------------------------------
# Field names match Rapsodo_Pitcher_GT.json keys exactly so that
# evaluate_ocr.py `score --mode pitching` can do direct dict lookup.
#
# spin_direction is a STRING field ("HH:MM" clock format, e.g. "12:58");
# it does NOT use a numeric validation range. See PITCHING_CLOCK_REGEX
# below and PitchingOCRReader._read_spin_direction().
# ROI values below were measured empirically from all 18 frames of the
# pitcher GT video: for each full frame we ran a connected-components
# pass on (gray < 60) in the right-hand column 0.72..0.90, filtered for
# components with 100 < h < 300 px (i.e. the italic-bold digit glyphs,
# not the thin labels), grouped them by y, and took the min/max x,y of
# each group. Across the 13 entries that have all four fields populated
# the text bounds varied by < ±0.005 fractional units, so the padding
# below (left/top 1.5%, right/bottom 2.5% — right/bottom larger to
# capture the italic slant overhang) is a conservative safety margin.
#
# The previous placeholder (0.74, y, 0.22, 0.12) inherited from
# HITTING_ROI put the ROI right edge at 0.96 — well past the iPad
# screen boundary at ~0.90 — so every crop had ~40% black empty space
# on the right. That broke layout analysis for PSMs 7/11/4 and caused
# e.g. velocity 69.8 to read as "9", spin_efficiency 76.1 as "6.", etc.
PITCHING_ROI: dict[str, tuple[float, float, float, float]] = {
    "velocity_mph":        (0.733, 0.296, 0.103, 0.108),
    "total_spin_rpm":      (0.727, 0.446, 0.113, 0.108),
    "spin_direction":      (0.724, 0.596, 0.121, 0.108),
    # spin_efficiency uses TIGHTER top padding (1.5% vs 2.5%) because the
    # "SPIN EFFICIENCY" label row sits closer to its value than the other
    # three labels do to theirs. With 2.5% top pad Tesseract reads "FICIENCY"
    # and returns "1" / "3" (the closing stroke of Y). With 1.5% it still
    # captures the top of the digit ascenders safely.
    "spin_efficiency_pct": (0.733, 0.756, 0.102, 0.098),
}
# Only numeric fields appear here. spin_direction is validated via regex.
#
# Ranges are tightened beyond the physical envelope to create a silent-
# wrong firewall for Rapsodo's italic-bold pitcher font. The failure
# mode we must block is "leading digit eaten": the italic 7/8/9 has a
# long slanted top stroke that PSM 7/11/4 occasionally drops under
# certain (block, C) thresholds, so e.g. spin_efficiency 76.1 reads as
# "6.1". Both values are inside a physical range [0, 100], so the
# weighted vote rule alone cannot reject the misread — we have to
# narrow the range so the truncated reading falls outside it.
#
# Calibration against the 18-entry Rapsodo_Pitcher_GT sample shows:
#   - real pitcher velocities sit in 59..77 mph → 40..110 range
#     firmly rejects "46.0" (74.6 with '7' eaten) and "2.4" (72.4)
#   - real spin_efficiency reads are 54.6..96.9 % → a 30..100 floor
#     rejects "6.1" / "6.8" / "4.0" / "2.0" leading-digit eats
#   - total_spin_rpm already sits in 1795..2963 → 1000..3500 floor
#     rejects "795" / "622" style leading-digit eats
PITCHING_VALIDATION: dict[str, tuple[float, float]] = {
    "velocity_mph":        (40.0, 110.0),
    "total_spin_rpm":      (1000.0, 3500.0),
    "spin_efficiency_pct": (30.0, 100.0),
}

# spin_direction clock format: "HH:MM" with 00–12 hours and 00–59 minutes.
# Used by PitchingOCRReader._read_spin_direction() to validate the raw
# tesseract output after running with the colon-aware whitelist.
PITCHING_CLOCK_REGEX: str = r"^([01]?\d):[0-5]\d$"

# Tesseract whitelist used ONLY for spin_direction — the colon is a
# legal character here (it is not in the default digit whitelist).
PITCHING_CLOCK_WHITELIST: str = "0123456789:"


def build_tesseract_config_clock(psm: int) -> str:
    """Tesseract config for spin_direction ('HH:MM' clock format)."""
    return (
        f"--psm {psm} --oem 3 "
        f"-c tessedit_char_whitelist={PITCHING_CLOCK_WHITELIST}"
    )

# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------
CHANGE_TOLERANCE: float = 0.3

# ---------------------------------------------------------------------------
# Video player (tools/video_player.py)
# ---------------------------------------------------------------------------
# Run OCR every N frames; video display still updates every frame.
# At 30 FPS this gives 2 OCR calls/sec for the hit counter check.
OCR_SAMPLE_EVERY_N_FRAMES: int = 15

# Hit counter ROI — the integer "N HITS" displayed top-left of the Rapsodo UI.
# Monitored every OCR_SAMPLE_EVERY_N_FRAMES; a detected increment triggers
# a one-shot metrics OCR after NEW_HIT_WAIT_MS stabilisation delay.
# (x, y, w, h) as fractions of frame dimensions — approximate, tune if needed.
HIT_COUNTER_ROI: tuple[float, float, float, float] = (0.02, 0.24, 0.16, 0.08)

# Pitch counter ROI — the "N PITCH" / "N PITCHES" label in the pitching UI.
# Mirrors HIT_COUNTER_ROI role. Height trimmed from an initial 0.08 to
# 0.04 after the first probe-pitcher-counter run showed the Next-row
# underneath was being included and confusing the layout analyzer.
# Width also trimmed from 0.13 to 0.10 to drop stale trailing letters.
# Y adjusted from 0.25 to 0.27 to center the text row.
PITCH_COUNTER_ROI: tuple[float, float, float, float] = (0.13, 0.27, 0.10, 0.04)

# Milliseconds to wait after a hit counter increment before reading metrics.
# Allows the Rapsodo animation to finish so the numbers are stable.
# Fix 19: this is now the MINIMUM wait — actual trigger waits for
# frame stability detection (see STABILITY_* constants below).
NEW_HIT_WAIT_MS: int = 1500

# ---------------------------------------------------------------------------
# Fix 19 — Frame stability detection (replaces fixed-delay trigger)
# ---------------------------------------------------------------------------
# After counter increment + minimum wait, Producer monitors the metrics
# ROI area for pixel-level stability. Trigger fires only when N consecutive
# frames show negligible change (absdiff variance < threshold).

# Number of consecutive stable frames required before triggering OCR.
STABILITY_REQUIRED_FRAMES: int = 5

# Maximum variance of cv2.absdiff between consecutive frames in the
# metrics ROI. Below this threshold, the frame pair is considered "stable".
STABILITY_VARIANCE_THRESHOLD: float = 50.0

# Maximum frames to wait for stability after minimum delay. If stability
# is not reached within this window, trigger anyway (fallback to prevent
# infinite wait on noisy video).
STABILITY_MAX_WAIT_FRAMES: int = 90  # 3 seconds at 30 FPS

# Fix 25 — multi-frame trigger vote: after stability is reached, collect
# this many consecutive stable frames for OCR. Each frame is OCR'd
# independently and the final metrics are determined by majority vote
# across frames. This counters Tesseract's frame-to-frame digit
# confusion (e.g. "7" ↔ "1") caused by video compression artifacts.
TRIGGER_VOTE_FRAMES: int = 3

# Minimum fraction of dark pixels in a binarised ROI crop required for the
# crop to be considered "populated" (not showing --- or empty).
# Used by InvalidDetector's pixel-ratio method.
INVALID_DARK_PIXEL_THRESHOLD: float = 0.02

# ---------------------------------------------------------------------------
# Hit counter robustness (Fix 2 — option D: PSM vote + sanity check)
# ---------------------------------------------------------------------------
# Maximum allowed positive delta between consecutive HitCounterReader reads.
# Values beyond `_last_hit_count + HIT_COUNTER_MAX_JUMP` are rejected as
# OCR misreads (e.g. "4" → "44" or "3" → "5" observed in run_log2.txt).
#
# Set to 1 after Fix 2 verification: at 30 fps the counter is sampled every
# OCR_SAMPLE_EVERY_N_FRAMES (= 15, i.e. 0.5s), while real hits are always
# several seconds apart. There is therefore no legal scenario where the
# counter increments by more than 1 between two samples. Any `+2` or above
# must be an OCR misread, and the previous value of 2 let a single
# "3 → 5" misread at frame 1425 silently swallow hits #4 and #5.
HIT_COUNTER_MAX_JUMP: int = 1

# Pitching counter MAX_JUMP — more lenient than hitting because the
# pitching counter ROI has smaller font and produces more OCR misses,
# so the observed counter often jumps by 2-3 between successful reads.
# Set to 3 after analysis of ScreenRecording pitcher video: counter
# reads are {1, 2, 5, 4, 5, 6} over 6000 frames, with many None gaps.
# MAX_JUMP=1 blocks everything after the 1→2 transition.
# Fix 17: tightened from 3 to 1 — with backward recovery and 3/5
# disambiguation in Phase 1, the counter should now read reliably and
# a +3 jump is almost certainly a misread that skips two balls.
PITCH_COUNTER_MAX_JUMP: int = 1

# Fix 17 — backward recovery: if the counter baseline was inflated by a
# misread (e.g. 3→5), consecutive readings at a LOWER value should
# eventually override the baseline. After this many consecutive samples
# all reading the same backward value, trust it and reset baseline.
# This prevents a single misread from permanently blocking detection.
COUNTER_BACKWARD_RECOVERY_STREAK: int = 3

# Minimum number of PSM modes (out of OCR_PSM_FALLBACK_MODES) that must
# agree on the same integer for HitCounterReader to accept the reading.
# With the default 3 PSMs this requires a 2/3 majority, which rejects
# single-PSM misreads such as PSM 7 reading "3" as "5".
HIT_COUNTER_VOTE_MIN_AGREEMENT: int = 2

# Maximum plausible counter value for pitch counter EasyOCR extraction.
# EasyOCR reads the full ROI (e.g. "3818" for counter=3 with "PITCHES"
# label noise). The leading 1–2 digit prefix is the counter; values
# beyond this limit are treated as noise.
PITCH_COUNTER_MAX_VALUE: int = 50

# Minimum EasyOCR confidence for counter digit extraction. Below this
# threshold the detection is likely OCR noise from the label text.
PITCH_COUNTER_EASYOCR_MIN_CONF: float = 0.05
