"""Batch HSV/PnP calibration of the eight-ball cross. Run with --help."""
import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

import cv2
import numpy as np

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))   # Src/UI_Control：讓 cv_utils 等模組可匯入
from cv_utils.cross_calibration import (
    DetectionConfig, camera_parameters, detect_balls, fit_static_camera,
    label_and_solve, object_points, rotation_difference_deg, stereo_geometry,
    triangulate_world,
)
from cv_utils.fluorescent_balls import read_image, write_image

ROOT = Path(__file__).resolve().parents[4]   # 專案根目錄
EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")


def image_pairs(folder):
    groups = []
    for role in ("cf", "cs"):
        directory = folder / role
        if not directory.is_dir():
            raise ValueError("Missing folder: {}".format(directory))
        groups.append({p.name for p in directory.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS})
    if not groups[0] or groups[0] != groups[1]:
        raise ValueError("cf/cs must contain the same nonempty set of image filenames; mismatches: {}"
                         .format(sorted(groups[0] ^ groups[1])))
    return sorted(groups[0])


def annotated_image(image, candidates, pose, error=None, detect_only=False):
    canvas = image.copy()
    selected = pose.get("candidate_indices_ball1_to_ball8", []) if pose else []
    for i, candidate in enumerate(candidates):
        point = tuple(np.rint(candidate["center_px"]).astype(int))
        number = selected.index(i)+1 if i in selected else None
        colour = (0, 255, 255) if number else (0, 165, 255)
        cv2.circle(canvas, point, 8, colour, 1, cv2.LINE_AA)
        cv2.putText(canvas, "b{}".format(number) if number else "?{}".format(i),
                    (point[0]+10, point[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
    if pose:
        for point in np.rint(pose["projected_points_px"]).astype(int):
            cv2.drawMarker(canvas, tuple(point), (0, 255, 0), cv2.MARKER_CROSS, 6, 1)
        caption = "{}  RMS {:.3f} px  max {:.3f} px".format(
            "FIT" if pose["accepted"] else "REJECTED", pose["rms_px"], pose["max_error_px"])
    elif detect_only and not error:
        caption = "DETECTION ONLY: {} unlabelled candidates".format(len(candidates))
    else:
        caption = "REJECTED: " + str(error or "no pose")
    cv2.putText(canvas, caption[:150], (15, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                (0, 255, 255), 2, cv2.LINE_AA)
    return canvas


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--images", type=Path, default=ROOT / "Db/Record/Calibrate_Picture_Auto")
    parser.add_argument("--intrinsics", type=Path,
                        default=ROOT / "Db/Calibration/intrinsic_20261008/intrinsics_all.json")
    parser.add_argument("--front-sn", default="24380115")
    parser.add_argument("--side-sn", default="21055356")
    parser.add_argument("--horizontal-depth-mm", type=float, default=15.0,
                        help="+15 = horizontal balls behind vertical rod, viewed with ball6 left/ball8 right")
    parser.add_argument("--ball3-height-mm", type=float,
                        help="Measured ball3 centre height above the chosen ground plane; not inferred from ball5")
    parser.add_argument("--output", type=Path, help="New, empty output directory (existing files are never replaced)")
    parser.add_argument("--detect-only", action="store_true", help="Save candidates/masks without loading intrinsics")
    parser.add_argument("--h-min", type=int, default=135)
    parser.add_argument("--h-max", type=int, default=168)
    parser.add_argument("--s-min", type=int, default=105)
    parser.add_argument("--v-min", type=int, default=50)
    parser.add_argument("--min-radius-px", type=float, default=3.0)
    parser.add_argument("--min-area-px", type=float, default=20.0)
    parser.add_argument("--max-area-px", type=float, default=2500.0)
    parser.add_argument("--max-candidates", type=int, default=24)
    for role in ("cf", "cs"):
        parser.add_argument("--roi-"+role, type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    parser.add_argument("--max-rms-px", type=float, default=2.0)
    parser.add_argument("--max-error-px", type=float, default=4.0)
    parser.add_argument("--static-tolerance-px", type=float, default=3.0)
    parser.add_argument("--min-frames", type=int, default=3)
    return parser


def run(args):
    config = DetectionConfig(args.h_min, args.h_max, args.s_min, args.v_min,
                             args.min_radius_px, args.min_area_px, args.max_area_px, args.max_candidates)
    config.validate()
    thresholds = [args.max_rms_px, args.max_error_px, args.static_tolerance_px]
    if not np.isfinite(thresholds).all() or min(thresholds) <= 0 or args.min_frames < 3:
        raise ValueError("Thresholds must be positive and min-frames must be at least 3.")
    if args.ball3_height_mm is not None and (not np.isfinite(args.ball3_height_mm) or args.ball3_height_mm <= 0):
        raise ValueError("Ball3 height must be a positive measurement in mm.")
    if args.front_sn == args.side_sn:
        raise ValueError("Front and side camera serials must differ.")
    world = object_points(args.horizontal_depth_mm)
    names = image_pairs(args.images)
    serials = {"cf": args.front_sn, "cs": args.side_sn}
    intrinsics = {} if args.detect_only else json.loads(args.intrinsics.read_text(encoding="utf-8"))
    if not args.detect_only:
        for sn in serials.values():
            if sn not in intrinsics:
                raise ValueError("SN{} missing from {}".format(sn, args.intrinsics))
    output = args.output or ROOT / "Db/Calibration/cross_pnp" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1, "method": "HSV + distance peaks + geometry + SQPnP/IPPE seeds + LM",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "opencv_version": cv2.__version__, "status": "detection_only" if args.detect_only else "review_required",
        "images_directory": str(args.images.resolve()), "stationary_device_and_cameras": True,
        "camera_serials": serials, "detector": asdict(config),
        "thresholds": {"rms_px": args.max_rms_px, "max_error_px": args.max_error_px,
                       "static_tolerance_px": args.static_tolerance_px, "min_frames": args.min_frames},
        "world": {"unit": "mm", "origin": "ball3 centre", "X": "ball6 toward ball8",
                  "Z": "up along ball5 toward ball1", "Y": "Z cross X (behind labelled face)",
                  "horizontal_depth_mm": args.horizontal_depth_mm,
                  "ball_ids": ["ball{}".format(i) for i in range(1, 9)],
                  "object_points_mm": world.tolist(),
                  "ground_z_mm": -args.ball3_height_mm if args.ball3_height_mm is not None else None},
        "transform_convention": "X_camera_mm = R_world_to_camera @ X_world_mm + t_world_to_camera_mm",
        "projection_convention": "P_undistorted_pixels requires undistorted pixels; raw pixels require K and distortion",
        "warnings": [], "frames": {"cf": {}, "cs": {}}, "cameras": {},
    }
    if not args.detect_only:
        report["intrinsics_source"] = str(args.intrinsics.resolve())
        report["intrinsics_sha256"] = hashlib.sha256(args.intrinsics.read_bytes()).hexdigest()
        report["intrinsics"] = {role: intrinsics[sn] for role, sn in serials.items()}
    if args.ball3_height_mm is None:
        report["warnings"].append("Ground plane height is unknown. Ball3 origin is not the pitching rubber/ground.")
    parameters = {}
    for role, sn in serials.items():
        for name in names:
            frame = {"candidates": [], "pose": None, "error": None}
            image = None
            try:
                image = read_image(args.images / role / name)
                frame["image_size"] = [image.shape[1], image.shape[0]]
                if not args.detect_only:
                    parameters[role] = camera_parameters(intrinsics[sn], frame["image_size"])
                candidates, mask = detect_balls(image, config, getattr(args, "roi_"+role))
                frame["candidates"] = candidates
                # Include original extension in artifact name to avoid stem collisions.
                write_image(output / role / (name+".mask.png"), mask)
                if not args.detect_only:
                    K, dist = parameters[role]
                    frame["pose"] = label_and_solve(candidates, world, K, dist, args.max_rms_px,
                                                    args.max_error_px, args.max_candidates)
            except (ValueError, cv2.error) as exc:
                frame["error"] = str(exc)
            report["frames"][role][name] = frame
            if image is not None:
                annotated = annotated_image(image, frame["candidates"], frame["pose"], frame["error"], args.detect_only)
                write_image(output / role / (name+".annotated.jpg"), annotated)
                if frame["candidates"]:
                    centers = np.array([c["center_px"] for c in frame["candidates"]])
                    x0, y0 = np.maximum(np.floor(centers.min(axis=0)).astype(int)-45, 0)
                    x1, y1 = np.minimum(np.ceil(centers.max(axis=0)).astype(int)+65,
                                        [image.shape[1], image.shape[0]])
                    crop = annotated[y0:y1, x0:x1]
                    write_image(output / role / (name+".detail.jpg"),
                                cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))
            pose = frame["pose"]
            summary = "RMS {:.3f} px, {}".format(pose["rms_px"], "fit" if pose["accepted"] else "REJECTED") if pose else frame["error"] or "detected"
            print("{}/{}: {} candidates, {}".format(role, name, len(frame["candidates"]), summary), flush=True)
        if args.detect_only:
            continue
        try:
            poses = {name: frame["pose"] for name, frame in report["frames"][role].items() if frame["pose"]}
            K, dist = parameters[role]
            joint = fit_static_camera(poses, world, K, dist, args.max_rms_px, args.max_error_px,
                                      args.static_tolerance_px, args.min_frames)
            centers = np.array([poses[name]["camera_center_world_mm"] for name in joint["used_frames"]])
            center_deviation = np.linalg.norm(centers-np.median(centers, axis=0), axis=1)
            angle_deviation = [rotation_difference_deg(poses[name], joint) for name in joint["used_frames"]]
            joint["individual_pose_center_max_deviation_mm"] = float(center_deviation.max())
            joint["individual_pose_rotation_max_deviation_deg"] = float(max(angle_deviation))
            joint["distance_to_ball3_mm"] = float(np.linalg.norm(joint["camera_center_world_mm"]))
            joint["serial"] = sn
            report["cameras"][role] = joint
            if center_deviation.max() > 50 or max(angle_deviation) > 2 or joint["pose_ambiguous"]:
                report["warnings"].append(role+": pose is unstable or ambiguous despite small reprojection error; verify intrinsics, depth geometry and camera placement.")
            if K[0, 0]/intrinsics[sn]["image_size"][0] > 5:
                report["warnings"].append(role+": fx is over five image widths; verify serial/lens/focus/ROI and independently measured camera-to-target distance.")
        except (ValueError, KeyError, cv2.error) as exc:
            report["warnings"].append(role+": no joint extrinsic: "+str(exc))
    if len(report["cameras"]) == 2:
        front, side = report["cameras"]["cf"], report["cameras"]["cs"]
        Kf, df = parameters["cf"]
        Ks, ds = parameters["cs"]
        try:
            report["stereo"] = stereo_geometry(front, side, Kf, Ks)
            reconstructed = triangulate_world(front["median_image_points_px"], side["median_image_points_px"],
                                             front, side, Kf, df, Ks, ds)
            errors = np.linalg.norm(reconstructed-world, axis=1)
            report["target_self_check"] = {
                "note": "Same calibration target, not independent measurement validation.",
                "triangulated_ball_centres_mm": reconstructed.tolist(),
                "per_ball_error_mm": errors.tolist(), "rms_mm": float(np.sqrt(np.mean(errors**2))),
                "max_mm": float(errors.max()),
            }
            if errors.max() > 10:
                report["warnings"].append("Target triangulation exceeds 10 mm; do not use for stride measurements before validation.")
        except ValueError as exc:
            report["warnings"].append("Stereo check failed: "+str(exc))
    if not args.detect_only:
        report["status"] = "review_required" if len(report["cameras"]) == 2 else "incomplete"
        report["independently_validated_for_measurement"] = False
        report["warnings"].append("Validate known distances throughout the pitching volume. Pixel fit error is not metric accuracy.")
        # Deliberately separate schema: old F/recoverPose reconstruction loses
        # metric scale and uses raw pixels. It cannot consume this F directly.
        extrinsics = {key: value for key, value in report.items() if key != "frames"}
        save_json(output / "extrinsics.json", extrinsics)
    save_json(output / "report.json", report)
    lines = ["Eight-ball cross calibration", "Status: "+report["status"], ""]
    for role, camera in report["cameras"].items():
        lines.append("{} SN{}: {} frames, RMS {:.3f} px, max {:.3f} px, camera-to-ball3 {:.3f} m".format(
            role, camera["serial"], len(camera["used_frames"]), camera["rms_px"], camera["max_error_px"],
            camera["distance_to_ball3_mm"]/1000))
    lines.extend(["", "Warnings:"]+["- "+warning for warning in report["warnings"]])
    (output / "summary.txt").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print("\n"+"\n".join(lines)+"\n\nOutput: "+str(output), flush=True)
    return output, report


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _, report = run(args)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, "Calibration failed: {}\n".format(exc))
    return 2 if report["status"] == "incomplete" else 0


if __name__ == "__main__":
    sys.exit(main())
