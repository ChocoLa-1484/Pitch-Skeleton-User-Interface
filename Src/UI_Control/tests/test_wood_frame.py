"""Synthetic ground-truth checks for paired wood-frame PnP and persisted rigs."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from Src.calibration.chessboard_extrinsic import load_rig
from Src.calibration import wood_frame
from Src.calibration.wood_frame import (fit_wood_frame_rig, load_wood_frame_rig,
    rectangle_target, save_wood_frame_rig, validate_target, wood_frame_target)


class WoodFrameTests(unittest.TestCase):
    def setUp(self):
        cv2.setNumThreads(1)
        self.K = np.array([[1400., 0, 960.], [0, 1380., 542.], [0, 0, 1.]])
        self.cameras = {role: {"intrinsic_matrix": self.K.tolist(),
                              "distortion_coefficients": [.005, -.002, 0., 0., 0.],
                              "image_size": [1920, 1084]} for role in ("cf", "cs")}
        self.target = rectangle_target(600., 800., 3.)
        self.R = cv2.Rodrigues(np.array([.01, -.17, .025]))[0]
        self.t = np.array([780., 20., 70.])
        self.pairs, self.truth = self.make_pairs(self.target)

    def make_pairs(self, target, count=8, noise=0., base=None):
        ids, xyz = validate_target(target)
        # Rotate the XZ rectangle to face the cameras, then vary its tilt.
        if base is None:
            base = cv2.Rodrigues(np.array([np.pi / 2, 0., 0.]))[0]
        view_ids = target.get("view_point_ids", {role: ids for role in ("cf", "cs")})
        rng = np.random.default_rng(851)
        pairs, truth = [], {}
        for index in range(count):
            Rcf = cv2.Rodrigues(rng.uniform(-.35, .35, 3))[0] @ base
            tcf = rng.uniform([-600., 100., 2800.], [-100., 450., 4500.])
            pair = {"id": str(index)}
            truth[str(index)] = (Rcf, tcf)
            for role in ("cf", "cs"):
                R, t = (Rcf, tcf) if role == "cf" else (self.R @ Rcf, self.R @ tcf + self.t)
                role_xyz = xyz[[ids.index(i) for i in view_ids[role]]]
                pixels = cv2.projectPoints(role_xyz, cv2.Rodrigues(R)[0], t, self.K,
                                           np.array(self.cameras[role]["distortion_coefficients"]))[0].reshape(-1, 2)
                pixels += rng.normal(0., noise, pixels.shape)
                pair[role] = {"image_size": [1920, 1084],
                              "points": dict(zip(view_ids[role], pixels.tolist())),
                              "path": f"{role}/{index}.png"}
            pairs.append(pair)
        return pairs, truth

    def test_recovers_stereo_and_same_pair_world_despite_moving_frame(self):
        original = copy.deepcopy((self.cameras, self.target, self.pairs))
        rig = fit_wood_frame_rig(self.cameras, self.target, self.pairs)
        np.testing.assert_allclose(rig["R_cs_from_cf"], self.R, atol=1e-6)
        np.testing.assert_allclose(rig["t_cs_from_cf_mm"], self.t, atol=.01)
        Rcf, tcf = self.truth[rig["best_pair_id"]]
        pose = rig["world"]["cameras"]["cf"]
        np.testing.assert_allclose(pose["R_camera_from_world"], Rcf, atol=1e-6)
        np.testing.assert_allclose(pose["t_camera_from_world_mm"], tcf, atol=.01)
        self.assertEqual(rig["fit"]["support_pairs"], len(self.pairs))
        self.assertLess(rig["fit"]["other_pairs_median_rms_px"], .001)
        self.assertEqual(original, (self.cameras, self.target, self.pairs))

    def test_consensus_rejects_mismatched_pair_even_if_individual_pnp_is_perfect(self):
        pairs = copy.deepcopy(self.pairs)
        pairs[0]["cs"] = copy.deepcopy(pairs[1]["cs"])
        rig = fit_wood_frame_rig(self.cameras, self.target, pairs)
        self.assertNotEqual(rig["best_pair_id"], "0")
        self.assertIn("0", rig["rejected_pairs"])
        np.testing.assert_allclose(rig["t_cs_from_cf_mm"], self.t, atol=.01)
        bad = next(d for d in rig["candidate_diagnostics"] if d["id"] == "0")
        self.assertLess(bad["cameras"]["cs"]["rms_px"], .001)
        self.assertGreater(bad["cross_projection_rms_px"], 9.)

    def test_noise_and_general_nonplanar_points(self):
        target = copy.deepcopy(self.target)
        target["points"] += [{"id": "P5", "xyz_mm": [180., 200., 300.]},
                             {"id": "P6", "xyz_mm": [420., 120., 500.]}]
        pairs, _ = self.make_pairs(target, noise=.12)
        rig = fit_wood_frame_rig(self.cameras, target, pairs)
        np.testing.assert_allclose(rig["R_cs_from_cf"], self.R, atol=.003)
        np.testing.assert_allclose(rig["t_cs_from_cf_mm"], self.t, atol=8.)
        self.assertFalse(rig["candidate_diagnostics"][0]["cameras"]["cf"]["planar"])

    def test_json_roundtrip_existing_loader_and_corruption_rejection(self):
        rig = fit_wood_frame_rig(self.cameras, self.target, self.pairs)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "木框" / "stereo.json"
            save_wood_frame_rig(path, rig)
            self.assertEqual(load_wood_frame_rig(path), rig)
            self.assertEqual(load_rig(path)["method"], "pnp")
            bad = copy.deepcopy(rig)
            bad["world"]["cameras"]["cf"]["t_camera_from_world_mm"][0] += 10
            before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, "世界座標"):
                save_wood_frame_rig(path, bad)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_wood_frame_rig(path)

    def test_rejects_nonfinite_out_of_image_missing_id_and_size_mismatch(self):
        for change in ("nan", "outside", "id", "size", "duplicate"):
            with self.subTest(change=change):
                pairs = copy.deepcopy(self.pairs)
                view = pairs[0]["cf"]
                if change == "nan":
                    view["points"]["P1"][0] = float("nan")
                elif change == "outside":
                    view["points"]["P1"][0] = 1920.
                elif change == "id":
                    view["points"].pop("P1")
                elif change == "size":
                    view["image_size"] = [1920, 1080]
                else:
                    view["points"]["P1"] = view["points"]["P2"]
                with self.assertRaises(ValueError):
                    fit_wood_frame_rig(self.cameras, self.target, pairs)

    def test_rejects_bad_target_intrinsics_and_insufficient_pairs(self):
        with self.assertRaises(ValueError):
            rectangle_target(0., 10., 3.)
        self.assertEqual(rectangle_target(10., 20., .3)["points"][0]["xyz_mm"], [0., .3, 0.])
        for values in ([{"id": str(i), "xyz_mm": [i, 0, 0]} for i in range(4)],
                       [{"id": "x", "xyz_mm": [i, i % 2, 0]} for i in range(4)]):
            with self.assertRaises(ValueError):
                validate_target({"schema": "wood_frame_target_v1", "units": "mm", "points": values})
        cameras = copy.deepcopy(self.cameras)
        cameras["cf"]["intrinsic_matrix"][0][0] = -1.
        with self.assertRaises(ValueError):
            fit_wood_frame_rig(cameras, self.target, self.pairs)
        with self.assertRaises(ValueError):
            fit_wood_frame_rig(self.cameras, self.target, self.pairs[:2])

    def test_inconsistent_camera_poses_do_not_get_a_plausible_best_pair(self):
        pairs = copy.deepcopy(self.pairs[:5])
        for index, pair in enumerate(pairs):
            pair["cs"] = copy.deepcopy(self.pairs[(index + 2) % len(self.pairs)]["cs"])
        with self.assertRaisesRegex(ValueError, "一致"):
            fit_wood_frame_rig(self.cameras, self.target, pairs)

    def fixed_frame_setup(self, noise=0.):
        self.target = wood_frame_target()
        self.R = cv2.Rodrigues(np.array([.01, 1.05, -.02]))[0]
        self.t = np.array([0., 0., 3300.]) - self.R @ np.array([0., 0., 3300.])
        base = cv2.Rodrigues(np.array([0., -.25, 0.]))[0]
        self.pairs, self.truth = self.make_pairs(self.target, count=15, noise=noise, base=base)

    def test_fixed_geometry_preserves_user_coordinates_and_thickness_once(self):
        target = wood_frame_target()
        self.assertEqual(target["view_point_ids"], {"cf": ["F1", "F2", "F3", "F4"],
                                                    "cs": ["S1", "S2", "S3", "S4"]})
        np.testing.assert_array_equal(validate_target(target)[1],
            [[80, 320, -3], [320, 320, -3], [320, 80, -3], [80, 80, -3],
             [403, 320, 30], [403, 320, 270], [403, 80, 270], [403, 80, 30]])
        self.assertEqual(target["metadata"]["cardboard_thickness_mm"], 3.)
        target["points"][0]["xyz_mm"][0] = -999.
        self.assertEqual(wood_frame_target()["points"][0]["xyz_mm"][0], 80.)

    def test_different_planar_faces_recover_one_shared_world_and_rig(self):
        self.fixed_frame_setup()
        rig = fit_wood_frame_rig(self.cameras, self.target, self.pairs)
        np.testing.assert_allclose(rig["R_cs_from_cf"], self.R, atol=1e-6)
        np.testing.assert_allclose(rig["t_cs_from_cf_mm"], self.t, atol=.01)
        Rcf, tcf = self.truth[rig["best_pair_id"]]
        for role, R, t in (("cf", Rcf, tcf), ("cs", self.R @ Rcf, self.R @ tcf + self.t)):
            pose = rig["world"]["cameras"][role]
            np.testing.assert_allclose(pose["R_camera_from_world"], R, atol=1e-6)
            np.testing.assert_allclose(pose["t_camera_from_world_mm"], t, atol=.01)
        self.assertTrue(all(d["cameras"][role]["planar"] for d in rig["candidate_diagnostics"]
                            for role in ("cf", "cs")))
        self.assertEqual(rig["fit"]["support_pairs"], 15)

    def test_fixed_faces_noise_consensus_and_bad_pair(self):
        self.fixed_frame_setup(noise=.08)
        self.pairs[0]["cs"] = copy.deepcopy(self.pairs[1]["cs"])
        rig = fit_wood_frame_rig(self.cameras, self.target, self.pairs)
        self.assertIn("0", rig["rejected_pairs"])
        self.assertNotEqual(rig["best_pair_id"], "0")
        np.testing.assert_allclose(rig["R_cs_from_cf"], self.R, atol=.006)
        np.testing.assert_allclose(rig["t_cs_from_cf_mm"], self.t, atol=18.)
        self.assertLess(rig["fit"]["other_pairs_median_rms_px"], 2.)

    def test_role_specific_ids_cannot_be_swapped_or_undefined(self):
        self.fixed_frame_setup()
        self.pairs[0]["cf"]["points"] = copy.deepcopy(self.pairs[0]["cs"]["points"])
        with self.assertRaisesRegex(ValueError, "ID"):
            fit_wood_frame_rig(self.cameras, self.target, self.pairs)
        self.target["view_point_ids"]["cs"][0] = "unknown"
        with self.assertRaisesRegex(ValueError, "ID"):
            validate_target(self.target)

    def test_planar_branches_are_selected_by_consensus_not_solver_order(self):
        self.fixed_frame_setup()
        solver = wood_frame._poses
        branch_counts = []
        def reversed_branches(*args):
            solutions, planar = solver(*args)
            branch_counts.append(len(solutions))
            return list(reversed(solutions)), planar
        with patch.object(wood_frame, "_poses", side_effect=reversed_branches):
            rig = fit_wood_frame_rig(self.cameras, self.target, self.pairs)
        self.assertGreater(max(branch_counts), 1)
        np.testing.assert_allclose(rig["R_cs_from_cf"], self.R, atol=1e-6)
        np.testing.assert_allclose(rig["t_cs_from_cf_mm"], self.t, atol=.01)

    def repeated_pose(self, distance, tilt):
        ids, xyz = validate_target(self.target)
        Rcf = cv2.Rodrigues(np.array([np.pi / 2 + tilt, 0., 0.]))[0]
        tcf = np.array([0., 0., distance]) - Rcf @ xyz.mean(axis=0)
        pair = {}
        for role in ("cf", "cs"):
            R, t = (Rcf, tcf) if role == "cf" else (self.R @ Rcf, self.R @ tcf + self.t)
            pixels = cv2.projectPoints(xyz, cv2.Rodrigues(R)[0], t, self.K,
                np.array(self.cameras[role]["distortion_coefficients"]))[0].reshape(-1, 2)
            pair[role] = {"image_size": [1920, 1084], "points": dict(zip(ids, pixels.tolist()))}
        return [dict(copy.deepcopy(pair), id=str(i)) for i in range(3)]

    def test_weak_perspective_repeated_pose_does_not_silently_choose_mirror_rig(self):
        pairs = self.repeated_pose(distance=12000., tilt=.1)
        with self.assertRaisesRegex(ValueError, "無法區分"):
            fit_wood_frame_rig(self.cameras, self.target, pairs)

    def test_exact_frontoparallel_target_still_solves_and_warns_about_diversity(self):
        pairs = self.repeated_pose(distance=4000., tilt=0.)
        rig = fit_wood_frame_rig(self.cameras, self.target, pairs)
        np.testing.assert_allclose(rig["t_cs_from_cf_mm"], self.t, atol=.01)
        self.assertTrue(any("8°" in warning for warning in rig["warnings"]))


if __name__ == "__main__":
    unittest.main()
