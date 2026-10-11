"""JSON persistence shared by replay and analysis; no Qt or model dependency."""
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np


def json_value(value):
    if isinstance(value, np.ndarray):
        return json_value(value.tolist())
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def read_optional_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def atomic_json(path, data):
    """A failed serialization/write must not truncate an existing analysis."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(json_value(data), ensure_ascii=False, indent=2, allow_nan=False)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.stem + ".", suffix=".tmp", delete=False) as file:
            temporary = file.name
            file.write(payload)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def save_pose_records(path, records):
    """Keep unvisited frames on a partial reanalysis, and never erase with []."""
    if not records:
        return False
    old = read_optional_json(path, [])
    if not isinstance(old, list):
        raise ValueError(f"Existing pose JSON is not a record list: {path}")
    # A newly analyzed frame replaces all people in that frame; old people must
    # not survive as duplicate/ghost detections after selection or retracking.
    frames = {int(row["frame_number"]) for row in records}
    merged = [r for r in old if int(r["frame_number"]) not in frames] + records
    merged.sort(key=lambda r: int(r["frame_number"]))
    atomic_json(path, merged)
    return True
