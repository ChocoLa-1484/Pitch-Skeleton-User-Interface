"""Regression coverage for the real 2D widget, without camera capture/inference."""
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
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ.setdefault("CONDA_PREFIX", sys.prefix)
if Path("C:/Windows/Fonts").is_dir():
    os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
sys.path.insert(0, str(UI_DIR.parents[1]))


class PitchReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old_cwd = os.getcwd()
        os.chdir(UI_DIR)
        cls.addClassCleanup(os.chdir, cls.old_cwd)
        # Match the application's Qt/SDK DLL order, without constructing Model.
        import Src.UI_Control.main as main
        from PyQt5.QtWidgets import QApplication
        import Src.UI_Control.widgets.pitch_widget as pitch_widget
        import numpy as np
        import pandas as pd
        cls.module, cls.np, cls.pd = pitch_widget, np, pd
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        with patch.object(self.module.PosePitchTabControl, 'load_video_list'):
            self.tab = self.module.PosePitchTabControl(SimpleNamespace(setImageSize=Mock()))
        self.addCleanup(self.close_tab)
        self.tab.is_video = True
        self.tab.output_dir = str(self.folder)
        self.tab.frame_offset = 0
        loader = self.tab.video_loader
        loader.video_name, loader.video_name_2 = 'CF_test', 'CS_test'
        loader.video_path = str(self.folder / 'CF_test.mp4')
        loader.video_path_2 = str(self.folder / 'CS_test.mp4')
        loader.folder_path = str(self.folder)
        loader.video_fps = loader.video_fps_raw = 60.
        loader.video_size = (640, 480)
        loader.total_frames = 12
        loader.frame_offset = 0
        loader.is_loading = False
        frames = [self.np.full((480, 640, 3), 50 + i, self.np.uint8) for i in range(12)]
        loader.video_frames, loader.video_frames_2 = frames, [x.copy() for x in frames]
        self.tab.initFrameSlider()

    def close_tab(self):
        self.tab.timer.stop()
        if hasattr(self.tab, '_play_timer'):
            self.tab._play_timer.stop()
        self.tab.single_video_scan_running = False
        self.app.removeEventFilter(self.tab)
        self.tab.close()
        self.tab.deleteLater()
        self.app.processEvents()

    def set_events(self):
        self.tab.preparation_frame = 2
        self.tab.foot_contact_frame = 4
        self.tab.max_shoulder_angle_frame = 7
        self.tab.max_shoulder_angle_value = 160.
        self.tab.wrist_speed_frame = 10
        self.tab.max_wrist_speed_after_er = 15.
        self.tab.mer_locked = self.tab.release_locked = True

    def pose_row(self, frame):
        points = [[200. + i * 3 + frame, 100. + i * 9, .95, 0] for i in range(26)]
        return {'frame_number': frame, 'person_id': 1, 'bbox': [100, 50, 400, 450], 'keypoints': points}

    def set_poses(self):
        rows = [self.pose_row(f) for f in range(12)]
        for estimator, analyzer in ((self.tab.pose_estimater, self.tab.pose_analyzer),
                                    (self.tab.pose_estimater_2, self.tab.pose_analyzer_2)):
            estimator.setProcessedData(self.pd.DataFrame(rows))
            for frame in range(12):
                analyzer.addAnalyzeInfo(frame)

    def test_mer_br_scenes_survive_gc_and_redraw_without_accumulating(self):
        self.set_events()
        self.set_poses()
        self.tab._render_keyframe_results()
        for view in (self.tab.ui.fcView, self.tab.ui.merView, self.tab.ui.brView):
            self.assertIs(view.scene().parent(), view)
        gc.collect()
        self.app.processEvents()
        self.tab._render_keyframe_results()
        for view in (self.tab.ui.fcView, self.tab.ui.merView, self.tab.ui.brView):
            self.assertEqual(len(view.scene().items()), 1)
        self.assertTrue(self.tab.ui.maxER_ptn.isEnabled())
        self.assertTrue(self.tab.ui.release_ptn.isEnabled())

    def test_real_analyzer_emits_mer_angle_and_phase_detection_reaches_release(self):
        rows = [self.pose_row(i) for i in range(12)]
        for frame, angle in ((5, 160.), (6, 175.), (7, 160.)):
            points = rows[frame]['keypoints']
            points[8][:2] = [200., 200.]
            points[10][:2] = [200. + 100 * self.np.cos(self.np.deg2rad(angle)),
                              200. - 100 * self.np.sin(self.np.deg2rad(angle))]
            points[6][:2] = [180., 300.]
        for frame, x in ((8, 380.), (9, 410.)):
            rows[frame]['keypoints'][10][:2] = [x, 150.]
            rows[frame]['keypoints'][6][:2] = [180., 300.]
        self.tab.pose_estimater_2.setProcessedData(self.pd.DataFrame(rows))
        for frame in range(12):
            self.tab.pose_analyzer_2.addAnalyzeInfo(frame)
        self.assertAlmostEqual(self.tab._get_frame_analysis_data(6)['right_shoulder_external_rotation_angle'], 175.)
        self.tab.preparation_frame = 2
        self.tab.foot_contact_frame = 4
        for frame in range(5, 10):
            self.tab.phaseDetection(frame)
        self.assertTrue(self.tab.mer_locked)
        self.assertEqual(self.tab.max_shoulder_angle_frame, 6)
        self.assertTrue(self.tab.release_locked)
        self.assertEqual(self.tab.wrist_speed_frame, 8)

    def test_single_person_landing_does_not_require_manual_person_selection(self):
        self.set_poses()
        self.assertIsNone(self.tab.pose_estimater_2.person_id)
        self.assertTrue(self.tab.pose_analyzer_2.is_left_ankle_landed(8))
        extra = self.pose_row(8)
        extra['person_id'] = 2
        rows = [self.pose_row(i) for i in range(12)] + [extra]
        self.tab.pose_estimater_2.setProcessedData(self.pd.DataFrame(rows))
        self.assertFalse(self.tab.pose_analyzer_2.is_left_ankle_landed(8))

    def test_available_recording_skeleton_reaches_all_events(self):
        folder = UI_DIR.parents[1] / 'Db/Record/20261008_Pitcher01/20261008_1130_P05'
        files = list(folder.glob('CS_*.json'))
        if not files:
            self.skipTest('Local P05 recording is not present')
        rows = json.loads(files[0].read_text(encoding='utf-8'))
        total = max(r['frame_number'] for r in rows) + 1
        self.tab.video_loader.total_frames = total
        self.tab.video_loader.video_frames = [self.tab.video_loader.video_frames[0]] * total
        self.tab.video_loader.video_frames_2 = [self.tab.video_loader.video_frames_2[0]] * total
        self.tab.pose_estimater_2.setProcessedData(self.pd.DataFrame(rows))
        self.tab.pose_analyzer_2.fps = 67.
        for frame in range(total):
            self.tab.pose_analyzer_2.addAnalyzeInfo(frame)
            self.tab.phaseDetection(frame)
        self.assertTrue(self.tab.mer_locked)
        self.assertTrue(self.tab.release_locked)
        self.assertLess(self.tab.foot_contact_frame, self.tab.max_shoulder_angle_frame)
        self.assertLess(self.tab.max_shoulder_angle_frame, self.tab.wrist_speed_frame)

    def test_raw_frame_fallback_and_missing_frame_guard(self):
        with patch.object(self.tab.image_drawer_2, 'drawAngleInfo', side_effect=ValueError('bad pose')):
            self.assertTrue(self.tab._display_keyframe_image(7, self.tab.ui.merView, 'max_shoulder_er'))
        self.assertTrue(self.tab._display_keyframe_image(10, self.tab.ui.brView))
        for value in (None, -1, 12):
            self.assertFalse(self.tab._display_keyframe_image(value, self.tab.ui.brView))

    def test_enable_analysis_does_not_reset_or_reload_known_events(self):
        self.set_events()
        with patch.object(self.tab, 'updateVideoInfo') as reload:
            self.tab.toggleSkeletonVideo(2)
        reload.assert_not_called()
        self.assertEqual(self.tab.max_shoulder_angle_frame, 7)
        self.assertEqual(self.tab.wrist_speed_frame, 10)
        self.assertTrue(self.tab.pose_estimater_2.is_detect)

    def test_explicit_reanalysis_clears_events_but_reuses_loaded_skeleton(self):
        self.set_events()
        self.set_poses()
        with patch.object(self.tab, 'playBtnClicked') as play:
            self.tab.restart_video_analysis()
        self.assertIsNone(self.tab.max_shoulder_angle_frame)
        self.assertIsNone(self.tab.wrist_speed_frame)
        self.assertEqual(len(self.tab.pose_estimater_2.person_df), 12)
        self.assertTrue(self.tab.pose_analyzer_2.analyze_df.empty)
        self.assertEqual(self.tab._last_phase_frame, -1)
        play.assert_called_once()

    def test_first_playback_frame_is_analyzed_even_if_slider_already_zero(self):
        self.tab.is_play = True
        self.tab._current_play_frame = 0
        with patch.object(self.tab, 'analyzeFrame') as analyze:
            self.tab._playNextFrame()
        analyze.assert_called_once()
        self.assertEqual(self.tab._current_play_frame, 1)

    def test_first_analysis_frame_keeps_front_camera_baseline(self):
        self.tab.ui.skeletonVideoCheckBox.blockSignals(True)
        self.tab.ui.skeletonVideoCheckBox.setChecked(True)
        self.tab.ui.skeletonVideoCheckBox.blockSignals(False)
        self.tab.pose_estimater.detectKpt = Mock(return_value=(None, None, 60))
        self.tab.pose_estimater_2.detectKpt = Mock(return_value=(None, None, 60))
        self.tab.analyzeFrame()
        self.tab.pose_estimater.detectKpt.assert_called_once()
        self.assertEqual(self.tab.pose_estimater.detectKpt.call_args.args[1], 0)
        self.tab.pose_estimater_2.detectKpt.assert_called_once()

    def test_normal_offline_last_frame_exports_and_renders_late_release(self):
        self.set_events()
        self.set_poses()
        self.tab.ui.skeletonVideoCheckBox.blockSignals(True)
        self.tab.ui.skeletonVideoCheckBox.setChecked(True)
        self.tab.ui.skeletonVideoCheckBox.blockSignals(False)
        self.tab.ui.frameSlider.blockSignals(True)
        self.tab.ui.frameSlider.setValue(11)
        self.tab.ui.frameSlider.blockSignals(False)
        self.tab._last_phase_frame = 11
        self.tab._analysis_dirty = True
        self.tab.pose_estimater_2.detectKpt = Mock(return_value=(None, None, 60))
        self.tab.analyzeFrame()
        self.assertTrue(self.tab._analysis_finalized)
        self.assertTrue(self.tab.release_view_rendered)
        self.assertEqual(len(self.tab.ui.brView.scene().items()), 1)
        payload = json.loads((self.folder / 'CF_test_BRandRapsodo.json').read_text(encoding='utf-8'))
        self.assertEqual(payload['Keyframes']['BR']['frame'], 10)
        self.assertEqual(len(json.loads((self.folder / 'CS_test.json').read_text())), 12)
        with patch.object(self.tab, 'exportReleasePointJson') as export:
            self.tab._complete_video_analysis()
        export.assert_not_called()

    def test_single_video_scan_also_exports(self):
        self.tab.single_video_mode = True
        self.tab.video_loader.video_name = self.tab.video_loader.video_name_2 = 'single'
        def infer(image, frame, is_video):
            estimator = self.tab.pose_estimater_2
            estimator.setProcessedData(self.pd.DataFrame([self.pose_row(i) for i in range(frame + 1)]))
            return None, None, 60
        self.tab.pose_estimater_2.detectKpt = infer
        with patch.object(self.tab, 'phaseDetection', side_effect=lambda frame: self.set_events() if frame == 11 else None):
            self.tab.scan_single_video_keyframes()
        self.assertTrue((self.folder / 'single_BRandRapsodo.json').is_file())
        self.assertEqual(len(json.loads((self.folder / 'single.json').read_text())), 12)
        self.assertFalse(self.tab.single_video_scan_running)

    def test_replay_restores_mer_br_without_roi_or_skeleton(self):
        payload = {'Keyframes': {'preparation_frame': 2, 'FC': {'frame': 4},
                    'MER': {'frame': 7, 'shoulder_angle': 160.}, 'BR': {'frame': 10, 'wrist_speed': 15.}}}
        (self.folder / 'CF_test_BRandRapsodo.json').write_text(json.dumps(payload))
        self.assertTrue(self.tab._load_saved_analysis())
        self.assertEqual(self.tab.max_shoulder_angle_frame, 7)
        self.assertEqual(self.tab.wrist_speed_frame, 10)
        self.assertEqual(len(self.tab.ui.merView.scene().items()), 1)
        self.assertEqual(len(self.tab.ui.brView.scene().items()), 1)

    def test_export_preserves_roi_and_sanitizes_nonfinite_values(self):
        self.set_events()
        path = self.folder / 'CF_test_BRandRapsodo.json'
        path.write_text(json.dumps({'roi_frame_1': 'keep', 'metrics': {'spin': 100}, 'external': 42}))
        self.tab.max_wrist_speed_after_er = float('nan')
        self.tab.exportReleasePointJson()
        payload = json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda x: self.fail(x))
        self.assertEqual(payload['roi_frame_1'], 'keep')
        self.assertEqual(payload['metrics'], {'spin': 100})
        self.assertEqual(payload['external'], 42)
        self.assertIsNone(payload['Keyframes']['BR']['wrist_speed'])

    def test_export_failure_remains_retryable(self):
        self.set_events()
        self.tab._analysis_dirty = True
        with patch.object(self.tab.video_loader, 'saveVideo', side_effect=OSError('disk full')):
            self.assertFalse(self.tab._complete_video_analysis())
        self.assertTrue(self.tab._analysis_dirty)
        self.assertFalse(self.tab._analysis_finalized)
        self.assertIn('失敗', self.tab.ui.RecordStatelabel.text())

    def test_empty_camera_does_not_erase_existing_pose_json(self):
        path = self.folder / 'CF_test.json'
        original = [self.pose_row(1), self.pose_row(3)]
        path.write_text(json.dumps(original))
        self.tab.pose_estimater_2.setProcessedData(self.pd.DataFrame([self.pose_row(2)]))
        self.tab.video_loader.saveVideo(str(self.folder))
        self.assertEqual(json.loads(path.read_text()), original)
        self.tab.pose_estimater.setProcessedData(self.pd.DataFrame([self.pose_row(1)]))
        self.tab.video_loader.saveVideo(str(self.folder))
        self.assertEqual([r['frame_number'] for r in json.loads(path.read_text())], [1, 3])

    def test_old_video_completion_callback_cannot_finalize_new_video(self):
        self.tab._video_end_pending = True
        self.tab._video_generation = 2
        with patch.object(self.tab, 'handleVideoEnd') as handle:
            self.tab._finishVideoEnd(1)
        handle.assert_not_called()


if __name__ == '__main__':
    unittest.main()
