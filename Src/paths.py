"""Stable resource paths, independent of the launcher or working directory."""
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
ROOT_DIR = SRC_DIR.parent
UI_DIR = SRC_DIR / "UI_Control"
DB_DIR = ROOT_DIR / "Db"
CAMERA_CONFIG_DIR = SRC_DIR / "cameras" / "config"
RAPSODO_DIR = SRC_DIR / "rapsodo"
