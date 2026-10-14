"""Validated, reproducible metric measurements for main_metric (no Qt/model imports)."""
import copy
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from Src.calibration.chessboard_cross.chessboard_metric import write_json
from Src.calibration.chessboard_cross.chessboard_extrinsic import CONVENTION, load_rig
from Src.analysis.stride_workflow import (annotation_template, annotations_from_pose, find_videos,
                              measure_annotations, save_measurement, video_frame)

DEFINITION = "initial_rear_heel_to_same_rear_heel_at_front_foot_contact"


class MetricSession:
    def __init__(self, rig_path, video_dir, reference_frame=0, contact_frame=0,
                 cs_offset=0, heel="right", max_error=3.):
        self.rig_path = Path(rig_path).resolve()
        self.rig = load_rig(self.rig_path)
        self.rig_sha256 = hashlib.sha256(self.rig_path.read_bytes()).hexdigest()
        self.cameras = self.rig["intrinsics"]
        self.R = np.asarray(self.rig["R_cs_from_cf"], float)
        self.t = np.asarray(self.rig["t_cs_from_cf_mm"], float).reshape(3)
        self.video_dir = Path(video_dir).resolve()
        self.videos = find_videos(self.video_dir)
        self.reference_frame, self.contact_frame = reference_frame, contact_frame
        self.cs_offset, self.heel, self.max_error = cs_offset, heel, max_error
        if heel not in ("left", "right") or not np.isfinite(max_error) or not 0 < max_error <= 3:
            raise ValueError("腳跟須為 left/right；重投影門檻須在 (0, 3] px。")
        for value in (reference_frame, contact_frame, cs_offset):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError("幀號與偏移須為整數。")
        if reference_frame < 0 or contact_frame <= reference_frame:
            raise ValueError("落地幀必須晚於初始幀（從 0 開始）。")
        self.metadata = {}
        for camera, path in self.videos.items():
            shift = cs_offset if camera == "cs" else 0
            for frame in (reference_frame, contact_frame):
                _, metadata = video_frame(path, frame + shift, self.cameras[camera]["image_size"])
            stat = path.stat()
            self.metadata[camera] = {**metadata, "path": str(path.resolve()),
                                     "file_size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        fps = [m["fps"] for m in self.metadata.values()]
        if not np.isfinite(fps).all() or min(fps) <= 0 or abs(fps[0] - fps[1]) > .01:
            raise ValueError("兩相機須有相同、有效的 FPS；不同 FPS 請先以時間戳同步。")

    def template(self, ground=False):
        document = annotation_template(self.videos, self.cameras, self.contact_frame,
            self.cs_offset, self.heel, ground, self.reference_frame)
        document.update({"measurement_definition": DEFINITION, "heel_side": self.heel,
                         "point_source": "manual original-video pixel annotations"})
        return document

    def frame(self, camera, stage):
        number = self.reference_frame if stage == "reference" else self.contact_frame
        number += self.cs_offset if camera == "cs" else 0
        return video_frame(self.videos[camera], number, self.cameras[camera]["image_size"])[0]

    def pose_annotations(self):
        document = annotations_from_pose(self.videos, self.cameras, self.contact_frame,
            self.cs_offset, self.heel, self.reference_frame)
        document.update({"measurement_definition": DEFINITION, "heel_side": self.heel})
        document["pose_files"] = {c: {"path": str(p.with_suffix('.json')),
            "sha256": hashlib.sha256(p.with_suffix('.json').read_bytes()).hexdigest()}
            for c, p in self.videos.items()}
        return document

    def validate_annotations(self, document):
        expected = self.template()
        for camera, video in self.videos.items():
            if Path(document.get("videos", {}).get(camera, "")).resolve() != video.resolve():
                raise ValueError(f"選點的 {camera.upper()} 影片與目前影片不符。")
            for field in (f"frame_{camera}", f"reference_frame_{camera}"):
                if document.get(field) != expected[field]:
                    raise ValueError(f"選點 {field}={document.get(field)} 與目前設定 {expected[field]} 不符。")
        # Old CLI documents have no heel_side; require their explicit descriptions.
        side = document.get("heel_side")
        descriptions = (document.get("reference_description", ""), document.get("heel_description", ""))
        if side != self.heel and not (side is None and all(self.heel + " heel" in x for x in descriptions)):
            raise ValueError("選點的左右腳與設定不符，請使用同一隻後腳腳跟。")
        if document.get("measurement_definition", DEFINITION) != DEFINITION:
            raise ValueError("本入口量測初始後腳到同一後腳，選點的量測定義不符。")

    def measure(self, document):
        self.validate_annotations(document)
        result = measure_annotations(document, self.cameras, self.R, self.t, self.max_error)
        result.update({"schema": "rear_heel_measurement_v1", "status": "geometry_passed_unvalidated",
            "measurement_definition": DEFINITION, "heel_side": self.heel,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "calibration": {"path": str(self.rig_path), "sha256": self.rig_sha256,
                "method": self.rig["method"], "baseline_mm": float(np.linalg.norm(self.t)),
                "warnings": self.rig.get("warnings", []),
                "held_out_note": self.rig.get("held_out_note")},
            "videos": self.metadata, "annotations": copy.deepcopy(document),
            "synchronization": {"cs_frame_offset": self.cs_offset,
                "convention": "frame_cs = frame_cf + cs_frame_offset", "visually_verified": False},
            "contact_frame_visually_verified": False,
            "quality_limits": {"max_reprojection_px": self.max_error, "min_ray_angle_deg": 1.},
            "accuracy_note": "重投影檢查通過不等於厘米精度；仍須確認同步、落地幀、同一腳跟和獨立實尺距離。"})
        return result

    def compare(self, document, other_rig_path):
        other = MetricSession(other_rig_path, self.video_dir, self.reference_frame,
            self.contact_frame, self.cs_offset, self.heel, self.max_error)
        first, second = self.measure(document), other.measure(document)
        return {"calibration": second["calibration"], "distance_3d_cm": second["distance_3d_cm"],
            "ground_distance_cm": second["ground_distance_cm"],
            "difference_3d_cm": abs(first["distance_3d_cm"] - second["distance_3d_cm"]),
            "difference_ground_cm": (None if first["ground_distance_cm"] is None else
                abs(first["ground_distance_cm"] - second["ground_distance_cm"])),
            "note": "相同選點的模型敏感度比較，不是獨立驗證或誤差上限。"}

    def export(self, result, output=None):
        # Recheck geometry and provenance: never export a result from stale settings.
        fresh = self.measure(result["annotations"])
        if result["calibration"]["sha256"] != fresh["calibration"]["sha256"]:
            raise ValueError("校正已變更，請重新量測。")
        fresh["contact_frame_visually_verified"] = bool(result.get("contact_frame_visually_verified"))
        fresh["synchronization"]["visually_verified"] = bool(result.get("synchronization", {}).get("visually_verified"))
        if "comparison" in result:
            fresh["comparison"] = result["comparison"]
        if output is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            output = self.video_dir / "metric_measurements" / stamp
        output = Path(output).resolve()
        if output.exists() and any(output.iterdir()):
            raise ValueError("匯出資料夾已有檔案，請選新的資料夾以保留先前結果。")
        output.mkdir(parents=True, exist_ok=True)
        write_json(output / "calibration_snapshot.json", self.rig)
        write_json(output / "annotations.json", fresh["annotations"])
        save_measurement(output, fresh, fresh["annotations"], self.videos, self.cameras)
        return output


def infer_annotations(session, model):
    """Infer four original frames, with no tracker state or temporal smoothing.

    Exactly one detected person is required in each image. Camera/time identity
    still needs visual confirmation. This never rewrites existing pose JSONs.
    """
    from mmdet.apis import inference_detector
    from mmpose.apis import inference_topdown
    from mmpose.evaluation.functional import nms

    document = session.template()
    scores = {}
    index = 24 if session.heel == "left" else 25
    for name in ("reference", "heel"):
        for camera in ("cf", "cs"):
            frame = session.frame(camera, name)
            detection = inference_detector(model.detector, frame,
                                            test_pipeline=model.detector_test_pipeline)
            pred = detection.pred_instances.cpu().numpy()
            mask = (pred.labels == model.detect_args.det_cat_id) & (pred.scores >= model.detect_args.score_thr)
            boxes = np.concatenate([pred.bboxes[mask], pred.scores[mask, None]], axis=1)
            boxes = boxes[nms(boxes, model.detect_args.nms_thr)] if len(boxes) else boxes
            if len(boxes) != 1:
                raise ValueError(f"{name}/{camera}: 偵測到 {len(boxes)} 人，請人工選點以確認同一投手。")
            pose = inference_topdown(model.pose_estimator, frame, boxes[:, :4])[0].pred_instances
            xy = np.asarray(pose.keypoints)[0]
            confidence = np.asarray(pose.keypoint_scores)[0]
            if len(xy) != 26 or not np.isfinite(xy[index]).all() or not np.isfinite(confidence[index]) or confidence[index] < .5:
                raise ValueError(f"{name}/{camera}: 缺少可信的 HALPE-26 腳跟，請人工選點。")
            document["points"][name][camera] = xy[index].tolist()
            scores[name + "/" + camera] = float(confidence[index])
    document.update({"point_source": "HALPE-26 fresh inference, original frames, no temporal smoothing",
                     "heel_confidences": scores})
    return document
