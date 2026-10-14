"""
關鍵幀數據導出模塊（簡化版）
用於保存投球分析的關鍵幀信息到JSON文件
只保存UI標籤所需的核心數據
"""

import json
import os
from datetime import datetime
from typing import Dict, Optional, Any
from Src.analysis.analysis_io import atomic_json


class SimpleKeyframeLogger:
    """
    一個簡單的關鍵幀記錄器，只保存UI標籤所需的數據。
    """
    
    def __init__(self, output_dir: str = "keyframe_logs"):
        """
        初始化記錄器。
        
        Args:
            output_dir: 輸出日誌的目錄。
        """
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def save_keyframe_data(self,
                           fps: float,
                           video_name: str,
                           pitcher_id: str = "Unknown",
                           pitch_no: int = 1,
                           kneeUp_frame: Optional[int] = None,
                           fc_data: Optional[Dict[str, Any]] = None,
                           mer_data: Optional[Dict[str, Any]] = None,
                           br_data: Optional[Dict[str, Any]] = None,
                           analysis: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """
        將三個關鍵幀的核心數據保存到一個簡單的JSON文件中。

        Args:
            video_name: 影片名稱。
            pitcher_id: 投手ID。
            pitch_no: 投球編號。
            kneeUp_frame: 膝蓋抬起幀號（如果有）。
            fc_data (dict): Foot Contact 數據，應包含 'frame', 'shoulder_hip_angle', 'stride_distance'。
            mer_data (dict): Max External Rotation 數據，應包含 'frame', 'shoulder_angle'。
            br_data (dict): Ball Release 數據，應包含 'frame', 'wrist_speed', 'extension_distance'。

        Returns:
            如果成功，返回保存的JSON文件路徑，否則返回None。
        """
        
        # 準備要寫入的數據結構
        export_data = {
            "fps": fps,
            "pitch_info": {
                "pitcher_id": pitcher_id,
                "pitch_number": pitch_no,
                "video_name": video_name,
                "export_time": datetime.now().isoformat()
            },
            "knee_up": {"frame_number": kneeUp_frame} if kneeUp_frame is not None else None,
            "foot_contact": {},
            "max_shoulder_er": {},
            "release": {}
        }

        # 填充 Foot Contact 數據
        if fc_data:
            export_data["foot_contact"] = {
                "frame_number": fc_data.get("frame"),
                "shoulder_hip_angle_deg": fc_data.get("shoulder_hip_angle"),
                "stride_distance_cm": fc_data.get("stride_distance") * 100 if fc_data.get("stride_distance") is not None else None
            }

        # 填充 Max Shoulder ER 數據
        if mer_data:
            export_data["max_shoulder_er"] = {
                "frame_number": mer_data.get("frame"),
                "shoulder_angle_deg": mer_data.get("shoulder_angle")
            }

        # 填充 Release 數據
        if br_data:
            export_data["release"] = {
                "frame_number": br_data.get("frame"),
                "wrist_speed_mps": br_data.get("wrist_speed"),
                "extension_distance_cm": br_data.get("extension_distance") * 100 if br_data.get("extension_distance") is not None else None
            }

        # 生成文件名
        filename = f"{video_name}_keyframes.json"
        filepath = os.path.join(self.output_dir, filename)
        if analysis is not None:
            export_data['Analysis'] = analysis

        # 保存到JSON文件
        try:
            atomic_json(filepath, export_data)
            print(f"✓ 關鍵幀日誌已保存: {filepath}")
            return filepath
        except Exception as e:
            print(f"✗ 保存關鍵幀日誌失敗: {e}")
            return None


# =================================================================
# 如何在 video_widget_compare.py 中使用它的範例
# =================================================================
#
# 1. 在 __init__ 中創建一個實例:
#    from .utils.keyframe_export import SimpleKeyframeLogger
#    self.keyframe_logger = SimpleKeyframeLogger(output_dir="results/keyframe_logs")
#
# 2. 創建一個新的方法來收集和保存數據:
#    def export_all_keyframe_data(self):
#        if not all([self.foot_contact_frame, self.max_shoulder_angle_frame, self.wrist_speed_frame]):
#            print("尚未檢測到所有關鍵幀，無法導出。")
#            return
#
#        # 準備數據字典
#        fc_data = {
#            "frame": self.foot_contact_frame,
#            "shoulder_hip_angle": self.image_drawer.keyframe_shoulder_hip_angle,
#            "stride_distance": self.image_drawer.keyframe_stride_distance
#        }
#        mer_data = {
#            "frame": self.max_shoulder_angle_frame,
#            "shoulder_angle": self.image_drawer.keyframe_shoulder_angle
#        }
#        br_data = {
#            "frame": self.wrist_speed_frame,
#            "wrist_speed": self.image_drawer.keyframe_wrist_speed,
#            "extension_distance": self.image_drawer.keyframe_extension_distance
#        }
#
#        # 呼叫保存方法
#        self.keyframe_logger.save_keyframe_data(
#            video_name=self.video_loader.video_name,
#            pitcher_id="Pitcher01", # 您需要一個方法來設置投手ID
#            pitch_no=1,            # 您需要一個方法來設置投球編號
#            fc_data=fc_data,
#            mer_data=mer_data,
#            br_data=br_data
#        )
#
# 3. 在UI上添加一個 "導出" 按鈕，並將其點擊事件連接到這個新方法:
#    self.ui.exportButton.clicked.connect(self.export_all_keyframe_data)
#
