"""Timing regressions: airborne pauses and early backward swings are not events."""
import unittest
import numpy as np
import pandas as pd

from Src.analysis.pitch_events import detect_pitch_events, PitchEventDetector
from Src.analysis.PitchingAnalyzer import PitchingAnalyzer


def pitch_rows():
    rows = []
    for frame in range(22):
        p = np.full((26, 4), [300., 300., .95, 0.])
        p[11, :2], p[13, :2], p[15, :2] = [300, 300], [360, 410], [430, 480]
        p[16, :2] = [200, 480]
        p[[20, 22, 24], :2] = [[450, 500], [445, 500], [420, 500]]
        if frame < 5:
            p[15, :2] = [230, 380]
            p[13, 1] = 200 + 10 * abs(frame - 2)
            p[[20, 22, 24], 1] = 400
        elif frame < 10:
            p[15, :2] = [420, 435]  # Stationary, but still above the ground.
            p[[20, 22, 24], 1] = 455
        p[6, :2], p[8, :2], p[10, :2] = [300, 200], [340, 260], [220, 250]
        if frame >= 10:
            p[8, :2], p[10, :2] = [340, 180], [300, 100]
        if frame == 15:
            p[10, :2] = [240, 160]
        elif frame == 16:
            p[10, :2] = [260, 100]
        elif frame >= 17:
            p[10, :2] = [500 + (frame - 17) * 10, 170]
        p[0] = [np.nan, np.nan, 0., 0.]  # Face is irrelevant to these timings.
        rows.append({'frame_number': frame, 'person_id': 1, 'bbox': [0, 0, 640, 480], 'keypoints': p.tolist()})
    return rows


class PitchEventTests(unittest.TestCase):
    def test_live_trigger_uses_opposite_leg_and_throwing_wrist_for_each_hand(self):
        for hand, knee, wrist, other_knee, other_wrist in (('right', 13, 10, 14, 9), ('left', 14, 9, 13, 10)):
            detector = PitchingAnalyzer(hand)
            detector.init_frames = 0
            p = np.full((26, 4), [300., 500., .95, 0.])
            p[[11, 12], 1] = 300
            p[18, 1] = 200
            p[other_knee, 1] = 200
            self.assertIsNone(detector.process(p))
            p[knee, 1], p[knee, 2] = 200, .1
            self.assertIsNone(detector.process(p))
            p[knee, 2] = .95
            self.assertEqual(detector.process(p), 'START')
            p[15, 0], p[16, 0] = 100, 500
            p[other_wrist, 1] = 100
            detector.process(p)
            self.assertEqual(detector.state, 'KNEEUP')
            p[wrist, 1] = 100
            detector.process(p)
            self.assertEqual(detector.state, 'DELIVERY')
            p[wrist, 1] = 500
            detector.process(p)
            for _ in range(detector.follow_frames - 1):
                self.assertIsNone(detector.process(p))
            self.assertEqual(detector.process(p), 'STOP')

    def test_ground_contact_and_raised_arm_reject_the_early_false_peaks(self):
        event = detect_pitch_events(pd.DataFrame(pitch_rows()))
        self.assertEqual((event.preparation, event.fc, event.mer, event.br), (2, 10, 15, 17))
        self.assertTrue(event.mer_confirmed and event.br_confirmed)

    def test_timing_is_invariant_to_image_scale_translation_and_throwing_side(self):
        for scale in (.5, 2.):
            for left in (False, True):
                rows = pitch_rows()
                for row in rows:
                    p = np.array(row['keypoints'])
                    if left:
                        p[:, 0] = 800 - p[:, 0]
                        for a, b in ((1, 2), (3, 4), (5, 6), (7, 8), (9, 10), (11, 12),
                                     (13, 14), (15, 16), (20, 21), (22, 23), (24, 25)):
                            p[[a, b]] = p[[b, a]]
                    p[:, :2] = p[:, :2] * scale + 50
                    row['keypoints'] = p.tolist()
                event = detect_pitch_events(pd.DataFrame(rows), throwing_hand='left' if left else 'right')
                self.assertEqual((event.fc, event.mer, event.br), (10, 15, 17))

    def test_missing_support_points_cannot_produce_contact(self):
        rows = pitch_rows()
        for row in rows:
            for i in (20, 22, 24):
                row['keypoints'][i][2] = .2
        self.assertIsNone(detect_pitch_events(pd.DataFrame(rows)).fc)

    def test_multiple_people_are_never_combined_into_one_motion(self):
        rows = pitch_rows()
        extra = dict(rows[5], person_id=2)
        self.assertIsNone(detect_pitch_events(pd.DataFrame(rows + [extra])).fc)
        self.assertEqual(detect_pitch_events(pd.DataFrame(rows + [extra]), person_id=1).fc, 10)

    def test_gaps_cannot_confirm_landing(self):
        rows = [row for row in pitch_rows() if row['frame_number'] != 11]
        self.assertEqual(detect_pitch_events(pd.DataFrame(rows)).fc, 12)

    def test_cached_prefixes_match_full_detector_for_both_hands_and_rates(self):
        for hand in ('right', 'left'):
            rows = pitch_rows()
            if hand == 'left':
                for row in rows:
                    p = np.array(row['keypoints'])
                    p[:, 0] = 800 - p[:, 0]
                    for a, b in ((5, 6), (7, 8), (9, 10), (11, 12),
                                 (13, 14), (15, 16), (20, 21), (22, 23), (24, 25)):
                        p[[a, b]] = p[[b, a]]
                    row['keypoints'] = p.tolist()
            # Imported observations may not be sorted; seek backwards as well.
            df = pd.DataFrame(rows[::-1])
            cache = PitchEventDetector()
            for fps in (30, 60, 180):
                for frame in list(range(22)) + [10, 21, 4, 17]:
                    self.assertEqual(
                        detect_pitch_events(df, fps, hand, frame, cache=cache),
                        detect_pitch_events(df, fps, hand, frame))

    def test_cache_reloads_replaced_appended_and_revised_observations(self):
        df = pd.DataFrame(pitch_rows()[:16])
        cache = PitchEventDetector()
        self.assertIsNone(cache.detect(df).br)
        # A growing dataframe can keep its identity.
        for row in pitch_rows()[16:]:
            df.loc[len(df)] = row
        self.assertEqual(cache.detect(df).br, 17)
        # Nested edits require the estimator's monotonic pose_revision.
        for points in df.keypoints:
            for index in (20, 22, 24):
                points[index][2] = .2
        self.assertIsNone(cache.detect(df, revision=1).fc)
        df = pd.DataFrame(pitch_rows())
        self.assertEqual(cache.detect(df, revision=1).fc, 10)
        for points in df.keypoints:
            for index in (20, 22, 24):
                points[index][2] = .2
        cache.invalidate()
        self.assertIsNone(cache.detect(df, revision=1).fc)

    def test_cache_switches_person_hand_and_fps_without_stale_events(self):
        rows = pitch_rows()
        other = [dict(row, person_id=2) for row in pitch_rows()]
        for row in other:
            for index in (20, 22, 24):
                row['keypoints'][index][2] = .2
        df = pd.DataFrame(rows + other)
        cache = PitchEventDetector()
        self.assertEqual(cache.detect(df, person_id=1).fc, 10)
        self.assertIsNone(cache.detect(df, person_id=2).fc)
        left = cache.detect(df, person_id=1, throwing_hand='left')
        self.assertEqual(left, detect_pitch_events(df, person_id=1, throwing_hand='left'))
        self.assertNotEqual(left.fc, 10)
        self.assertIsNone(cache.detect(df, person_id=1, fps=180).fc)
        self.assertEqual(cache.detect(df, person_id=1, fps=60).fc, 10)

    def test_later_duplicate_person_or_bad_row_does_not_change_earlier_prefix(self):
        for extra in (dict(pitch_rows()[-1]),
                      dict(pitch_rows()[-1], person_id=2, frame_number=22),
                      dict(pitch_rows()[-1], keypoints=[], frame_number=22)):
            df = pd.DataFrame(pitch_rows() + [extra])
            cache = PitchEventDetector()
            self.assertEqual(cache.detect(df, through_frame=20).br, 17)
            self.assertIsNone(cache.detect(df).fc)

    def test_modifying_returned_events_does_not_change_cached_result(self):
        df = pd.DataFrame(pitch_rows())
        cache = PitchEventDetector()
        cache.detect(df).fc = 99
        self.assertEqual(cache.detect(df).fc, 10)

    def test_invalid_fps_uses_default_timing(self):
        df = pd.DataFrame(pitch_rows())
        for fps in (None, 0, float('nan'), float('inf'), 'unknown'):
            self.assertEqual(detect_pitch_events(df, fps).fc, 10)


if __name__ == '__main__':
    unittest.main()
