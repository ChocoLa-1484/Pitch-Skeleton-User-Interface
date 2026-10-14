# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------
CAMERA_DEVICE_INDEX: int = 0
CAPTURE_FPS: int = 1

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
OUTPUT_JSON_PATH: str = r"C:\rapsodo-metrics-ocr\rapsodo_data.json"
OUTPUT_JSON_PITCHING: str = r"C:\rapsodo-metrics-ocr\rapsodo_data_pitching.json"
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
# This is the default single-PSM form used by PitchCounterReader.
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
# ---------------------------------------------------------------------------
# Pitching ROIs
# ---------------------------------------------------------------------------
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
    # V-BREAK / H-BREAK 顯示在中央地圖正下方，文字格式 'V: -5.9"' / 'H: 17.0"'（帶正負號）。
    # 座標來源：debug/calibrate_vh.py 對 Rapsodo_Pitcher_20260412_V1.mp4 frame 3000
    # (3840×2160 原始影片尺寸) 校準。使用 EasyOCR 直接定位 + production pipeline
    # 反向驗證。ROI 完整框住 'V:-5.9"' / 'H:17.0"'，依靠 Tesseract whitelist
    # '0123456789.-' 過濾 V/H/:/" 字元。
    # 跨幀驗證：
    #   frame 3000 (V=-5.9, H=17.0): 兩欄位 err=0.00 ✓
    #   frame 6000 (V=20.1, H=20.3): 兩欄位 err=0.00 ✓（6 大欄位全對）
    "v_break_in":          (0.491, 0.524, 0.052, 0.043),
    "h_break_in":          (0.560, 0.525, 0.051, 0.042),
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
    # V-BREAK / H-BREAK 為帶正負號的英寸值。Rapsodo 顯示範圍最常落在
    # ±30" 內（投手實際球路位移），保留 ±35 緩衝避免極端球被誤拒。
    # OCR 輸出若帶 "-" 號需保留（pipeline 需支援負號 whitelist）。
    "v_break_in":          (-35.0, 35.0),
    "h_break_in":          (-35.0, 35.0),
}

# Fix 31 — 球速單位雙模式。Rapsodo iPad UI 可切 MPH / KPH，台灣棒球界
# 慣用 KPH（113.3 kph ≈ 70 mph）。若沿用 mph 範圍守門，kph 模式下：
#   - 65..110 kph (~40..68 mph) 的球會被「誤當 mph 值寫入」→ SILENT_WRONG
#   - 111..180 kph (~69..112 mph) 的球被範圍擋掉 → 全 null
# 解法：依使用者選擇的單位套用對應範圍，JSON key 亦切換。
# kph 範圍 (65, 180) 來源：mph 範圍 (40, 110) × 1.60934，保留邊界緩衝。
PITCHING_VELOCITY_RANGE_MPH: tuple[float, float] = (40.0, 110.0)
PITCHING_VELOCITY_RANGE_KPH: tuple[float, float] = (65.0, 180.0)

# Fix 33 — V/H break 單位雙模式。Rapsodo iPad UI 可切 inch / cm，
# 台灣使用者（V3 影片）顯示 "V: 27.5cm / H: 41.0cm"，原 ±35 in 範圍
# 碰到 cm 會部分值被擋、部分值落在範圍內造成單位謊報 (SILENT_WRONG)。
# 解法：依使用者選擇的單位套用對應範圍，JSON key 亦切 _in / _cm。
# cm 範圍 (-90, 90) 來源：in 範圍 (-35, 35) × 2.54，保留邊界緩衝。
PITCHING_BREAK_RANGE_INCH: tuple[float, float] = (-35.0, 35.0)
PITCHING_BREAK_RANGE_CM: tuple[float, float] = (-90.0, 90.0)

# Fix 34 — 單位換算常數。拆「source」（Rapsodo UI 顯示單位，OCR 實際讀到
# 的數字）與「output」（JSON 寫出單位）為獨立兩層：source 決定 VALIDATION
# range 守門；output 決定 JSON key 尾綴，兩者不同時在 DataWriter 做乘法
# 換算後寫入。常數取 NIST 標準定義，避免估算誤差累積。
MPH_PER_KPH: float = 0.621371192237334   # 1 kph = 0.621371 mph
KPH_PER_MPH: float = 1.609344             # 1 mph = 1.609344 kph
INCH_PER_CM: float = 0.3937007874015748   # 1 cm = 0.393701 in
CM_PER_INCH: float = 2.54                 # 1 in = 2.54 cm

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

# Pitch counter ROI — the "N PITCH" / "N PITCHES" label in the pitching UI.
# Height trimmed to 0.04 because probe-pitcher-counter showed the next row
# underneath was being included and confusing the layout analyzer.
# Width trimmed to 0.10 to drop stale trailing letters.
# Y adjusted to 0.27 to center the text row.
PITCH_COUNTER_ROI: tuple[float, float, float, float] = (0.13, 0.27, 0.10, 0.04)

# Milliseconds to wait after a counter increment before reading metrics.
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
# Counter robustness (Fix 2 — PSM vote + sanity check)
# ---------------------------------------------------------------------------
# Maximum allowed positive delta between consecutive counter reads.
# Values beyond `_last_hit_count + PITCH_COUNTER_MAX_JUMP` are rejected as
# OCR misreads. Fix 17: tightened from 3 to 1 — with backward recovery and
# 3/5 disambiguation in Phase 1 the counter reads reliably, so a +2 or
# above jump is almost certainly a misread.
PITCH_COUNTER_MAX_JUMP: int = 1

# Fix 17 — backward recovery: if the counter baseline was inflated by a
# misread (e.g. 3→5), consecutive readings at a LOWER value should
# eventually override the baseline. After this many consecutive samples
# all reading the same backward value, trust it and reset baseline.
# This prevents a single misread from permanently blocking detection.
COUNTER_BACKWARD_RECOVERY_STREAK: int = 3

# Maximum plausible counter value for pitch counter EasyOCR extraction.
# EasyOCR reads the full ROI (e.g. "3818" for counter=3 with "PITCHES"
# label noise). The leading 1–2 digit prefix is the counter; values
# beyond this limit are treated as noise.
PITCH_COUNTER_MAX_VALUE: int = 50

# Minimum EasyOCR confidence for counter digit extraction. Below this
# threshold the detection is likely OCR noise from the label text.
PITCH_COUNTER_EASYOCR_MIN_CONF: float = 0.05
