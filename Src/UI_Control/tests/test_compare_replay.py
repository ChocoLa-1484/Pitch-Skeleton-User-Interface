"""Exercise comparison controls with real Qt widgets and saved pose data."""
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

UI_DIR = Path(__file__).resolve().parents[1]
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['QT_QPA_FONTDIR'] = 'C:/Windows/Fonts'
os.environ.setdefault('CONDA_PREFIX', sys.prefix)


class CompareReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        old = os.getcwd()
        os.chdir(UI_DIR)
        cls.addClassCleanup(os.chdir, old)
        import Src.UI_Control.main
        from PyQt5.QtWidgets import QApplication
        from Src.UI_Control.widgets import video_widget_compare
        import numpy as np
        import pandas as pd
        cls.app = QApplication.instance() or QApplication([])
        cls.module, cls.np, cls.pd = video_widget_compare, np, pd

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.tab = self.module.PoseVideoCompareTabControl(SimpleNamespace(setImageSize=Mock()))
        self.addCleanup(self.close_tab)
        self.estimators = (self.tab.pose_estimater, self.tab.pose_estimater_2,
                           self.tab.pose_estimater_3, self.tab.pose_estimater_4)
        self.drawers = (self.tab.image_drawer, self.tab.image_drawer_2,
                        self.tab.image_drawer_3, self.tab.image_drawer_4)
        for group, loader in enumerate((self.tab.video_loader, self.tab.video_loader_2)):
            loader.video_name, loader.video_name_2 = f'CF_{group}', f'CS_{group}'
            loader.folder_path = str(self.folder)
            loader.total_frames, loader.video_fps = (12, 18)[group], 60.
            loader.video_size, loader.is_loading, loader.frame_offset = (320, 180), False, 0
            frames = [self.np.full((180, 320, 3), 30 + frame + group * 40, self.np.uint8)
                      for frame in range(loader.total_frames)]
            loader.video_frames, loader.video_frames_2 = frames, [frame.copy() for frame in frames]
            for camera in range(2):
                rows = [{'frame_number': frame, 'person_id': 1, 'bbox': [30, 20, 290, 170],
                         'keypoints': [[40 + i * 8 + frame, 30 + (i % 10) * 12, .99, 0] for i in range(26)]}
                        for frame in range(loader.total_frames)]
                estimator = self.estimators[group * 2 + camera]
                estimator.setProcessedData(self.pd.DataFrame(rows))
                estimator.detectKpt = Mock(side_effect=AssertionError('Replay must not run inference'))
            self.tab.initFrameSlider(loader.total_frames, (self.tab.ui.frameSlider_2, self.tab.ui.frameSlider_3)[group],
                                     (self.tab.ui.frameNumLabel_2, self.tab.ui.frameNumLabel_3)[group])
        self.tab._configure_master_slider()
        self.tab.initGraph()

    def close_tab(self):
        self.tab.is_play = False
        if self.tab._play_timer is not None:
            self.tab._play_timer.stop()
        for attr in ('pose_analyzer', 'pose_analyzer_2', 'pose_analyzer_3', 'pose_analyzer_4'):
            viewer = getattr(self.tab, attr).viewer3D
            viewer.timer.stop()
            viewer.canvas.close()
        self.tab.close()
        self.tab.deleteLater()
        self.app.processEvents()

    def test_skeletons_render_for_all_four_views_during_seek_and_playback(self):
        with patch.object(self.tab, 'showImage') as display:
            self.tab.ui.showSkeletonCheckBox.setChecked(True)
            self.assertEqual(display.call_count, 4)
            for group, call in enumerate(display.call_args_list):
                source = (self.tab.video_loader, self.tab.video_loader_2)[group // 2].getVideoImage(0)[group % 2]
                self.assertFalse(self.np.array_equal(call.args[0], source))
            self.tab.ui.frameSlider_3.setValue(4)
            self.tab.ui.frameSlider.setValue(3)
            self.assertEqual((self.tab.ui.frameSlider_2.value(), self.tab.ui.frameSlider_3.value()), (3, 7))
            self.tab.playBtnClicked()
            self.tab._play_timer.stop()
            self.tab._playNextFrame()
            self.assertEqual((self.tab.ui.frameSlider_2.value(), self.tab.ui.frameSlider_3.value()), (4, 8))
            for estimator in self.estimators:
                estimator.detectKpt.assert_not_called()
            self.assertTrue(all(drawer.show_skeleton for drawer in self.drawers))

    def test_independent_seek_updates_sync_offset_and_playback_without_snapping_back(self):
        self.tab.ui.frameSlider.setValue(3)
        self.tab.ui.frameSlider_3.setValue(7)
        self.assertEqual(self.tab.ui.frameSlider_2.value(), 3)
        self.assertEqual(self.tab.compare_frame_offset, 4)
        self.tab.ui.frameSlider.setValue(5)
        self.assertEqual((self.tab.ui.frameSlider_2.value(), self.tab.ui.frameSlider_3.value()), (5, 9))
        self.tab.playBtnClicked()
        self.tab._play_timer.stop()
        self.tab.ui.frameSlider_2.setValue(6)
        self.tab._playNextFrame()
        self.assertEqual((self.tab.ui.frameSlider_2.value(), self.tab.ui.frameSlider_3.value()), (7, 10))

    def test_sync_slider_reaches_longer_clip_end_and_toggles_preserve_positions(self):
        self.tab.ui.frameSlider.setValue(16)
        self.assertEqual((self.tab.ui.frameSlider_2.value(), self.tab.ui.frameSlider_3.value()), (11, 16))
        self.tab.ui.showSkeletonCheckBox.setChecked(True)
        self.tab.ui.showBboxCheckBox.setChecked(True)
        self.assertEqual((self.tab.ui.frameSlider_2.value(), self.tab.ui.frameSlider_3.value()), (11, 16))
        self.assertTrue(all(drawer.show_bbox for drawer in self.drawers))

    def test_bottom_overlay_uses_its_own_frame_number(self):
        self.tab.ui.frameSlider_3.setValue(5)
        with patch.object(self.drawers[2], 'drawInfo', wraps=self.drawers[2].drawInfo) as front, \
             patch.object(self.drawers[3], 'drawInfo', wraps=self.drawers[3].drawInfo) as side:
            self.tab.ui.frameSlider.setValue(4)
        self.assertEqual(front.call_args.args[1], 9)
        self.assertEqual(side.call_args.args[1], 9)

    def test_playback_finishes_at_longer_clip_and_restarts(self):
        self.tab.ui.frameSlider.setValue(16)
        self.tab.playBtnClicked()
        self.tab._play_timer.stop()
        self.tab._playNextFrame()
        self.assertEqual((self.tab.ui.frameSlider_2.value(), self.tab.ui.frameSlider_3.value()), (11, 17))
        self.tab._playNextFrame()
        self.assertFalse(self.tab.is_play)
        self.tab.playBtnClicked()
        self.tab._play_timer.stop()
        self.assertTrue(self.tab.is_play)
        self.assertEqual((self.tab.ui.frameSlider_2.value(), self.tab.ui.frameSlider_3.value()), (0, 0))

    def write_events(self, group, data):
        path = self.folder / f'events_{group}.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return str(path)

    def test_independent_keyframes_and_edits_do_not_change_other_group_or_saved_json(self):
        paths = [self.write_events(group, {'Keyframes': {'preparation_frame': group + 1,
                 'FC': {'frame': fc}, 'MER': {'frame': mer, 'shoulder_angle': None}, 'BR': {'frame': br}}})
                 for group, (fc, mer, br) in enumerate(((3, 5, 8), (7, 10, 15)))]
        before = [Path(path).read_bytes() for path in paths]
        self.tab.display_keyframe_data_from_json(*paths)
        self.tab.ui.footContact_ptn_2.click()
        self.assertEqual((self.tab.ui.frameSlider_2.value(), self.tab.ui.frameSlider_3.value()), (0, 7))
        self.tab.ui.maxER_ptn.click()
        self.assertEqual((self.tab.ui.frameSlider_2.value(), self.tab.ui.frameSlider_3.value()), (5, 7))
        self.tab.keyframe_edits[1, 'wrist_speed_frame'].setValue(14)
        self.assertEqual((self.tab.wrist_speed_frame, self.tab.wrist_speed_frame_2), (8, 14))
        self.assertEqual(self.tab.ui.frameSlider_2.value(), 5)
        self.assertEqual([Path(path).read_bytes() for path in paths], before)

    def test_missing_events_in_one_group_do_not_disable_valid_events_in_other_group(self):
        path = self.write_events(1, {'foot_contact': {'frame_number': 6, 'stride_distance_cm': None},
                                     'max_shoulder_er': {'frame_number': 9, 'shoulder_angle_deg': None},
                                     'release': {'frame_number': None}})
        self.tab.display_keyframe_data_from_json(None, path, is_processed=True)
        self.assertFalse(self.tab.ui.footContact_ptn.isEnabled())
        self.assertTrue(self.tab.ui.footContact_ptn_2.isEnabled())
        self.assertTrue(self.tab.ui.maxER_ptn_2.isEnabled())
        self.assertFalse(self.tab.ui.release_ptn_2.isEnabled())
        self.tab._update_phase_visualization()  # Partial phases must be safe.

    def test_2d_load_restores_all_skeletons_and_builds_each_full_curve(self):
        for index, estimator in enumerate(self.estimators):
            name = ('CF', 'CS')[index % 2] + f'_{index // 2}'
            estimator.person_df.to_json(self.folder / f'{name}.json', orient='records')
            estimator.reset()
        with patch.object(self.tab.pose_analyzer, 'addAnalyzeInfo') as first, \
             patch.object(self.tab.pose_analyzer_3, 'addAnalyzeInfo') as second:
            self.tab.load2DProcessedData()
        self.assertEqual((first.call_count, second.call_count), (12, 18))
        self.assertTrue(all(not estimator.person_df.empty for estimator in self.estimators))

    def test_chart_has_visible_curves_and_uses_each_groups_actual_frame(self):
        for analyzer, count, bias in ((self.tab.pose_analyzer, 12, 0), (self.tab.pose_analyzer_3, 18, 20)):
            analyzer.analyze_df = self.pd.DataFrame({'frame_number': range(count),
                                                   'shoulder_hiple_angle': [float(f + bias) for f in range(count)]})
        self.tab.initGraph()
        self.tab.ui.frameSlider_3.setValue(4)
        self.tab.ui.frameSlider.setValue(2)
        graph = self.tab.graph_plotter
        self.assertEqual(len(graph._displayed_plots), 1)
        self.assertGreaterEqual(self.tab.ui.graph_widget.minimumHeight(), 180)
        self.assertEqual(graph.scatters_1[2].data['y'].tolist(), [2.])
        self.assertEqual(graph.scatters_2[2].data['y'].tolist(), [26.])
        self.tab.pose_analyzer_3.analyze_df.loc[6, 'shoulder_hiple_angle'] = float('nan')
        self.tab.pose_analyzer_3._frame_data_cache.clear()
        self.tab._update_graph()
        self.assertEqual(len(graph.scatters_2[2].data), 0)


if __name__ == '__main__':
    unittest.main()
