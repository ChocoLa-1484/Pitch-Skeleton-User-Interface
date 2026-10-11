"""HSV eight-ball calibration, in millimetres; no Qt or camera SDK required.

World: origin ball3, X from ball6 toward ball8, Z up, Y = Z cross X.
OpenCV: X_camera = R_world_to_camera @ X_world + t_world_to_camera_mm.
"""
from dataclasses import dataclass
from itertools import combinations, permutations

import cv2
import numpy as np

from .calibration_workflow import validate_intrinsic
from .fluorescent_balls import DetectionConfig as HSVDetectionConfig, detect_balls


@dataclass
class DetectionConfig(HSVDetectionConfig):
    """Compatibility wrapper adding the cross's correspondence search limit."""
    max_candidates: int = 24

    def validate(self):
        super().validate()
        if not 8 <= self.max_candidates <= 40:
            raise ValueError("Cross max_candidates must be between 8 and 40.")


def object_points(horizontal_depth_mm=15.0):
    """Ball1..8; distances are centre-to-centre, not gaps between surfaces.

    Positive depth puts the horizontal balls BEHIND the vertical rod when
    looking at the labelled face (ball6 left, ball8 right, ball1 up).
    Ball3 is the single central ball on the vertical rod, not a ninth ball.
    """
    if not np.isfinite(horizontal_depth_mm):
        raise ValueError("Horizontal depth must be finite.")
    return np.array([[0, 0, 150], [0, 0, 100], [0, 0, 0],
                     [0, 0, -150], [0, 0, -350],
                     [-350, horizontal_depth_mm, 0],
                     [-150, horizontal_depth_mm, 0],
                     [100, horizontal_depth_mm, 0]], dtype=np.float64)


def camera_parameters(entry, image_size):
    validate_intrinsic(entry)
    if tuple(entry["image_size"]) != tuple(image_size):
        raise ValueError("Image size {} differs from intrinsic size {}; do not silently resize K."
                         .format(image_size, entry["image_size"]))
    return (np.asarray(entry["intrinsic_matrix"], np.float64),
            np.asarray(entry["distortion_coefficients"], np.float64).reshape(-1))


def _vertical_orders(points, line_tolerance=3.0):
    """Find five collinear markers with the 5:10:15:20 spacing signature.

    Undistorted image coordinates are required. The 1D projectivity handles
    foreshortening, and both rod directions are tested (no image-left rule).
    """
    groups = set()
    for a, b in combinations(range(len(points)), 2):
        direction = points[b]-points[a]
        length = np.linalg.norm(direction)
        if length < 35:
            continue
        direction /= length
        off = points-points[a]
        near = np.flatnonzero(np.abs(off[:, 0]*direction[1]-off[:, 1]*direction[0]) < line_tolerance)
        for indices in combinations(near.tolist(), 5):
            groups.add(indices)
    results = {}
    z = np.array([0.15, 0.10, 0, -0.15, -0.35])
    for group in groups:
        cloud = points[list(group)]
        _, _, vt = np.linalg.svd(cloud-cloud.mean(axis=0), full_matrices=False)
        coordinate = cloud @ vt[0]
        indices = np.array(group)[np.argsort(coordinate)]
        for order in (indices, indices[::-1]):
            values = points[order] @ vt[0]
            span = np.ptp(values)
            if span < 35:
                continue
            values = (values-values.mean())/span
            design = np.column_stack((z, np.ones(5), -z*values))
            a, b, c = np.linalg.lstsq(design, values, rcond=None)[0]
            denominator = c*z+1
            if np.any(denominator <= 0.1):
                continue
            error = np.sqrt(np.mean(((a*z+b)/denominator-values)**2))
            if error < 0.025:
                results[tuple(order)] = float(error)
    return sorted(results, key=results.get)


def _pose_summary(world, image_points, K, dist, rvec, tvec):
    rvec, tvec = np.asarray(rvec).reshape(3), np.asarray(tvec).reshape(3)
    R = cv2.Rodrigues(rvec)[0]
    camera_points = world @ R.T + tvec
    if not np.isfinite(camera_points).all() or np.min(camera_points[:, 2]) <= 0:
        return None
    projected = cv2.projectPoints(world, rvec, tvec, K, dist)[0].reshape(-1, 2)
    errors = np.linalg.norm(projected-image_points, axis=1)
    if not np.isfinite(errors).all():
        return None
    transform = np.eye(4)
    transform[:3, :3], transform[:3, 3] = R, tvec
    return {"rvec": rvec.tolist(), "R_world_to_camera": R.tolist(),
            "t_world_to_camera_mm": tvec.tolist(),
            "T_world_to_camera": transform.tolist(),
            "camera_center_world_mm": (-R.T @ tvec).tolist(),
            "P_undistorted_pixels": (K @ transform[:3]).tolist(),
            "rms_px": float(np.sqrt(np.mean(errors**2))),
            "max_error_px": float(errors.max()), "per_point_error_px": errors.tolist(),
            "projected_points_px": projected.tolist(),
            "min_camera_depth_mm": float(camera_points[:, 2].min())}


def solve_pose(world, image_points, K, dist, refine=True):
    """Refine SQPnP and planar initializations; expose weak-depth pose ambiguity."""
    world = np.ascontiguousarray(world, dtype=np.float64)
    image_points = np.ascontiguousarray(image_points, dtype=np.float64)
    if (world.ndim != 2 or world.shape[1] != 3 or image_points.shape != (len(world), 2)
            or len(world) < 6 or not np.isfinite(world).all() or not np.isfinite(image_points).all()
            or np.linalg.matrix_rank(world-world.mean(axis=0)) < 2):
        raise ValueError("PnP needs finite, non-collinear, corresponding 3D/2D points.")
    try:
        result = cv2.solvePnPGeneric(world, image_points, K, dist, flags=cv2.SOLVEPNP_SQPNP)
    except cv2.error as exc:
        raise ValueError("SQPnP failed: {}".format(exc)) from exc
    seeds = list(zip(result[1], result[2])) if result[0] else []
    # The 15 mm separation gives very weak depth relative to camera distance.
    # IPPE's two PLANAR seeds are refined using the actual NONPLANAR points.
    # Never use the planar surrogate's pose as the final calibration.
    if refine:
        planar = world.copy()
        planar[:, 1] = 0
        try:
            initial = cv2.solvePnPGeneric(planar, image_points, K, dist, flags=cv2.SOLVEPNP_IPPE)
            if initial[0]:
                seeds.extend(zip(initial[1], initial[2]))
        except cv2.error:
            pass
    solutions = []
    for rv, tv in seeds:
        if not np.isfinite(rv).all() or not np.isfinite(tv).all():
            continue
        if refine:
            rv, tv = cv2.solvePnPRefineLM(world, image_points, K, dist, rv.copy(), tv.copy(),
                                        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
                                                  100, 1e-10))
        pose = _pose_summary(world, image_points, K, dist, rv, tv)
        if pose and not any(rotation_difference_deg(pose, old) < 0.1 for old in solutions):
            solutions.append(pose)
    if not solutions:
        raise ValueError("No PnP solution with all markers in front of camera.")
    solutions.sort(key=lambda p: p["rms_px"])
    best = solutions[0]
    best["alternative_pose"] = solutions[1] if len(solutions) > 1 else None
    best["pose_ambiguous"] = bool(len(solutions) > 1
                                   and solutions[1]["rms_px"]-best["rms_px"] < 0.2
                                   and rotation_difference_deg(best, solutions[1]) > 2)
    return best


def rotation_difference_deg(first, second):
    relative = np.asarray(first["R_world_to_camera"]) @ np.asarray(second["R_world_to_camera"]).T
    return float(np.degrees(np.arccos(np.clip((np.trace(relative)-1)/2, -1, 1))))


def label_and_solve(candidates, world, K, dist, max_rms_px=2.0, max_error_px=4.0,
                    max_candidates=24, ambiguity_margin_px=0.20):
    if not 8 <= len(candidates) <= max_candidates:
        raise ValueError("Found {} candidates; need 8..{}. Adjust HSV/ROI or retake occluded images."
                         .format(len(candidates), max_candidates))
    raw = np.asarray([c["center_px"] for c in candidates], np.float64)
    points = cv2.undistortPoints(raw.reshape(-1, 1, 2), K, dist, P=K).reshape(-1, 2)
    orders = _vertical_orders(points)
    proposals = []
    for vertical in orders:
        remaining = sorted(set(range(len(points)))-set(vertical))
        for horizontal in permutations(remaining, 3):
            ordered = tuple(vertical) + horizontal
            # The three horizontal markers must also be collinear. Ball3 is
            # deliberately excluded: its depth is different by 15 mm.
            trio = points[list(horizontal)]
            a, b = trio[1]-trio[0], trio[2]-trio[0]
            if np.linalg.norm(b) < 8 or abs(a[0]*b[1]-a[1]*b[0])/np.linalg.norm(b) > 3:
                continue
            # ball7 lies between ball6 and ball8 even under perspective.
            if not 0 < np.dot(a, b)/np.dot(b, b) < 1:
                continue
            try:
                pose = solve_pose(world, raw[list(ordered)], K, dist, refine=False)
            except ValueError:
                continue
            proposals.append((pose["rms_px"], ordered))
    if not proposals:
        raise ValueError("No eight-ball geometry matched the 5:10:15:20 and 20:15:10 layout.")
    # Refine several correspondences; retain runner-up to detect labelling ambiguity.
    fits = []
    for _, ordered in sorted(proposals)[:12]:
        pose = solve_pose(world, raw[list(ordered)], K, dist)
        fits.append((pose["rms_px"], ordered, pose))
    fits.sort(key=lambda item: item[0])
    _, ordered, pose = fits[0]
    pose["candidate_indices_ball1_to_ball8"] = [int(index) for index in ordered]
    pose["image_points_px"] = raw[list(ordered)].tolist()
    pose["runner_up_rms_px"] = fits[1][0] if len(fits) > 1 else None
    failures = []
    if pose["rms_px"] > max_rms_px or pose["max_error_px"] > max_error_px:
        failures.append("reprojection_error")
    if len(fits) > 1 and fits[1][0]-fits[0][0] < ambiguity_margin_px:
        failures.append("ambiguous_ball_labels")
    pose["accepted"] = not failures
    pose["rejection_reasons"] = failures
    return pose


def fit_static_camera(poses, world, K, dist, max_rms_px=2.0, max_error_px=4.0,
                      static_tolerance_px=3.0, min_frames=3):
    """Fit ONE pose to repeated observations only after image-space stability checks."""
    good = {name: pose for name, pose in poses.items() if pose.get("accepted")}
    if len(good) < min_frames:
        raise ValueError("Only {} accepted frames; need {} for a static-camera fit."
                         .format(len(good), min_frames))
    names = list(good)
    observations = np.asarray([good[name]["image_points_px"] for name in names])
    median = np.median(observations, axis=0)
    motion = np.max(np.linalg.norm(observations-median, axis=2), axis=1)
    stable = motion <= static_tolerance_px
    if stable.sum() < min_frames or stable.sum() < 0.6*len(names):
        raise ValueError("Image points are not stationary; do not pool moved devices/cameras.")
    observations = observations[stable]
    repeated_world = np.tile(world, (len(observations), 1))
    pose = solve_pose(repeated_world, observations.reshape(-1, 2), K, dist)
    if pose["rms_px"] > max_rms_px or pose["max_error_px"] > max_error_px:
        raise ValueError("Joint PnP exceeds reprojection limits (RMS {:.3f}, max {:.3f} px)."
                         .format(pose["rms_px"], pose["max_error_px"]))
    pose["used_frames"] = [name for name, use in zip(names, stable) if use]
    pose["excluded_motion_frames"] = [name for name, use in zip(names, stable) if not use]
    pose["frame_motion_max_px"] = dict(zip(names, motion.tolist()))
    pose["median_image_points_px"] = np.median(observations, axis=0).tolist()
    pose["point_repeatability_std_px"] = np.std(observations, axis=0).tolist()
    return pose


def stereo_geometry(front, side, K_front, K_side):
    """Metric relative pose, with x_front.T F x_side = 0 on UNDISTORTED pixels."""
    Rf, Rs = (np.asarray(p["R_world_to_camera"]) for p in (front, side))
    tf, ts = (np.asarray(p["t_world_to_camera_mm"]) for p in (front, side))
    R = Rf @ Rs.T
    t = tf - R @ ts
    x, y, z = t
    E = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]]) @ R
    F = np.linalg.inv(K_front).T @ E @ np.linalg.inv(K_side)
    norm = np.linalg.norm(F)
    if norm < 1e-12:
        raise ValueError("Stereo baseline is zero.")
    return {"R_side_to_front": R.tolist(), "t_side_to_front_mm": t.tolist(),
            "baseline_mm": float(np.linalg.norm(t)), "E_side_to_front": E.tolist(),
            "F_undistorted_side_to_front": (F/norm).tolist(),
            "F_convention": "x_front_undistorted.T @ F @ x_side_undistorted = 0"}


def triangulate_world(front_pixels, side_pixels, front, side, K_front, dist_front,
                      K_side, dist_side):
    """Reconstruct mm coordinates from synchronized corresponding RAW pixels."""
    pf, ps = np.asarray(front_pixels, float), np.asarray(side_pixels, float)
    if pf.ndim != 2 or pf.shape[1] != 2 or pf.shape != ps.shape or not len(pf):
        raise ValueError("Need matching Nx2 pixel arrays.")
    if not np.isfinite(pf).all() or not np.isfinite(ps).all():
        raise ValueError("Pixels must be finite.")
    uf = cv2.undistortPoints(pf.reshape(-1, 1, 2), K_front, dist_front).reshape(-1, 2)
    us = cv2.undistortPoints(ps.reshape(-1, 1, 2), K_side, dist_side).reshape(-1, 2)
    projections = [np.column_stack((p["R_world_to_camera"], p["t_world_to_camera_mm"]))
                   for p in (front, side)]
    homogeneous = cv2.triangulatePoints(projections[0], projections[1], uf.T, us.T)
    if np.any(np.abs(homogeneous[3]) < 1e-10):
        raise ValueError("Triangulation is at infinity; check baseline and correspondence.")
    points = (homogeneous[:3]/homogeneous[3]).T
    for projection in projections:
        if np.any((points @ projection[:, :3].T + projection[:, 3])[:, 2] <= 0):
            raise ValueError("Triangulated points lie behind a camera.")
    return points


def ground_distance_mm(point_a, point_b):
    """Horizontal Euclidean distance, valid when world Z is truly vertical."""
    points = np.asarray([point_a, point_b], float)
    if points.shape != (2, 3) or not np.isfinite(points).all():
        raise ValueError("Need two finite world 3D points.")
    return float(np.linalg.norm(points[1, :2]-points[0, :2]))


def pixel_to_plane(pixel, pose, K, dist, plane_z_mm):
    """Ray-plane intersection, ONLY for a point known to lie on that plane."""
    pixel = np.asarray(pixel, float)
    if pixel.shape != (2,) or not np.isfinite(pixel).all() or not np.isfinite(plane_z_mm):
        raise ValueError("Need a finite pixel and measured plane height.")
    ray = cv2.undistortPoints(pixel.reshape(1, 1, 2), K, dist).reshape(2)
    R = np.asarray(pose["R_world_to_camera"])
    center = np.asarray(pose["camera_center_world_mm"])
    direction = R.T @ np.r_[ray, 1.0]
    if abs(direction[2]) < 1e-10:
        raise ValueError("Ray is parallel to the ground plane.")
    scale = (plane_z_mm-center[2])/direction[2]
    if scale <= 0:
        raise ValueError("Plane intersection is behind the camera.")
    return center+scale*direction
