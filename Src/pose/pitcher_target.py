"""Select one central pitcher and keep spatial continuity between detections."""

import numpy as np


class PitcherTargetSelector:
    def __init__(self, acquisition_roi=(0.25, 0.15, 0.75, 0.85),
                 max_lost_frames=15, min_area_ratio=0.005):
        # Normalized bbox-center bounds, used only when acquiring a target.
        self.acquisition_roi = acquisition_roi
        self.max_lost_frames = max_lost_frames
        self.min_area_ratio = min_area_ratio
        self.reset()

    def reset(self):
        self.last_bbox = None
        self.lost_frames = 0
        self.frame_shape = None

    def _missing(self):
        if self.last_bbox is not None:
            self.lost_frames += 1
            if self.lost_frames >= self.max_lost_frames:
                self.last_bbox = None
                self.lost_frames = 0
        # Never infer a pose from a stale box or the whole image on a miss.
        return []

    def select(self, bboxes, frame_shape):
        height, width = frame_shape[:2]
        if height <= 0 or width <= 0:
            return self._missing()
        if self.frame_shape != (height, width):
            self.reset()
            self.frame_shape = (height, width)

        boxes = np.asarray(bboxes, dtype=float).reshape(-1, 4).copy()
        boxes = boxes[np.isfinite(boxes).all(axis=1)]
        boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, width)
        boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, height)
        sizes = boxes[:, 2:] - boxes[:, :2]
        areas = sizes[:, 0] * sizes[:, 1]
        valid = ((sizes[:, 0] > 0) & (sizes[:, 1] >= 0.1 * height)
                 & (areas >= self.min_area_ratio * width * height))
        boxes, areas = boxes[valid], areas[valid]
        if not len(boxes):
            return self._missing()

        centers = (boxes[:, :2] + boxes[:, 2:]) * 0.5
        if self.last_bbox is None:
            normalized = centers / [width, height]
            x1, y1, x2, y2 = self.acquisition_roi
            central = ((normalized[:, 0] >= x1) & (normalized[:, 0] <= x2)
                       & (normalized[:, 1] >= y1) & (normalized[:, 1] <= y2))
            boxes, areas, normalized = boxes[central], areas[central], normalized[central]
            if not len(boxes):
                return self._missing()
            # Position has priority over size; size breaks equal-distance ties.
            distance = np.linalg.norm(normalized - [0.5, 0.5], axis=1)
            index = np.lexsort((-areas, distance))[0]
        else:
            previous = np.asarray(self.last_bbox)
            previous_size = previous[2:] - previous[:2]
            previous_area = np.prod(previous_size)
            previous_center = (previous[:2] + previous[2:]) * 0.5
            distance = np.linalg.norm((centers - previous_center) / previous_size, axis=1)
            intersection_size = np.maximum(
                0, np.minimum(boxes[:, 2:], previous[2:])
                - np.maximum(boxes[:, :2], previous[:2]))
            intersection = np.prod(intersection_size, axis=1)
            iou = intersection / (areas + previous_area - intersection)
            area_ratio = areas / previous_area
            # Permit stride/arm-driven box changes, but reject distant people
            # and sudden scale changes even if detector ordering changes.
            matches = ((distance <= 0.6) & (iou >= 0.15)
                       & (area_ratio >= 0.3) & (area_ratio <= 3.0))
            if not matches.any():
                return self._missing()
            score = iou - 0.25 * distance - 0.1 * np.abs(np.log(area_ratio))
            index = np.argmax(np.where(matches, score, -np.inf))

        self.last_bbox = boxes[index].tolist()
        self.lost_frames = 0
        return [self.last_bbox.copy()]
