"""Metric stride measurement using FM_8POINT and chessboard scale recovery."""

# Support both python -m and direct file execution from any working directory.
import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))

from Src.analysis.stride_workflow import entrypoint

if __name__ == "__main__":
    entrypoint("8point")
