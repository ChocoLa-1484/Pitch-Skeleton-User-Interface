"""Measure from a saved metric stereo rig; calibration is a separate command."""
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from Src.calibration.chessboard_cross.chessboard_metric import (DEFAULT_CALIBRATION, DEFAULT_VIDEO,
    read_json, save_image, triangulate, write_json)
from Src.calibration.chessboard_cross.chessboard_extrinsic import load_rig


def find_videos(folder):
    result = {}
    for camera in ("cf", "cs"):
        found = [p for p in Path(folder).glob("*.mp4") if p.name.lower().startswith(camera + "_")]
        if len(found) != 1:
            raise ValueError(f"Expected exactly one {camera.upper()}_*.mp4 in {folder}; found {len(found)}")
        result[camera] = found[0]
    return result


def video_frame(path, frame_number, expected_size):
    cap = cv2.VideoCapture(str(path))
    try:
        size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if size != tuple(expected_size):
            raise ValueError(f"{path.name}: video size {size} != calibrated size {expected_size}; recalibrate for this crop/resize.")
        if frame_number < 0 or frame_number >= count:
            raise ValueError(f"Frame {frame_number} outside {path.name} (0..{count-1})")
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ok, image = cap.read()
        if not ok:
            raise ValueError(f"Cannot decode {path.name}, frame {frame_number}")
        return image, {"frame_count": count, "fps": cap.get(cv2.CAP_PROP_FPS), "image_size": size}
    finally:
        cap.release()


def default_contact_frame(folder):
    files = sorted(Path(folder).glob("*BRandRapsodo.json"))
    if len(files) == 1:
        value = read_json(files[0]).get("Keyframes", {}).get("FC", {}).get("frame")
        if isinstance(value, int):
            return value, f"{files[0].name}:Keyframes.FC.frame (verify visually, zero-based)"
    return 0, "No FC event metadata; select the contact frame manually"


def inspect_videos(videos, cameras, frame, offset, output):
    report = {"frame_cf": frame, "frame_cs": frame + offset, "cameras": {},
              "synchronization": "frame_cs = frame_cf + offset; visually verify exposure timing"}
    for camera, path in videos.items():
        number = frame + (offset if camera == "cs" else 0)
        image, metadata = video_frame(path, number, cameras[camera]["image_size"])
        save_image(output / f"{camera}_frame_{number:04d}.jpg", image)
        metadata["video"] = str(path.resolve())
        pose_file = path.with_suffix(".json")
        if pose_file.exists():
            records = read_json(pose_file)
            frames = sorted({r["frame_number"] for r in records if "keypoints" in r})
            metadata.update({"pose_frame_count": len(frames), "pose_available_at_contact": number in frames})
        report["cameras"][camera] = metadata
    if abs(report["cameras"]["cf"]["fps"] - report["cameras"]["cs"]["fps"]) > .01:
        raise ValueError("Video frame rates differ; timestamp synchronization is required.")
    return report


def pose_motion_audit(videos, reference_frame, contact_frame, offset):
    """Pixel-space evidence only; never interpret this as a metric distance."""
    report = {"units": "original image pixels, NOT mm/cm", "cameras": {}}
    for camera, video in videos.items():
        path = video.with_suffix(".json")
        if not path.exists():
            continue
        shift = offset if camera == "cs" else 0
        records = read_json(path)
        initial = [r for r in records if r.get("frame_number") == reference_frame + shift]
        current = [r for r in records if r.get("frame_number") == contact_frame + shift]
        item = {"reference_frame": reference_frame + shift, "contact_frame": contact_frame + shift,
                "available": len(initial) == len(current) == 1}
        if item["available"]:
            for side, index in (("left", 24), ("right", 25)):
                a, b = initial[0].get("keypoints", []), current[0].get("keypoints", [])
                if min(len(a), len(b)) > index:
                    item[side + "_heel_pixel_displacement"] = float(np.linalg.norm(np.array(a[index][:2]) - b[index][:2]))
        report["cameras"][camera] = item
    return report


def annotation_template(videos, cameras, frame, offset, heel, ground=False, reference_frame=0):
    names = ["reference", "heel"] + (["ground0", "ground1", "ground2"] if ground else [])
    return {"schema": "stride_annotations_v1", "pixel_space": "original_distorted_pixels",
            "frame_cf": frame, "frame_cs": frame + offset,
            "reference_frame_cf": reference_frame, "reference_frame_cs": reference_frame + offset,
            "videos": {c: str(p.resolve()) for c, p in videos.items()},
            "image_sizes": {c: cameras[c]["image_size"] for c in videos},
            "reference_description": heel + " heel at initial standing frame",
            "heel_description": heel + " heel at front-foot contact",
            "points": {n: {"cf": None, "cs": None} for n in names},
            "instructions": "Reference uses reference_frame_cf/cs; heel and ground points use frame_cf/cs. Match the same physical point in both views at each time. Ground points must be three non-collinear points on the chosen horizontal plane. Do not use an ankle or assume the upright board is ground."}


def annotate(videos, cameras, frame, offset, heel, ground, destination, reference_frame=0):
    """Pick exact correspondences on video frames; no inferred feet or rubber."""
    if tuple(cameras["cf"]["image_size"]) != tuple(cameras["cs"]["image_size"]):
        raise ValueError("The two-panel picker requires equal image dimensions. Use original-pixel JSON annotations for unequal cameras.")
    document = annotation_template(videos, cameras, frame, offset, heel, ground, reference_frame)
    names = list(document["points"])
    sequence = [(name, camera) for name in names for camera in ("cf", "cs")]
    picked = []
    title = "Stride: initial rear heel -> rear heel at front-foot contact"
    scale = min(800 / cameras[c]["image_size"][0] for c in cameras)
    frames = {}
    cached_frames = {}
    def load(active_frame):
        for camera, path in videos.items():
            number = active_frame + (offset if camera == "cs" else 0)
            key = (camera, number)
            if key not in cached_frames:
                if len(cached_frames) >= 8:
                    cached_frames.pop(next(iter(cached_frames)))
                cached_frames[key] = video_frame(path, number, cameras[camera]["image_size"])[0]
            frames[camera] = cached_frames[key]
    load(reference_frame)
    width = int(frames["cf"].shape[1] * scale)
    height = int(frames["cf"].shape[0] * scale)
    def click(event, x, y, flags, param):
        if event != cv2.EVENT_LBUTTONDOWN or len(picked) == len(sequence) or y >= height:
            return
        name, camera = sequence[len(picked)]
        clicked_camera = "cf" if x < width else "cs"
        if clicked_camera != camera:
            return
        local_x = x if camera == "cf" else x - width
        picked.append([local_x / scale, y / scale])
    cv2.namedWindow(title, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(title, click)
    try:
        while True:
            active_frame = reference_frame if len(picked) < 2 else frame
            load(active_frame)
            tiles = []
            for camera in ("cf", "cs"):
                image = cv2.resize(frames[camera], (width, height))
                for (name, cam), pixel in zip(sequence, picked):
                    point_time = reference_frame if name == "reference" else frame
                    if cam == camera and point_time == active_frame:
                        xy = tuple((np.array(pixel) * scale).astype(int))
                        cv2.circle(image, xy, 5, (0, 255, 255), 2)
                        cv2.putText(image, name, (xy[0]+6, xy[1]-6), cv2.FONT_HERSHEY_SIMPLEX, .5, (0, 255, 255), 1)
                cv2.putText(image, f"{camera.upper()} frame {active_frame + (offset if camera == 'cs' else 0)}", (12, 25), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
                tiles.append(image)
            canvas = np.vstack([np.hstack(tiles), np.zeros((85, width * 2, 3), np.uint8)])
            prompt = f"Click {sequence[len(picked)][1].upper()}: {sequence[len(picked)][0]}" if len(picked) < len(sequence) else "ENTER: save and measure"
            cv2.putText(canvas, prompt, (10, height + 28), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
            cv2.putText(canvas, "j/l: -/+1 frame   a/d: -/+10   u: undo   r: reset   ESC: cancel", (10, height + 60), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 1)
            cv2.imshow(title, canvas)
            key = cv2.waitKey(30) & 0xff
            if key == 27 or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                return None
            if key in (ord("j"), ord("l"), ord("a"), ord("d")):
                new_frame = active_frame + {ord("j"): -1, ord("l"): 1, ord("a"): -10, ord("d"): 10}[key]
                try:
                    load(new_frame)
                    if len(picked) < 2:
                        reference_frame = new_frame
                        picked.clear()
                    else:
                        frame = new_frame
                        del picked[2:]
                except ValueError:
                    load(active_frame)
            if key == ord("u") and picked:
                picked.pop()
            if key == ord("r"):
                picked.clear()
            if key in (10, 13) and len(picked) == len(sequence):
                document["frame_cf"], document["frame_cs"] = frame, frame + offset
                document["reference_frame_cf"], document["reference_frame_cs"] = reference_frame, reference_frame + offset
                for (name, camera), pixel in zip(sequence, picked):
                    document["points"][name][camera] = pixel
                document["reference_description"] = f"User-selected {heel} heel at initial standing frame {reference_frame}"
                document["point_source"] = "manual original-video pixel annotations"
                write_json(destination, document)
                return document
    finally:
        cv2.destroyWindow(title)


def measure_annotations(document, cameras, R, t, max_error=3.):
    if document.get("schema") != "stride_annotations_v1" or document.get("pixel_space") != "original_distorted_pixels":
        raise ValueError("Annotations must use stride_annotations_v1 and original_distorted_pixels.")
    if not document.get("reference_description") or document["reference_description"].startswith("REQUIRED"):
        raise ValueError("Describe the physical reference point in reference_description.")
    points = document["points"]
    if not {"reference", "heel"} <= points.keys():
        raise ValueError("Annotations require reference and heel in both cameras.")
    for camera in ("cf", "cs"):
        for key in (f"frame_{camera}", f"reference_frame_{camera}"):
            number = document.get(key, document[f"frame_{camera}"])
            if not isinstance(number, int) or isinstance(number, bool) or number < 0:
                raise ValueError("Frame numbers must be nonnegative zero-based integers.")
        if tuple(document["image_sizes"][camera]) != tuple(cameras[camera]["image_size"]):
            raise ValueError("Annotation image dimensions do not match intrinsics.")
        for name, point in points.items():
            pixel = np.asarray(point[camera], float)
            if pixel.shape != (2,) or not np.isfinite(pixel).all():
                raise ValueError(f"Missing/invalid {camera} pixel for {name}.")
            if np.any(pixel < 0) or np.any(pixel >= np.array(cameras[camera]["image_size"])):
                raise ValueError(f"Pixel outside image: {name}/{camera}")
    names = list(points)
    a, b = np.array([points[n]["cf"] for n in names]), np.array([points[n]["cs"] for n in names])
    xyz, positive, errors, angles = triangulate(a, b, cameras, R, t)
    if not positive.all() or min(angles) < 1. or max(errors) > max_error:
        raise ValueError(f"Unreliable point geometry: positive_depth={positive.tolist()}, max_error={max(errors):.2f}px, min_ray_angle={min(angles):.2f}deg. Check same physical points and sync.")
    lookup = dict(zip(names, xyz))
    delta = lookup["heel"] - lookup["reference"]
    result = {"frame_cf": document["frame_cf"], "frame_cs": document["frame_cs"],
              "reference_frame_cf": document.get("reference_frame_cf", document["frame_cf"]),
              "reference_frame_cs": document.get("reference_frame_cs", document["frame_cs"]),
              "reference_description": document["reference_description"],
              "heel_description": document.get("heel_description"),
              "distance_3d_mm": float(np.linalg.norm(delta)),
              "distance_3d_cm": float(np.linalg.norm(delta)/10),
              "ground_distance_mm": None, "ground_distance_cm": None,
              "points_cf_camera_mm": lookup, "max_reprojection_px": float(max(errors)),
              "min_ray_angle_deg": float(min(angles)), "independently_validated_for_measurement": False}
    ground_names = ["ground0", "ground1", "ground2"]
    if any(n in points for n in ground_names):
        if not all(n in points for n in ground_names):
            raise ValueError("Provide all three ground points or none.")
        v = lookup["ground1"] - lookup["ground0"]
        w = lookup["ground2"] - lookup["ground0"]
        normal = np.cross(v, w)
        if np.linalg.norm(v) < 100 or np.linalg.norm(w) < 100 or np.linalg.norm(normal) / (np.linalg.norm(v)*np.linalg.norm(w)) < .1:
            raise ValueError("Ground points must be well spread (>100 mm) and non-collinear.")
        normal /= np.linalg.norm(normal)
        distance = np.linalg.norm(delta - normal * np.dot(delta, normal))
        result.update({"ground_distance_mm": float(distance), "ground_distance_cm": float(distance/10),
                       "ground_normal_cf": normal,
                       "absolute_height_difference_mm": float(abs(delta @ normal))})
    return result


def pose_heel(path, frame, heel, min_confidence=.5):
    records = [r for r in read_json(path) if r.get("frame_number") == frame and "keypoints" in r]
    if len(records) != 1:
        raise ValueError(f"{path.name}: need exactly one person at frame {frame}; found {len(records)}. Use --annotate for missing/ambiguous skeletons.")
    index = 24 if heel == "left" else 25
    keypoints = records[0]["keypoints"]
    if (len(keypoints) <= index or len(keypoints[index]) < 3
            or not np.isfinite(keypoints[index][:3]).all() or keypoints[index][2] < min_confidence):
        raise ValueError(f"{path.name}, frame {frame}: no confident HALPE-26 {heel} heel. Use --annotate.")
    return keypoints[index][:2]


def annotations_from_pose(videos, cameras, frame, offset, heel, reference_frame):
    document = annotation_template(videos, cameras, frame, offset, heel, reference_frame=reference_frame)
    # New 2D exports explicitly declare their legacy playback-timeline indices.
    # Resolve them to the original video frames used by this metric workflow.
    timeline_offsets = {camera: 0 for camera in videos}
    for metadata_path in sorted(next(iter(videos.values())).parent.glob('*_BRandRapsodo.json')):
        metadata = read_json(metadata_path)
        analysis = metadata.get('Analysis', {})
        if analysis.get('pose_frame_number_space') != 'playback_timeline':
            continue
        shift = analysis.get('frame_offset')
        if not isinstance(shift, int) or isinstance(shift, bool):
            raise ValueError(f"Invalid playback frame offset: {metadata_path.name}")
        for camera, path in videos.items():
            if path.stem == metadata.get('video_name'):
                timeline_offsets[camera] = 0
            elif path.stem == metadata.get('video_name_2'):
                timeline_offsets[camera] = shift
    for name, base_frame in (("reference", reference_frame), ("heel", frame)):
        for camera, path in videos.items():
            document["points"][name][camera] = pose_heel(path.with_suffix(".json"),
                base_frame + (offset if camera == "cs" else 0) + timeline_offsets[camera], heel)
    document["point_source"] = "HALPE-26 JSON, exact frame_number; heel indices 24/25; confidence >=0.5"
    document["pose_timeline_offsets"] = timeline_offsets
    return document


def save_measurement(output, result, annotations, videos, cameras):
    write_json(output / "measurement.json", result)
    fields = ["reference_frame_cf", "reference_frame_cs", "frame_cf", "frame_cs", "distance_3d_cm", "ground_distance_cm", "max_reprojection_px", "min_ray_angle_deg"]
    with (output / "measurement.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fields)
        writer.writeheader()
        writer.writerow({k: result[k] for k in fields})
    for camera in ("cf", "cs"):
        frame = annotations[f"frame_{camera}"]
        reference_frame = annotations.get(f"reference_frame_{camera}", frame)
        for stage, number in (("initial", reference_frame), ("contact", frame)):
            image, _ = video_frame(videos[camera], number, cameras[camera]["image_size"])
            for name, point in annotations["points"].items():
                if (reference_frame if name == "reference" else frame) != number:
                    continue
                xy = tuple(np.rint(point[camera]).astype(int))
                cv2.circle(image, xy, 6, (0, 255, 255), 2)
                cv2.putText(image, name, (xy[0]+8, xy[1]-8), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 255, 255), 2)
            cv2.putText(image, f"{camera.upper()} {stage} frame {number}", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, .8, (0, 255, 255), 2)
            save_image(output / f"{camera}_{stage}_measurement.jpg", image)


def main(method):
    parser = argparse.ArgumentParser(description=f"{method}: foot-contact measurement using a saved stereo.json")
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--output", type=Path, help=f"Default: calibration/{method}")
    parser.add_argument("--video-dir", type=Path, default=DEFAULT_VIDEO)
    parser.add_argument("--frame", type=int, help="Zero-based CF frame; default from Keyframes.FC.frame")
    parser.add_argument("--reference-frame", type=int, default=0, help="Initial standing CF frame for the rear heel (default 0)")
    parser.add_argument("--cs-frame-offset", type=int, default=0)
    parser.add_argument("--stereo", type=Path, help="Saved rig; default: calibration/{method}/stereo.json")
    parser.add_argument("--annotations", type=Path, help="Saved original-pixel point correspondences")
    parser.add_argument("--annotate", action="store_true", help="Open video frame picker and click reference/heel in both cameras")
    parser.add_argument("--pose-annotations", action="store_true", help="Read exact heel keypoints at reference/contact frames; refuse missing skeletons")
    parser.add_argument("--ground-plane", action="store_true", help="Also pick 3 ground points to calculate horizontal distance")
    parser.add_argument("--heel", choices=("left", "right"), default="right", help="Label of measured heel (default rear/right); does not substitute ankle points")
    parser.add_argument("--reference-description", help="Optional description of the initial rear-heel reference")
    parser.add_argument("--expected-cm", nargs=2, type=float, default=(60., 70.), help="Validation interval only; NEVER used to fit scale")
    parser.add_argument("--max-reprojection-px", type=float, default=3.)
    args = parser.parse_args()
    if sum(bool(x) for x in (args.annotate, args.annotations, args.pose_annotations)) > 1:
        parser.error("Choose --annotate, --annotations or --pose-annotations")
    if args.pose_annotations and args.ground_plane:
        parser.error("--ground-plane needs manually annotated ground points; use --annotate")
    if not 0 < args.expected_cm[0] <= args.expected_cm[1] or not np.isfinite(args.expected_cm).all():
        parser.error("expected-cm must be a positive finite interval")
    if not np.isfinite(args.max_reprojection_px) or args.max_reprojection_px <= 0:
        parser.error("max-reprojection-px must be positive")
    cv2.setNumThreads(4)
    rig_path = args.stereo or args.calibration / method / "stereo.json"
    if not rig_path.is_file():
        raise FileNotFoundError(
            f"Missing {rig_path}; run python Src/calibration/chessboard_cross/calibrate_chessboard_extrinsic.py --method {method} first")
    result = load_rig(rig_path)
    if result['method'] != method:
        raise ValueError(f"Expected a {method} rig, got {result['method']}")
    cameras = result['intrinsics']
    R = np.asarray(result['R_cs_from_cf'], float)
    t = np.asarray(result['t_cs_from_cf_mm'], float).reshape(3)
    warnings = result.get('warnings', [])
    output = args.output or args.calibration / method
    output.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    write_json(output / "run_status.json", {"status": "incomplete", "started_utc": started, "measurement": None,
                                           "stereo": str(rig_path.resolve())})
    videos = find_videos(args.video_dir)
    frame, source = default_contact_frame(args.video_dir)
    if args.frame is not None:
        frame, source = args.frame, "--frame"
    document = read_json(args.annotations) if args.annotations else None
    if document:
        for c in videos:
            if Path(document["videos"][c]).resolve() != videos[c].resolve():
                raise ValueError(f"Annotations refer to a different {c} video")
        frame = document["frame_cf"]
        args.cs_frame_offset = document["frame_cs"] - frame
        args.reference_frame = document.get("reference_frame_cf", frame)
        source = "annotations"
    audit = inspect_videos(videos, cameras, frame, args.cs_frame_offset, output)
    audit["frame_source"] = source
    audit["warnings"] = ["FC metadata is only a starting frame. Verify actual foot contact visually; no automatic contact detection is performed.",
                         "Initial rear heel to the SAME rear heel measures rear-foot displacement, not the front-foot stride."]
    write_json(output / "video_audit.json", audit)
    write_json(output / "pose_motion_audit.json", pose_motion_audit(videos, args.reference_frame, frame, args.cs_frame_offset))
    write_json(output / "annotations.template.json", annotation_template(videos, cameras, frame,
               args.cs_frame_offset, args.heel, args.ground_plane, args.reference_frame))
    for camera, path in videos.items():
        number = (document.get(f"reference_frame_{camera}", document[f"frame_{camera}"]) if document else
                  args.reference_frame + (args.cs_frame_offset if camera == "cs" else 0))
        initial, _ = video_frame(path, number, cameras[camera]["image_size"])
        save_image(output / f"{camera}_initial_frame_{number:04d}.jpg", initial)
    if args.annotate:
        document = annotate(videos, cameras, frame, args.cs_frame_offset, args.heel, args.ground_plane, output / "annotations.json", args.reference_frame)
    if args.pose_annotations:
        document = annotations_from_pose(videos, cameras, frame, args.cs_frame_offset, args.heel, args.reference_frame)
    measurement = None
    if document:
        if args.reference_description:
            document["reference_description"] = args.reference_description
        measurement = measure_annotations(document, cameras, R, t, args.max_reprojection_px)
        value = measurement["ground_distance_cm"]
        measurement["expected_interval_cm"] = args.expected_cm
        measurement["within_expected_ground_interval"] = None if value is None else bool(args.expected_cm[0] <= value <= args.expected_cm[1])
        measurement["calibration_warnings"] = warnings
        measurement["status"] = "review_required"
        if measurement["ground_distance_cm"] is None:
            measurement["warnings"] = ["No ground plane supplied: only 3D displacement is available; no horizontal-distance validation performed."]
        save_measurement(output, measurement, document, videos, cameras)
        write_json(output / "annotations.json", document)
    summary = [f"Method: {method}", f"Baseline: {np.linalg.norm(t):.2f} mm",
               f"Saved calibration: {rig_path.resolve()}",
               f"FC frame: {document['frame_cf'] if document else frame} (CF); {document['frame_cs'] if document else frame + args.cs_frame_offset} (CS)",
               f"Reference: initial standing {args.heel} heel, frame {document.get('reference_frame_cf', frame) if document else args.reference_frame} (CF)",
               "Verify FC visually. Same-rear-heel displacement differs from front-foot stride."]
    if measurement:
        summary += [f"3D reference-to-heel: {measurement['distance_3d_cm']:.2f} cm",
                    f"Ground distance: {measurement['ground_distance_cm']} cm",
                    "Status: review_required; verify frame, reference, heel and independent measured length."]
    else:
        summary += ["Measurement pending: run --annotate (optionally --ground-plane), then reuse annotations.json in both methods.",
                    "No stride distance inferred from missing skeleton data or from the 60-70 cm validation interval."]
    summary += ["REVIEW: " + w for w in warnings]
    (output / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8")
    write_json(output / "run_status.json", {"status": "measurement_review_required" if measurement else "needs_annotations", "started_utc": started,
               "stereo": str(rig_path.resolve()), "measurement": "measurement.json" if measurement else None})
    print("\n".join(summary))
    print(f"Saved: {output}")


def entrypoint(method):
    try:
        main(method)
    except (ValueError, KeyError, FileNotFoundError, cv2.error) as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
