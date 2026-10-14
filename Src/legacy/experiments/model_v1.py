import os
import sys
from Src.paths import DB_DIR, UI_DIR
# 1. 定義環境路徑
ENV_PATH = r'E:\pitch_pro_env'

# 2. 定義 TensorRT 與 cuDNN 的路徑
TRT_DLL_PATH = os.path.join(ENV_PATH, r'Lib\site-packages\tensorrt_libs')
CUDNN_DLL_PATH = os.path.join(ENV_PATH, r'Lib\site-packages\nvidia\cudnn\bin')

# 3. 強制掛載所有 DLL 路徑
for path in [TRT_DLL_PATH, CUDNN_DLL_PATH]:
    if os.path.exists(path):
        # print(f"[System] 強制掛載 DLL 路徑: {path}")
        os.add_dll_directory(path)
        # 加入 PATH 確保其他相依套件也找得到
        os.environ['PATH'] = path + os.pathsep + os.environ['PATH']
    else:
        print(f"[Warning] 找不到路徑: {path}")
from argparse import Namespace
import numpy as np
import torch
from ultralytics import YOLO
import onnxruntime as ort


class Model(object):
    @staticmethod
    def _has_tensorrt_runtime() -> bool:
        # 除了檢查 PATH，直接檢查剛才掛載的路徑最準確
        dll_names = ['nvinfer_10.dll', 'nvinfer.dll']
        
        # 檢查系統 PATH
        path_entries = os.environ.get('PATH', '').split(os.pathsep)
        for entry in path_entries:
            if not entry: continue
            for dll_name in dll_names:
                if os.path.exists(os.path.join(entry, dll_name)):
                    return True
        return False

    def __init__(self, 
                 det_model_path=str(DB_DIR / 'pretrain/yolo11s.pt'),
                 pose_onnx_path=str(DB_DIR / 'pretrain/ViTPose_26kpts_fixed.onnx')):
        
        self.detect_args = Namespace(device='cuda:0', det_cat_id=0, score_thr=0.8, nms_thr=0.7, imgsz=480)
        self.pose_args = Namespace(device='cuda:0', kpt_thr=0.2)

        # print(f"[Model] 正在載入 YOLO 偵測器: {det_model_path}")
        self.detector = YOLO(det_model_path)

        # print(f"[Model] 正在載入姿態模型: {pose_onnx_path}")
        self.pose_onnx_path = pose_onnx_path
        available_providers = set(ort.get_available_providers())
        providers = []

        trt_runtime_ready = self._has_tensorrt_runtime()
        
        # 針對 RTX 5090 的頂級配置
        if 'TensorrtExecutionProvider' in available_providers and trt_runtime_ready:
            # print("[Model] 偵測到 TensorRT 環境，正在啟動加速引擎...")
            providers.append(('TensorrtExecutionProvider', {
                'device_id': 0,
                'trt_fp16_enable': False,          # 5090 必開，速度提升核心
                'trt_engine_cache_enable': True,
                'trt_engine_cache_path': str(UI_DIR / 'trt_cache'),
                'trt_max_workspace_size': 8 * 1024 * 1024 * 1024, # 給予 8GB 空間。5090 顯存很大，給多一點編譯才會過
                'trt_builder_optimization_level': 3,               # 先從 3 開始，5 有時會導致編譯失敗或無限卡住
                'trt_cuda_graph_enable': False,                    # 開啟 CUDA Graph，大幅降低 CPU 負擔
                'trt_int8_enable': False,                          # 除非你有量化表，否則先關閉
            }))
        
        if 'CUDAExecutionProvider' in available_providers:
            providers.append('CUDAExecutionProvider')
        
        providers.append('CPUExecutionProvider')

        # 載入模型 (第一次載入會因為建立 TRT Engine 比較久，約 1-3 分鐘)
        self._init_pose_session(providers)
        self._trt_active = 'TensorrtExecutionProvider' in self.pose_estimator.get_providers()

        self.tracker = None 
        self.image_size = (0, 0, 0)

    def _init_pose_session(self, providers):
        self.pose_estimator = ort.InferenceSession(self.pose_onnx_path, providers=providers)
        # print(f"[Model] 最終啟動的 Providers: {self.pose_estimator.get_providers()}")

        self.pose_input_name = self.pose_estimator.get_inputs()[0].name
        self.pose_input_shape = self.pose_estimator.get_inputs()[0].shape
        self.pose_output_names = [out.name for out in self.pose_estimator.get_outputs()]

    def _rebuild_pose_without_tensorrt(self):
        available_providers = set(ort.get_available_providers())
        fallback_providers = []
        if 'CUDAExecutionProvider' in available_providers:
            fallback_providers.append('CUDAExecutionProvider')
        fallback_providers.append('CPUExecutionProvider')

        print("[Model] 偵測到 TensorRT 空輸出，切換為 CUDA/CPU Providers 重新載入姿態模型...")
        self._init_pose_session(fallback_providers)
        self._trt_active = False

    def run_pose(self, input_tensor: np.ndarray):
        feed = {self.pose_input_name: input_tensor}
        output_names = self.pose_output_names if self.pose_output_names else None

        outputs = self.pose_estimator.run(output_names, feed)
        if outputs:
            return outputs

        if self._trt_active:
            self._rebuild_pose_without_tensorrt()
            output_names = self.pose_output_names if self.pose_output_names else None
            outputs = self.pose_estimator.run(output_names, feed)

        return outputs

    def reset_tracker(self):
        """重置追蹤狀態（用於 UI 的重置按鈕）"""
        predictor = getattr(self.detector, 'predictor', None)

        if predictor is None:
            self.tracker = None
            # print("[Model] Tracker reset skipped (predictor not initialized yet).")
            return

        trackers = getattr(predictor, 'trackers', None)
        if trackers is not None:
            for trk in trackers:
                reset_fn = getattr(trk, 'reset', None)
                if callable(reset_fn):
                    reset_fn()

        # 清空 track callback 建立的快取，避免跨流程沿用舊 ID 狀態
        if hasattr(predictor, 'trackers'):
            predictor.trackers = None
        if hasattr(predictor, 'vid_path'):
            predictor.vid_path = None

        # 下次 track() 重新建立 predictor / tracker，行為等同新流程
        self.detector.predictor = None
        self.tracker = None
        # print("[Model] Tracker state reset.")

    def setImageSize(self, image_size: tuple):
        self.image_size = image_size
        self.reset_tracker()

    def clear_gpu_cache(self):
        """釋放 RTX 5090 顯存"""
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            # print("[Model] GPU Cache Cleared.")
