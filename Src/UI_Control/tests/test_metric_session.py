"""End-to-end measurement tests with synthetic videos and known metric geometry."""
import copy
import csv
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cv_utils.chessboard_metric import read_json, write_json
from cv_utils.metric_session import CONVENTION, MetricSession, load_rig
from cv_utils import stride_workflow


class MetricSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.K = np.array([[500., 0, 320], [0, 500., 240], [0, 0, 1]])
        self.t = np.array([-1000., 0, 0])
        camera = {"intrinsic_matrix": self.K.tolist(), "distortion_coefficients": [0.] * 5,
                  "image_size": [640, 480]}
        self.rig = {"schema": "metric_stereo_v1", "units": "mm", "method": "pnp",
            "coordinate_convention": CONVENTION, "R_cs_from_cf": np.eye(3).tolist(),
            "t_cs_from_cf_mm": self.t.tolist(), "intrinsics": {"cf": camera, "cs": camera},
            "warnings": ["synthetic test rig"]}
        self.rig_path = self.root / "stereo.json"
        write_json(self.rig_path, self.rig)
        self.xyz = {"reference": [0., 0, 5000], "heel": [650., 350, 5000],
            "ground0": [-400., 0, 4600], "ground1": [400., 0, 4600], "ground2": [-400., 0, 5400]}
        for camera in ("cf", "cs"):
            video = self.root / f"{camera.upper()}_test.mp4"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 30., (640, 480))
            self.assertTrue(writer.isOpened())
            for _ in range(8):
                writer.write(np.zeros((480, 640, 3), np.uint8))
            writer.release()
            records = []
            for stage, number in (("reference", 1), ("heel", 5)):
                points = [[100., 100., .9] for _ in range(26)]
                points[25] = self.project(self.xyz[stage], camera) + [.9]
                records.append({"frame_number": number + (1 if camera == "cs" else 0),
                                "person_id": 1, "keypoints": points})
            write_json(video.with_suffix(".json"), records)
        self.session = MetricSession(self.rig_path, self.root, 1, 5, 1, "right")

    def project(self, xyz, camera):
        t = self.t if camera == "cs" else np.zeros(3)
        return cv2.projectPoints(np.array([xyz], float), np.zeros(3), t, self.K, None)[0].reshape(2).tolist()

    def annotations(self, ground=True):
        document = self.session.template(ground)
        for name in document["points"]:
            for camera in ("cf", "cs"):
                document["points"][name][camera] = self.project(self.xyz[name], camera)
        return document

    def test_measurement_cli_only_reads_saved_rig_without_refitting_or_intrinsic_files(self):
        annotations_path = self.root / 'annotations.json'
        write_json(annotations_path, self.annotations())
        before = self.rig_path.read_bytes()
        output = self.root / 'measurement'
        argv = ['measure_stride_pnp.py', '--stereo', str(self.rig_path),
                '--video-dir', str(self.root), '--output', str(output),
                '--annotations', str(annotations_path)]
        with patch.object(sys, 'argv', argv), \
             patch('cv_utils.chessboard_extrinsic.fit_chessboard_rig',
                   side_effect=AssertionError('Measurement must not recalibrate')):
            stride_workflow.main('pnp')
        self.assertEqual(self.rig_path.read_bytes(), before)
        self.assertFalse((output / 'stereo.json').exists())
        result = read_json(output / 'measurement.json')
        self.assertAlmostEqual(result['ground_distance_cm'], 65., places=5)
        status = read_json(output / 'run_status.json')
        self.assertEqual(status['stereo'], str(self.rig_path.resolve()))

    def test_missing_or_wrong_method_rig_is_rejected_before_measurement(self):
        output = self.root / 'measurement'
        argv = ['measure_stride_pnp.py', '--calibration', str(self.root / 'missing'),
                '--output', str(output)]
        with patch.object(sys, 'argv', argv):
            with self.assertRaisesRegex(FileNotFoundError, 'calibrate_chessboard_extrinsic.py'):
                stride_workflow.main('pnp')
        argv += ['--stereo', str(self.rig_path)]
        with patch.object(sys, 'argv', argv):
            with self.assertRaisesRegex(ValueError, 'Expected a 8point rig'):
                stride_workflow.main('8point')
        self.assertFalse(output.exists())

    def test_true_distance_ground_projection_and_export_match(self):
        result = self.session.measure(self.annotations())
        self.assertAlmostEqual(result["ground_distance_cm"], 65., places=6)
        self.assertAlmostEqual(result["distance_3d_cm"], np.hypot(65, 35), places=6)
        self.assertEqual(result["frame_cs"], 6)
        self.assertEqual(result["reference_frame_cs"], 2)
        self.assertFalse(result["independently_validated_for_measurement"])
        output = self.session.export(result)
        stored = read_json(output / "measurement.json")
        self.assertEqual(stored["ground_distance_cm"], result["ground_distance_cm"])
        with (output / "measurement.csv").open(encoding="utf-8-sig", newline="") as file:
            row = next(csv.DictReader(file))
        self.assertAlmostEqual(float(row["ground_distance_cm"]), 65.)
        self.assertEqual(len(list(output.glob("*_measurement.jpg"))), 4)
        self.assertEqual(read_json(output / "annotations.json"), stored["annotations"])
        with self.assertRaisesRegex(ValueError, "已有檔案"):
            self.session.export(result, output)

    def test_pose_uses_same_rear_heel_exact_frames_and_offset(self):
        result = self.session.measure(self.session.pose_annotations())
        self.assertAlmostEqual(result["distance_3d_cm"], np.hypot(65, 35), places=6)
        self.assertIsNone(result["ground_distance_cm"])
        self.assertEqual(result["heel_side"], "right")

    def test_missing_pose_does_not_interpolate_or_return_zero(self):
        path = self.root / "CF_test.json"
        write_json(path, read_json(path)[:1])
        with self.assertRaisesRegex(ValueError, "frame 5"):
            self.session.pose_annotations()

    def test_new_main_pose_exports_resolve_playback_to_original_frames(self):
        path = self.root / "CS_test.json"
        records = read_json(path)
        for row in records:
            row['frame_number'] += 1
        write_json(path, records)
        write_json(self.root / 'CF_test_BRandRapsodo.json', {
            'video_name': 'CF_test', 'video_name_2': 'CS_test',
            'Analysis': {'pose_frame_number_space': 'playback_timeline', 'frame_offset': 1}})
        document = self.session.pose_annotations()
        self.assertEqual(document['pose_timeline_offsets'], {'cf': 0, 'cs': 1})
        self.assertAlmostEqual(self.session.measure(document)['distance_3d_cm'], np.hypot(65, 35), places=6)

    def test_multiple_people_and_low_confidence_rejected(self):
        path = self.root / "CF_test.json"
        records = read_json(path)
        write_json(path, records + [records[0]])
        with self.assertRaisesRegex(ValueError, "found 2"):
            self.session.pose_annotations()
        records[0]["keypoints"][25][2] = .2
        write_json(path, records)
        with self.assertRaisesRegex(ValueError, "confident"):
            self.session.pose_annotations()

    def test_stale_or_wrong_annotations_rejected(self):
        for field, value in (("frame_cs", 5), ("reference_frame_cf", 0),
                             ("heel_side", "left"), ("measurement_definition", "front_heel")):
            document = self.annotations()
            document[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.session.measure(document)
        document = self.annotations()
        document["videos"]["cf"] = str(self.root / "another.mp4")
        with self.assertRaisesRegex(ValueError, "影片不符"):
            self.session.measure(document)

    def test_bad_geometry_is_not_exportable(self):
        document = self.annotations(False)
        document["points"]["heel"]["cs"][1] += 50
        with self.assertRaisesRegex(ValueError, "Unreliable"):
            self.session.measure(document)
        self.assertFalse((self.root / "metric_measurements").exists())

    def test_negative_out_of_range_and_reversed_frames_rejected(self):
        for reference, contact, offset in ((0, 5, -1), (1, 7, 1), (5, 1, 0), (1, 1, 0)):
            with self.subTest((reference, contact, offset)), self.assertRaises(ValueError):
                MetricSession(self.rig_path, self.root, reference, contact, offset)

    def test_bad_unit_rotation_size_and_distortion_rejected(self):
        variants = []
        for field, value in (("units", "m"), ("R_cs_from_cf", np.zeros((3, 3)).tolist()),
                              ("coordinate_convention", "X_cf = R @ X_cs + t")):
            rig = copy.deepcopy(self.rig)
            rig[field] = value
            variants.append(rig)
        rig = copy.deepcopy(self.rig)
        rig["intrinsics"]["cf"]["distortion_coefficients"] = [0., 0.]
        variants.append(rig)
        for rig in variants:
            write_json(self.rig_path, rig)
            with self.assertRaises(ValueError):
                load_rig(self.rig_path)
        rig = copy.deepcopy(self.rig)
        rig["intrinsics"]["cf"]["image_size"] = [1280, 960]
        write_json(self.rig_path, rig)
        with self.assertRaisesRegex(ValueError, "video size"):
            MetricSession(self.rig_path, self.root, 1, 5, 1)

    def test_scale_comparison_uses_same_points_and_never_fits_expected_distance(self):
        other = copy.deepcopy(self.rig)
        other["method"] = "8point"
        other["t_cs_from_cf_mm"] = (2 * self.t).tolist()
        path = self.root / "other.json"
        write_json(path, other)
        result = self.session.compare(self.annotations(), path)
        self.assertAlmostEqual(result["ground_distance_cm"], 130., places=6)
        self.assertAlmostEqual(result["difference_ground_cm"], 65., places=6)


if __name__ == "__main__":
    unittest.main()
