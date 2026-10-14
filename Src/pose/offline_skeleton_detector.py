"""
離線骨架檢測器 - 用於後台批量處理視頻文件夾中的所有視頻並保存關鍵點坐標

使用方式：
    detector = OfflineSkeletonDetector(model=pose_model)
    detector.process_folder("path/to/videos", output_folder="path/to/output")
"""

import os
import sys
import cv2
import json
import numpy as np
import pandas as pd
from pathlib import Path
from typing import Optional, Callable, Dict, List
from tqdm import tqdm
from threading import Thread
import time

from Src.pose.detect_skeleton import PoseEstimater
from Src.common.timer import FPSTimer


class OfflineSkeletonDetector:
    """
    離線骨架檢測器 - 處理視頻文件夾中的所有視頻並保存關鍵點坐標
    
    特性：
    - 支持批量處理多個視頻文件
    - 自動提取視頻中的所有幀並進行骨架檢測
    - 保存關鍵點坐標到JSON文件
    - 支持後台線程執行
    - 進度跟蹤和回調通知
    """
    
    # 支持的視頻格式
    SUPPORTED_VIDEO_FORMATS = ('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv')
    
    def __init__(self, model=None, verbose: bool = True):
        """
        初始化離線骨架檢測器
        
        Args:
            model: Model對象，包含骨架檢測模型
            verbose: 是否顯示詳細的進度信息
        """
        self.model = model
        self.verbose = verbose
        self.pose_estimater = None
        self.is_processing = False
        self.current_progress = 0
        self.total_progress = 0
        self.current_video = ""
        self.error_log = []
        
        # 初始化骨架檢測器
        if model is not None:
            self.pose_estimater = PoseEstimater(model=model)
            self.pose_estimater.setDetect(True)
    
    def _log(self, message: str):
        """輸出日誌信息"""
        if self.verbose:
            print(f"[OfflineSkeletonDetector] {message}")
    
    def _get_video_files(self, folder_path: str) -> List[str]:
        """
        獲取文件夾中的所有視頻文件
        
        Args:
            folder_path: 文件夾路徑
            
        Returns:
            視頻文件路徑列表
        """
        if not os.path.isdir(folder_path):
            self._log(f"❌ 文件夾不存在: {folder_path}")
            return []
        
        video_files = []
        for file in os.listdir(folder_path):
            if file.lower().endswith(self.SUPPORTED_VIDEO_FORMATS):
                full_path = os.path.join(folder_path, file)
                video_files.append(full_path)
        
        video_files.sort()
        return video_files
    
    def _extract_frames_from_video(self, video_path: str) -> tuple:
        """
        從視頻中提取所有幀
        
        Args:
            video_path: 視頻文件路徑
            
        Returns:
            (幀列表, 幀率, 幀數) 或異常返回 (None, 0, 0)
        """
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                raise ValueError("無法打開視頻文件")
            
            fps = cap.get(cv2.CAP_PROP_FPS)
            frames = []
            frame_count = 0
            
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                frames.append(frame)
                frame_count += 1
            
            cap.release()
            
            if frame_count == 0:
                raise ValueError("視頻沒有有效幀")
            
            return frames, fps, frame_count
        
        except Exception as e:
            error_msg = f"提取幀失敗 ({video_path}): {str(e)}"
            self._log(f"❌ {error_msg}")
            self.error_log.append(error_msg)
            return None, 0, 0
    
    def _detect_skeleton_in_frames(self, frames: List, video_name: str = "") -> pd.DataFrame:
        """
        在所有幀中檢測骨架
        
        Args:
            frames: 幀列表
            video_name: 視頻名稱（用於日誌）
            
        Returns:
            包含檢測結果的DataFrame
        """
        if self.pose_estimater is None:
            self._log("❌ 骨架檢測器未初始化")
            return pd.DataFrame()

        for frame_num, frame in enumerate(frames):
            try:
                # 執行骨架檢測
                self.pose_estimater.detectKpt(
                    frame, 
                    frame_num=frame_num, 
                    is_video=True, 
                    is_processed=False
                )
            
            except Exception as e:
                error_msg = f"幀 {frame_num} 檢測失敗: {str(e)}"
                self._log(f"⚠️  {error_msg}")
                self.error_log.append(error_msg)
                continue

        combined_df = self.pose_estimater.person_df.copy()
        
        # --- 以下保留診斷邏輯 ---
        if not combined_df.empty:
            total_rows = len(combined_df)
            unique_frames = combined_df['frame_number'].nunique()
            avg_per_frame = total_rows / unique_frames if unique_frames > 0 else 0
            
            self._log(f"    📊 診斷信息 - 總行數: {total_rows}, 檢測幀數: {unique_frames}, 平均每幀: {avg_per_frame:.1f}")
            
            # 異常檢測：若平均行數接近總幀數，表示檢測器狀態污染
            if avg_per_frame > len(frames) * 0.9:
                self._log(f"    ⚠️  【警告】檢測數據異常！平均每幀 {avg_per_frame:.1f} 列接近總幀數 {len(frames)}")
                self._log(f"    可能原因：檢測器內部狀態未清理，上一個視頻結果洩露")
                self.error_log.append(f"檢測器狀態污染 ({video_name}): avg_per_frame={avg_per_frame:.1f}")
            
            return combined_df
        
        return pd.DataFrame()
    
    def _save_keypoints_to_json(self, df: pd.DataFrame, output_path: str) -> bool:
        """
        將關鍵點保存到JSON文件（格式與 cv_control.py saveVideo 一致）
        
        Args:
            df: 包含關鍵點數據的DataFrame
            output_path: 輸出JSON文件路徑
            
        Returns:
            是否保存成功
        """
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # 復制DataFrame以避免修改原始數據
            save_df = df.copy()
            
            # 保存無延遲坐標 - 如果存在 'raw_keypoints' 則使用它替換 'keypoints'
            # 這與 cv_control.py 的 saveVideo 方法保持一致
            if 'raw_keypoints' in save_df.columns:
                save_df['keypoints'] = save_df['raw_keypoints']
            
            # 轉換DataFrame為JSON格式 (使用 orient='records' 與 cv_control 一致)
            json_data = save_df.to_json(orient='records')
            
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(json_data)
            
            self._log(f"✓ 關鍵點已保存: {output_path}")
            return True
        
        except Exception as e:
            error_msg = f"保存JSON失敗 ({output_path}): {str(e)}"
            self._log(f"❌ {error_msg}")
            self.error_log.append(error_msg)
            return False
    
    def _save_metadata_to_json(self, metadata: Dict, output_path: str) -> bool:
        """
        將元數據保存到JSON文件
        
        Args:
            metadata: 元數據字典
            output_path: 輸出JSON文件路徑
            
        Returns:
            是否保存成功
        """
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # 轉換numpy類型為Python原生類型
            def convert_to_native(obj):
                if isinstance(obj, np.integer):
                    return int(obj)
                elif isinstance(obj, np.floating):
                    return float(obj)
                elif isinstance(obj, np.ndarray):
                    return obj.tolist()
                return obj
            
            metadata_serializable = {k: convert_to_native(v) for k, v in metadata.items()}
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(metadata_serializable, f, indent=2, ensure_ascii=False)
            
            self._log(f"✓ 元數據已保存: {output_path}")
            return True
        
        except Exception as e:
            error_msg = f"保存元數據失敗 ({output_path}): {str(e)}"
            self._log(f"❌ {error_msg}")
            self.error_log.append(error_msg)
            return False
    
    def process_single_video(self, video_path: str, output_folder: str) -> bool:
        """
        處理單個視頻文件
        
        Args:
            video_path: 視頻文件路徑
            output_folder: 輸出文件夾
            
        Returns:
            是否處理成功
        """
        video_name = os.path.splitext(os.path.basename(video_path))[0]
        self.current_video = video_name
        
        self._log(f"\n📹 開始處理視頻: {video_name}")
        
        # 【關鍵】處理前重置骨架檢測器狀態，避免前一個視頻的數據污染
        if self.pose_estimater is not None:
            self.pose_estimater.reset()
            self.pose_estimater.setDetect(True)
            self._log(f"  ✓ 重置檢測器狀態")
        
        cap = cv2.VideoCapture(video_path)
        frame_num = 0

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                if frame is None:
                    continue

                try:
                    # 直接餵給模型，處理完這幀後，frame 的記憶體就會被下一次循環覆蓋
                    self.pose_estimater.detectKpt(
                        frame, 
                        frame_num=frame_num, 
                        is_video=True, 
                        is_processed=False
                    )
                except TypeError:
                    self._log(f"⚠️ 第 {frame_num} 幀偵測不到目標，已跳過")
                
                frame_num += 1
            results_df = self.pose_estimater.person_df.copy()

            # 保存關鍵點到JSON
            json_output_path = os.path.join(output_folder, f"{video_name}_keypoints.json")
            if not self._save_keypoints_to_json(results_df, json_output_path):
                return False

            self._log(f"✓ 視頻處理完成: {video_name} (記憶體已清理)")
            return True
        
        except Exception as e:
                self._log(f"❌ 處理影片時發生嚴重崩潰: {e}")
                return False
        finally:
            cap.release()
    
    def process_folder(self, folder_path: str, 
                      progress_callback: Optional[Callable] = None) -> Dict:
        """
        批量處理文件夾中的所有視頻
        
        Args:
            folder_path: 包含視頻的文件夾路徑
            output_folder: 輸出文件夾路徑
            progress_callback: 進度回調函數 callback(current, total, video_name)
            
        Returns:
            處理結果字典 {
                'total_videos': 總視頻數,
                'processed': 成功處理的視頻數,
                'failed': 失敗的視頻數,
                'errors': 錯誤列表
            }
        """
        self.is_processing = True
        self.error_log.clear()
        
        self._log(f"\n{'='*60}")
        self._log(f"開始批量處理視頻文件夾")
        self._log(f"輸入文件夾: {folder_path}")
        self._log(f"{'='*60}\n")
        
        # 獲取所有視頻文件
        video_files = self._get_video_files(folder_path)
        
        if not video_files:
            self._log("❌ 未找到任何視頻文件")
            self.is_processing = False
            return {
                'total_videos': 0,
                'processed': 0,
                'failed': 0,
                'errors': self.error_log
            }
        
        self._log(f"✓ 找到 {len(video_files)} 個視頻文件\n")
        
        # 處理每個視頻
        processed_count = 0
        failed_count = 0
        
        for idx, video_path in enumerate(video_files, 1):
            self.current_progress = idx
            self.total_progress = len(video_files)
            
            if progress_callback:
                progress_callback(idx, len(video_files), os.path.basename(video_path))
            
            if self.process_single_video(video_path, folder_path):
                processed_count += 1
            else:
                failed_count += 1
        
        # 生成處理報告
        self._log(f"\n{'='*60}")
        self._log(f"批量處理完成")
        self._log(f"  總視頻數: {len(video_files)}")
        self._log(f"  成功: {processed_count}")
        self._log(f"  失敗: {failed_count}")
        
        if self.error_log:
            self._log(f"\n錯誤日誌:")
            for error in self.error_log:
                self._log(f"  - {error}")
        
        self._log(f"{'='*60}\n")
        
        self.is_processing = False
        
        return {
            'total_videos': len(video_files),
            'processed': processed_count,
            'failed': failed_count,
            'errors': self.error_log
        }
    
    def process_folder_async(self, folder_path: str,
                            progress_callback: Optional[Callable] = None) -> Thread:
        """
        異步（後台）批量處理視頻文件夾
        
        Args:
            folder_path: 包含視頻的文件夾路徑
            output_folder: 輸出文件夾路徑
            progress_callback: 進度回調函數
            
        Returns:
            後台處理線程
        """
        def run_processing():
            self.process_folder(folder_path, progress_callback)
        
        thread = Thread(target=run_processing, daemon=True)
        thread.start()
        return thread
    
    def get_status(self) -> Dict:
        """
        獲取當前處理狀態
        
        Returns:
            狀態字典 {
                'is_processing': 是否在處理,
                'current_progress': 當前進度,
                'total_progress': 總進度,
                'current_video': 當前視頻名稱,
                'progress_percent': 進度百分比
            }
        """
        progress_percent = 0
        if self.total_progress > 0:
            progress_percent = int((self.current_progress / self.total_progress) * 100)
        
        return {
            'is_processing': self.is_processing,
            'current_progress': self.current_progress,
            'total_progress': self.total_progress,
            'current_video': self.current_video,
            'progress_percent': progress_percent
        }
    
    def reset(self):
        """重置檢測器狀態"""
        self.is_processing = False
        self.current_progress = 0
        self.total_progress = 0
        self.current_video = ""
        self.error_log.clear()
