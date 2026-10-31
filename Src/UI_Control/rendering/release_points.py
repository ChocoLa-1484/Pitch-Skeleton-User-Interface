"""Release wrist positions in each camera's own image coordinates."""
import cv2
import numpy as np


def release_point(estimator, frame):
    if frame is None:
        return None
    points = estimator.getPersonDf(frame_num=frame, is_select=True, is_kpt=True)
    if points is None or len(points) <= estimator.pitch_hand_id:
        return None
    point = np.asarray(points[estimator.pitch_hand_id], dtype=float)
    if len(point) < 3 or not np.isfinite(point[:3]).all() or point[2] < .6:
        return None
    return tuple(float(value) for value in point[:2])


def draw_release_points(image, points):
    if image is None:
        return image
    result = image.copy()
    points = [point for point in points if point is not None]
    for index, point in enumerate(points):
        if len(point) < 2 or not np.isfinite(point[:2]).all():
            continue
        x, y = (int(value) for value in point[:2])
        if 0 <= x < result.shape[1] and 0 <= y < result.shape[0]:
            color = (253, 251, 115) if index == len(points) - 1 else (0, 0, 255)
            cv2.circle(result, (x, y), 8, color, 2)
    return result
