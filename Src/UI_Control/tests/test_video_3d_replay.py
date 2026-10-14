"""Real Qt 3D-page regression tests and synthetic metric geometry (no inference)."""
import gc
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

UI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(UI_DIR.parents[1]))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ.setdefault('CONDA_PREFIX', sys.prefix)
os.environ['QT_QPA_FONTDIR'] = 'C:/Windows/Fonts'


class Video3DTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        old = os.getcwd()
        os.chdir(UI_DIR)
        cls.addClassCleanup(os.chdir, old)
        import Src.UI_Control.main as main
        import cv2
        import numpy as np
        import pandas as pd
        import Src.UI_Control.widgets.video_widget_2 as video_widget_2
        from PyQt5.QtWidgets import QApplication
        from Src.UI_Control.viewers.metric_3d_viewer import MetricTriangulate3DViewer
        cls.cv2, cls.np, cls.pd = cv2, np, pd
        cls.module, cls.Viewer = video_widget_2, MetricTriangulate3DViewer
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.rig_path = self.folder / 'stereo.json'
        self.K = self.np.array([[700., 0, 320], [0, 700., 240], [0, 0, 1]])
        self.dist = self.np.array([.03, -.005, .001, -.001, 0.])
        camera = {'intrinsic_matrix': self.K.tolist(), 'distortion_coefficients': self.dist.tolist(), 'image_size': [640, 480]}
        self.rig = {'schema': 'metric_stereo_v1', 'method': 'pnp', 'units': 'mm',
                    'coordinate_convention': 'X_cs = R_cs_from_cf @ X_cf + t_cs_from_cf_mm',
                    'intrinsics': {'cf': camera, 'cs': camera},
                    'R_cs_from_cf': self.np.eye(3).tolist(), 't_cs_from_cf_mm': [-1000., 0, 0], 'warnings': []}
        self.rig_path.write_text(json.dumps(self.rig), encoding='utf-8')
        self.viewers = []
        self.tab = None
        self.addCleanup(self.close_all)

    def close_all(self):
        if self.tab is not None:
            self.tab.is_play = False
            if hasattr(self.tab, '_play_timer'):
                self.tab._play_timer.stop()
            self.viewers.extend([self.tab.viewer3d, self.tab.pose_analyzer.viewer3D, self.tab.pose_analyzer_2.viewer3D])
            self.tab.close()
        for viewer in {v for v in self.viewers if v is not None}:
            viewer.timer.stop()
            viewer.canvas.close()
        self.app.processEvents()

    def viewer(self, camera='cf'):
        viewer = self.Viewer(self.rig_path, camera, 60.)
        self.viewers.append(viewer)
        return viewer

    def points(self, shift=0.):
        rng = self.np.random.default_rng(2)
        xyz = rng.uniform([-300, -800, 4500], [500, 700, 6000], (26, 3))
        xyz[:, 0] += shift
        pairs = []
        for translation in ([0., 0, 0], [-1000., 0, 0]):
            pixels = self.cv2.projectPoints(xyz, self.np.zeros(3), self.np.array(translation), self.K, self.dist)[0].reshape(-1, 2)
            pairs.append({'keypoints': self.np.c_[pixels, self.np.full(26, .99)].tolist()})
        return xyz / 1000., pairs

    def setup_tab(self):
        self.tab = self.module.PoseVideoTabControl(SimpleNamespace(setImageSize=Mock()))
        tab, loader = self.tab, self.tab.video_loader
        loader.video_name, loader.video_name_2 = 'CF_test', 'CS_test'
        loader.folder_path = str(self.folder)
        loader.video_path, loader.video_path_2 = (str(self.folder / (n + '.avi')) for n in (loader.video_name, loader.video_name_2))
        frames = [self.np.full((480, 640, 3), 40 + n, self.np.uint8) for n in range(12)]
        for path in (loader.video_path, loader.video_path_2):
            writer = self.cv2.VideoWriter(path, self.cv2.VideoWriter_fourcc(*'MJPG'), 60, (640, 480))
            self.assertTrue(writer.isOpened())
            for frame in frames:
                writer.write(frame)
            writer.release()
        loader.video_frames, loader.video_frames_2 = frames, [f.copy() for f in frames]
        loader.total_frames, loader.video_fps = 12, 60.
        loader.video_size, loader.is_loading = (640, 480), False
        tab.calibration_path.setText(str(self.rig_path))
        tab.updateVideoInfo()
        self.assertTrue(tab._calibration_ready, tab.calibration_status.text())
        rows = [[], []]
        for f in range(12):
            _, pairs = self.points(shift=f * 10.)
            for i in range(2):
                rows[i].append({'frame_number': f, 'person_id': 1, 'bbox': [0, 0, 639, 479], 'keypoints': pairs[i]['keypoints']})
        for estimator, data in zip((tab.pose_estimater, tab.pose_estimater_2), rows):
            estimator.setProcessedData(self.pd.DataFrame(data))
            estimator.detectKpt = Mock(return_value=(None, None, 60))
        tab.preparation_frame, tab.foot_contact_frame = 2, 4
        tab.max_shoulder_angle_frame, tab.wrist_speed_frame = 7, 11
        tab.mer_locked = tab.release_locked = True
        tab._manual_events = {'foot_contact_frame', 'max_shoulder_angle_frame', 'wrist_speed_frame'}
        return tab

    def run_tab_frames(self, tab):
        for f in range(12):
            tab.ui.frameSlider.blockSignals(True)
            tab.ui.frameSlider.setValue(f)
            tab.ui.frameSlider.blockSignals(False)
            tab.analyzeFrame()

    def test_distortion_and_camera_order_recover_true_scale(self):
        xyz, pairs = self.points()
        for order in ('cf', 'cs'):
            viewer = self.viewer(order)
            viewer.add_2d_keypoints(*(pairs if order == 'cf' else pairs[::-1]), 0)
            self.np.testing.assert_allclose(viewer.all_3d_frames[0], xyz, atol=1e-6)
            _, moved = self.points(650.)
            viewer.add_2d_keypoints(*(moved if order == 'cf' else moved[::-1]), 1)
            self.assertAlmostEqual(viewer.displacement(1), .65, places=6)
            self.assertAlmostEqual(viewer.compute_joint_velocity_causal(25, .014, 1), 39., places=5)

    def test_rejected_joint_and_degenerate_ray_do_not_crash_other_joints(self):
        viewer = self.viewer()
        xyz, pairs = self.points()
        pairs[0]['keypoints'][10][2] = .1
        pairs[1]['keypoints'][4][:2] = pairs[0]['keypoints'][4][:2]
        pairs[1]['keypoints'][3][1] += 100
        viewer.add_2d_keypoints(*pairs, 0)
        self.assertTrue(self.np.isnan(viewer.all_3d_frames[0][[3, 4, 10]]).all())
        self.np.testing.assert_allclose(viewer.all_3d_frames[0][25], xyz[25], atol=1e-6)
        self.assertTrue(self.np.isnan(viewer.arm_cocking_angle(0)))
        viewer.save_to_json(self.folder / 'points.json')
        saved = json.loads((self.folder / 'points.json').read_text())
        self.assertEqual(saved['frames'][0]['keypoints'][10], [None] * 3)

    def test_velocity_does_not_bridge_missing_frames(self):
        viewer = self.viewer()
        for f in (0, 2):
            _, pairs = self.points(f * 10.)
            viewer.add_2d_keypoints(*pairs, f)
        self.assertTrue(self.np.isnan(viewer.compute_joint_velocity_causal(10, .014, 2)))

    def test_full_analysis_exports_last_frame_br_without_skeleton_checkbox(self):
        tab = self.setup_tab()
        self.assertFalse(tab.ui.showSkeletonCheckBox.isChecked())
        self.run_tab_frames(tab)
        output = json.loads((self.folder / 'CF_test_pnp_keyframes.json').read_text(encoding='utf-8'))
        self.assertEqual(output['release']['frame_number'], 11)
        self.assertAlmostEqual(output['foot_contact']['stride_distance_cm'], 4., places=5)
        self.assertEqual(len(output['Analysis']['analyzed_frames']), 12)
        self.assertEqual(output['Analysis']['calibration_snapshot'], self.rig)
        self.assertTrue((self.folder / 'CF_test_pnp_analysis.json').is_file())
        self.assertTrue((self.folder / 'CS_test_pnp_poses.json').is_file())
        gc.collect()
        self.app.processEvents()
        for view in (tab.ui.fcView, tab.ui.merView, tab.ui.brView):
            self.assertIs(view.scene().parent(), view)
            self.assertEqual(len(view.scene().items()), 1)
        tab._render_keyframe_results()
        self.assertEqual(len(tab.ui.brView.scene().items()), 1)

    def test_restore_without_roi_and_reject_stale_calibration(self):
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        tab.is_processed = True
        tab.updateVideoInfo()
        self.assertEqual(tab.wrist_speed_frame, 11)
        self.assertEqual(len(tab.viewer3d.all_3d_frames), 12)
        tab.viewer3d.set_frame(7)
        self.assertTrue(tab.viewer3d._camera_range_initialized)
        self.assertEqual(len(tab.ui.merView.scene().items()), 1)
        self.rig['t_cs_from_cf_mm'][0] = -1100.
        self.rig_path.write_text(json.dumps(self.rig), encoding='utf-8')
        tab.updateVideoInfo()
        self.assertIsNone(tab.wrist_speed_frame)
        self.assertEqual(len(tab.viewer3d.all_3d_frames), 0)

    def test_method_switch_invalidates_cache_and_8point_changes_scale(self):
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        tab.calibration_combo.setCurrentIndex(1)
        self.assertFalse(tab._calibration_ready)
        self.assertIsNone(tab.wrist_speed_frame)
        self.assertFalse(tab.viewer3d.all_3d_frames)
        self.rig['method'] = '8point'
        self.rig['t_cs_from_cf_mm'][0] = -2000.
        self.rig_path.write_text(json.dumps(self.rig), encoding='utf-8')
        tab.calibration_path.setText(str(self.rig_path))
        self.assertTrue(tab._apply_selected_calibration())
        xyz, pairs = self.points()
        tab.viewer3d.add_2d_keypoints(*pairs, 0)
        self.np.testing.assert_allclose(tab.viewer3d.all_3d_frames[0], xyz * 2, atol=2e-6)
        self.assertIs(tab.pose_analyzer.viewer3D, tab.viewer3d)
        self.assertIs(tab.pose_analyzer_2.viewer3D, tab.viewer3d)

    def test_bad_calibration_never_falls_back_to_legacy(self):
        tab = self.setup_tab()
        tab.calibration_path.setText(str(self.folder / 'missing.json'))
        self.assertFalse(tab._apply_selected_calibration())
        self.assertFalse(tab._calibration_ready)
        self.assertFalse(tab.viewer3d.all_3d_frames)

    def test_offset_rejects_boundary_clipping(self):
        tab = self.setup_tab()
        tab.offset_spin.setValue(2)
        self.assertTrue(tab._apply_selected_calibration())
        self.assertFalse(tab._valid_stereo_frame(0))
        self.assertFalse(tab._valid_stereo_frame(1))
        self.assertTrue(tab._valid_stereo_frame(2))
        tab.offset_spin.setValue(-2)
        tab._apply_selected_calibration()
        self.assertFalse(tab._valid_stereo_frame(11))

    def test_preview_falls_back_to_original_on_overlay_error(self):
        tab = self.setup_tab()
        with patch.object(tab.image_drawer, 'drawAngleInfo', side_effect=ValueError('missing pose')):
            tab._display_keyframe_with_text(7, tab.ui.merView, 'max_shoulder_er')
        self.assertEqual(len(tab.ui.merView.scene().items()), 1)

    def test_save_failure_keeps_retry_available(self):
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        tab._analysis_dirty = True
        with patch.object(tab.viewer3d, 'save_to_json', side_effect=OSError('disk full')):
            tab._complete_video_analysis(manual=True)
        self.assertTrue(tab._analysis_dirty)
        tab._complete_video_analysis(manual=True)
        self.assertFalse(tab._analysis_dirty)

    def test_legacy_mode_still_exports_replay_and_coordinates(self):
        tab = self.setup_tab()
        rows = [e.person_df.copy() for e in (tab.pose_estimater, tab.pose_estimater_2)]
        tab.calibration_combo.setCurrentIndex(2)
        self.assertTrue(tab._apply_selected_calibration())
        for estimator, df in zip((tab.pose_estimater, tab.pose_estimater_2), rows):
            estimator.setProcessedData(df)
        tab.preparation_frame, tab.foot_contact_frame = 2, 4
        tab.max_shoulder_angle_frame, tab.wrist_speed_frame = 7, 11
        tab.mer_locked = tab.release_locked = True
        self.run_tab_frames(tab)
        self.assertTrue((self.folder / 'CF_test_legacy_keyframes.json').is_file())
        coordinates = json.loads((self.folder / 'CF_test_legacy_3D.json').read_text())
        self.assertIsInstance(coordinates, list)
        self.assertEqual(len(coordinates), 12)

    def test_processed_pose_timeline_remapping(self):
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        tab.is_processed = True
        tab.offset_spin.setValue(2)
        self.assertTrue(tab._apply_selected_calibration())
        # Saved second-camera frame 0 now appears at playback frame 2.
        first = tab.pose_estimater_2.getPersonDf(frame_num=2, is_kpt=True)
        _, pairs = self.points(0.)
        self.np.testing.assert_allclose(first, pairs[1]['keypoints'])
        self.assertIsNone(tab.pose_estimater_2.getPersonDf(frame_num=0))

    def test_first_playback_frame_is_analyzed_even_when_slider_already_zero(self):
        tab = self.setup_tab()
        tab._play_timer = Mock()
        tab._current_play_frame = 0
        tab.is_play = True
        with patch.object(tab, 'analyzeFrame') as analyze:
            tab._playNextFrame()
            analyze.assert_called_once()

    def test_real_p05_cached_poses_keep_events_visible_when_3d_is_sparse(self):
        repo = UI_DIR.parents[1]
        folder = repo / 'Db/Record/20261008_Pitcher01/20261008_1130_P05'
        cf = next(folder.glob('CF_*.mp4'), None)
        cs = next(folder.glob('CS_*.mp4'), None)
        calibration = repo / 'Db/Calibration/chessboard/pnp/stereo.json'
        if cf is None or cs is None or not calibration.is_file():
            self.skipTest('Local P05 recording not available')
        tab = self.setup_tab()
        loader = tab.video_loader
        loader.video_path, loader.video_path_2 = str(cf), str(cs)
        loader.video_name, loader.video_name_2 = cf.stem, cs.stem
        loader.folder_path = str(folder)
        cap = self.cv2.VideoCapture(str(cf))
        try:
            count, fps = int(cap.get(self.cv2.CAP_PROP_FRAME_COUNT)), cap.get(self.cv2.CAP_PROP_FPS)
            ok, first = cap.read()
            self.assertTrue(ok)
        finally:
            cap.release()
        loader.video_frames, loader.video_frames_2 = [first] * count, [first] * count
        loader.total_frames, loader.video_fps, loader.video_size = count, fps, first.shape[1::-1]
        tab.calibration_path.setText(str(calibration))
        tab.is_processed = True
        tab.updateVideoInfo()
        self.assertTrue(tab._calibration_ready, tab.calibration_status.text())
        loader.folder_path = str(self.folder)  # Never write into the real recording.
        with patch.object(tab, '_updateFrame'):
            for f in range(count):
                tab.ui.frameSlider.blockSignals(True)
                tab.ui.frameSlider.setValue(f)
                tab.ui.frameSlider.blockSignals(False)
                tab.analyzeFrame()
        for f, view, kind in ((tab.foot_contact_frame, tab.ui.fcView, 'foot_contact'),
                              (tab.max_shoulder_angle_frame, tab.ui.merView, 'max_shoulder_er'),
                              (tab.wrist_speed_frame, tab.ui.brView, 'release')):
            self.assertIsNotNone(f)
            cap = self.cv2.VideoCapture(str(cf))
            try:
                cap.set(self.cv2.CAP_PROP_POS_FRAMES, f)
                ok, actual = cap.read()
                self.assertTrue(ok)
                loader.video_frames[f] = actual
            finally:
                cap.release()
            tab._display_keyframe_with_text(f, view, kind)
            self.assertEqual(len(view.scene().items()), 1)
        output = json.loads((self.folder / f'{cf.stem}_pnp_keyframes.json').read_text(encoding='utf-8'))
        self.assertIsNone(output['foot_contact']['stride_distance_cm'])
        self.assertEqual(output['Analysis']['sources'][cf.stem]['path'], str(cf.resolve()))
        print('P05 candidate frames:', tab.foot_contact_frame, tab.max_shoulder_angle_frame, tab.wrist_speed_frame)
        tab._display_keyframe_with_text(None, tab.ui.merView, 'max_shoulder_er')
        self.assertEqual(len(tab.ui.merView.scene().items()), 1)


if __name__ == '__main__':
    unittest.main()
