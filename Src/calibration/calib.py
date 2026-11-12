"""
相機內參校正工具（單台相機，棋盤格法）

內參只跟「相機機身 + 鏡頭」有關，與安裝位置無關，所以一台一台單獨校正，
不需要雙機同步或 GPIO 觸發線。

使用方式（專案根目錄）：
    python Src/calibration/calib.py

左欄依序設定：
    1. 校正方法：即時拍攝，或讀取已拍好的照片資料夾
    2. 相機：即時拍攝時連接相機；讀照片時填照片所屬的相機序號
    3. 儲存位置：左投／右投、CF（正面）／CS（側面）
    4. 棋盤格規格（內角點數與每格邊長）
    5. 鏡頭焦距（選填）：只用來檢查 fx，以及長焦時鎖住畸變
    6. 擷取與校正：每隔幾秒自動擷取（板子拿穩才拍），也可按空白鍵手動擷取

右欄是預覽畫面，下方顯示還缺哪些位置、角度；樣本夠了按「執行校正」。

輸出（<日期> 為拍攝當天，讀照片時沿用照片資料夾的日期）：
    Db/Calibration/Intrinsics/Intrinsic_<日期>/Right_CF|Right_CS|Left_CF|Left_CS/<序號>_<日期>_<隨機碼>/  每次拍攝的照片
    Db/Calibration/Intrinsics/Intrinsic_<日期>/Intrinsics_all.json   當天校正的相機（內參、品質、每張照片的誤差與傾斜角）
Intrinsics/ 底下沒有總表；3D 與錄影讀內參時合併所有日期資料夾，同一台相機取最新的一次。
"""

# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "True")

import cv2
import numpy as np
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QPushButton, QLabel, QTextEdit,
                             QComboBox, QSpinBox, QDoubleSpinBox, QFrame, QScrollArea,
                             QMessageBox, QFileDialog, QLineEdit, QProgressDialog,
                             QCheckBox, QButtonGroup, QProgressBar, QSizePolicy)
from PyQt5.QtCore import QTimer, Qt, QUrl
from PyQt5.QtGui import QImage, QPixmap, QDesktopServices, QFont

import PySpin
from Src.cameras.single_camera.flir_camera_system import FlirCameraSystem
from Src.calibration import calib_store
from Src.calibration.board_defaults import DEFAULT_PATTERN, DEFAULT_SQUARE_MM

from Src.paths import CAMERA_CONFIG_DIR, DB_DIR
CONFIG_PATH = str(CAMERA_CONFIG_DIR / "GH3_camera_config.yaml")

IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff'}
_SB_FLAGS = cv2.CALIB_CB_NORMALIZE_IMAGE | getattr(cv2, "CALIB_CB_ACCURACY", 0)
_SUBPIX_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)


def list_cameras():
    """列舉目前連接的所有 FLIR 相機，回傳 [(序號, 型號), ...]"""
    result = []
    system = PySpin.System.GetInstance()
    try:
        cam_list = system.GetCameras()
        for i in range(cam_list.GetSize()):
            cam = cam_list.GetByIndex(i)
            try:
                tl = cam.TLDevice
                sn = tl.DeviceSerialNumber.GetValue()
                model = tl.DeviceModelName.GetValue()
            except Exception:
                sn, model = f"<index {i}>", "unknown"
            result.append((sn, model))
            del cam
        cam_list.Clear()
    finally:
        system.ReleaseInstance()
    return result


# ---------- 角點偵測與板子姿態（不含 Qt，背景執行緒也可呼叫） ----------

def _gray(image):
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)


def find_chessboard_corners(image, pattern):
    """全解析度角點，回傳 (N, 1, 2) float32；找不到回傳 None。

    findChessboardCornersSB 對模糊與光線不均比傳統 findChessboardCorners 穩定，角點本身就是次像素。
    全解析度找不到時依序改用窮舉模式、半解析度（大格子在全解析度有時反而找不到），
    半解析度的結果再用 cornerSubPix 在全解析度細修。
    """
    gray = _gray(image)
    for flags in (_SB_FLAGS, _SB_FLAGS | cv2.CALIB_CB_EXHAUSTIVE):
        found, corners = cv2.findChessboardCornersSB(gray, pattern, flags=flags)
        if found:
            return np.asarray(corners, dtype=np.float32).reshape(-1, 1, 2)
    small = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    found, corners = cv2.findChessboardCornersSB(small, pattern, flags=_SB_FLAGS)
    if not found:
        return None
    corners = ((np.asarray(corners, dtype=np.float32) + 0.5) * 2 - 0.5).reshape(-1, 1, 2)
    return cv2.cornerSubPix(gray, corners, (5, 5), (-1, -1), _SUBPIX_CRITERIA)


def detect_preview_corners(image, pattern, scale):
    """預覽用：縮圖偵測（約 20 ms），座標換回原解析度；找不到回傳 None。"""
    small = cv2.resize(_gray(image), None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    found, corners = cv2.findChessboardCornersSB(small, pattern, flags=cv2.CALIB_CB_NORMALIZE_IMAGE)
    if not found:
        return None
    return ((np.asarray(corners, dtype=np.float32) + 0.5) / scale - 0.5).reshape(-1, 1, 2)


def board_geometry(corners, pattern, image_size):
    """估計單組樣本的位置、傾斜與大小，給擷取指引用（不需要內參）。

    以仿射近似板子投影：兩個奇異值的比值 = cos(傾斜角)，被壓縮的影像方向就是傾斜方向
    （上下被壓扁＝板子上下傾斜）。長焦鏡頭透視很弱，這個近似反而更準。
    """
    cols, rows = pattern
    pts = np.asarray(corners, dtype=np.float64).reshape(-1, 2)
    grid = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2).astype(np.float64)
    design = np.c_[grid, np.ones(len(grid))]
    affine = np.linalg.lstsq(design, pts, rcond=None)[0][:2].T
    U, S, _ = np.linalg.svd(affine)
    tilt = float(np.degrees(np.arccos(np.clip(S[1] / S[0], 0, 1)))) if S[0] > 0 else 0.0
    squeeze = float(np.degrees(np.arctan2(abs(U[1, 1]), abs(U[0, 1]))))
    if tilt < CalibWindow.FRONTAL_MAX_DEG:
        kind = "frontal"
    elif tilt < CalibWindow.MIN_TILT_DEG:
        kind = "slight"
    elif squeeze >= 60:
        kind = "vertical"
    elif squeeze <= 30:
        kind = "horizontal"
    else:
        kind = "diagonal"
    w, h = image_size
    area = float(cv2.contourArea(cv2.convexHull(pts.astype(np.float32)))) / float(w * h)
    col = np.clip((pts[:, 0] / w * 3).astype(int), 0, 2)
    row = np.clip((pts[:, 1] / h * 3).astype(int), 0, 2)
    outline = pts[[0, cols - 1, len(pts) - 1, len(pts) - cols]]
    return {"tilt": tilt, "kind": kind, "area": area,
            "cells": set((row * 3 + col).tolist()), "outline": outline}


# ---------- 外觀 ----------

STYLE = """
QMainWindow, QWidget#root { background: #f3f5f9; }
QScrollArea { border: none; background: transparent; }
QWidget#leftPane { background: transparent; }
QFrame#card { background: #ffffff; border: 1px solid #e2e6ee; border-radius: 10px; }
QLabel#cardTitle { font-size: 14px; font-weight: 600; color: #1f2937; }
QLabel#stepBadge { background: #2563eb; color: white; border-radius: 11px; font-weight: 600;
                   min-width: 22px; max-width: 22px; min-height: 22px; max-height: 22px; }
QLabel#hint { color: #6b7280; font-size: 12px; }
QLabel#path { color: #374151; font-size: 12px; background: #f6f8fb; border: 1px solid #e5e7eb;
              border-radius: 6px; padding: 4px 6px; }
QLabel#status { font-size: 13px; font-weight: 600; padding: 4px 2px; }
QLabel#summary { font-size: 13px; color: #374151; }
QPushButton { padding: 6px 12px; border: 1px solid #cfd6e1; border-radius: 6px; background: #ffffff;
              color: #1f2937; }
QPushButton:hover { background: #eef3ff; border-color: #9db6f5; }
QPushButton:disabled { color: #a3abb8; background: #f3f4f6; border-color: #e5e7eb; }
QPushButton#primary { background: #2563eb; color: white; border: none; font-weight: 600; padding: 9px 14px; }
QPushButton#primary:hover { background: #1d4ed8; }
QPushButton#primary:disabled { background: #a9bff2; color: #eef2fb; }
QPushButton#record { background: #16a34a; color: white; border: none; font-weight: 600; padding: 8px 14px; }
QPushButton#record:hover { background: #15803d; }
QPushButton#record:checked { background: #dc2626; }
QPushButton#record:disabled { background: #a7d8b8; color: #f0faf3; }
QPushButton#seg { border-radius: 0px; padding: 7px 10px; }
QPushButton#seg:checked { background: #2563eb; color: white; border-color: #2563eb; font-weight: 600; }
QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit { padding: 4px 6px; border: 1px solid #cfd6e1;
              border-radius: 6px; background: #ffffff; min-height: 20px; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled { background: #f3f4f6; color: #8a93a3; }
QTextEdit#log { background: #ffffff; border: 1px solid #e2e6ee; border-radius: 8px; font-family: Consolas, 'Microsoft JhengHei UI';
                font-size: 12px; color: #374151; }
QProgressBar { border: 1px solid #d6dbe4; border-radius: 6px; background: #eef1f6; height: 14px; text-align: center;
               font-size: 11px; }
QProgressBar::chunk { background: #22c55e; border-radius: 5px; }
QLabel#preview { background: #111827; color: #9ca3af; border-radius: 10px; font-size: 15px; }
"""

CHIP_STYLE = {
    "ok": "background:#dcfce7; color:#166534; border:1px solid #86efac;",
    "missing": "background:#fef3c7; color:#92400e; border:1px solid #fcd34d;",
    "optional": "background:#f3f4f6; color:#6b7280; border:1px solid #e5e7eb;",
}


class CalibWindow(QMainWindow):
    # 預覽效能：全解析度偵測太慢，預覽改用縮圖且降低偵測頻率；實際擷取樣本仍用全解析度
    PREVIEW_SCALE = 0.4          # 偵測用縮圖比例
    PREVIEW_DETECT_EVERY = 3     # 每 N 次畫面更新才偵測一次

    # 自動擷取
    AUTO_INTERVAL_S = 3.0        # 預設間隔：移動板子並拿穩約需 2～3 秒
    AUTO_RETRY_S = 0.8           # 這次沒收（重複、找不到角點）時多久後再試
    AUTO_MIN_SHIFT_PX = 25.0     # 自動擷取時，與每一組既有樣本平均位移都要超過此值才收
    STABLE_PX = 6.0              # 預覽兩次偵測之間角點平均移動小於此值才算拿穩（避免模糊）

    # 品質判斷門檻
    RMS_GOOD = 0.5               # RMS 低於此值為良好
    RMS_MAX = 1.0                # RMS 高於此值判定不合格
    MIN_SAMPLES = 15             # 建議樣本數
    MIN_TILT_DEG = 20.0          # 傾斜超過此角度才算一個「傾斜」樣本
    FRONTAL_MAX_DEG = 12.0       # 傾斜小於此角度算「正對」
    NEAR_AREA = 0.20             # 板子占畫面比例 ≥ 此值算「近」
    FAR_AREA = 0.06              # 板子占畫面比例 ≤ 此值算「遠」
    DUPLICATE_SHIFT_PX = 5.0     # 與既有樣本平均位移小於此值視為重複
    PIXEL_SIZE_MM = 0.00586      # GS3-U3-23S6C 像素大小 5.86 µm，用來把鏡頭焦距換算成 fx
    TELEPHOTO_MM = 25.0          # 焦距達此值自動勾選「鎖住畸變」
    FX_TOLERANCE = 0.15          # fx 與焦距推算值相差超過 15% 就警告
    AXIS_MIN_TILT_DEG = 10.0     # 傾斜超過此角度的樣本才拿來看「往哪個方向轉」
    AXIS_CONCENTRATION = 0.8     # 轉軸方向集中度超過此值（1＝全部同一軸）就警告
    REGION_NAMES = ["左上", "上", "右上", "左", "中", "右", "左下", "下", "右下"]
    ANGLE_ITEMS = [("frontal", "正對", "板子正對相機（傾斜 < 12°）"),
                   ("vertical", "上下傾斜", "板子上緣往前或往後倒 ≥ 20°"),
                   ("horizontal", "左右傾斜", "板子往左或往右轉 ≥ 20°"),
                   ("diagonal", "斜向傾斜", "板子沿對角線方向轉 ≥ 20°")]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("相機內參校正")
        self.resize(1400, 920)

        self.camera = None
        self.connected_sn = None
        self.last_frame = None
        self.objpoints = []
        self.imgpoints = []
        self.captured_count = 0
        self.image_size = None
        self._sample_spec = None
        self._photo_paths = []
        self._live_saved = []         # 本次即時拍攝存下的照片（清空樣本時可一併刪除）
        self._session_date = None     # 輸出資料夾日期；讀照片時沿用照片資料夾的日期
        self._capture_dir = None      # 本次即時拍攝的 <序號>_<日期>_<隨機碼> 資料夾（存第一張時決定）
        self.board_fixed = False      # 校正精靈固定棋盤格規格時設為 True

        self._preview_tick = 0
        self._preview_found = False
        self._preview_corners = None
        self._preview_stable = False

        self._worker = ThreadPoolExecutor(max_workers=1)
        self._pending = None
        self.auto_running = False
        self._next_capture_at = 0.0

        self.initUI()
        self.refresh_cameras()
        self.update_guidance()

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_display)
        self.timer.start(60)
        self.auto_timer = QTimer()
        self.auto_timer.timeout.connect(self._auto_tick)

    # ---------- UI ----------

    def _card(self, step, title, subtitle=None, extra=None):
        card = QFrame()
        card.setObjectName("card")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(12, 9, 12, 10)
        outer.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        badge = QLabel(str(step))
        badge.setObjectName("stepBadge")
        badge.setAlignment(Qt.AlignCenter)
        name = QLabel(title)
        name.setObjectName("cardTitle")
        head.addWidget(badge)
        head.addWidget(name)
        head.addStretch()
        if extra is not None:
            head.addWidget(extra)
        outer.addLayout(head)
        if subtitle:
            outer.addWidget(self._hint(subtitle))
        return card, outer

    @staticmethod
    def _hint(text):
        label = QLabel(text)
        label.setObjectName("hint")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _button(text, name=None, checkable=False):
        button = QPushButton(text)
        if name:
            button.setObjectName(name)
        button.setCheckable(checkable)
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)   # 空白鍵留給「手動擷取」
        return button

    def _segmented(self, options):
        row = QHBoxLayout()
        row.setSpacing(0)
        group = QButtonGroup(self)
        buttons = {}
        for key, text in options:
            button = self._button(text, "seg", checkable=True)
            group.addButton(button)
            row.addWidget(button, 1)
            buttons[key] = button
        group.setExclusive(True)
        return row, group, buttons

    def initUI(self):
        self.setStyleSheet(STYLE)
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        main = QHBoxLayout(root)
        main.setContentsMargins(14, 14, 14, 14)
        main.setSpacing(14)

        # ---- 左欄：設定 ----
        left = QWidget()
        left.setObjectName("leftPane")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        left_layout.setSpacing(8)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(left)
        scroll.setFixedWidth(410)
        main.addWidget(scroll)

        # 1. 校正方法
        self.method_card, lay = self._card(1, "校正方法")
        row, self.method_group, self.method_buttons = self._segmented(
            [("live", "即時拍攝"), ("photo", "讀取照片資料夾")])
        self.method_buttons["live"].setChecked(True)
        self.method_group.buttonClicked.connect(lambda _: self._apply_method())
        lay.addLayout(row)
        left_layout.addWidget(self.method_card)

        # 2. 相機
        self.camera_card, lay = self._card(2, "選擇相機")
        self.cmb_camera = QComboBox()
        self.cmb_camera.currentIndexChanged.connect(self._camera_selection_changed)
        lay.addWidget(self.cmb_camera)
        self.live_camera_row = QWidget()
        cam_row = QHBoxLayout(self.live_camera_row)
        cam_row.setContentsMargins(0, 0, 0, 0)
        self.btn_refresh = self._button("重新掃描")
        self.btn_connect = self._button("連接", "primary")
        self.btn_disconnect = self._button("中斷連接")
        self.btn_refresh.clicked.connect(self.refresh_cameras)
        self.btn_connect.clicked.connect(self.connect_camera)
        self.btn_disconnect.clicked.connect(self.disconnect_camera)
        self.btn_disconnect.setEnabled(False)
        cam_row.addWidget(self.btn_refresh)
        cam_row.addWidget(self.btn_connect, 1)
        cam_row.addWidget(self.btn_disconnect)
        lay.addWidget(self.live_camera_row)
        self.photo_sn_row = QWidget()
        sn_row = QHBoxLayout(self.photo_sn_row)
        sn_row.setContentsMargins(0, 0, 0, 0)
        self.txt_photo_sn = QLineEdit()
        self.txt_photo_sn.setPlaceholderText("照片所屬的相機序號")
        self.txt_photo_sn.textChanged.connect(lambda _: self._update_paths())
        sn_row.addWidget(QLabel("相機序號"))
        sn_row.addWidget(self.txt_photo_sn, 1)
        lay.addWidget(self.photo_sn_row)
        left_layout.addWidget(self.camera_card)

        # 3. 儲存位置
        self.btn_open_dir = self._button("開啟資料夾")
        self.btn_open_dir.clicked.connect(self.open_output_dir)
        self.target_card, lay = self._card(3, "儲存至", extra=self.btn_open_dir)
        row, self.hand_group, self.hand_buttons = self._segmented([("right", "右投"), ("left", "左投")])
        self.hand_buttons["right"].setChecked(True)
        self.hand_group.buttonClicked.connect(lambda _: self._update_paths())
        lay.addLayout(row)
        row, self.view_group, self.view_buttons = self._segmented(
            [("CF", "CF 正面"), ("CS", "CS 側面")])
        self.view_buttons["CF"].setChecked(True)
        self.view_group.buttonClicked.connect(lambda _: self._update_paths())
        lay.addLayout(row)
        self.lbl_photo_dir = QLabel()
        self.lbl_photo_dir.setObjectName("path")
        self.lbl_photo_dir.setWordWrap(True)
        self.lbl_photo_dir.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_result_dir = QLabel()
        self.lbl_result_dir.setObjectName("path")
        self.lbl_result_dir.setWordWrap(True)
        self.lbl_result_dir.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # 換行後 QLabel 在捲動區裡不會自己長高，固定保留兩行（路徑已縮短到兩行內）
        two_lines = self.lbl_photo_dir.fontMetrics().lineSpacing() * 2 + 12
        for label in (self.lbl_photo_dir, self.lbl_result_dir):
            label.setMinimumHeight(two_lines)
            lay.addWidget(label)
        left_layout.addWidget(self.target_card)

        # 4. 棋盤格
        self.board_card, lay = self._card(4, "棋盤格規格", "內角點 = 格數 − 1（例：9 × 8 格的板子填 8 × 7）")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        self.spn_cols = QSpinBox()
        self.spn_cols.setRange(3, 30)
        self.spn_cols.setValue(DEFAULT_PATTERN[0])
        self.spn_rows = QSpinBox()
        self.spn_rows.setRange(3, 30)
        self.spn_rows.setValue(DEFAULT_PATTERN[1])
        self.spn_square = QDoubleSpinBox()
        self.spn_square.setRange(0.1, 1000.0)
        self.spn_square.setDecimals(2)
        self.spn_square.setValue(DEFAULT_SQUARE_MM)
        self.spn_square.setSuffix(" mm")
        grid.addWidget(QLabel("內角點 寬"), 0, 0)
        grid.addWidget(self.spn_cols, 0, 1)
        grid.addWidget(QLabel("高"), 0, 2)
        grid.addWidget(self.spn_rows, 0, 3)
        grid.addWidget(QLabel("每格邊長"), 1, 0)
        grid.addWidget(self.spn_square, 1, 1, 1, 3)
        lay.addLayout(grid)
        left_layout.addWidget(self.board_card)

        # 5. 鏡頭：焦距本身不參與計算，只用來檢查 fx；長焦鎖住畸變才會改變結果
        self.lens_card, lay = self._card(5, "鏡頭（選填）",
                                         "焦距只用來檢查算出的 fx 是否合理；長焦（≥ 25 mm）估不出畸變，"
                                         "會自動勾選鎖住畸變，這一項會影響結果。")
        lens_row = QHBoxLayout()
        self.spn_focal = QDoubleSpinBox()
        self.spn_focal.setRange(0.0, 500.0)
        self.spn_focal.setDecimals(1)
        self.spn_focal.setSpecialValueText("未填")
        self.spn_focal.setSuffix(" mm")
        self.chk_lock_dist = QCheckBox("鎖住畸變（係數固定為 0）")
        self.spn_focal.valueChanged.connect(
            lambda v: self.chk_lock_dist.setChecked(v >= self.TELEPHOTO_MM))
        lens_row.addWidget(QLabel("焦距"))
        lens_row.addWidget(self.spn_focal)
        lens_row.addWidget(self.chk_lock_dist, 1)
        lay.addLayout(lens_row)
        left_layout.addWidget(self.lens_card)

        # 6. 擷取與校正
        self.capture_card, lay = self._card(6, "擷取與校正")
        self.live_group = QWidget()
        live = QVBoxLayout(self.live_group)
        live.setContentsMargins(0, 0, 0, 0)
        interval_row = QHBoxLayout()
        self.spn_interval = QDoubleSpinBox()
        self.spn_interval.setRange(1.0, 10.0)
        self.spn_interval.setSingleStep(0.5)
        self.spn_interval.setDecimals(1)
        self.spn_interval.setValue(self.AUTO_INTERVAL_S)
        self.spn_interval.setSuffix(" 秒")
        interval_row.addWidget(QLabel("每隔"))
        interval_row.addWidget(self.spn_interval)
        interval_row.addWidget(QLabel("擷取一次（建議 3 秒）"))
        interval_row.addStretch()
        live.addLayout(interval_row)
        live.addWidget(self._hint("板子拿穩、且與已拍樣本明顯不同才會收下。"))
        live_buttons = QHBoxLayout()
        self.btn_auto = self._button("▶ 開始自動擷取", "record", checkable=True)
        self.btn_auto.toggled.connect(self._toggle_auto)
        self.btn_capture = self._button("手動擷取（空白鍵）")
        self.btn_capture.clicked.connect(self.capture_sample)
        live_buttons.addWidget(self.btn_auto, 1)
        live_buttons.addWidget(self.btn_capture)
        live.addLayout(live_buttons)
        lay.addWidget(self.live_group)

        self.photo_group = QWidget()
        photo = QVBoxLayout(self.photo_group)
        photo.setContentsMargins(0, 0, 0, 0)
        self.btn_import_photos = self._button("選擇照片資料夾…")
        self.btn_import_photos.clicked.connect(self.choose_photo_folder)
        self.lbl_photo_source = QLabel("尚未載入照片")
        self.lbl_photo_source.setObjectName("hint")
        self.lbl_photo_source.setWordWrap(True)
        photo.addWidget(self.btn_import_photos)
        photo.addWidget(self.lbl_photo_source)
        photo.addWidget(self._hint("每次選一台相機的資料夾。選本程式拍的 <序號>_<日期>_<隨機碼> 資料夾時，"
                                   "序號、左右投、CF／CS 與日期會自動帶入，結果存回同一天。"))
        lay.addWidget(self.photo_group)

        bottom = QHBoxLayout()
        self.btn_reset = self._button("清空樣本")
        self.btn_reset.clicked.connect(self.reset_samples)
        self.btn_calibrate = self._button("執行校正", "primary")
        self.btn_calibrate.clicked.connect(self.run_calibration)
        bottom.addWidget(self.btn_reset)
        bottom.addWidget(self.btn_calibrate, 1)
        lay.addLayout(bottom)
        left_layout.addWidget(self.capture_card)
        left_layout.addStretch()

        # ---- 右欄：畫面、指引、紀錄 ----
        right = QVBoxLayout()
        right.setSpacing(10)
        main.addLayout(right, 1)

        self.lbl_view = QLabel("尚未連接相機")
        self.lbl_view.setObjectName("preview")
        self.lbl_view.setAlignment(Qt.AlignCenter)
        self.lbl_view.setMinimumHeight(420)
        self.lbl_view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
        right.addWidget(self.lbl_view, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("status")
        right.addWidget(self.lbl_status)

        guide = QFrame()
        guide.setObjectName("card")
        guide_layout = QHBoxLayout(guide)
        guide_layout.setContentsMargins(14, 12, 14, 12)
        guide_layout.setSpacing(18)

        region_box = QVBoxLayout()
        title = QLabel("位置（畫面九宮格）")
        title.setObjectName("cardTitle")
        region_box.addWidget(title)
        region_grid = QGridLayout()
        region_grid.setSpacing(4)
        self.region_cells = []
        for i, name in enumerate(self.REGION_NAMES):
            cell = QLabel(name)
            cell.setAlignment(Qt.AlignCenter)
            cell.setFixedSize(68, 34)
            region_grid.addWidget(cell, i // 3, i % 3)
            self.region_cells.append(cell)
        region_box.addLayout(region_grid)
        region_box.addStretch()
        guide_layout.addLayout(region_box)

        angle_box = QVBoxLayout()
        title = QLabel("角度")
        title.setObjectName("cardTitle")
        angle_box.addWidget(title)
        angle_grid = QGridLayout()
        angle_grid.setSpacing(4)
        self.angle_chips = {}
        for i, (key, text, tip) in enumerate(self.ANGLE_ITEMS):
            chip = QLabel(text)
            chip.setAlignment(Qt.AlignCenter)
            chip.setFixedSize(104, 34)
            chip.setToolTip(tip)
            angle_grid.addWidget(chip, i // 2, i % 2)
            self.angle_chips[key] = chip
        angle_box.addLayout(angle_grid)
        title = QLabel("距離（建議）")
        title.setObjectName("cardTitle")
        angle_box.addWidget(title)
        distance_row = QHBoxLayout()
        distance_row.setSpacing(4)
        self.distance_chips = {}
        for key, text, tip in (("near", "近", f"板子占畫面 ≥ {self.NEAR_AREA:.0%}"),
                               ("far", "遠", f"板子占畫面 ≤ {self.FAR_AREA:.0%}")):
            chip = QLabel(text)
            chip.setAlignment(Qt.AlignCenter)
            chip.setFixedSize(104, 30)
            chip.setToolTip(tip)
            distance_row.addWidget(chip)
            self.distance_chips[key] = chip
        angle_box.addLayout(distance_row)
        angle_box.addStretch()
        guide_layout.addLayout(angle_box)

        summary_box = QVBoxLayout()
        title = QLabel("樣本")
        title.setObjectName("cardTitle")
        summary_box.addWidget(title)
        self.progress = QProgressBar()
        self.progress.setRange(0, self.MIN_SAMPLES)
        self.progress.setFormat("%v / %m 組")
        summary_box.addWidget(self.progress)
        self.lbl_summary = QLabel()
        self.lbl_summary.setObjectName("summary")
        self.lbl_summary.setWordWrap(True)
        self.lbl_summary.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        summary_box.addWidget(self.lbl_summary, 1)
        guide_layout.addLayout(summary_box, 1)
        right.addWidget(guide)

        self.log_area = QTextEdit()
        self.log_area.setObjectName("log")
        self.log_area.setReadOnly(True)
        self.log_area.setMinimumHeight(110)
        self.log_area.setMaximumHeight(170)
        right.addWidget(self.log_area)

        self._apply_method()

    def log(self, msg):
        # 最新訊息插在最上方，擷取成功與否不必捲動就看得到；多行訊息維持原本順序
        cursor = self.log_area.textCursor()
        cursor.movePosition(cursor.Start)
        cursor.insertText(f"[{datetime.now():%H:%M:%S}] {msg}\n")
        self.log_area.moveCursor(cursor.Start)

    def _set_status(self, text, color="#374151"):
        self.lbl_status.setText(text)
        self.lbl_status.setStyleSheet(f"color: {color};")

    # ---------- 方法、儲存位置 ----------

    def method(self):
        return "photo" if self.method_buttons["photo"].isChecked() else "live"

    def hand(self):
        return "left" if self.hand_buttons["left"].isChecked() else "right"

    def view(self):
        return "CS" if self.view_buttons["CS"].isChecked() else "CF"

    def _apply_method(self):
        photo = self.method() == "photo"
        if photo and self.camera is not None:
            self.disconnect_camera()
        self.live_camera_row.setVisible(not photo)
        self.photo_sn_row.setVisible(photo)
        self.live_group.setVisible(not photo)
        self.photo_group.setVisible(photo)
        if photo and not self.txt_photo_sn.text().strip() and self.cmb_camera.currentData():
            self.txt_photo_sn.setText(self.cmb_camera.currentData())
        if self.camera is None:
            self.lbl_view.setText("選擇照片資料夾後顯示最後一張有效照片" if photo else "尚未連接相機")
        self._update_paths()

    def _camera_selection_changed(self, _index):
        if self.method() == "photo" and self.cmb_camera.currentData():
            self.txt_photo_sn.setText(self.cmb_camera.currentData())
        self._update_paths()

    def _current_sn(self):
        if self.connected_sn:
            return self.connected_sn
        if self.method() == "photo":
            return self.txt_photo_sn.text().strip() or None
        return self.cmb_camera.currentData()

    def _output_date(self):
        return self._session_date or datetime.now().strftime("%Y%m%d")

    def photo_dir(self):
        return calib_store.intrinsic_photo_dir(self._output_date(), self.hand(), self.view())

    def result_dir(self):
        return calib_store.intrinsic_date_dir(self._output_date())

    @staticmethod
    def _short(path):
        try:
            parts = Path(path).resolve().relative_to(Path(DB_DIR).resolve().parent).parts
        except ValueError:
            parts = Path(path).parts
        if len(parts) > 5:   # 太深的路徑只留頭尾，完整路徑放在滑鼠提示
            parts = parts[:2] + ("…",) + parts[-3:]
        # 零寬空白讓長路徑能在 \ 與 _ 後換行，不把左欄撐寬
        return "\\".join(parts).replace("\\", "\\​").replace("_", "_​")

    def _update_paths(self):
        sn = self._current_sn() or "<序號>"
        if self.method() == "photo":
            source = self._photo_paths and str(Path(self._photo_paths[0]).parent)
            self.lbl_photo_dir.setText("照片：" + (self._short(source) if source else "選擇資料夾後顯示"))
            self.lbl_photo_dir.setToolTip(source or "")
        elif self._capture_dir:
            self.lbl_photo_dir.setText("照片：" + self._short(self._capture_dir))
            self.lbl_photo_dir.setToolTip(self._capture_dir)
        else:
            self.lbl_photo_dir.setText("照片：" + self._short(self.photo_dir()) + f"\\{sn}_<日期>_<隨機碼>")
            self.lbl_photo_dir.setToolTip("每次開始拍攝會建立新的 <序號>_<日期>_<隨機碼> 資料夾，同一天可以拍很多次")
        self.lbl_result_dir.setText(f"結果：{self._short(self.result_dir())}\\"
                                    f"{calib_store.DATED_INTRINSICS_NAME}")
        self.lbl_result_dir.setToolTip(self.result_dir())

    def open_output_dir(self):
        if self.method() == "live":
            path = self._capture_dir if self._capture_dir and os.path.isdir(self._capture_dir) else self.photo_dir()
        else:
            path = self.result_dir()
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ---------- 相機 ----------

    def refresh_cameras(self):
        self.cmb_camera.clear()
        try:
            cams = list_cameras()
        except Exception as e:
            self.log(f"掃描相機失敗: {e}")
            return

        if not cams:
            self.log("未偵測到任何相機。請確認相機已接上、且 SpinView 等程式已關閉。")
            return

        for sn, model in cams:
            self.cmb_camera.addItem(f"SN {sn}　{model}", userData=sn)
        self.log(f"偵測到 {len(cams)} 台相機。")

    def connect_camera(self):
        if self.camera is not None:
            self.log("已有相機連接中，請先中斷連接。")
            return

        sn = self.cmb_camera.currentData()
        if not sn:
            self.log("請先選擇相機。")
            return

        try:
            self.camera = FlirCameraSystem(CONFIG_PATH, sn)
            self.connected_sn = sn
            self.log(f"已連接相機 SN={sn}")
            self.btn_connect.setEnabled(False)
            self.btn_disconnect.setEnabled(True)
            self.cmb_camera.setEnabled(False)
            self.reset_samples()
        except Exception as e:
            self.camera = None
            self.log(f"連接失敗: {e}")
        self.update_guidance()

    def disconnect_camera(self):
        self._stop_auto()
        if self.camera is not None:
            try:
                self.camera.release()
            except Exception as e:
                self.log(f"釋放相機時發生問題: {e}")
            self.camera = None
        self.last_frame = None
        self._preview_found = False
        self.lbl_view.setText("已中斷連接")
        self.btn_connect.setEnabled(True)
        self.btn_disconnect.setEnabled(False)
        self.cmb_camera.setEnabled(True)
        self._set_status("")
        self.log("已中斷連接。")
        self.update_guidance()

    # ---------- 畫面 ----------

    def update_display(self):
        if self.camera is None:
            return

        try:
            ok, raw = self.camera.get_grayscale_image()
        except Exception as e:
            self.log(f"取得畫面失敗: {e}")
            return

        if not ok or raw is None:
            return

        # 相機輸出為 BayerRG8，轉成 BGR 才能顯示與偵測
        frame = cv2.cvtColor(raw, cv2.COLOR_BayerBG2BGR)
        self.last_frame = frame
        if self.image_size is None:
            h, w = frame.shape[:2]
            self.image_size = (w, h)

        self._preview_tick += 1
        pattern = (self.spn_cols.value(), self.spn_rows.value())
        if self._preview_tick % self.PREVIEW_DETECT_EVERY == 0:
            corners = detect_preview_corners(frame, pattern, self.PREVIEW_SCALE)
            previous = self._preview_corners
            self._preview_stable = (corners is not None and previous is not None
                                    and previous.shape == corners.shape
                                    and float(np.mean(np.linalg.norm(
                                        (corners - previous).reshape(-1, 2), axis=1))) < self.STABLE_PX)
            self._preview_found = corners is not None
            self._preview_corners = corners
            if not self.auto_running:
                if self._preview_found:
                    self._set_status("● 偵測到棋盤格，可以擷取", "#15803d")
                else:
                    self._set_status("○ 未偵測到完整棋盤格：整塊板子都要在畫面內", "#6b7280")

        display = frame.copy()
        self._draw_samples(display)
        if self._preview_found and self._preview_corners is not None:
            cv2.drawChessboardCorners(display, pattern, self._preview_corners, True)
        self.lbl_view.setPixmap(self.to_pixmap(display))

    def _draw_samples(self, image):
        """把已收的樣本外框畫在畫面上，看得出哪裡還沒拍到。"""
        if not self.imgpoints or self.image_size != (image.shape[1], image.shape[0]):
            return
        pattern = (self.spn_cols.value(), self.spn_rows.value())
        thickness = max(2, image.shape[1] // 640)
        for corners in self.imgpoints:
            if corners.reshape(-1, 2).shape[0] != pattern[0] * pattern[1]:
                continue
            outline = board_geometry(corners, pattern, self.image_size)["outline"]
            cv2.polylines(image, [outline.astype(np.int32).reshape(-1, 1, 2)], True,
                          (235, 180, 60), thickness, cv2.LINE_AA)

    def to_pixmap(self, bgr):
        # 先用 OpenCV 縮到顯示尺寸再交給 Qt，避免每幀搬運整張 1920x1084 的資料
        target_w = max(self.lbl_view.width(), 1)
        target_h = max(self.lbl_view.height(), 1)
        h, w = bgr.shape[:2]
        scale = min(target_w / w, target_h / h)
        if scale < 1.0:
            bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        rgb = np.ascontiguousarray(rgb)
        h, w, ch = rgb.shape
        qt_img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
        return QPixmap.fromImage(qt_img.copy())

    # ---------- 擷取 ----------

    def _board_spec(self):
        return (self.spn_cols.value(), self.spn_rows.value(), self.spn_square.value())

    @staticmethod
    def _find_sample_corners(frame, pattern):
        corners = find_chessboard_corners(frame, pattern)
        return None if corners is None else np.asarray(corners, dtype=np.float32).reshape(-1, 1, 2)

    def _object_points(self, pattern, square):
        objp = np.zeros((pattern[0] * pattern[1], 3), np.float32)
        objp[:, :2] = np.mgrid[0:pattern[0], 0:pattern[1]].T.reshape(-1, 2) * square
        return objp

    def _min_shift(self, corners):
        current = corners.reshape(-1, 2)
        shifts = [np.mean(np.linalg.norm(current - np.asarray(p).reshape(-1, 2), axis=1))
                  for p in self.imgpoints]
        return float(min(shifts)) if shifts else None

    def capture_sample(self):
        """手動擷取（空白鍵）：在目前畫面同步偵測角點。"""
        if self.camera is None or self.last_frame is None:
            self.log("尚未連接相機或畫面未就緒。")
            return False
        frame = self.last_frame
        if not self._can_accept(frame):
            return False
        corners = self._find_sample_corners(frame, (self.spn_cols.value(), self.spn_rows.value()))
        return self._accept_sample(frame, corners, self.DUPLICATE_SHIFT_PX)

    def _can_accept(self, frame):
        if self._sample_spec is not None and self._sample_spec != self._board_spec():
            self.log("棋盤格規格已變更，請恢復原規格或清空樣本後重新擷取。")
            return False
        size = (frame.shape[1], frame.shape[0])
        if self.imgpoints and self.image_size != size:
            self.log("影像尺寸與既有樣本不同，請清空樣本後重新擷取。")
            return False
        return True

    def _accept_sample(self, frame, corners, min_shift):
        """檢查並收下一組樣本，回傳是否收下。手動與自動擷取共用。"""
        if not self._can_accept(frame):
            return False
        if corners is None:
            self.log("未擷取：未完整偵測到棋盤格。請調整角度／距離，確認整塊板子都在畫面內。")
            return False
        corners = np.asarray(corners, dtype=np.float32).reshape(-1, 1, 2)
        pattern = (self.spn_cols.value(), self.spn_rows.value())
        if self.imgpoints and corners.shape[0] != np.asarray(self.imgpoints[0]).reshape(-1, 2).shape[0]:
            self.log("未擷取：棋盤格角點數與既有樣本不同，請恢復原規格或清空樣本後重拍。")
            return False
        # 板子沒移動就重複擷取，等於同一組資料算好幾次，會讓誤差假性偏低
        shift = self._min_shift(corners)
        if shift is not None and shift < min_shift:
            self.log(f"未擷取：與已拍的樣本幾乎相同（平均位移 {shift:.1f} px），請換位置或角度。")
            return False

        size = (frame.shape[1], frame.shape[0])
        self.objpoints.append(self._object_points(pattern, self.spn_square.value()))
        self.imgpoints.append(corners)
        self.captured_count += 1
        self.image_size = size
        self._sample_spec = self._board_spec()
        if not self._on_sample_accepted(frame):
            self.objpoints.pop()
            self.imgpoints.pop()
            self.captured_count -= 1
            if not self.imgpoints:
                self._sample_spec = None
            self.update_guidance()
            return False

        info = board_geometry(corners, pattern, size)
        self.log(f"已擷取第 {self.captured_count} 組｜傾斜 {info['tilt']:.0f}°｜占畫面 {info['area']:.0%}")
        self.update_guidance()
        return True

    def _on_sample_accepted(self, frame):
        """即時拍攝時把原始影像存進 Intrinsic_<日期>/Right_CF…/<序號>_<日期>_<隨機碼>/；存不了就不收這組。"""
        if self.camera is None:
            return True
        if self._session_date is None:
            self._session_date = datetime.now().strftime("%Y%m%d")
        if self._capture_dir is None:
            self._capture_dir = calib_store.new_capture_dir(self._session_date, self.hand(), self.view(),
                                                            self.connected_sn)
        folder = Path(self._capture_dir)
        # 序號已在資料夾名稱；檔名用組數＋時間，依檔名排序就是拍攝順序
        name = f"{self.captured_count:03d}_{datetime.now():%H%M%S_%f}"[:-3] + ".png"
        try:
            folder.mkdir(parents=True, exist_ok=True)
            ok, data = cv2.imencode(".png", frame)
            if not ok:
                raise OSError("PNG 編碼失敗")
            data.tofile(str(folder / name))   # tofile 支援中文路徑
        except (OSError, cv2.error) as exc:
            self.log(f"未收下本組：照片存檔失敗 {exc}")
            return False
        path = str((folder / name).resolve())
        self._photo_paths.append(path)
        self._live_saved.append(path)
        return True

    # ---------- 自動擷取 ----------

    def _toggle_auto(self, checked):
        if checked:
            if self.camera is None:
                self.log("請先連接相機再開始自動擷取。")
                self.btn_auto.setChecked(False)
                return
            self.auto_running = True
            self._next_capture_at = time.monotonic() + 1.0
            self.btn_auto.setText("■ 停止自動擷取")
            self.auto_timer.start(100)
            self.log(f"開始自動擷取：每 {self.spn_interval.value():g} 秒一次，板子拿穩才拍。")
        else:
            self._stop_auto(log=True)
        self.update_guidance()

    def _stop_auto(self, log=False):
        was_running = self.auto_running
        self.auto_running = False
        if hasattr(self, "auto_timer"):
            self.auto_timer.stop()
        self._pending = None
        if self.btn_auto.isChecked():
            self.btn_auto.blockSignals(True)
            self.btn_auto.setChecked(False)
            self.btn_auto.blockSignals(False)
        self.btn_auto.setText("▶ 開始自動擷取")
        if log and was_running:
            self.log(f"已停止自動擷取，目前 {self.captured_count} 組。")

    def _auto_tick(self):
        if not self.auto_running:
            return
        if self.camera is None:
            self._stop_auto()
            return
        now = time.monotonic()
        if self._pending is not None:
            future, frame = self._pending
            if not future.done():
                self._set_status("◌ 偵測角點中…", "#2563eb")
                return
            self._pending = None
            try:
                corners = future.result()
            except Exception as exc:
                corners = None
                self.log(f"偵測角點失敗：{exc}")
            accepted = self._accept_sample(frame, corners, self.AUTO_MIN_SHIFT_PX)
            self._next_capture_at = now + (self.spn_interval.value() if accepted else self.AUTO_RETRY_S)
            if accepted:
                self._set_status(f"✓ 已收下第 {self.captured_count} 組，請換位置或角度", "#15803d")
            return
        remaining = self._next_capture_at - now
        if remaining > 0:
            if not self.lbl_status.text().startswith("✓") or remaining < self.spn_interval.value() - 1.0:
                self._set_status(f"◷ {remaining:.1f} 秒後擷取", "#2563eb")
            return
        if self.last_frame is None or not self._preview_found:
            self._set_status("○ 等待棋盤格完整出現在畫面中…", "#b45309")
            return
        if not self._preview_stable:
            self._set_status("◌ 板子移動中，請拿穩…", "#b45309")
            return
        frame = self.last_frame.copy()
        if not self._can_accept(frame):
            self._stop_auto()
            return
        pattern = (self.spn_cols.value(), self.spn_rows.value())
        self._pending = (self._worker.submit(find_chessboard_corners, frame, pattern), frame)

    # ---------- 擷取指引 ----------

    def sample_summary(self):
        """統計已收樣本的位置、角度、距離，回傳給指引面板與報告使用。"""
        pattern = (self.spn_cols.value(), self.spn_rows.value())
        regions = [0] * 9
        angles = {key: 0 for key, _, _ in self.ANGLE_ITEMS}
        distance = {"near": 0, "far": 0}
        if self.image_size is not None:
            for corners in self.imgpoints:
                if np.asarray(corners).reshape(-1, 2).shape[0] != pattern[0] * pattern[1]:
                    continue
                info = board_geometry(corners, pattern, self.image_size)
                for cell in info["cells"]:
                    regions[cell] += 1
                if info["kind"] in angles:
                    angles[info["kind"]] += 1
                if info["area"] >= self.NEAR_AREA:
                    distance["near"] += 1
                if info["area"] <= self.FAR_AREA:
                    distance["far"] += 1
        missing_regions = [n for n, c in zip(self.REGION_NAMES, regions) if c == 0]
        missing_angles = [text for key, text, _ in self.ANGLE_ITEMS if angles[key] == 0]
        return {"regions": regions, "angles": angles, "distance": distance,
                "missing_regions": missing_regions, "missing_angles": missing_angles}

    def update_guidance(self):
        summary = self.sample_summary()
        for cell, name, count in zip(self.region_cells, self.REGION_NAMES, summary["regions"]):
            cell.setText(f"{name}\n{count}" if count else name)
            cell.setStyleSheet("border-radius:6px; font-size:12px; "
                               + CHIP_STYLE["ok" if count else "missing"])
        for key, chip in self.angle_chips.items():
            count = summary["angles"][key]
            text = next(t for k, t, _ in self.ANGLE_ITEMS if k == key)
            chip.setText(f"{text}  {count}" if count else text)
            chip.setStyleSheet("border-radius:6px; font-size:12px; "
                               + CHIP_STYLE["ok" if count else "missing"])
        for key, chip in self.distance_chips.items():
            count = summary["distance"][key]
            chip.setText(("近" if key == "near" else "遠") + (f"  {count}" if count else ""))
            chip.setStyleSheet("border-radius:6px; font-size:12px; "
                               + CHIP_STYLE["ok" if count else "optional"])

        n = self.captured_count
        self.progress.setMaximum(max(self.MIN_SAMPLES, n))
        self.progress.setValue(n)
        lines = []
        if summary["missing_regions"]:
            lines.append("缺位置：" + "、".join(summary["missing_regions"]))
        if summary["missing_angles"]:
            lines.append("缺角度：" + "、".join(summary["missing_angles"]))
        if n < self.MIN_SAMPLES:
            lines.append(f"樣本還差 {self.MIN_SAMPLES - n} 組")
        if n == 0:
            self.lbl_summary.setText("尚未擷取樣本。\n板子要拍到畫面四角與中央，並上下、左右、斜向各傾斜 20° 以上。")
        elif lines:
            self.lbl_summary.setText("\n".join(lines))
        else:
            self.lbl_summary.setText("✓ 位置、角度、數量都已足夠，可以按「執行校正」。")
        self._update_lock_state()
        self._update_paths()

    def _update_lock_state(self):
        # 有樣本後鎖住會改變輸出位置或樣本意義的設定，清空樣本才能改
        locked = self.captured_count > 0
        tip = "已有樣本；清空樣本後才能修改" if locked else ""
        for widget in (self.method_card, self.target_card):
            widget.setEnabled(not locked)
            widget.setToolTip(tip)
        self.board_card.setEnabled(not locked and not self.board_fixed)
        self.board_card.setToolTip(tip)
        self.txt_photo_sn.setEnabled(not locked)
        enough = self.captured_count >= 10
        self.btn_calibrate.setEnabled(enough)
        self.btn_calibrate.setToolTip("" if enough else "至少 10 組才能校正，建議 15 組以上")

    # ---------- 讀照片 ----------

    def choose_photo_folder(self):
        if self.camera is not None:
            self.log("請先中斷相機連接，再讀取照片資料夾。")
            return
        start = self.photo_dir()
        if not os.path.isdir(start):
            start = calib_store.INTRINSIC_DIR if os.path.isdir(calib_store.INTRINSIC_DIR) else calib_store.CALIB_DIR
        folder = QFileDialog.getExistingDirectory(self, "選擇單台相機的棋盤格照片資料夾", start)
        if not folder:
            return
        sn = self.txt_photo_sn.text().strip()
        if not sn:
            # 本程式拍的 <序號>_<日期>_<隨機碼> 資料夾自帶序號
            parsed = calib_store.parse_intrinsic_photo_dir(Path(folder).resolve())
            sn = (parsed and parsed[3]) or ""
        if not sn:
            self.log("這個資料夾看不出相機序號，請先填入照片所屬的相機序號。")
            self.txt_photo_sn.setFocus()
            return
        self.load_photo_folder(folder, sn)

    def load_photo_folder(self, folder, serial_number):
        """Stage a photo batch; cancellation/failure leaves existing samples intact."""
        if self.camera is not None:
            self.log("請先中斷相機連接，再讀取照片資料夾。")
            return False
        sn = str(serial_number).strip()
        if not sn or not sn.isascii() or not sn.isalnum():
            self.log("相機序號請使用英文字母或數字。")
            return False
        try:
            files = sorted(p for p in Path(folder).iterdir()
                           if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES)
        except OSError as exc:
            self.log(f"無法讀取照片資料夾：{exc}")
            return False
        if not files:
            self.log("資料夾內沒有照片；請選擇含 JPG／PNG／BMP／TIFF 的單台相機資料夾。")
            return False

        spec = self._board_spec()
        pattern, square = spec[:2], spec[2]
        points, paths, size, preview = [], [], None, None
        progress = QProgressDialog("正在讀取棋盤格照片…", "取消", 0, len(files), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        try:
            for index, path in enumerate(files):
                progress.setValue(index)
                progress.setLabelText(f"偵測棋盤格 {index + 1}/{len(files)}：{path.name}")
                QApplication.processEvents()
                if progress.wasCanceled():
                    self.log("已取消讀取，保留原有樣本。")
                    return False
                try:
                    # imdecode/fromfile supports Chinese Windows paths.
                    frame = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
                    if frame is None:
                        raise ValueError("無法解碼影像")
                    current_size = (frame.shape[1], frame.shape[0])
                    if size is not None and current_size != size:
                        raise ValueError(f"影像尺寸 {current_size} 與本批 {size} 不同")
                    corners = self._find_sample_corners(frame, pattern)
                    if corners is None:
                        raise ValueError("未完整偵測到棋盤格")
                    if points:
                        shift = min(np.mean(np.linalg.norm(
                            corners.reshape(-1, 2) - p.reshape(-1, 2), axis=1)) for p in points)
                        if shift < self.DUPLICATE_SHIFT_PX:
                            raise ValueError(f"與已讀的照片幾乎相同（位移 {shift:.1f} px）")
                    points.append(corners)
                    paths.append(str(path.resolve()))
                    size, preview = current_size, frame
                except (OSError, ValueError, cv2.error) as exc:
                    self.log(f"略過 {path.name}：{exc}")
            QApplication.processEvents()
            if progress.wasCanceled():
                self.log("已取消讀取，保留原有樣本。")
                return False
        finally:
            progress.close()
        if not points:
            self.log("沒有可用的棋盤格照片，保留原有樣本；請確認內角點數量與照片。")
            return False

        # 選的是 Intrinsic_<日期>/Right_CF[/<序號>_…] 時，左投右投、正側面與日期跟著照片
        parsed = calib_store.parse_intrinsic_photo_dir(Path(folder).resolve())
        if parsed:
            date, hand, view, folder_sn = parsed
            if folder_sn and folder_sn != sn:
                self.log(f"注意：資料夾名稱的序號是 {folder_sn}，與填入的 {sn} 不同；以填入的序號存檔。")
            self._session_date = date
            self.hand_buttons[hand].setChecked(True)
            self.view_buttons[view].setChecked(True)
        else:
            self._session_date = datetime.now().strftime("%Y%m%d")
        self.objpoints = [self._object_points(pattern, square) for _ in points]
        self.imgpoints = points
        self.captured_count = len(points)
        self.image_size = size
        self.connected_sn = sn
        self._sample_spec = spec
        self._photo_paths = paths
        self._live_saved = []
        self.last_frame = preview
        self.txt_photo_sn.setText(sn)
        self.lbl_photo_source.setText(f"SN{sn}｜{len(points)}/{len(files)} 張有效照片")
        self.lbl_photo_source.setToolTip(str(Path(folder).resolve()))
        display = preview.copy()
        self._draw_samples(display)
        cv2.drawChessboardCorners(display, pattern, points[-1], True)
        self.lbl_view.setPixmap(self.to_pixmap(display))
        self.update_guidance()
        covered, _ = self._coverage()
        self.log(f"已載入 SN{sn}：{len(points)}/{len(files)} 張有效照片，尺寸 {size}，"
                 f"覆蓋 {covered}/9 區。已取代先前樣本；按「執行校正」計算內參。")
        return True

    # ---------- 校正 ----------

    def _coverage(self):
        """把畫面切成 3x3 九宮格，回傳 (已覆蓋區數, 未覆蓋的區名)"""
        if self.image_size is None or not self.imgpoints:
            return 0, list(self.REGION_NAMES)
        w, h = self.image_size
        pts = np.vstack([np.asarray(c).reshape(-1, 2) for c in self.imgpoints])
        col = np.clip((pts[:, 0] / w * 3).astype(int), 0, 2)
        row = np.clip((pts[:, 1] / h * 3).astype(int), 0, 2)
        hit = set((row * 3 + col).tolist())
        missing = [name for i, name in enumerate(self.REGION_NAMES) if i not in hit]
        return len(hit), missing

    def _calibrate(self):
        """執行校正並計算每組樣本的誤差與傾斜角"""
        flags = 0
        if self.chk_lock_dist.isChecked():
            # 長焦鏡頭視角窄、幾乎沒有畸變，照片裡沒有足夠資訊估畸變，
            # 放著不鎖會估出不合理的係數（2026-10-08 正面 50 mm 的 k2 = 33.5），去畸變時反而把影像扭曲
            flags = (cv2.CALIB_FIX_K1 | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3
                     | cv2.CALIB_ZERO_TANGENT_DIST)
        rms, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(
            self.objpoints, self.imgpoints, self.image_size, None, None, flags=flags
        )
        per_image_rms = []
        tilts = []
        self._tilt_axes = []     # 每組板子的傾斜轉軸方向（度，影像平面內）
        for i in range(len(self.objpoints)):
            proj, _ = cv2.projectPoints(self.objpoints[i], rvecs[i], tvecs[i], mtx, dist)
            d = np.linalg.norm(np.asarray(self.imgpoints[i]).reshape(-1, 2) - proj.reshape(-1, 2), axis=1)
            per_image_rms.append(float(np.sqrt(np.mean(d ** 2))))
            # 板子法向量與相機光軸的夾角
            R, _ = cv2.Rodrigues(rvecs[i])
            tilts.append(float(np.degrees(np.arccos(min(1.0, abs(R[2, 2]))))))
            # 板子是繞哪個軸轉離正對相機：轉軸 = 光軸 × 板子法向量（落在影像平面內）
            normal = R[:, 2] * np.sign(R[2, 2] or 1.0)
            self._tilt_axes.append(float(np.degrees(np.arctan2(normal[0], -normal[1]))))
        return rms, mtx, dist, np.array(per_image_rms), np.array(tilts)

    def _assess(self, rms, per_image_rms, tilts, mtx=None):
        """依門檻判斷品質，回傳 (結論, 問題清單, 異常樣本索引)"""
        problems = []
        # RMS 小不代表 fx 對：長焦時 fx 與距離可以互相抵消，RMS 照樣很小，所以另外用鏡頭焦距檢查
        focal = self.spn_focal.value()
        if mtx is not None and focal > 0:
            expected = focal / self.PIXEL_SIZE_MM
            ratio = mtx[0, 0] / expected
            if abs(ratio - 1) > self.FX_TOLERANCE:
                problems.append(f"fx={mtx[0, 0]:.0f}，與 {focal:g} mm 鏡頭推算的 {expected:.0f} 相差 "
                                f"{(ratio - 1) * 100:+.0f}%：請確認鏡頭焦距，並讓板子往上下、左右不同方向傾斜 30° 以上（只朝同一方向轉會讓 fx 算不準）")
        bad = [i for i, e in enumerate(per_image_rms)
               if e > self.RMS_MAX and e > 2 * rms]

        if rms > self.RMS_MAX:
            verdict = "不合格，建議重拍"
            problems.append(f"整體誤差 {rms:.3f} px 超過 {self.RMS_MAX} px")
        elif rms > self.RMS_GOOD:
            verdict = "可用，但建議補拍改善"
            problems.append(f"整體誤差 {rms:.3f} px 介於 {self.RMS_GOOD}～{self.RMS_MAX} px")
        else:
            verdict = "良好"

        if bad:
            problems.append(f"{len(bad)} 組樣本誤差異常（第 {', '.join(str(i + 1) for i in bad)} 組），"
                            "可能是模糊或晃動")
        covered, missing = self._coverage()
        if missing:
            problems.append(f"畫面覆蓋 {covered}/9 區，缺：{'、'.join(missing)}（邊角沒拍到，畸變會校不準）")
        axis_concentration = self._axis_concentration(tilts)
        if axis_concentration is not None and axis_concentration > self.AXIS_CONCENTRATION:
            problems.append(f"板子幾乎都朝同一個方向轉（集中度 {axis_concentration:.2f}），fx 可能算不準："
                            "請讓板子上下、左右、斜向都傾斜（不要為了讓另一台相機看到而只朝一個方向轉）")
        if tilts.max() < self.MIN_TILT_DEG:
            problems.append(f"最大傾斜僅 {tilts.max():.0f}°，請加入傾斜 {self.MIN_TILT_DEG:.0f}° 以上的樣本")
        if self.captured_count < self.MIN_SAMPLES:
            problems.append(f"樣本僅 {self.captured_count} 組，建議至少 {self.MIN_SAMPLES} 組")

        if verdict == "良好" and problems:
            verdict = "良好，但有改善空間"
        return verdict, problems, bad

    def _axis_concentration(self, tilts):
        """傾斜轉軸方向的集中度（0～1）。轉軸沒有正負之分，所以角度先乘 2 再取平均向量長度；
        只朝同一方向轉（轉軸互相平行）時接近 1，這時焦距與距離分不開，fx 會算不準。"""
        axes = np.radians([a for a, t in zip(getattr(self, "_tilt_axes", []), tilts) if t >= self.AXIS_MIN_TILT_DEG])
        if len(axes) < 3:
            return None
        return float(np.hypot(np.mean(np.cos(2 * axes)), np.mean(np.sin(2 * axes))))

    def run_calibration(self):
        if self._sample_spec is not None and self._sample_spec != self._board_spec():
            self.log("棋盤格規格與樣本不符，請恢復原規格或重新讀取／擷取照片。")
            return
        if self.captured_count < 10:
            self.log(f"樣本僅 {self.captured_count} 組，建議至少 10 組不同角度再校正。")
            return
        if self.image_size is None:
            self.log("尚未取得影像尺寸。")
            return
        if not self.connected_sn:
            self.log("不知道這些樣本屬於哪台相機，請連接相機或填入序號後重新讀取照片。")
            return
        self._stop_auto(log=True)

        self.log("開始計算校正參數...")
        QApplication.processEvents()

        rms, mtx, dist, per_image_rms, tilts = self._calibrate()
        verdict, problems, bad = self._assess(rms, per_image_rms, tilts, mtx)

        # 有異常樣本時，詢問是否剔除後重算
        if bad and self.captured_count - len(bad) >= 10:
            reply = QMessageBox.question(
                self, "發現異常樣本",
                f"第 {', '.join(str(i + 1) for i in bad)} 組樣本誤差明顯偏高"
                f"（{', '.join(f'{per_image_rms[i]:.2f}' for i in bad)} px），"
                "可能是拍攝時模糊或晃動。\n\n要剔除這些樣本並重新計算嗎？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply == QMessageBox.Yes:
                for i in sorted(bad, reverse=True):
                    del self.objpoints[i]
                    del self.imgpoints[i]
                    if self._photo_paths:
                        del self._photo_paths[i]
                self.captured_count = len(self.imgpoints)
                self.update_guidance()
                self.log(f"已剔除 {len(bad)} 組異常樣本，剩 {self.captured_count} 組，重新計算...")
                QApplication.processEvents()
                rms, mtx, dist, per_image_rms, tilts = self._calibrate()
                verdict, problems, bad = self._assess(rms, per_image_rms, tilts, mtx)

        # 結果合成一則訊息，避免「最新在上」時多行順序顛倒
        report = (
            f"校正完成 SN={self.connected_sn}｜判定：{verdict}\n"
            f"  重投影誤差 RMS: {rms:.4f} px  (< {self.RMS_GOOD} 良好，> {self.RMS_MAX} 須重拍)\n"
            f"  單組誤差範圍: {per_image_rms.min():.3f} ~ {per_image_rms.max():.3f} px\n"
            f"  板子傾斜範圍: {tilts.min():.0f}° ~ {tilts.max():.0f}°\n"
            f"  fx={mtx[0,0]:.2f}  fy={mtx[1,1]:.2f}\n"
            f"  cx={mtx[0,2]:.2f}  cy={mtx[1,2]:.2f}\n"
            f"  畸變係數: {np.round(dist.ravel(), 6)}"
        )
        if problems:
            report += "\n  待改善：\n" + "\n".join(f"   - {p}" for p in problems)
        self.log(report)

        # 不合格時先確認，避免蓋掉之前較好的結果
        if rms > self.RMS_MAX:
            reply = QMessageBox.warning(
                self, "校正不合格",
                f"重投影誤差 {rms:.3f} px，超過 {self.RMS_MAX} px。\n\n"
                + "\n".join(f"・{p}" for p in problems)
                + "\n\n可能原因：棋盤格規格填錯、板子不平、樣本模糊。\n"
                "建議「清空樣本」後重拍。\n\n仍要儲存這次結果嗎？（會覆蓋這台相機之前的校正）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                self.log("未儲存本次結果。")
                return
        elif problems:
            QMessageBox.information(
                self, f"校正結果：{verdict}",
                f"重投影誤差 {rms:.3f} px。結果已儲存，但以下項目可以改善：\n\n"
                + "\n".join(f"・{p}" for p in problems))

        self._save_results(rms, mtx, dist, per_image_rms, tilts, verdict)

    def _save_results(self, rms, mtx, dist, per_image_rms, tilts, verdict):
        """寫入當天的 Intrinsics_all.json（不另存 npz；每張照片的誤差與傾斜角也在裡面）。"""
        if self._session_date is None:
            self._session_date = datetime.now().strftime("%Y%m%d")
        mean_err = float(np.mean(per_image_rms))
        entry = {
            "intrinsic_matrix": mtx.tolist(),
            "distortion_coefficients": dist.tolist(),
            "fx": float(mtx[0, 0]),
            "fy": float(mtx[1, 1]),
            "cx": float(mtx[0, 2]),
            "cy": float(mtx[1, 2]),
            "image_size": list(self.image_size),
            "rms": float(rms),
            "mean_reproj_err": mean_err,
            "verdict": verdict,
            "pattern": [self.spn_cols.value(), self.spn_rows.value()],
            "square_size_mm": self.spn_square.value(),
            "sample_count": self.captured_count,
            "distortion_locked": self.chk_lock_dist.isChecked(),
            "lens_focal_mm": self.spn_focal.value() or None,
            "pitch_hand": self.hand(),
            "view": self.view(),
            "detector": "findChessboardCornersSB",
            "calibrated_at": datetime.now().isoformat(timespec="seconds"),
            # 與 source_images 一一對應（剔除異常樣本後的順序），事後可查哪張照片拍得差
            "per_image_rms": [round(float(v), 4) for v in per_image_rms],
            "tilt_deg": [round(float(v), 1) for v in tilts],
        }
        if self._capture_dir:
            entry["capture_dir"] = self._capture_dir
        if self._photo_paths:
            entry["source_images"] = list(self._photo_paths)

        try:
            path, count = calib_store.save_dated_intrinsic(self._session_date, self.connected_sn, entry,
                                                           self.hand(), self.view())
            self.log(f"已寫入 {path}（當天共 {count} 台）；3D 與錄影會用這台最新的內參")
        except Exception as e:
            self.log(f"寫入當天的 {calib_store.DATED_INTRINSICS_NAME} 失敗：{e}")

    def reset_samples(self):
        self._stop_auto()
        if self._live_saved:
            reply = QMessageBox.question(
                self, "清空樣本",
                f"本次已存 {len(self._live_saved)} 張照片到\n{self._capture_dir}\n\n"
                "要一併刪除這些照片嗎？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                removed = 0
                for path in self._live_saved:
                    try:
                        os.remove(path)
                        removed += 1
                    except OSError:
                        pass
                try:
                    os.rmdir(self._capture_dir)   # 只有資料夾已清空時才會成功
                except OSError:
                    pass
                self.log(f"已刪除 {removed} 張本次照片。")
        self.objpoints = []
        self.imgpoints = []
        self.captured_count = 0
        self.image_size = None
        self._sample_spec = None
        self._photo_paths = []
        self._live_saved = []
        self._session_date = None
        self._capture_dir = None
        self.lbl_photo_source.setText("尚未載入照片")
        self.lbl_photo_source.setToolTip("")
        self.log("已清空樣本。")
        self.update_guidance()

    # ---------- 其他 ----------

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Space and self.method() == "live":
            self.capture_sample()
        elif event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        self.timer.stop()
        self._stop_auto()
        self._worker.shutdown(wait=False)
        if self.camera is not None:
            try:
                self.camera.release()
            except Exception:
                pass
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft JhengHei UI", 10))
    window = CalibWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
