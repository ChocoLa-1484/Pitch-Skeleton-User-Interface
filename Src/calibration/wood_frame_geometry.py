"""Recover a rigid frame's two-face geometry from calibrated monocular images.

Every view must contain BOTH faces. Front coordinates fix scale and world axes;
side-face dimensions stay measured, but its full relative rigid pose is fitted.
This does not use, estimate, or assume a stereo-camera transform.
"""
import copy

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

from Src.calibration.wood_frame import (
    _camera, _pixels, _poses, _project, _rms, validate_target, wood_frame_target,
)


FRONT_IDS = ["F1", "F2", "F3", "F4"]
SIDE_IDS = ["S1", "S2", "S3", "S4"]
POINT_IDS = FRONT_IDS + SIDE_IDS
SESSION_SCHEMA = "wood_frame_geometry_session_v1"


def _angle(R1, R2):
    return float(np.rad2deg(np.linalg.norm(cv2.Rodrigues(R1 @ R2.T)[0])))


def _normal_span(poses):
    normals = [p["R"][:, 2] for p in poses]
    return max(float(np.rad2deg(np.arccos(np.clip(a @ b, -1., 1.))))
               for a in normals for b in normals)


def fit_wood_frame_geometry(camera, views, *, target=None, min_views=5, max_rms_px=3.):
    """Return a standard target JSON containing fitted side coordinates.

    ``views`` contains unique ``id``, ``image_size``, and ``points`` mapping
    F1..F4/S1..S4 to full-resolution raw image pixels. At least five distinct
    tilted poses are required; 12–20 photos are recommended. Keep the frame
    rigid, and the camera's focal length/resolution/crop fixed throughout.
    """
    if not isinstance(min_views, int) or min_views < 5:
        raise ValueError("木框幾何至少需要 5 張；建議 12–20 張不同姿態照片。")
    if not np.isfinite(max_rms_px) or max_rms_px <= 0:
        raise ValueError("重投影誤差上限須為正數。")
    camera_arrays = _camera(camera, "geometry")
    target = copy.deepcopy(wood_frame_target() if target is None else target)
    ids, xyz = validate_target(target)
    if set(ids) != set(POINT_IDS):
        raise ValueError("木框幾何需要 F1–F4、S1–S4 共八個實體控制點。")
    front = xyz[[ids.index(i) for i in FRONT_IDS]]
    side_world = xyz[[ids.index(i) for i in SIDE_IDS]]
    # Center the known side face for numerical conditioning; its relative pose
    # is free in all six degrees, so no ideal hinge location/angle is imposed.
    side = side_world - side_world.mean(axis=0)
    for face in (front, side):
        singular = np.linalg.svd(face - face.mean(axis=0), compute_uv=False)
        if singular[-1] > singular[0] * 1e-6:
            raise ValueError("每個木框面的四點須共平面；本工具僅估計兩剛性平面間的相對位置。")
    views = copy.deepcopy(list(views))
    names = [v.get("id") for v in views]
    if len(views) < min_views or any(not isinstance(n, str) or not n.strip() for n in names):
        raise ValueError("至少需要 {} 張具有非空 ID 的雙面同框照片。".format(min_views))
    if len(set(names)) != len(names):
        raise ValueError("照片 ID 不可重複。")
    prepared = []
    for view in views:
        pixels = _pixels(view, POINT_IDS, camera_arrays[2], view["id"], "geometry")
        if any(_rms(pixels, previous["pixels"]) <= 1. for previous in prepared):
            raise ValueError(view["id"] + "：與已有照片姿態重複，請移除重複照片並補拍不同姿態。")
        face_poses = []
        for face_xyz, face_pixels in ((front, pixels[:4]), (side, pixels[4:])):
            if cv2.contourArea(cv2.convexHull(face_pixels.astype(np.float32))) < 100.:
                raise ValueError(view["id"] + "：每個面的四點面積太小，請靠近拍攝並避免側刃角度。")
            poses, _ = _poses(face_xyz, face_pixels, camera_arrays)
            poses = [p for p in poses if p["rms_px"] <= max_rms_px]
            if not poses:
                raise ValueError(view["id"] + "：單面 PnP 誤差過大，請檢查點位順序、尺寸與內參。")
            face_poses.append(poses)
        prepared.append({"id": view["id"], "pixels": pixels, "poses": face_poses})

    def select_poses(R, t):
        transformed = side @ R.T + t
        all_xyz = np.vstack((front, transformed))
        poses, errors = [], []
        for view in prepared:
            choices = []
            for pose in view["poses"][0]:
                if np.min((all_xyz @ pose["R"].T + pose["t"])[:, 2]) <= 0:
                    continue
                error = _rms(view["pixels"], _project(all_xyz, pose["R"], pose["t"], camera_arrays))
                choices.append((error, pose))
            if not choices:
                return None, float("inf")
            error, pose = min(choices, key=lambda entry: entry[0])
            poses.append(pose)
            errors.append(error)
        return poses, float(np.median(errors))

    candidates = []
    for view in prepared:
        for front_pose in view["poses"][0]:
            for side_pose in view["poses"][1]:
                R = front_pose["R"].T @ side_pose["R"]
                t = front_pose["R"].T @ (side_pose["t"] - front_pose["t"])
                poses, score = select_poses(R, t)
                if poses is not None:
                    candidates.append({"R": R, "t": t, "poses": poses, "score": score})
    candidates.sort(key=lambda p: p["score"])
    if not candidates:
        raise ValueError("雙面幾何無有效的正深度解，請檢查實體點位編號。")
    if _normal_span(candidates[0]["poses"]) < 8.:
        raise ValueError("照片傾角不足 8°；請改變木框俯仰與左右傾角，不可只平移或重複拍攝。")
    seeds = []
    for candidate in candidates:
        if any(_angle(candidate["R"], p["R"]) < 2.
               and np.linalg.norm(candidate["t"] - p["t"]) < 10. for p in seeds):
            continue
        seeds.append(candidate)
        if len(seeds) == 4:
            break

    def unpack(parameters):
        R = cv2.Rodrigues(parameters[:3])[0]
        points = np.vstack((front, side @ R.T + parameters[3:6]))
        poses = [(cv2.Rodrigues(parameters[i:i + 3])[0], parameters[i + 3:i + 6])
                 for i in range(6, len(parameters), 6)]
        return points, poses

    def residual(parameters):
        points, poses = unpack(parameters)
        return np.concatenate([(_project(points, R, t, camera_arrays) - view["pixels"]).ravel()
                               for view, (R, t) in zip(prepared, poses)])

    sparsity = lil_matrix((16 * len(prepared), 6 * (len(prepared) + 1)), dtype=int)
    for index in range(len(prepared)):
        sparsity[index * 16 + 8:(index + 1) * 16, :6] = 1
        sparsity[index * 16:(index + 1) * 16, 6 * (index + 1):6 * (index + 2)] = 1
    fits = []
    for seed in seeds:
        initial = np.concatenate([cv2.Rodrigues(seed["R"])[0].ravel(), seed["t"]] +
                                 [np.r_[cv2.Rodrigues(p["R"])[0].ravel(), p["t"]] for p in seed["poses"]])
        fit = least_squares(residual, initial, loss="soft_l1", f_scale=1., x_scale="jac",
                            jac_sparsity=sparsity.tocsr(), max_nfev=300,
                            ftol=1e-9, xtol=1e-9, gtol=1e-9)
        points, poses = unpack(fit.x)
        if (not fit.success or not np.isfinite(fit.x).all()
                or any(np.min((points @ R.T + t)[:, 2]) <= 0 for R, t in poses)):
            continue
        errors = np.sqrt(np.mean(residual(fit.x).reshape(-1, 8, 2) ** 2, axis=(1, 2)) * 2.)
        fits.append({"parameters": fit.x, "points": points, "poses": poses, "errors": errors,
                     "rms": float(np.sqrt(np.mean(errors ** 2))), "cost": fit.cost})
    if not fits:
        raise ValueError("幾何最佳化未收斂，請補拍更清晰且傾角更多的雙面同框照片。")
    fits.sort(key=lambda p: p["cost"])
    best = fits[0]
    if np.max(best["errors"]) > max_rms_px:
        bad = [v["id"] for v, error in zip(prepared, best["errors"]) if error > max_rms_px]
        raise ValueError("照片重投影誤差超過 {} px，請修正標記或移除後重拍：{}".format(max_rms_px, ", ".join(bad)))
    for alternative in fits[1:]:
        if (abs(alternative["rms"] - best["rms"]) <= .1
                and np.max(np.linalg.norm(alternative["points"] - best["points"], axis=1)) > 5.):
            raise ValueError("雙面幾何仍有多個近似解；請增加近距離及不同俯仰／傾角照片。")
    span = _normal_span([{"R": R} for R, _ in best["poses"]])
    if span < 8.:
        raise ValueError("最佳化後有效傾角仍不足 8°，請補拍不同傾角。")
    points = best["points"]
    front_normal = np.cross(front[1] - front[0], front[3] - front[0])
    side_normal = np.cross(points[5] - points[4], points[7] - points[4])
    cosine = float(front_normal @ side_normal / np.linalg.norm(front_normal) / np.linalg.norm(side_normal))
    angle = float(np.rad2deg(np.arccos(np.clip(cosine, -1., 1.))))
    point_map = dict(zip(POINT_IDS, points.tolist()))
    for point in target["points"]:
        point["xyz_mm"] = point_map[point["id"]]
    target.setdefault("metadata", {}).update({
        "shape": "photo_calibrated_two_face_frame",
        "geometry_status": "relative_face_pose_fitted",
        "side_plane": "Side coordinates fitted in the fixed front-face world coordinate system.",
        "angle_note": "No 90-degree constraint; angle uses normals from the labelled F1,F2,F4 and S1,S2,S4 order.",
        "geometry_calibration": {
            "method": "same_camera_both_faces_rigid_bundle_adjustment",
            "normal_angle_deg": angle, "acute_plane_angle_deg": min(angle, 180. - angle),
            "rms_px": best["rms"], "max_view_rms_px": float(np.max(best["errors"])),
            "front_normal_span_deg": span, "view_count": len(views),
            "camera": copy.deepcopy(camera),
            "views": [{"id": v["id"], "path": v.get("path"), "rms_px": float(error)}
                      for v, error in zip(views, best["errors"])],
            "limitation": "Measured face dimensions and intrinsics are held fixed. Residuals are fit quality, not independent field accuracy.",
        },
    })
    validate_target(target)
    return target
