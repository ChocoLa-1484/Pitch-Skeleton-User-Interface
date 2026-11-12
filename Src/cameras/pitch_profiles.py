"""Pitch-hand camera assignments and immutable per-recording calibration copies.

Profiles identify physical cameras; changing the pitching hand never swaps CF/CS
pixels or assumes a left rig is the mirror image of the right rig.
"""
import copy
import json
from pathlib import Path

import numpy as np

from Src.calibration import calib_store
from Src.analysis.analysis_io import atomic_json


PROFILE_NAME = "pitch_camera_profiles.json"
CONTEXT_NAME = "recording_context.json"


def profile_file():
    return Path(calib_store.CALIB_DIR) / PROFILE_NAME


def normalize_hand(hand):
    if hand not in ("left", "right"):
        raise ValueError("投球手別必須為 left 或 right。")
    return hand


def load_profiles():
    path = profile_file()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "pitch_camera_profiles_v1" or not isinstance(data.get("profiles"), dict):
        raise ValueError("左右投相機設定格式錯誤。")
    return data["profiles"]


def save_profile(hand, front, side, calibrations=None):
    hand = normalize_hand(hand)
    front, side = str(front).strip(), str(side).strip()
    if not front or not side or front == side:
        raise ValueError("請填入不同的正面 CF 與側面 CS 實體相機序號。")
    profiles = load_profiles()
    profiles[hand] = {"front": front, "side": side,
                      "calibrations": {k: str(Path(v).resolve()) for k, v in (calibrations or {}).items() if v}}
    atomic_json(profile_file(), {"schema": "pitch_camera_profiles_v1", "profiles": profiles})


def save_camera_assignments(assignments):
    """Save both hands together without changing their calibration settings."""
    profiles = load_profiles()
    for hand, (front, side) in assignments.items():
        normalize_hand(hand)
        front, side = str(front).strip(), str(side).strip()
        if not front and not side and hand not in profiles:
            continue  # An unused hand need not be configured yet.
        label = "左投" if hand == "left" else "右投"
        if not front or not side or front == side:
            raise ValueError(f"{label}請填入不同的正面 CF 與側面 CS 實體相機序號。")
        profile = profiles.setdefault(hand, {"calibrations": {}})
        profile.update(front=front, side=side)
    atomic_json(profile_file(), {"schema": "pitch_camera_profiles_v1", "profiles": profiles})


def get_profile(hand):
    hand = normalize_hand(hand)
    profile = load_profiles().get(hand)
    if profile is not None:
        front, side = str(profile.get("front", "")).strip(), str(profile.get("side", "")).strip()
        if not front or not side or front == side:
            raise ValueError("左右投設定中的 CF／CS 序號缺漏或重複。")
        return copy.deepcopy(profile)
    # Old installations only had one rig. Preserve it for right-handed use;
    # never infer the unknown left camera from enumeration order.
    if hand == "right":
        roles = calib_store.load_roles()
        if roles:
            return {"front": roles[0], "side": roles[1], "calibrations": {}}
    return None


def profile_serials(hand):
    profile = get_profile(hand)
    if profile:
        return profile["front"], profile["side"]
    if hand == "left":
        raise ValueError("尚未設定左投相機；請到「2D 相機 → 左右投相機設定」填入 CF／CS 序號。")
    return None


def calibration_path(hand, method):
    profile = get_profile(hand)
    configured = (profile or {}).get("calibrations", {}).get(method)
    if configured:
        return Path(configured)
    base = Path(calib_store.CALIB_DIR)
    # Existing right-hand defaults remain compatible. Left-hand defaults are
    # deliberately distinct and may not exist until that rig is calibrated.
    if hand == "left":
        base = base / "left"
    if method == "legacy":
        return base / "extrinsic" / "fundamental.json"
    return base / "chessboard" / method / "stereo.json"


def load_profile_legacy(hand, serials=None):
    serials = serials or profile_serials(hand)
    if not serials:
        return None
    front, side = serials
    path = calibration_path(hand, "legacy")
    if not path.exists():
        return None
    fund = json.loads(path.read_text(encoding="utf-8"))
    if (str(fund.get("front")), str(fund.get("side"))) != (front, side):
        raise ValueError("此手別的 F 矩陣與 CF／CS 相機序號不符，請選擇正確的外參檔。")
    # The same camera may be CF for both hands, so look up this hand's position first.
    intr_f = calib_store.get_intrinsic(front, hand, "CF")
    intr_s = calib_store.get_intrinsic(side, hand, "CS")
    if intr_f is None or intr_s is None:
        raise ValueError("此手別的相機尚未完成內參校正。")
    return {"front": front, "side": side, "K_F": intr_f[0], "dist_F": intr_f[1],
            "K_S": intr_s[0], "dist_S": intr_s[1], "F": np.asarray(fund["F"], dtype=float)}


def read_recording_context(folder):
    path = Path(folder) / CONTEXT_NAME
    if not path.exists():
        return None
    context = json.loads(path.read_text(encoding="utf-8"))
    normalize_hand(context.get("pitch_hand"))
    if context.get("schema") != "pitch_recording_v1":
        raise ValueError("錄影相機資訊格式錯誤。")
    return context


def recording_calibration(folder, method):
    context = read_recording_context(folder)
    if not context:
        return None
    name = context.get("calibrations", {}).get(method)
    if not name:
        return None
    path = Path(folder) / name
    if path.resolve().parent != Path(folder).resolve() or not path.is_file():
        raise ValueError("錄影校正快照遺失或路徑不正確。")
    return path


def save_recording_context(folder, hand, front, side):
    """Always save hand/serials, even when calibration is not available yet."""
    hand = normalize_hand(hand)
    context = {"schema": "pitch_recording_v1", "pitch_hand": hand,
               "front": front, "side": side, "calibrations": {}, "warnings": []}
    configured = get_profile(hand)
    if configured and (front, side) != (configured["front"], configured["side"]):
        raise ValueError("錄影相機序號與所選左右投設定不符。")
    for method in ("pnp", "8point", "legacy"):
        try:
            path = calibration_path(hand, method)
            if not path.is_file():
                continue
            if method == "legacy":
                data = load_profile_legacy(hand, (front, side))
                if data is None:
                    continue
                data = {k: v.tolist() if isinstance(v, np.ndarray) else v for k, v in data.items()}
                name = calib_store.SNAPSHOT_NAME
            else:
                from Src.analysis.metric_session import load_rig
                data = load_rig(path)
                if data["method"] != method:
                    raise ValueError("校正檔的 method 與設定不符。")
                for role, serial in (("cf", front), ("cs", side)):
                    known = data["intrinsics"][role].get("serial_number")
                    if known and str(known) != str(serial):
                        raise ValueError("校正檔相機序號與錄影相機不符。")
                data = json.loads(path.read_text(encoding="utf-8"))
                name = "calibration_" + method + ".json"
            atomic_json(Path(folder) / name, data)
            context["calibrations"][method] = name
        except (ValueError, KeyError, TypeError, OSError) as exc:
            context["warnings"].append(method + ": " + str(exc))
    atomic_json(Path(folder) / CONTEXT_NAME, context)
    return context
