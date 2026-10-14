import numpy as np
import pandas as pd
from Src.pose.one_euro_filter import OneEuroFilter
import os
import sys
from Src.UI_Control.viewers.triangulate_3d_viewer import Triangulate3DViewer

current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.join(current_dir, "..", "tracker"))

class PoseEstimater_3D:
    def __init__(self, model: Triangulate3DViewer):
        self.model = model
        self.img_shape = (0,0,0)
        self.person_df_3D = pd.DataFrame()
        self.pre_person_df_3D = pd.DataFrame()
        self.fps = None
        self.person_data_3D = []
        self.processed_frames = set()
        self.smooth_filter = OneEuroFilter()
        self.is_detect = False
        self.kpt_buffer = []
  
    def detectKpt(self, frameS_kpt, frameF_kpt, frame_num:int = None):
        if not self.is_detect:
            # print('not detect')
            return pd.DataFrame()

        #影片處理方式
        if frame_num not in self.processed_frames:
            self.model.add_2d_keypoints(frameS_kpt, frameF_kpt)
            self.processed_frames.add(frame_num)                    

        return self.person_df_3D

    def setPersonId(self, person_id):
        self.person_id = person_id
        print(f'person id: {self.person_id}')

    def setKptId(self, kpt_id):
        self.kpt_id = kpt_id
        # print(f'kpt id: {self.kpt_id}')
    
    def setPitchHandId(self,kpt_id):
        self.pitch_hand_id = kpt_id

    def setDetect(self, status:bool):
        self.is_detect = status
    
    def getPersonDf(self, frame_num=None, is_select=False, is_kpt=False):
        if self.person_df_3D.empty:
            return pd.DataFrame()
        condition = pd.Series([True] * len(self.person_df_3D))  # 初始條件設為全為 True
        if frame_num is not None:
            condition &= (self.person_df_3D['frame_number'] == frame_num)
 
        data = self.person_df_3D.loc[condition].copy()
        
        if data.empty:
            return None
        
        if is_kpt:
            data = data['keypoints'].iloc[0]

        return data
    
    def getPrePersonDf(self, *joint_ids):
        if self.pre_person_df_3D.empty:
            return tuple(None for _ in joint_ids)
        
        condition = self.pre_person_df_3D['person_id'] == self.person_id
        data = self.pre_person_df_3D.loc[condition]
        
        if data.empty:
            return tuple(None for _ in joint_ids)
        
        joint_data = tuple(data['keypoints'].iloc[0][joint_id] for joint_id in joint_ids)
        return joint_data
    
    def setProcessedData(self, person_df_3D:pd.DataFrame):
        if person_df_3D.empty:
            return
        self.person_df_3D = person_df_3D
        self.processed_frames = {frame_num for frame_num in self.person_df_3D['frame_number']}

    def update_person_df_3D(self, x:float, y:float,frame_num:int, correct_kpt_idx:int):
        self.person_df_3D.loc[(self.person_df_3D['frame_number'] == frame_num) &
                            (self.person_df_3D['person_id'] == self.person_id), 'keypoints'].iloc[0][correct_kpt_idx] = [x, y, 0.9, 1]

    def clearKptBuffer(self):
        self.kpt_buffer = []

    def reset(self):
        # 清空所有 DataFrame
        self.person_df_3D = pd.DataFrame()
        self.pre_person_df_3D = pd.DataFrame()
        
        # 清空列表和集合
        if self.person_data_3D:
            self.person_data_3D.clear()
        self.person_data_3D = []
        self.processed_frames.clear()
        
        # 清空緩衝區
        if self.kpt_buffer:
            self.kpt_buffer.clear()
        self.kpt_buffer = []
        self.person_data = []
        self.processed_frames = set()
        self.is_detect = False
        self.kpt_buffer = []