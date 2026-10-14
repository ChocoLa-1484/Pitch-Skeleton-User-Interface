import cv2
import numpy as np
import queue
import os
import pandas as pd
from enum import Enum
from Src.UI_Control.rendering.vis_image import ImageDrawer
from Src.cameras.video_threads import VideoCaptureThread, VideoWriterThread, VideoToImagesThread
from PyQt5.QtWidgets import *
from collections import deque
import time


def normalize_fps(fps, default=30.0, min_fps=1.0, max_fps=2000.0) -> float:
    try:
        value = float(fps)
    except (TypeError, ValueError):
        return float(default)

    if not np.isfinite(value) or value <= 0:
        return float(default)

    return float(max(min_fps, min(max_fps, value)))

class Camera:
    def __init__(self, camera_idx:int = 0):
        self.camera_idx = camera_idx
        self.is_opened = False
        self.frame_count = 0
        self.fps_control = 6
        self.frame_size = None
        self.frame_buffer = queue.Queue()
        self.frame_buffer_2 = queue.Queue()
        self.video_path = None
        self.video_writer = None
        self.video_thread = None
        # 預存最近60幀（用於自動錄影的前幀）
        self.pre_frames = deque(maxlen=45)
        # 自動錄影用的所有幀（包括預存的60幀 + 錄製期間的幀）
        self.record_frames = None
        self.is_auto_recording = False
        self.auto_record_start_time = None

    def open_camera(self):
        # 開啟相機，並設置回調來處理每一幀
        self.frame_count = 0
        self.video_thread = VideoCaptureThread()
        self.video_thread.frame_ready.connect(self.buffer_frame)
        self.video_thread.start_capture()
        self.is_opened = True

    def close_camera(self):
        # 關閉相機，清理資源
        if self.video_thread is not None:
            # 斷開所有 signal 連接
            try:
                self.video_thread.frame_ready.disconnect()
            except:
                pass
            self.video_thread.stop_capture()
            self.video_thread.wait()  # 確保線程完全結束
            self.video_thread.deleteLater()  # 標記待刪除
            self.video_thread = None
        
        self.is_opened = False
        
        # 清空並重建Queue（避免殘留數據）
        while not self.frame_buffer.empty():
            try:
                self.frame_buffer.get_nowait()
            except:
                break
        while not self.frame_buffer_2.empty():
            try:
                self.frame_buffer_2.get_nowait()
            except:
                break
        self.frame_buffer = queue.Queue()
        self.frame_buffer_2 = queue.Queue()
        self.pre_frames.clear()
        
        # 清理录影相关数据
        if self.record_frames is not None:
            self.record_frames.clear() if isinstance(self.record_frames, list) else None
            self.record_frames = None
        self.is_auto_recording = False
        
        # 清理视频写入器
        if self.video_writer is not None:
            self.video_writer.release()
            self.video_writer = None

    def toggleCamera(self, is_checked:bool):
        # 根據checkbox狀態切換相機
        if is_checked:
            self.open_camera()
            frame_width = int(self.video_thread.camera1.get_width())
            frame_height = int(self.video_thread.camera1.get_height())
            fps = self.video_thread.camera1.get_fps()
            self.frame_size = (frame_width, frame_height)
            return (frame_width, frame_height, fps)
        else:
            self.close_camera()
            self.frame_size = None
            return (0, 0, 0)

    def buffer_frame(self, frame:np.ndarray, frame_2:np.ndarray):
        # 接收每一幀並進行處理
        self.frame_count += 1
        
        # 維護預存幀池（始終更新，用於自動錄影的前幀）
        self.pre_frames.append((frame.copy(), frame_2.copy()))
        
        # 采样帧放入 queue（用于正常显示）
        if self.is_opened and self.frame_count % self.fps_control == 0:
            self.frame_buffer.put(frame)
            self.frame_buffer_2.put(frame_2)
            # print(self.frame_buffer.qsize())

        # 普通录影（受采样影响）
        if self.video_writer is not None and self.video_writer.is_writing:
            self.video_writer.write_frame(frame, frame_2)
        
        # 自动录影（不受采样影响，每帧都存）
        if self.is_auto_recording and self.record_frames is not None:
            self.record_frames.append((frame.copy(), frame_2.copy()))

    def startRecording(self, filename: str, filename_2: str):
        # 開始錄製影片
        if self.video_thread is None:
            return
        frame_width = int(self.video_thread.camera1.get_width())
        frame_height = int(self.video_thread.camera1.get_height())
        fps = self.video_thread.camera1.get_fps()
        self.video_path = filename
        self.video_path_2 = filename_2

        write_fps = normalize_fps(fps / 3 if fps else 0)
        self.video_writer = VideoWriterThread(filename, filename_2, frame_width, frame_height, fps=write_fps)
        self.video_writer.start_writing()

    def stop_recording(self):
        # 停止錄製影片
        if self.video_writer is not None:
            self.video_writer.stop_writing()  # 停止寫入
            self.video_writer.release()  # 釋放執行緒
    
    def start_auto_recording(self):
        """開始自動錄影，初始化 record_frames 並設置錄影標記"""
        # 從預存的60幀開始（pre_frames 已經由 buffer_frame 持續維護）
        self.record_frames = [(f.copy(), f2.copy()) for f, f2 in list(self.pre_frames)]
        self.auto_record_start_time = time.time()
        self.is_auto_recording = True
    
    def stop_auto_recording(self):
        """停止自動錄影，返回錄製的所有幀並清理"""
        self.is_auto_recording = False
        duration = time.time() - self.auto_record_start_time if self.auto_record_start_time else 0
        # 總幀數
        total_frames = len(self.record_frames) if self.record_frames else 0
        # 預存幀數 (假設預存是 60 幀)
        pre_roll_count = 30 
        # 實際在 duration 期間產生的幀數
        new_frames_count = total_frames - pre_roll_count
        # 計算這段時間的真實產出率
        if duration > 0 and new_frames_count > 0:
            actual_fps = new_frames_count / duration
        else:
            # 如果錄太短，就用回相機設定值 // 3
            actual_fps = (self.video_thread.camera1.get_fps() / 3) if self.video_thread else 30
            
        frames = self.record_frames[:] if self.record_frames else []
        self.record_frames = None
        
        return frames, normalize_fps(actual_fps) # 回傳計算出來的真實 FPS

    def setCameraId(self, new_idx: int):
        self.camera_idx = new_idx
        print(f'camera id: {self.camera_idx}')

    def setFPSControl(self, fps:int):
        self.fps_control = fps
        print(f'fps control: {self.fps_control}')

class DataType(Enum):
    DEFAULT = {"name": "default", "tips": "", "filter": ""}
    IMAGE = {"name": "image", "tips": "",
             "filter": "Image files (*.jpeg *.png *.tiff *.psd *.pdf *.eps *.gif)"}
    VIDEO = {"name": "video", "tips": "",
             "filter": "Video files ( *.WEBM *.MPG *.MP2 *.MPEG *.MPE *.MPV *.OGG *.MP4 *.M4P *.M4V *.AVI *.WMV *.MOV *.QT *.FLV *.SWF *.MKV)"}
    CSV = {"name": "csv", "tips": "",
           "filter": "Video files (*.csv)"}
    FOLDER = {"name": "folder", "tips": "", "filter": ""}

class VideoLoader:
    def __init__(self, image_drawer: ImageDrawer =None, image_drawer_2: ImageDrawer =None):
        self.image_drawer = image_drawer
        self.image_drawer_2 = image_drawer_2
        self.video_path = None
        self.video_path_2 = None
        self.folder_path = None
        self.video_size = None
        self.video_fps_raw = None
        self.video_fps = None
        self.video_frames = None
        self.video_frames_2 = None
        self.total_frames = None
        self.video_name = None
        self.video_name_2 = None
        self.is_loading = False
        self.frame_offset = 0  # 幀偏移：正值表示 video_path_2 延遲，負值表示 video_path 延遲
    
    def loadVideo(self, video_path:str = None, video_path_2:str = None):
        options = QFileDialog.Options()
        if video_path is None:
            video_path, _ = QFileDialog.getOpenFileName(None, "Select Video File", "", "Video Files (*.mp4 *.avi);;All Files (*)", options=options)
            if not video_path:
                return
            # 自動推斷 video_path_2
            folder = os.path.dirname(video_path)
            basename = os.path.basename(video_path)
            if basename.startswith("CS"):
                video_path_2 = os.path.join(folder, "CF" + basename[2:])
            else:
                # 若不是C1開頭，可自行定義規則
                video_path_2 = None
        if not video_path_2 or not os.path.exists(video_path_2):
            # 若自動推斷失敗，則手動選取
            video_path_2, _ = QFileDialog.getOpenFileName(None, "Select Video File 2", "", "Video Files (*.mp4 *.avi);;All Files (*)", options=options)
            if not video_path_2:
                return
        self.video_path = video_path
        self.video_path_2 = video_path_2
        self.video_name = os.path.splitext(os.path.basename(self.video_path))[0]
        self.video_name_2 = os.path.splitext(os.path.basename(self.video_path_2))[0]
        self.folder_path = os.path.dirname(video_path)
        self.is_loading = True
        self.v_t = VideoToImagesThread(self.video_path, self.video_path_2)
        self.v_t.emit_signal.connect(self.video_to_frame)
        self.v_t.start()

    def video_to_frame(self, video_frames, video_frames_2, fps, count):
        # 若影片沒有有效幀，直接中止載入並提示
        if count == 0 or not video_frames or not video_frames_2:
            self.total_frames = 0
            self.video_frames = []
            self.video_frames_2 = []
            self.video_fps = 0
            self.video_size = None
            QMessageBox.warning(None, "影片載入失敗", "影片沒有有效畫面，請確認檔案是否正確")
            self.close_thread(self.v_t)
            return

        self.total_frames = count
        self.video_frames = video_frames
        self.video_frames_2 = video_frames_2
        self.video_fps_raw = fps
        self.video_fps = round(normalize_fps(fps), 3)
        self.video_size = (self.video_frames[0].shape[1], self.video_frames[0].shape[0])
        
        self.close_thread(self.v_t)

    def close_thread(self, thread):
        thread.stop()
        self.is_loading = False
        thread = None
    
    def getVideoImage(self, frame_num:int) : 
        if self.video_frames is None or self.video_frames_2 is None:
            return None, None

        if len(self.video_frames) == 0 or len(self.video_frames_2) == 0:
            return None, None

        max_idx = min(len(self.video_frames), len(self.video_frames_2)) - 1
        if max_idx < 0:
            return None, None

        # 應用幀偏移：frame_offset > 0 表示 video_path_2 延遲（需要往前讀），< 0 表示 video_path 延遲
        safe_idx_1 = int(max(0, min(frame_num, max_idx)))
        safe_idx_2 = int(max(0, min(frame_num - self.frame_offset, max_idx)))
        return self.video_frames[safe_idx_1].copy(), self.video_frames_2[safe_idx_2].copy()
    
    def saveVideo(self, output_folder:str = None):
        os.makedirs(output_folder, exist_ok=True)

        json_path = os.path.join(output_folder, f"{self.video_name}.json")
        json_path_2 = os.path.join(output_folder, f"{self.video_name_2}.json")

        save_person_df = self.image_drawer.pose_estimater.person_df.copy()
        save_person_df_2 = self.image_drawer_2.pose_estimater.person_df.copy()

        # Persist no-lag coordinates for processed playback.
        # If raw_keypoints exists, write it into keypoints for compatibility.
        if 'raw_keypoints' in save_person_df.columns:
            save_person_df['keypoints'] = save_person_df['raw_keypoints']
        if 'raw_keypoints' in save_person_df_2.columns:
            save_person_df_2['keypoints'] = save_person_df_2['raw_keypoints']

        save_person_df.to_json(json_path, orient='records')
        save_person_df_2.to_json(json_path_2, orient='records')

        # save_location = os.path.join(output_folder, f"{self.video_name}_Sk26.mp4")
        # save_location_2 = os.path.join(output_folder, f"{self.video_name_2}_Sk26.mp4")

        # safe_fps = round(normalize_fps(self.video_fps), 3)
        # if self.video_fps_raw is not None:
        #     print(f"[VideoLoader] loaded fps={self.video_fps_raw}, save fps={safe_fps}")
        # video_writer = cv2.VideoWriter(save_location, cv2.VideoWriter_fourcc(*'mp4v'), safe_fps, self.video_size)
        # video_writer_2 = cv2.VideoWriter(save_location_2, cv2.VideoWriter_fourcc(*'mp4v'), safe_fps, self.video_size)

        # if not video_writer.isOpened() or not video_writer_2.isOpened():
        #     print("Error while opening video writer!")
        #     video_writer.release()
        #     video_writer_2.release()
        #     return

        # for frame_num, frame in enumerate(self.video_frames):
        #     image = self.image_drawer.drawInfo(img = frame, frame_num = frame_num)
        #     video_writer.write(image)
        # for frame_num, frame in enumerate(self.video_frames_2):
        #     image = self.image_drawer_2.drawInfo(img = frame, frame_num = frame_num)
        #     video_writer_2.write(image)

        # video_writer.release()
        # video_writer_2.release()
        print("Store video success")

    def reset(self):
        # 清理视频帧数据（彻底释放内存）
        if self.video_frames is not None:
            self.video_frames.clear() if isinstance(self.video_frames, list) else None
        if self.video_frames_2 is not None:
            self.video_frames_2.clear() if isinstance(self.video_frames_2, list) else None
        
        self.video_path = None
        self.video_path_2 = None
        self.folder_path = None
        self.video_size = None
        self.video_fps_raw = None
        self.video_fps = None
        self.video_frames = None
        self.video_frames_2 = None
        self.total_frames = None
        self.video_name = None
        self.video_name_2 = None
        self.is_loading = False
        self.frame_offset = 0

from typing import Optional, Tuple
class JsonLoader:
    def __init__(self, folder_path:str = None, file_name:str = None):
        self.folder_path = folder_path
        self.file_name = file_name
        self.person_df = pd.DataFrame()

    def load(self) -> Optional[Tuple[str, pd.DataFrame]]:
        if self.folder_path is None or self.file_name is None:
            print("folder_path 或 file_name 尚未設定。")
            return None

        json_path = os.path.join(self.folder_path, f"{self.file_name}.json")
        print(f"Loading from: {json_path}")

        if not os.path.exists(json_path):
            print(f"JSON 檔案 {json_path} 不存在。")
            return None

        try:
            self.person_df = pd.read_json(json_path)
            return json_path, self.person_df
        except Exception as e:
            print(f"讀取 JSON 發生錯誤：{e}")
            return None
