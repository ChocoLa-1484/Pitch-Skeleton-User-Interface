"""
tools/headless_driver.py — Headless pipeline driver for benchmarking and
red-line validation.

Runs the full VideoProcessor + OCRWorker pipeline on a video file without
opening the PyQt UI. Records total wall time, EasyOCR initialisation time,
and per-trigger cycle timing so we can measure optimisation effects.

Usage:
    python tools/headless_driver.py <video_path> [--playback max|realtime]
        [--source-velocity-unit mph|kph] [--output-velocity-unit mph|kph]
        [--source-break-unit in|cm] [--output-break-unit in|cm]
        [--out <json_path>]

Default playback mode is "realtime" since the optimisation target is
"1x realtime ≤ video length".
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[4]))


import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from PyQt5.QtCore import QCoreApplication

from Src.rapsodo.RapsodoMetricsOCRV2.core.data_writer import DataWriter
from tools.video_player import (
    PLAYBACK_MODE_MAX,
    PLAYBACK_MODE_REALTIME,
    VideoProcessor,
)

logger = logging.getLogger(__name__)


def _make_writer(
    out_path: Path,
    vel_src: str, vel_out: str, brk_src: str, brk_out: str,
) -> DataWriter:
    """Create an isolated DataWriter for the benchmark run."""
    session_id = datetime.now().strftime("benchmark_%Y%m%d_%H%M%S")
    return DataWriter(
        str(out_path), mode="pitching", session_id=session_id,
        velocity_source_unit=vel_src, velocity_output_unit=vel_out,
        break_source_unit=brk_src, break_output_unit=brk_out,
    )


def run_once(
    video_path: str,
    playback: str,
    out_path: Path,
    velocity_source_unit: str = "mph",
    velocity_output_unit: str | None = None,
    break_source_unit: str = "in",
    break_output_unit: str | None = None,
) -> dict:
    """Run one full pipeline and return timing + output-entry metrics."""
    app = QCoreApplication(sys.argv)

    vel_out = velocity_output_unit or velocity_source_unit
    brk_out = break_output_unit or break_source_unit
    writer = _make_writer(
        out_path,
        velocity_source_unit, vel_out, break_source_unit, brk_out,
    )

    t_start = time.perf_counter()
    timings = {"trigger_cycles": []}

    processor = VideoProcessor(
        video_path=video_path,
        player_name="benchmark",
        data_writer=writer,
        playback_mode=playback,
        mode="pitching",
        velocity_source_unit=velocity_source_unit,
        break_source_unit=break_source_unit,
    )

    orig_wait = processor._wait_for_stability
    def _timed_wait(*args, **kwargs):
        ts = time.perf_counter()
        result = orig_wait(*args, **kwargs)
        elapsed = time.perf_counter() - ts
        timings.setdefault("_pending_cycle", {})["stability_s"] = elapsed
        return result
    processor._wait_for_stability = _timed_wait

    worker = processor.worker
    orig_handle = worker._handle_trigger_task
    def _timed_handle(task):
        ts = time.perf_counter()
        orig_handle(task)
        elapsed = time.perf_counter() - ts
        cycle = timings.pop("_pending_cycle", {})
        cycle["ocr_s"] = elapsed
        cycle["frame_index"] = task.frame_index
        cycle["num_frames"] = len(task.extra_frames) if task.extra_frames else 1
        timings["trigger_cycles"].append(cycle)
    worker._handle_trigger_task = _timed_handle

    from Src.rapsodo.RapsodoMetricsOCRV2.modes import base_ocr_reader as bor
    orig_get_easyocr = bor._get_easyocr_reader
    def _timed_get():
        if bor._easyocr_reader is None:
            ts = time.perf_counter()
            reader = orig_get_easyocr()
            timings["easyocr_init_s"] = time.perf_counter() - ts
            return reader
        return orig_get_easyocr()
    bor._get_easyocr_reader = _timed_get
    import modes.pitching as pitching_mod
    pitching_mod._get_easyocr_reader = _timed_get

    processor.finished.connect(app.quit)
    processor.start()
    app.exec_()
    processor.wait()

    t_end = time.perf_counter()
    timings["total_s"] = t_end - t_start

    try:
        with open(out_path, "r", encoding="utf-8") as f:
            output = json.load(f)
        timings["entries"] = len(output.get("entries", []))
    except FileNotFoundError:
        timings["entries"] = 0

    return timings


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser()
    parser.add_argument("video_path")
    parser.add_argument("--playback", default="realtime", choices=["max", "realtime"])
    parser.add_argument(
        "--source-velocity-unit", default="mph", choices=("mph", "kph"),
        help="Velocity unit displayed in Rapsodo iPad UI",
    )
    parser.add_argument(
        "--output-velocity-unit", default=None, choices=("mph", "kph"),
        help="Velocity unit to write into JSON (default: same as --source-velocity-unit)",
    )
    parser.add_argument(
        "--source-break-unit", default="in", choices=("in", "cm"),
        help="V/H break unit displayed in Rapsodo iPad UI",
    )
    parser.add_argument(
        "--output-break-unit", default=None, choices=("in", "cm"),
        help="V/H break unit to write into JSON (default: same as --source-break-unit)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path (default: debug/headless_pitching_<playback>.json)",
    )
    args = parser.parse_args()
    vel_out = args.output_velocity_unit or args.source_velocity_unit
    brk_out = args.output_break_unit or args.source_break_unit

    if args.out is None:
        out_dir = Path(__file__).parent.parent / "debug"
        out_dir.mkdir(exist_ok=True)
        args.out = str(out_dir / f"headless_pitching_{args.playback}.json")

    out_path = Path(args.out)
    if out_path.exists():
        out_path.unlink()

    playback = PLAYBACK_MODE_MAX if args.playback == "max" else PLAYBACK_MODE_REALTIME

    vel_lbl = args.source_velocity_unit if args.source_velocity_unit == vel_out else f"{args.source_velocity_unit}->{vel_out}"
    brk_lbl = args.source_break_unit if args.source_break_unit == brk_out else f"{args.source_break_unit}->{brk_out}"
    label = f"pitching, {args.playback}, vel={vel_lbl}, brk={brk_lbl}"
    print(f"=== Headless driver: {args.video_path} ({label}) ===")
    timings = run_once(
        args.video_path, playback, out_path,
        velocity_source_unit=args.source_velocity_unit,
        velocity_output_unit=vel_out,
        break_source_unit=args.source_break_unit,
        break_output_unit=brk_out,
    )

    print()
    print(f"Total wall time: {timings['total_s']:.2f}s")
    if "easyocr_init_s" in timings:
        print(f"EasyOCR init:    {timings['easyocr_init_s']:.2f}s")
    print(f"Entries written: {timings['entries']}")
    print(f"Trigger cycles:  {len(timings['trigger_cycles'])}")
    if timings["trigger_cycles"]:
        stab = [c.get("stability_s", 0) for c in timings["trigger_cycles"]]
        ocr = [c.get("ocr_s", 0) for c in timings["trigger_cycles"]]
        print(f"  stability avg:  {sum(stab)/len(stab):.2f}s (min {min(stab):.2f}s, max {max(stab):.2f}s)")
        print(f"  OCR avg:        {sum(ocr)/len(ocr):.2f}s (min {min(ocr):.2f}s, max {max(ocr):.2f}s)")

    timings_path = out_path.with_suffix(".timings.json")
    with open(timings_path, "w", encoding="utf-8") as f:
        json.dump(timings, f, indent=2)
    print(f"Output JSON:     {out_path}")
    print(f"Timings JSON:    {timings_path}")


if __name__ == "__main__":
    main()
