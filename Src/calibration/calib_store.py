"""
校正資料的統一存放位置與讀寫函式。

所有校正產出都放在 Db/Calibration/ 底下（Db 不進 git）：

    Db/Calibration/
    ├─ camera_roles.json            哪台是正面、哪台是側面（校正精靈產生）
    ├─ sessions/<日期_時分秒>_<方法>_SN<正面>_SN<側面>/   每次校正的照片與紀錄（new_session_dir）
    ├─ photos/<日期_時分>/cf、cs/    「2D 相機」分頁拍的照片
    ├─ Intrinsics/                  只放日期資料夾，沒有總表；讀取時合併所有日期，同一台取最新
    │   └─ Intrinsic_<日期>/         每天一個資料夾（Windows 不分大小寫，舊的 intrinsic_<日期> 也讀得到）
    │       ├─ Right_CF|Right_CS|Left_CF|Left_CS/<序號>_<日期>_<隨機碼>/   calib.py 每次拍攝一個資料夾
    │       └─ Intrinsics_all.json  當天的內參，鍵為 序號_Right_CF（同一台可同時當左右投的 CF，各存一筆）
    └─ extrinsic/
        ├─ selected_points_cf.json  正面標註點（calibrate.py）
        ├─ selected_points_cs.json  側面標註點（calibrate.py）
        └─ fundamental.json         F 矩陣（calibrate.py，或 roma/roma_extrinsic.py --save）

路徑以本檔位置推算，不受執行時所在目錄影響。
"""
import os
import json
from datetime import datetime

import numpy as np

from Src.paths import DB_DIR
CALIB_DIR = str(DB_DIR / "Calibration")
INTRINSIC_SUBDIR = "Intrinsics"   # 2026-11-11 由 intrinsic 改名
INTRINSIC_DIR = os.path.join(CALIB_DIR, INTRINSIC_SUBDIR)
EXTRINSIC_DIR = os.path.join(CALIB_DIR, "extrinsic")

ROLES_JSON = os.path.join(CALIB_DIR, "camera_roles.json")
POINTS_JSON = {
    "cf": os.path.join(EXTRINSIC_DIR, "selected_points_cf.json"),
    "cs": os.path.join(EXTRINSIC_DIR, "selected_points_cs.json"),
}
FUNDAMENTAL_JSON = os.path.join(EXTRINSIC_DIR, "fundamental.json")

# 舊設備（2026-02 前人）的校正資料；讀寫它的舊工具已於 2026-10-13 刪除，資料夾保留備查，主流程不使用
OLD_RIG_DIR = os.path.join(CALIB_DIR, "old_rig_20260223")


def read_json(path):
    """讀取 JSON；檔案不存在回傳 None。"""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------- 校正工作階段 ----------

def new_session_dir(method, serials):
    """建立 sessions/<日期_時分秒>_<方法>_SN<序號>... 資料夾並回傳路徑。

    只建立資料夾本身；cf/、cs/、intrinsic/ 等子資料夾等到真的寫入時才建立，
    避免取消的校正留下一堆空資料夾。
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = "_".join([stamp, method] + ["SN" + str(s) for s in serials if s])
    path, n = os.path.join(CALIB_DIR, "sessions", name), 2
    while os.path.exists(path):  # 同一秒開兩次
        path, n = os.path.join(CALIB_DIR, "sessions", "{}_{}".format(name, n)), n + 1
    os.makedirs(path)
    return path


# ---------- 相機角色 ----------

def load_roles():
    """回傳 (front_sn, side_sn)；未設定則回傳 None。"""
    data = read_json(ROLES_JSON)
    if data and data.get("front") and data.get("side"):
        return str(data["front"]), str(data["side"])
    return None


def save_roles(front_sn, side_sn):
    write_json(ROLES_JSON, {
        "front": str(front_sn),
        "side": str(side_sn),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    })


# ---------- 內參 ----------
# 內參只存在 Intrinsics/Intrinsic_<日期>/Intrinsics_all.json。讀取時依日期由舊到新合併，
# 同一序號以最新日期（同一天內以 calibrated_at 較晚者）為準。

HAND_DIRS = {"right": "Right", "left": "Left"}
VIEWS = ("CF", "CS")
DATED_INTRINSICS_NAME = "Intrinsics_all.json"
_DATE_PREFIX = "intrinsic_"


def _date_of(name):
    """Intrinsic_20261111 → '20261111'；不是日期資料夾回傳 None（大小寫不拘）。"""
    if not name.lower().startswith(_DATE_PREFIX):
        return None
    date = name[len(_DATE_PREFIX):]
    return date if len(date) == 8 and date.isdigit() else None


def dated_intrinsic_files():
    """[(日期, Intrinsics_all.json 路徑)]，由舊到新；讀取當下的 INTRINSIC_DIR，測試可以替換。"""
    if not os.path.isdir(INTRINSIC_DIR):
        return []
    found = []
    for name in os.listdir(INTRINSIC_DIR):
        date, folder = _date_of(name), os.path.join(INTRINSIC_DIR, name)
        if date is None or not os.path.isdir(folder):
            continue
        for candidate in os.listdir(folder):
            if candidate.lower() == DATED_INTRINSICS_NAME.lower():
                found.append((date, os.path.join(folder, candidate)))
    return sorted(found)


def latest_intrinsics_json():
    """最新一天的 Intrinsics_all.json；還沒有任何內參時回傳今天的預定路徑（檔案不存在）。"""
    files = dated_intrinsic_files()
    if files:
        return files[-1][1]
    return os.path.join(intrinsic_date_dir(datetime.now().strftime("%Y%m%d")), DATED_INTRINSICS_NAME)


def intrinsic_key(sn, hand=None, view=None):
    """Intrinsics_all.json 的鍵。同一台相機可能同時當右投與左投的 CF（甚至換位置後焦距不同），
    所以以「序號＋位置」為鍵：24380115_Right_CF；校正精靈不知道左右投時為 24380115_CF。"""
    parts = [str(sn)]
    if hand:
        parts.append(HAND_DIRS[hand])
    if view:
        parts.append(view.upper())
    return "_".join(parts)


def _all_intrinsic_records():
    """[(排序戳, 序號, hand, view, entry)]，涵蓋所有日期資料夾，由舊到新。

    舊格式以序號為鍵、沒有 serial_number 欄位，鍵本身就是序號。"""
    records = []
    for date, path in dated_intrinsic_files():
        for key, entry in (read_json(path) or {}).items():
            if not isinstance(entry, dict) or "intrinsic_matrix" not in entry:
                continue
            sn = str(entry.get("serial_number") or key)
            hand = entry.get("pitch_hand") if entry.get("pitch_hand") in HAND_DIRS else None
            view = str(entry["view"]).upper() if entry.get("view") else None
            records.append(((date, str(entry.get("calibrated_at", ""))), sn, hand, view, entry))
    return sorted(records, key=lambda r: r[0])


def load_intrinsics():
    """回傳 {序號: 內參資料}，每台相機取最新的一次（不分位置）；沒有任何內參回傳空 dict。
    只用來列出「有哪些相機做過內參」；計算 3D 請用 get_intrinsic(序號, hand, view) 取對應位置的內參。"""
    return {sn: entry for _, sn, _, _, entry in _all_intrinsic_records()}


def find_intrinsic(sn, hand=None, view=None):
    """回傳這台相機在這個位置最新的內參條目；找不到回傳 None。

    依序找：同序號＋同左右投＋同 CF/CS → 同序號＋同 CF/CS 且沒記左右投（校正精靈）
    → 同序號的任何一筆（舊資料沒有記位置）。同一層有多筆時取最新。"""
    sn = str(sn)
    view = view.upper() if view else None
    candidates = [r for r in _all_intrinsic_records() if r[1] == sn]
    tiers = []
    if hand and view:
        tiers.append(lambda r: r[2] == hand and r[3] == view)
    if view:
        tiers.append(lambda r: r[2] is None and r[3] == view)
    tiers.append(lambda r: r[2] is None and r[3] is None)   # 舊格式
    tiers.append(lambda r: True)
    for match in tiers:
        found = [r for r in candidates if match(r)]
        if found:
            return found[-1][4]
    return None


def intrinsic_date_dir(date):
    """date 為 YYYYMMDD；同一天已有舊式小寫資料夾（intrinsic_<日期>）時沿用它。"""
    if os.path.isdir(INTRINSIC_DIR):
        for name in os.listdir(INTRINSIC_DIR):
            if _date_of(name) == date and os.path.isdir(os.path.join(INTRINSIC_DIR, name)):
                return os.path.join(INTRINSIC_DIR, name)
    return os.path.join(INTRINSIC_DIR, "Intrinsic_" + date)


def rig_dir_name(hand, view):
    """('right', 'CF') → 'Right_CF'"""
    return "{}_{}".format(HAND_DIRS[hand], view.upper())


def intrinsic_photo_dir(date, hand, view):
    return os.path.join(intrinsic_date_dir(date), rig_dir_name(hand, view))


def new_capture_dir(date, hand, view, sn):
    """Right_CF/<序號>_<日期>_<隨機碼>，同一天可以拍很多次；只回傳路徑，存第一張時才建立。"""
    import secrets
    parent = intrinsic_photo_dir(date, hand, view)
    while True:
        path = os.path.join(parent, "{}_{}_{}".format(sn, date, secrets.token_hex(3)))
        if not os.path.exists(path):
            return path


def _parse_rig(name):
    """'Right_CF' → ('right', 'CF')；其他回傳 None。"""
    hand, _, view = name.partition("_")
    hand, view = hand.lower(), view.upper()
    return (hand, view) if hand in HAND_DIRS and view in VIEWS else None


def parse_intrinsic_photo_dir(folder):
    """…/Intrinsic_<日期>/Right_CF[/<序號>_<日期>_<隨機碼>] → (日期, hand, view, 序號或 None)。

    選 Right_CF 這層時沒有序號；選到單次拍攝的資料夾時，序號取資料夾名稱開頭。不是這種結構回傳 None。
    """
    parts = os.path.normpath(str(folder)).split(os.sep)
    sn = None
    if len(parts) >= 3 and _parse_rig(parts[-1]) is None and _parse_rig(parts[-2]):
        sn = parts[-1].split("_", 1)[0] or None
        parts = parts[:-1]
    if len(parts) < 2:
        return None
    date, rig = _date_of(parts[-2]), _parse_rig(parts[-1])
    if date is None or rig is None:
        return None
    return (date,) + rig + (sn,)


def rekey_legacy_intrinsics(data):
    """舊格式只以序號為鍵；條目裡有記位置的，改用自己的 序號_Right_CF 鍵，避免被別的位置覆蓋。"""
    result = {}
    for key, entry in data.items():
        if isinstance(entry, dict) and "serial_number" not in entry and "intrinsic_matrix" in entry:
            hand = entry.get("pitch_hand") if entry.get("pitch_hand") in HAND_DIRS else None
            view = str(entry["view"]).upper() if entry.get("view") in VIEWS else None
            entry = dict(entry, serial_number=str(key))
            key = intrinsic_key(key, hand, view)
        result[key] = entry
    return result


def to_relative_path(path, base_dir):
    """存檔用：相對於 base_dir、以 / 分隔，整個 Db 搬到別台電腦仍對得上。
    路徑大小寫以磁碟上實際名稱為準；不同磁碟機無法相對時保留原路徑。"""
    from pathlib import Path
    if not path or not os.path.isabs(str(path)):
        return path
    try:
        relative = os.path.relpath(str(Path(path).resolve()), str(Path(base_dir).resolve()))
    except ValueError:
        return str(path)
    return relative.replace(os.sep, "/")


def resolve_intrinsic_path(path, base_dir):
    """讀檔用：Intrinsics_all.json 裡的照片路徑（相對或舊的絕對路徑）→ 絕對路徑。"""
    from pathlib import Path
    return str((Path(base_dir) / path).resolve()) if not os.path.isabs(str(path)) else str(path)


def save_dated_intrinsic(date, sn, entry, hand=None, view=None):
    """寫入 Intrinsic_<日期>/Intrinsics_all.json，鍵為 序號_Right_CF（見 intrinsic_key），
    同一天同一台在同一位置重做才會覆蓋。照片路徑存成相對於該日期資料夾的路徑。回傳 (路徑, 條目數)。"""
    day_dir = intrinsic_date_dir(date)
    path = os.path.join(day_dir, DATED_INTRINSICS_NAME)
    data = rekey_legacy_intrinsics(read_json(path) or {})
    entry = dict(entry, serial_number=str(sn))
    if entry.get("source_images"):
        entry["source_images"] = [to_relative_path(p, day_dir) for p in entry["source_images"]]
    if entry.get("capture_dir"):
        entry["capture_dir"] = to_relative_path(entry["capture_dir"], day_dir)
    if hand:
        entry["pitch_hand"] = hand
    if view:
        entry["view"] = view.upper()
    data[intrinsic_key(sn, hand, view)] = entry
    write_json(path, data)
    return path, len(data)


def get_intrinsic(sn, hand=None, view=None):
    """回傳 (K, dist)；該台尚未校正則回傳 None。給了 hand/view 時優先用那個位置的內參（見 find_intrinsic）。"""
    entry = find_intrinsic(sn, hand, view)
    if entry is None:
        return None
    K = np.array(entry["intrinsic_matrix"], dtype=np.float64)
    dist = np.array(entry["distortion_coefficients"], dtype=np.float64)
    return K, dist


# ---------- 外參（F 矩陣） ----------

def save_fundamental(F, front_sn, side_sn, inliers, total, threshold, extra=None):
    """extra：額外記錄的欄位（例如 RoMa 工具記下方法與來源影片）"""
    write_json(FUNDAMENTAL_JSON, {
        **(extra or {}),
        "F": np.asarray(F).tolist(),
        # 02 以側面為 pts1、正面為 pts2 估計，因此 F 滿足 x_front^T F x_side = 0
        "convention": "x_front^T F x_side = 0",
        "front": front_sn,
        "side": side_sn,
        "inliers": int(inliers),
        "total_points": int(total),
        "threshold_px": float(threshold),
        "estimated_at": datetime.now().isoformat(timespec="seconds"),
    })


def load_fundamental():
    """回傳 fundamental.json 內容（F 已轉為 ndarray）；不存在回傳 None。"""
    data = read_json(FUNDAMENTAL_JSON)
    if data is None:
        return None
    data["F"] = np.array(data["F"], dtype=np.float64)
    return data


def load_stereo_calibration():
    """組合 3D 重建需要的全部參數。

    回傳 dict：K_F, dist_F, K_S, dist_S, F, front, side
    任一項缺少時回傳 (None, 原因說明)，成功時回傳 (dict, None)。
    """
    roles = load_roles()
    if roles is None:
        return None, f"找不到相機角色設定 {ROLES_JSON}（請用 calib.py 第 4 區指定正面/側面）"
    front, side = roles

    # camera_roles.json 是舊的單一機架（視為右投）；左右投分開的請用 pitch_profiles
    intr_f = get_intrinsic(front, "right", "CF")
    if intr_f is None:
        return None, f"正面相機 SN{front} 尚未做內參校正"
    intr_s = get_intrinsic(side, "right", "CS")
    if intr_s is None:
        return None, f"側面相機 SN{side} 尚未做內參校正"

    fund = load_fundamental()
    if fund is None:
        return None, f"找不到 {FUNDAMENTAL_JSON}（請先用 start_calibrate.bat 完成校正桿外參，或在校正中心用 RoMa 計算外參）"
    if (fund.get("front"), fund.get("side")) != (front, side):
        return None, (f"F 矩陣是用 front={fund.get('front')} side={fund.get('side')} 算的，"
                      f"與目前角色 front={front} side={side} 不符，請重新執行 02")

    return {
        "K_F": intr_f[0], "dist_F": intr_f[1],
        "K_S": intr_s[0], "dist_S": intr_s[1],
        "F": fund["F"],
        "front": front, "side": side,
        "F_estimated_at": fund.get("estimated_at"),
    }, None


# ---------- 錄影快照 ----------
# 校正值只對「錄影當下的相機擺位」有效，因此每筆錄影資料夾各存一份，
# 日後重新校正也不會影響舊錄影的 3D 重建。

SNAPSHOT_NAME = "calibration.json"
_MATRIX_KEYS = ("K_F", "dist_F", "K_S", "dist_S", "F")


def save_snapshot(folder, front_sn=None, side_sn=None):
    """把目前的校正值存進錄影資料夾。回傳 (是否成功, 說明)。

    front_sn / side_sn 為實際錄影的相機序號，與校正角色不符時不存。
    """
    calib, reason = load_stereo_calibration()
    if calib is None:
        return False, reason
    if front_sn and side_sn and (str(front_sn), str(side_sn)) != (calib["front"], calib["side"]):
        return False, (f"錄影相機 front={front_sn} side={side_sn} 與校正角色 "
                       f"front={calib['front']} side={calib['side']} 不符")

    data = {k: (np.asarray(v).tolist() if k in _MATRIX_KEYS else v) for k, v in calib.items()}
    data["saved_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(os.path.join(folder, SNAPSHOT_NAME), data)
    return True, f"校正快照已存入 {SNAPSHOT_NAME}"


def load_snapshot(folder):
    """讀取錄影資料夾內的校正快照（矩陣已轉為 ndarray）；沒有則回傳 None。"""
    data = read_json(os.path.join(folder, SNAPSHOT_NAME))
    if data is None:
        return None
    for k in _MATRIX_KEYS:
        data[k] = np.array(data[k], dtype=np.float64)
    return data
