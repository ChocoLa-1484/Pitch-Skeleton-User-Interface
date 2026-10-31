"""Shared CS-image event timing for 2D and 3D replay (HALPE-26).

These are image-based candidates, not a measurement of glenohumeral rotation
or ball separation. Look-ahead confirms landing/peaks but returns the event's
frame, not the later confirmation frame. All distances scale with leg length.
"""
from dataclasses import dataclass, replace
import numpy as np
from Src.analysis.pitch_hand import pitch_joints

EVENT_METHOD = 'side_view_ground_contact_raised_arm_v3'


@dataclass
class PitchEvents:
    preparation: int = None
    fc: int = None
    mer: int = None
    br: int = None
    mer_confirmed: bool = False
    br_confirmed: bool = False


class _PoseSequence:
    """Parse a pose table once; prefix detection only takes NumPy views."""

    def __init__(self, person_df, person_id=None):
        self.frames = np.empty(0, dtype=int)
        self.points = np.empty((0, 26, 3), dtype=float)
        self.people = None
        self.bad_prefix = np.empty(0, dtype=int)
        self.person_prefix = np.empty(0, dtype=int)
        self.one_person = False
        if person_df is None or person_df.empty:
            return
        if not {'frame_number', 'person_id', 'keypoints'} <= set(person_df.columns):
            return
        rows = person_df
        if person_id is not None:
            rows = rows[rows.person_id == person_id]
        rows = rows.sort_values('frame_number')
        if rows.empty:
            return
        try:
            self.frames = rows.frame_number.to_numpy(dtype=int)
        except (ValueError, TypeError):
            return
        self.people = rows.person_id.reset_index(drop=True)
        self.person_prefix = np.cumsum(self.people.notna().to_numpy())
        self.one_person = self.people.nunique() == 1
        bad = np.zeros(len(rows), dtype=bool)
        keypoints = rows.keypoints.tolist()
        try:
            points = np.asarray(keypoints, dtype=float)
            if points.ndim != 3 or points.shape[1] != 26 or points.shape[2] < 3:
                raise ValueError('Expected HALPE-26 points')
            self.points = points[:, :, :3]
        except (ValueError, TypeError):
            # A corrupt later observation must not invalidate an earlier prefix.
            self.points = np.full((len(rows), 26, 3), np.nan)
            for index, value in enumerate(keypoints):
                try:
                    point = np.asarray(value, dtype=float)
                    if point.ndim != 2 or point.shape[0] != 26 or point.shape[1] < 3:
                        raise ValueError('Expected HALPE-26 points')
                    self.points[index] = point[:, :3]
                except (ValueError, TypeError):
                    bad[index] = True
        bad[1:] |= np.diff(self.frames) == 0
        self.bad_prefix = np.cumsum(bad)

    def prefix(self, through_frame):
        end = (len(self.frames) if through_frame is None else
               int(np.searchsorted(self.frames, through_frame, side='right')))
        if (not end or self.bad_prefix[end - 1] or not self.person_prefix[end - 1]
                or (not self.one_person and self.people.iloc[:end].nunique() != 1)):
            return self.frames[:0], self.points[:0], end
        return self.frames[:end], self.points[:end], end


class PitchEventDetector:
    """Reusable per-video cache for playback and growing inference tables.

    Dataframe replacement, row count, selected person and ``revision`` invalidate
    parsed coordinates. Callers editing an existing row or its nested keypoints
    must increment revision or call ``invalidate()``; object identity alone
    cannot detect an in-place list edit. Hand and FPS changes invalidate results.
    """

    def __init__(self):
        self.invalidate()

    def invalidate(self):
        self._source = None
        self._source_key = None
        self._sequence = None
        self._result_key = None
        self._result = None

    def detect(self, person_df, fps=60., throwing_hand='right', through_frame=None,
               person_id=None, revision=None):
        source_key = (len(person_df) if person_df is not None else 0, person_id, revision)
        if (person_df is not self._source or source_key != self._source_key
                or self._sequence is None):
            self._source = person_df  # Hold the object, so Python cannot reuse its id.
            self._source_key = source_key
            self._sequence = _PoseSequence(person_df, person_id)
            self._result_key = None
        frames, points, end = self._sequence.prefix(through_frame)
        result_key = (end, fps, throwing_hand)
        if result_key != self._result_key:
            self._result = _detect_arrays(frames, points, fps, throwing_hand)
            self._result_key = result_key
        # Events are mutable; do not let a consumer mutate the cached result.
        return replace(self._result)


def detect_pitch_events(person_df, fps=60., throwing_hand='right', through_frame=None,
                        person_id=None, cache=None, revision=None):
    if cache is not None:
        return cache.detect(person_df, fps, throwing_hand, through_frame, person_id, revision)
    if through_frame is not None and person_df is not None and not person_df.empty:
        person_df = person_df[person_df.frame_number <= through_frame]
    sequence = _PoseSequence(person_df, person_id)
    frames, points, _ = sequence.prefix(None)
    return _detect_arrays(frames, points, fps, throwing_hand)


def _detect_arrays(frames, p, fps, throwing_hand):
    result = PitchEvents()
    if not len(frames):
        return result
    try:
        fps = float(fps or 60)
    except (ValueError, TypeError):
        fps = 60.
    fps = max(fps, 1.) if np.isfinite(fps) else 60.
    joints = pitch_joints(throwing_hand)
    hip, knee, ankle, rear = joints.hip, joints.knee, joints.ankle, joints.rear_ankle
    shoulder, elbow, wrist = joints.shoulder, joints.elbow, joints.wrist
    foot = list(joints.foot)
    valid = np.isfinite(p).all(axis=2) & (p[:, :, 2] >= .6)
    leg_ok = valid[:, [hip, knee, ankle]].all(axis=1)
    lengths = np.linalg.norm(p[:, hip, :2] - p[:, knee, :2], axis=1) + np.linalg.norm(p[:, knee, :2] - p[:, ankle, :2], axis=1)
    lengths = lengths[leg_ok & (lengths > 1)]
    if not len(lengths):
        return result
    leg = float(np.median(lengths))
    lift = p[:, hip, 1] - p[:, knee, 1]
    raised = valid[:, [hip, knee]].all(axis=1) & (lift > -.2 * leg)
    if not raised.any():
        return result
    prep = int(np.argmax(np.where(raised, lift, -np.inf)))
    result.preparation = int(frames[prep])
    after = frames > frames[prep]
    stride = p[:, ankle, 0] - p[:, rear, 0]
    striding = after & valid[:, [ankle, rear]].all(axis=1) & (np.abs(stride) > .55 * leg)
    if not striding.any():
        return result
    direction = 1. if np.median(stride[striding]) >= 0 else -1.

    # A backward arm swing below the elbow can also have an angle near 180°.
    # Only evaluate cocking once the throwing arm is raised, wrist above elbow.
    arm_ok = valid[:, [shoulder, elbow, wrist]].all(axis=1)
    forearm = p[:, wrist, :2] - p[:, elbow, :2]
    norm = np.linalg.norm(forearm, axis=1)
    cocked = (after & arm_ok & (norm > .08 * leg)
              & (p[:, wrist, 1] < p[:, elbow, 1] - .015 * leg)
              & (p[:, wrist, 1] < p[:, shoulder, 1] - .05 * leg)
              & (p[:, elbow, 1] < p[:, shoulder, 1] + .25 * leg)
              & (direction * forearm[:, 0] < 0))
    angle = np.degrees(np.arccos(np.clip(direction * forearm[:, 0] / np.maximum(norm, 1e-9), -1, 1)))
    mer = int(np.argmax(np.where(cocked, angle, -np.inf))) if cocked.any() else None

    # Estimate the eventual support height using the foot during the stride.
    # An aerial pause is not contact: it must reach this height and stay there.
    foot_y = np.max(np.where(valid[:, foot], p[:, foot, 1], -np.inf), axis=1)
    foot_ok = np.isfinite(foot_y)
    limit = frames[mer] if mer is not None else frames[-1]
    stance = striding & foot_ok & (frames <= limit)
    window = max(3, int(round(.045 * fps)))
    if len(frames) >= window:
        # Read-only rolling views replace per-frame Python slicing/reductions.
        # Sorted unique frames make a span of window-1 equivalent to no gaps.
        rolling = np.lib.stride_tricks.sliding_window_view
        starts = np.arange(len(frames) - window + 1)
        stable = np.flatnonzero((starts > prep) & rolling(stance, window).all(axis=1)
                               & (frames[window - 1:] - frames[:len(starts)] == window - 1))
        ankle_windows = rolling(p[:, ankle, 0], window)[stable]
        foot_windows = rolling(foot_y, window)[stable]
        steady = ((np.ptp(ankle_windows, axis=1) <= .04 * leg)
                  & (np.ptp(foot_windows, axis=1) <= .04 * leg))
        stable, foot_windows = stable[steady], foot_windows[steady]
        if len(stable):
            ground = np.percentile(np.median(foot_windows, axis=1), 80)
            contacts = stable[np.min(foot_windows, axis=1) >= ground - .025 * leg]
            if len(contacts):
                result.fc = int(frames[contacts[0]])
    if result.fc is None or mer is None or frames[mer] <= result.fc:
        return result
    # Select the maximum raised-arm angle after contact, rather than locking
    # the first local fluctuation. Reviewable candidate until it turns forward.
    cocked &= frames > result.fc
    if not cocked.any():
        return result
    mer = int(np.argmax(np.where(cocked, angle, -np.inf)))
    result.mer = int(frames[mer])
    later = (frames > frames[mer]) & arm_ok
    result.mer_confirmed = bool(np.any(later & (angle < angle[mer] - 5)))
    speeds = np.full(len(frames), np.nan)
    consecutive = np.diff(frames) == 1
    speed_ok = consecutive & valid[1:, wrist] & valid[:-1, wrist]
    speeds[1:] = np.where(speed_ok, np.linalg.norm(np.diff(p[:, wrist, :2], axis=0), axis=1) * fps, np.nan)
    releasing = (later & (frames <= frames[mer] + int(round(.3 * fps)))
                 & (p[:, wrist, 1] <= p[:, shoulder, 1] + .15 * leg) & np.isfinite(speeds))
    if result.mer_confirmed and releasing.any():
        br = int(np.argmax(np.where(releasing, speeds, -np.inf)))
        result.br = int(frames[br])
        result.br_confirmed = bool(np.any((frames > frames[br]) & (frames <= frames[br] + window)
                                          & np.isfinite(speeds) & (speeds < .9 * speeds[br])))
    return result
