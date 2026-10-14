from argparse import ArgumentParser
from mmcv.transforms import Compose
from mmengine.logging import print_log
import sys
import os
from Src.paths import SRC_DIR, DB_DIR
sys.path.append(str(SRC_DIR / 'tracker'))
from tracker.mc_bot_sort import BoTSORT
from tracker.tracking_utils.timer import Timer
from mmpose.apis import init_model as init_pose_estimator
from mmpose.utils import adapt_mmdet_pipeline
try:
    from mmdet.apis import inference_detector, init_detector
    has_mmdet = True
except (ImportError, ModuleNotFoundError):
    has_mmdet = False




class Model(object):
    def __init__(self):
        self.detect_args = self.setDetectParser()
        self.pose_args = self.setPoseParser()
        self.tracker_args = self.setTrackerParser()
        self.detector = init_detector(
        self.detect_args.det_config, self.detect_args.det_checkpoint, device=self.detect_args.device)
        self.detector.cfg.test_dataloader.dataset.pipeline[
            0].type = 'mmdet.LoadImageFromNDArray'
        self.detector_test_pipeline = Compose(self.detector.cfg.test_dataloader.dataset.pipeline)

        self.pose_estimator = init_pose_estimator(
            self.pose_args.pose_config,
            self.pose_args.pose_checkpoint
        )
        self.tracker = BoTSORT(self.tracker_args, frame_rate=30.0)
        self.image_size = (0,0,0)

    def setDetectParser(self) -> ArgumentParser:
        parser = ArgumentParser()
        parser.add_argument('--det-config', default=str(SRC_DIR / 'mmyolo_main/configs/yolov8/yolov8_s_syncbn_fast_8xb16-500e_coco.py'), help='Config file for detection')
        parser.add_argument('--det-checkpoint', default=str(DB_DIR / 'pretrain/yolov8_s_syncbn_fast_8xb16-500e_coco_20230117_180101-5aa5f0f1.pth'), help='Checkpoint file for detection')
        parser.add_argument(
        '--device', default='cuda:0', help='Device used for inference')
        parser.add_argument(
        '--det-cat-id',
        type=int,
        default=0,
        help='Category id for bounding box detection model')
        parser.add_argument(
            '--score-thr', type=float, default=0.3, help='Bbox score threshold')
        parser.add_argument(
            '--nms-thr',
            type=float,
            default=0.3,
            help='IoU threshold for bounding box NMS')
        parser.add_argument(
            '--kpt-thr',
            type=float,
            default=0.3,
            help='Visualizing keypoint thresholds')
        parser.add_argument(
            '--draw-heatmap',
            action='store_true',
            default=False,
            help='Draw heatmap predicted by the model')
        parser.add_argument(
            '--show-kpt-idx',
            action='store_true',
            default=False,
            help='Whether to show the index of keypoints')
        parser.add_argument(
            '--skeleton-style',
            default='mmpose',
            type=str,
            choices=['mmpose', 'openpose'],
            help='Skeleton style selection')
        parser.add_argument(
            '--radius',
            type=int,
            default=3,
            help='Keypoint radius for visualization')
        parser.add_argument(
            '--show-interval', type=int, default=0, help='Sleep seconds per frame')
        parser.add_argument(
            '--alpha', type=float, default=0.8, help='The transparency of bboxes')
        args = parser.parse_args()
        return args

    def setPoseParser(self) -> ArgumentParser:
        parser = ArgumentParser()
        parser.add_argument('--pose-config', default=str(SRC_DIR / 'mmpose_main/configs/body_2d_keypoint/topdown_heatmap/haple/ViTPose_base_simple_halpe_256x192.py'), help='Config file for pose')
        parser.add_argument('--pose-checkpoint', default=str(DB_DIR / 'pretrain/epoch_210.pth'), help='Checkpoint file for pose')
        parser.add_argument(
            '--device', default='cuda:0', help='Device used for inference')
        parser.add_argument(
            '--kpt-thr',
            type=float,
            default=0.3,
            help='Visualizing keypoint thresholds')
        parser.add_argument(
            '--show-kpt-idx',
            action='store_true',
            default=False,
            help='Whether to show the index of keypoints')
        parser.add_argument(
            '--skeleton-style',
            default='mmpose',
            type=str,
            choices=['mmpose', 'openpose'],
            help='Skeleton style selection')
        parser.add_argument(
            '--radius',
            type=int,
            default=3,
            help='Keypoint radius for visualization')
        args = parser.parse_args()
        return args

    def setTrackerParser(self) -> ArgumentParser:
        parser = ArgumentParser()
        # tracking args
        parser.add_argument("--track_high_thresh", type=float, default=0.3, help="tracking confidence threshold")
        parser.add_argument("--track_low_thresh", default=0.05, type=float, help="lowest detection threshold")
        parser.add_argument("--new_track_thresh", default=0.4, type=float, help="new track thresh")
        parser.add_argument("--track_buffer", type=int, default=360, help="the frames for keep lost tracks")
        parser.add_argument("--match_thresh", type=float, default=0.8, help="matching threshold for tracking")
        parser.add_argument("--aspect_ratio_thresh", type=float, default=1.6,
                            help="threshold for filtering out boxes of which aspect ratio are above the given value.")
        parser.add_argument('--min_box_area', type=float, default=10, help='filter out tiny boxes')
        parser.add_argument("--fuse-score", dest="mot20", default=True, action='store_true',
                            help="fuse score and iou for association")

        # CMC
        parser.add_argument("--cmc-method", default="sparseOptFlow", type=str, help="cmc method: sparseOptFlow | files (Vidstab GMC) | orb | ecc")

        # ReID
        parser.add_argument("--with-reid", dest="with_reid", default=False , help="with ReID module.")
        parser.add_argument("--fast-reid-config", dest="fast_reid_config", default=str(SRC_DIR / 'tracker/fast_reid/configs/MOT17/sbs_S50.yml'),
                            type=str, help="reid config file path")
        parser.add_argument("--fast-reid-weights", dest="fast_reid_weights", default=str(SRC_DIR / 'tracker/fast_reid/mot17_sbs_S50.pth'),
                            type=str, help="reid config file path")
        parser.add_argument('--proximity_thresh', type=float, default=0.5,
                            help='threshold for rejecting low overlap reid matches')
        parser.add_argument('--appearance_thresh', type=float, default=0.25,
                            help='threshold for rejecting low appearance similarity reid matches')

        tracker_args = parser.parse_args()

        tracker_args.jde = False
        tracker_args.ablation = False
        return tracker_args

    def init_tracker(self):
        self.tracker = None
        self.tracker = BoTSORT(self.tracker_args, frame_rate=30.0)

    def reset_tracker(self):
        self.tracker = None
        self.tracker = BoTSORT(self.tracker_args, frame_rate=30.0)

    def setImageSize(self, image_size:tuple):
        self.reset_tracker()
        self.image_size = image_size
    
    def clear_gpu_cache(self):
        """清理 GPU 記憶體緩存"""
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            print("[Model] GPU cache cleared")
