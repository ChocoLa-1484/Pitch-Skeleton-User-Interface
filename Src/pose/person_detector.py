"""Person detector backends shared by live capture, replay and metric tools.

Two backends produce the same output, ``(bboxes_xyxy, scores)`` for class
person, so the pitcher selector and ViTPose stay unchanged:

* ``ultralytics``: YOLO26 ``.pt`` weights (default ``yolo26m``).
* ``mmdet``: the original mmyolo YOLOv8-s config + checkpoint.

Choose with ``Model(detector=...)`` or the ``PITCH_DETECTOR`` environment
variable; values are the keys of ``DETECTORS``.
"""
import os

import numpy as np

from Src.paths import DB_DIR, SRC_DIR

DEFAULT_DETECTOR = 'yolo26m'
DETECTORS = {
    'yolo26m': {'backend': 'ultralytics', 'weights': DB_DIR / 'pretrain/yolo26m.pt'},
    'yolo26l': {'backend': 'ultralytics', 'weights': DB_DIR / 'pretrain/yolo26l.pt'},
    'yolo26x': {'backend': 'ultralytics', 'weights': DB_DIR / 'pretrain/yolo26x.pt'},
    'yolov8s': {'backend': 'mmdet',
                'config': SRC_DIR / 'mmyolo_main/configs/yolov8/yolov8_s_syncbn_fast_8xb16-500e_coco.py',
                'weights': DB_DIR / 'pretrain/yolov8_s_syncbn_fast_8xb16-500e_coco_20230117_180101-5aa5f0f1.pth'},
}


def resolve_detector(name=None):
    name = (name or os.environ.get('PITCH_DETECTOR') or DEFAULT_DETECTOR).strip().lower()
    if name not in DETECTORS:
        raise ValueError('未知的人物偵測器 {!r}，可用：{}'.format(name, ', '.join(DETECTORS)))
    spec = DETECTORS[name]
    if not os.path.isfile(spec['weights']):
        raise FileNotFoundError('找不到偵測器權重：{}（請放到 Db\\pretrain\\）'.format(spec['weights']))
    return name, spec


class UltralyticsPersonDetector:
    """YOLO26 detector restricted to class 0 (person)."""

    def __init__(self, weights, device='cuda:0', score_thr=0.3, imgsz=640):
        from ultralytics import YOLO
        self.model = YOLO(str(weights))
        self.device = device
        self.score_thr = score_thr
        self.imgsz = imgsz

    def __call__(self, image):
        """image: BGR ndarray. Returns (N x 4 xyxy float32, N scores)."""
        result = self.model.predict(image, classes=[0], conf=self.score_thr, imgsz=self.imgsz,
                                    device=self.device, verbose=False)[0]
        boxes = result.boxes
        if boxes is None or len(boxes) == 0:
            return np.zeros((0, 4), np.float32), np.zeros(0, np.float32)
        return (boxes.xyxy.cpu().numpy().astype(np.float32),
                boxes.conf.cpu().numpy().astype(np.float32))


def mmdet_person_boxes(model, image):
    """Original mmyolo path; kept so YOLOv8-s can still be selected."""
    from mmdet.apis import inference_detector
    result = inference_detector(model.detector, image, test_pipeline=model.detector_test_pipeline)
    pred = result.pred_instances.cpu().numpy()
    keep = (pred.labels == model.detect_args.det_cat_id) & (pred.scores > model.detect_args.score_thr)
    return pred.bboxes[keep].astype(np.float32), pred.scores[keep].astype(np.float32)


def detect_person_boxes(model, image):
    """(bboxes, scores) before NMS, for whichever backend ``model`` uses."""
    if getattr(model, 'detector_backend', 'mmdet') == 'ultralytics':
        return model.detector(image)
    return mmdet_person_boxes(model, image)
