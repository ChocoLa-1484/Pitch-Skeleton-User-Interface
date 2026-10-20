"""Shared CS-image event timing for 2D and 3D replay (HALPE-26).

These are image-based candidates, not a measurement of glenohumeral rotation
or ball separation. Look-ahead confirms landing/peaks but returns the event's
frame, not the later confirmation frame. All distances scale with leg length.
"""
from dataclasses import dataclass
import numpy as np

EVENT_METHOD = 'side_view_ground_contact_raised_arm_v3'


@dataclass
class PitchEvents:
    preparation: int = None
    fc: int = None
    mer: int = None
    br: int = None
    mer_confirmed: bool = False
    br_confirmed: bool = False


def detect_pitch_events(person_df, fps=60., throwing_hand='right', through_frame=None, person_id=None):
    result = PitchEvents()
    if person_df is None or person_df.empty:
        return result
    rows = person_df
    if person_id is not None:
        rows = rows[rows.person_id == person_id]
    if through_frame is not None:
        rows = rows[rows.frame_number <= through_frame]
    # Never join different people into one motion sequence.
    if rows.empty or rows.person_id.nunique() != 1 or rows.frame_number.duplicated().any():
        return result
    rows = rows.sort_values('frame_number')
    frames = rows.frame_number.to_numpy(dtype=int)
    try:
        p = np.asarray([np.asarray(k, float)[:, :3] for k in rows.keypoints])
    except (ValueError, TypeError):
        return result
    if p.ndim != 3 or p.shape[1:] != (26, 3):
        return result
    fps = max(float(fps or 60), 1.)
    left = throwing_hand == 'left'
    hip, knee, ankle, rear = (12, 14, 16, 15) if left else (11, 13, 15, 16)
    shoulder, elbow, wrist = (5, 7, 9) if left else (6, 8, 10)
    foot = [21, 23, 25] if left else [20, 22, 24]
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
    stable = []
    for i in range(prep + 1, len(frames) - window + 1):
        end = i + window
        if (not stance[i:end].all() or not np.all(np.diff(frames[i:end]) == 1)
                or np.ptp(p[i:end, ankle, 0]) > .04 * leg
                or np.ptp(foot_y[i:end]) > .04 * leg):
            continue
        stable.append(i)
    if stable:
        ground = np.percentile([np.median(foot_y[i:i+window]) for i in stable], 80)
        for i in stable:
            if np.min(foot_y[i:i+window]) >= ground - .025 * leg:
                result.fc = int(frames[i])
                break
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
