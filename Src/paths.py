"""Stable resource paths, independent of the launcher or working directory."""
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
ROOT_DIR = SRC_DIR.parent
UI_DIR = SRC_DIR / "UI_Control"
DB_DIR = ROOT_DIR / "Db"
# Db 底下的資料分類：Record 只放投球；其他輸出各自一個資料夾
RECORD_DIR = DB_DIR / "Record"                          # 每球錄影：<日期>_Pitcher<編號>/<日期_時分>_P<球號>
MANUAL_RECORD_DIR = DB_DIR / "ManualRecord"             # 「2D 相機」分頁手動錄影：<日期_時分>/
CALIB_PHOTO_DIR = DB_DIR / "Calibration" / "photos"     # 「2D 相機」分頁拍照：<日期_時分>/cf、cs
RAPSODO_DATA_DIR = DB_DIR / "Rapsodo"                   # Rapsodo 平板擷取與 OCR 輸出
CAMERA_CONFIG_DIR = SRC_DIR / "cameras" / "config"
RAPSODO_DIR = SRC_DIR / "rapsodo"
