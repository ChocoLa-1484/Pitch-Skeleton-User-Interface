"""Decode physical marker corners, never sort corners by image coordinates.

The supplied board has ID 0 rotated 90 degrees CCW on the front, and ID 1
upright on the side. OpenCV returns canonical TL/TR/BR/BL regardless of image
rotation, hence front canonical corners correspond to F4/F1/F2/F3.
"""
import cv2
import numpy as np


def marker_config(target):
    config = target.get("aruco")
    if not isinstance(config, dict) or config.get("corner_basis") != "marker_black_border":
        raise ValueError("此 target 未設定 ArUco 黑色外框角點對應；請載入新版木框幾何。")
    aruco = getattr(cv2, "aruco", None)
    name = config.get("dictionary")
    if aruco is None:
        raise ValueError("目前 OpenCV 缺少 aruco 模組，請使用含 ArUco 的 OpenCV 環境。")
    if not isinstance(name, str) or not name.startswith("DICT_") or not hasattr(aruco, name):
        raise ValueError("無效的 ArUco 字典名稱。")
    dictionary = aruco.getPredefinedDictionary(getattr(aruco, name))
    markers = config.get("markers", {})
    if not isinstance(markers, dict) or set(markers) != {"cf", "cs"}:
        raise ValueError("ArUco 設定需包含 cf 與 cs。")
    marker_ids = []
    for role, entry in markers.items():
        if not isinstance(entry, dict):
            raise ValueError("ArUco 標記設定格式錯誤。")
        ident, ids = entry.get("id"), entry.get("point_ids_in_canonical_order")
        expected = target.get("view_point_ids", {}).get(role, [])
        if (type(ident) is not int or not 0 <= ident < len(dictionary.bytesList)
                or not isinstance(ids, list) or len(ids) != 4
                or any(not isinstance(i, str) for i in ids)
                or len(set(ids)) != 4 or set(ids) != set(expected)):
            raise ValueError("ArUco ID 或四角與實體控制點的對應無效。")
        marker_ids.append(ident)
    if len(set(marker_ids)) != 2:
        raise ValueError("正面與側面必須使用不同的 ArUco ID。")
    return config, dictionary


def detect_frame_markers(image, target, roles=("cf", "cs")):
    """Return points and diagnostics for requested faces, in original pixels.

    Missing, occluded, or duplicate expected IDs produce no points for that
    face. Other visible markers never substitute for an expected marker.
    Legacy targets without ArUco metadata remain manual-only.
    """
    config, dictionary = marker_config(target)
    if not roles or any(role not in ("cf", "cs") for role in roles):
        raise ValueError("偵測角色必須為 cf／cs。")
    if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.size == 0:
        raise ValueError("ArUco 需要原始解析度的 uint8 影像。")
    if image.ndim == 2:
        gray = image
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY if image.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
    else:
        raise ValueError("ArUco 影像需為灰階、BGR 或 BGRA。")
    aruco = cv2.aruco
    params = aruco.DetectorParameters() if hasattr(aruco, "DetectorParameters") else aruco.DetectorParameters_create()
    params.cornerRefinementMethod = aruco.CORNER_REFINE_SUBPIX
    if hasattr(aruco, "ArucoDetector"):
        corners, ids, _ = aruco.ArucoDetector(dictionary, params).detectMarkers(gray)
    else:
        corners, ids, _ = aruco.detectMarkers(gray, dictionary, parameters=params)
    found_ids = [] if ids is None else np.asarray(ids).reshape(-1).tolist()
    results = {}
    for role in roles:
        entry = config["markers"][role]
        ident = entry["id"]
        matches = [i for i, value in enumerate(found_ids) if value == ident]
        result = {"points": {}, "marker_id": ident, "dictionary": config["dictionary"],
                  "detected_ids": found_ids, "status": "missing", "note": "找不到 ID {}，請補拍或手動標記。".format(ident)}
        if len(matches) > 1:
            result.update(status="duplicate", note="畫面有多個 ID {}，不能判定是哪塊木框。".format(ident))
        elif matches:
            xy = np.asarray(corners[matches[0]], float).reshape(4, 2)
            size = np.array([gray.shape[1], gray.shape[0]])
            if np.isfinite(xy).all() and np.all(xy >= 0) and np.all(xy < size):
                result.update(points=dict(zip(entry["point_ids_in_canonical_order"], xy.tolist())),
                              status="detected", note="已辨識 ID {}；請核對角點疊圖。".format(ident))
        results[role] = result
    return results
