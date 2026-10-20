"""Timing regressions: airborne pauses and early backward swings are not events."""
import unittest
import numpy as np
import pandas as pd

from Src.analysis.pitch_events import detect_pitch_events


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


if __name__ == '__main__':
    unittest.main()
