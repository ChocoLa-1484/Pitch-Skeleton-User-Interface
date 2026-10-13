"""Metric stride measurement using chessboard PnP and stereo refinement."""
import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))   # Src/UI_Control：讓 cv_utils 等模組可匯入
from cv_utils.stride_workflow import entrypoint

if __name__ == "__main__":
    entrypoint("pnp")
