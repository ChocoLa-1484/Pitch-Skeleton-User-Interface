"""Anatomical HALPE-26 joints; the lead leg is opposite the throwing arm."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PitchJoints:
    shoulder: int
    elbow: int
    wrist: int
    hip: int
    knee: int
    ankle: int
    rear_hip: int
    rear_ankle: int
    foot: tuple


def pitch_joints(hand):
    if hand == 'left':
        return PitchJoints(5, 7, 9, 12, 14, 16, 11, 15, (21, 23, 25))
    if hand == 'right':
        return PitchJoints(6, 8, 10, 11, 13, 15, 12, 16, (20, 22, 24))
    raise ValueError('投球手別必須為 left 或 right。')


def estimator_hand(estimator):
    return 'left' if estimator.pitch_hand_id == 9 else 'right'
