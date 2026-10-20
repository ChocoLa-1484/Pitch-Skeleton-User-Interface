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
            self.tab._set_analysis_running(False)
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

    def setup_tab(self, mock_events=True):
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
        # 預設已改回學姊的方法（legacy）；這組測試驗證的是棋盤 PnP 公制模式，需明確選取
        self.assertEqual(tab.calibration_combo.currentData(), 'legacy')
        tab.calibration_combo.setCurrentIndex(tab.calibration_combo.findData('pnp'))
        tab.calibration_path.setText(str(self.rig_path))
        tab.updateVideoInfo()
        tab._analysis_timer.stop()  # Drive the sequential scan deterministically.
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
        if mock_events:
            # This camera geometry fixture is not a pitching motion. Keep event
            # timing deterministic while exercising real reconstruction and I/O.
            from Src.analysis.pitch_events import PitchEvents
            events = patch.object(self.module, 'detect_pitch_events', return_value=PitchEvents(2, 4, 7, 11, True, True))
            events.start()
            self.addCleanup(events.stop)
        return tab

    def run_tab_frames(self, tab):
        if not tab._analysis_running:
            tab.toggleDetect()
        tab._analysis_timer.stop()
        for f in range(tab.video_loader.total_frames):
            tab._analyze_next_frame()
        self.assertTrue(tab._analysis_complete, tab.calibration_status.text())
        tab._play_timer.stop()
        tab.is_play = False

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
        tab.ui.showSkeletonCheckBox.setChecked(False)
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
        self.np.testing.assert_array_equal(tab.viewer3d.K_L, tab.K_F)
        self.np.testing.assert_array_equal(tab.viewer3d.K_R, tab.K_S)
        self.np.testing.assert_array_equal(tab.viewer3d.F, tab.F.T)
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
        tab.updateVideoInfo()
        tab._play_timer.stop()
        self.assertFalse(tab._analysis_running)
        self.assertTrue(tab.viewer3d._camera_range_initialized)

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

    def test_first_playback_frame_is_rendered_even_when_slider_already_zero(self):
        tab = self.setup_tab()
        tab._play_timer = Mock()
        tab._current_play_frame = 0
        tab.is_play = True
        with patch.object(tab, 'analyzeFrame') as analyze:
            tab._playNextFrame()
            analyze.assert_called_once()

    def test_automatic_analysis_then_cached_replay_never_infers_or_saves(self):
        tab = self.setup_tab()
        self.assertTrue(tab._analysis_running)
        self.run_tab_frames(tab)
        self.assertFalse(tab._analysis_dirty)
        before = {p.name: p.read_bytes() for p in self.folder.glob('*.json')}
        with patch.object(tab, '_analyze_video_frame', side_effect=AssertionError('replay analyzed')), \
             patch.object(tab.pose_estimater, 'detectKpt', side_effect=AssertionError('replay inferred')), \
             patch.object(tab.pose_estimater_2, 'detectKpt', side_effect=AssertionError('replay inferred')):
            tab.updateVideoInfo()
            self.assertFalse(tab._analysis_running)
            self.assertTrue(tab.is_play)
            tab._play_timer.stop()
            self.assertTrue(tab.ui.showSkeletonCheckBox.isChecked())
            self.assertTrue(tab.image_drawer.show_skeleton)
            self.assertTrue(tab.image_drawer_2.show_skeleton)
            for frame in (11, 0, 7, 4, 10):
                tab.ui.frameSlider.setValue(frame)
            self.assertEqual(tab.wrist_speed_frame, 11)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.folder.glob('*.json')})

    def test_reanalysis_completes_every_frame_and_saves_without_button(self):
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        output = self.folder / 'CF_test_pnp_keyframes.json'
        output.unlink()
        tab.toggleDetect()
        self.assertTrue(tab._analysis_running)
        self.assertFalse(tab.ui.frameSlider.isEnabled())
        self.run_tab_frames(tab)
        result = json.loads(output.read_text(encoding='utf-8'))
        self.assertTrue(result['Analysis']['scan_complete'])
        self.assertEqual(result['Analysis']['scanned_frames'], list(range(12)))
        self.assertFalse(hasattr(tab, 'save_analysis_button'))
        self.assertTrue(tab.ui.frameSlider.isEnabled())

    def test_reanalysis_discards_manual_events_and_saves_new_detection(self):
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        tab.ui.frameSlider.setValue(5)
        tab._set_manual_event('foot_contact_frame')
        self.assertEqual(tab.foot_contact_frame, 5)
        self.assertIn('foot_contact_frame', tab._manual_events)
        tab.toggleDetect()
        self.assertFalse(tab._manual_events)
        self.assertIsNone(tab.foot_contact_frame)
        self.run_tab_frames(tab)
        result = json.loads((self.folder / 'CF_test_pnp_keyframes.json').read_text(encoding='utf-8'))
        self.assertEqual(result['foot_contact']['frame_number'], 4)
        self.assertEqual(result['Analysis']['manual_events'], [])

    def test_keyframe_clicks_do_not_resize_video_frames(self):
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        tab.resize(1366, 950)
        tab.show()
        self.app.processEvents()
        views = (tab.ui.FrameView, tab.ui.FrameView_2)
        before = [view.geometry() for view in views]
        for frame, thumbnail, button in ((4, tab.ui.fcView, tab.ui.footContact_ptn),
                                         (7, tab.ui.merView, tab.ui.maxER_ptn),
                                         (11, tab.ui.brView, tab.ui.release_ptn)):
            tab.is_play = True
            button.click()
            self.app.processEvents()
            self.assertFalse(tab.is_play)
            self.assertEqual(tab.ui.frameSlider.value(), frame)
            self.assertEqual([view.geometry() for view in views], before)
            tab.ui.frameSlider.setValue(0)
            QTest.mouseClick(thumbnail.viewport(), Qt.LeftButton)
            self.app.processEvents()
            self.assertEqual(tab.ui.frameSlider.value(), frame)
            self.assertEqual([view.geometry() for view in views], before)

    def test_sidebar_tabs_do_not_resize_video_or_keyframe_rows(self):
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        tab.show()
        views = (tab.ui.FrameView, tab.ui.FrameView_2, tab.ui.fcView,
                 tab.ui.merView, tab.ui.brView, tab.ui.graph_widget)

        def settle_layout():
            # Layout requests and deferred image fitting use separate Qt turns.
            for _ in range(3):
                self.app.processEvents()

        for width, height in ((1366, 950), (1700, 1050), (1100, 800)):
            tab.resize(width, height)
            tab.ui.replay_sidebar.setCurrentIndex(0)
            settle_layout()
            before = [view.geometry() for view in views]
            for index in (1, 2, 3, 0, 2):
                with self.subTest(size=(width, height), sidebar=index):
                    tab.ui.replay_sidebar.setCurrentIndex(index)
                    settle_layout()
                    self.assertEqual([view.geometry() for view in views], before)
            tab.ui.maxER_ptn.click()
            settle_layout()
            self.assertEqual([view.geometry() for view in views], before)

    def test_overlay_matches_2d_for_low_confidence_missing_and_selected_joints(self):
        from Src.UI_Control.rendering.vis_image import ImageDrawer as Drawer2D
        tab = self.setup_tab()
        rows = tab.pose_estimater.person_df.to_dict('records')
        points = rows[0]['keypoints']
        points[10][2] = .4
        points[8][:2] = [float('nan'), float('nan')]
        tab.pose_estimater.setProcessedData(self.pd.DataFrame(rows))
        tab.pose_estimater.person_id = 1
        drawer = Drawer2D(tab.pose_estimater, tab.pose_analyzer)
        drawer.setShowSkeleton(True)
        tab.image_drawer.setShowSkeleton(True)
        frame = tab.video_loader.video_frames[0]
        two = drawer.drawInfo(frame, 0)
        three = tab.image_drawer.drawInfo(frame, 0)
        self.np.testing.assert_array_equal(two, three)
        self.assertFalse(self.np.array_equal(frame, three))

    def test_canonical_2d_poses_override_method_cache_and_invalidate_3d_results(self):
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        rows = tab.pose_estimater_2.person_df.to_dict('records')
        rows[0]['keypoints'][10][0] += 10
        (self.folder / 'CS_test.json').write_text(json.dumps(rows))
        tab.updateVideoInfo()
        tab._analysis_timer.stop()
        self.assertTrue(tab._analysis_running)
        actual = tab.pose_estimater_2.getPersonDf(frame_num=0, is_kpt=True)
        self.np.testing.assert_array_equal(actual, rows[0]['keypoints'])

    def test_cancel_load_keeps_current_replay(self):
        tab = self.setup_tab()
        with patch.object(self.module.QFileDialog, 'getOpenFileName', return_value=('', '')), \
             patch.object(tab.video_loader, 'loadVideo') as load:
            tab.loadVideo()
        load.assert_not_called()
        self.assertEqual(tab.wrist_speed_frame, 11)
        self.assertEqual(tab.video_loader.video_name, 'CF_test')

    def test_single_load_pairs_front_and_side_before_reset(self):
        tab = self.setup_tab()
        with patch.object(tab.video_loader, 'loadVideo') as load, patch.object(tab, 'checkVideoLoad'):
            tab.loadVideo(video_path=str(self.folder / 'CS_test.avi'))
        load.assert_called_once_with(str(self.folder / 'CF_test.avi'), str(self.folder / 'CS_test.avi'))

    def test_missing_pose_cache_is_rebuilt_even_with_saved_events(self):
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        (self.folder / 'CS_test_pnp_poses.json').unlink()
        tab.updateVideoInfo()
        tab._analysis_timer.stop()
        self.assertTrue(tab._analysis_running)

    def test_manual_event_correction_autosaves_and_rejects_wrong_order(self):
        tab = self.setup_tab()
        self.run_tab_frames(tab)
        tab.ui.frameSlider.setValue(10)
        tab._set_manual_event('wrist_speed_frame')
        data = json.loads((self.folder / 'CF_test_pnp_keyframes.json').read_text(encoding='utf-8'))
        self.assertEqual(data['release']['frame_number'], 10)
        tab.ui.frameSlider.setValue(3)
        tab._set_manual_event('wrist_speed_frame')
        self.assertEqual(tab.wrist_speed_frame, 10)
        self.assertIn('FC < MER < BR', tab.calibration_status.text())

    def test_both_pages_agree_on_p02_p03_p04_without_metric_coordinates(self):
        from Src.UI_Control.widgets.pitch_widget import PosePitchTabControl
        tab = self.setup_tab(mock_events=False)
        tab._set_analysis_running(False)
        with patch.object(PosePitchTabControl, 'load_video_list'):
            pitch = PosePitchTabControl(SimpleNamespace(setImageSize=Mock()))
        try:
            for folder_name, stem, expected in (
                    ('20261008_1050_P02', 'CS_20261008_1050_Pitcher02_P02', (110, 117, 120)),
                    ('20261008_1050_P03', 'CS_20261008_1050_Pitcher02_P03', (100, 108, 111)),
                    ('20261008_1052_P04', 'CS_20261008_1052_Pitcher02_P04', (111, 118, 120))):
                path = UI_DIR.parents[1] / 'Db/Record/20261008_Pitcher02' / folder_name / (stem + '.json')
                if not path.is_file():
                    self.skipTest('Local P02/P03/P04 poses are not available')
                rows = json.loads(path.read_text(encoding='utf-8'))
                for controller in (tab, pitch):
                    controller.resetPhaseDetection()
                    controller.pose_estimater_2.setProcessedData(self.pd.DataFrame(rows))
                    controller.pose_estimater.reset()
                    controller.video_loader.video_fps = 67.
                    with patch.object(controller, '_render_keyframe_results'):
                        for frame in range(max(r['frame_number'] for r in rows) + 1):
                            controller.phaseDetection(frame)
                    self.assertEqual((controller.foot_contact_frame, controller.max_shoulder_angle_frame,
                                      controller.wrist_speed_frame), expected, stem)
        finally:
            pitch.timer.stop()
            self.app.removeEventFilter(pitch)
            pitch.close()
            pitch.deleteLater()



if __name__ == '__main__':
    unittest.main()
