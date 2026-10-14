import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

import json
import numpy as np
import cv2

from Src.calibration import calib_store

# 點到對極線的距離小於此值（像素）才算內點
INLIER_THRESHOLD_PX = 1.0

def load_correspondences(json_path_left, json_path_right):
    # 讀取左右視角的 json 檔
    with open(json_path_left, 'r') as f_left:
        data_left = json.load(f_left)
    with open(json_path_right, 'r') as f_right:
        data_right = json.load(f_right)

    # 找出兩者共有的影像 key（確保一一對應）
    common_keys = sorted(set(data_left.keys()) & set(data_right.keys()), key=lambda x: int(x.split('.')[0]))

    pts1_all = []  # 左相機點
    pts2_all = []  # 右相機點

    for key in common_keys:
        pts_left = np.array(data_left[key])
        pts_right = np.array(data_right[key])

        if pts_left.shape == pts_right.shape and pts_left.shape[0] >= 6:
            pts1_all.append(pts_left)
            pts2_all.append(pts_right)
        else:
            print(f"警告：跳過 {key}，點數不足或不一致。")

    # 合併成 Nx2 陣列
    pts1_all = np.vstack(pts1_all)
    pts2_all = np.vstack(pts2_all)

    return pts1_all, pts2_all

pts1, pts2 = load_correspondences(calib_store.POINTS_JSON["cs"], calib_store.POINTS_JSON["cf"])

print("總共匹配點對數：", len(pts1))

def normalize_points(pts):
    mean = np.mean(pts, axis=0)
    std = np.std(pts)
    scale = np.sqrt(2) / std
    T = np.array([[scale, 0, -scale * mean[0]],
                  [0, scale, -scale * mean[1]],
                  [0,     0,               1]])
    pts_h = np.hstack((pts, np.ones((pts.shape[0], 1))))
    pts_norm = (T @ pts_h.T).T
    return pts_norm, T

def compute_fundamental_matrix(x1, x2):
    x1_norm, T1 = normalize_points(x1)
    x2_norm, T2 = normalize_points(x2)
    
    A = np.array([
        x2_norm[:, 0] * x1_norm[:, 0],
        x2_norm[:, 0] * x1_norm[:, 1],
        x2_norm[:, 0],
        x2_norm[:, 1] * x1_norm[:, 0],
        x2_norm[:, 1] * x1_norm[:, 1],
        x2_norm[:, 1],
        x1_norm[:, 0],
        x1_norm[:, 1],
        np.ones(x1.shape[0])
    ]).T

    _, _, V = np.linalg.svd(A)
    F = V[-1].reshape(3, 3)

    # Enforce rank 2
    U, S, Vt = np.linalg.svd(F)
    S[2] = 0
    F_rank2 = U @ np.diag(S) @ Vt

    # Denormalize
    F_denorm = T2.T @ F_rank2 @ T1
    return F_denorm / F_denorm[2, 2]

def ransac_fundamental(x1, x2, threshold=1.0, iterations=2000):
    max_inliers = []
    best_F = None

    n_points = x1.shape[0]
    for _ in range(iterations):
        idx = np.random.choice(n_points, 8, replace=False)
        F_candidate = compute_fundamental_matrix(x1[idx], x2[idx])
        
        # 計算對應誤差
        x1_h = np.hstack((x1, np.ones((n_points, 1))))
        x2_h = np.hstack((x2, np.ones((n_points, 1))))
        Fx1 = (F_candidate @ x1_h.T).T
        x2Fx1 = np.sum(x2_h * Fx1, axis=1)
        errors = np.abs(x2Fx1) / np.linalg.norm(Fx1[:, :2], axis=1)
        inliers = np.where(errors < threshold)[0]

        if len(inliers) > len(max_inliers):
            max_inliers = inliers
            best_F = F_candidate

    return best_F, max_inliers

F, inliers = ransac_fundamental(pts1, pts2, threshold=INLIER_THRESHOLD_PX)
print("估計出的基本矩陣 F：\n", F)
print("內點數量：", len(inliers), "/", len(pts1))

roles = calib_store.load_roles()
front_sn, side_sn = roles if roles else (None, None)
if roles is None:
    print("警告：尚未設定相機角色（calib.py 第 4 區），F 將無法被 3D 程式採用")
calib_store.save_fundamental(F, front_sn, side_sn, len(inliers), len(pts1), INLIER_THRESHOLD_PX)
print(f"F 已儲存至 {calib_store.FUNDAMENTAL_JSON}（front={front_sn} side={side_sn}）")

def compute_essential_matrix(F, K_L, K_R):
    E = K_R.T @ F @ K_L
    # 強制 rank 2
    U, S, Vt = np.linalg.svd(E)
    S = [1, 1, 0]
    E = U @ np.diag(S) @ Vt
    return E

def decompose_essential_matrix(E):
    U, _, Vt = np.linalg.svd(E)
    if np.linalg.det(U @ Vt) < 0:
        Vt = -Vt

    W = np.array([[0, -1, 0],
                  [1,  0, 0],
                  [0,  0, 1]])

    R1 = U @ W @ Vt
    R2 = U @ W.T @ Vt
    t = U[:, 2]
    return [(R1,  t), (R1, -t), (R2,  t), (R2, -t)]

def triangulate_and_disambiguate(pts1, pts2, K_L, K_R, F):
    E = compute_essential_matrix(F, K_L, K_R)
    pose_candidates = decompose_essential_matrix(E)

    # 左相機投影矩陣 P1
    P1 = K_L @ np.hstack((np.eye(3), np.zeros((3, 1))))

    max_positive_depth = -1
    best_points_3d = None
    best_pose = None

    for R, t in pose_candidates:
        P2 = K_R @ np.hstack((R, t.reshape(3, 1)))  # 右相機投影矩陣

        pts4d_h = cv2.triangulatePoints(P1, P2, pts1.T, pts2.T)
        pts3d = (pts4d_h[:3] / pts4d_h[3]).T

        # 計算左右相機前方的點數（Z > 0）
        z1 = pts3d[:, 2]
        z2 = (R @ pts3d.T + t.reshape(3, 1))[2, :]
        num_positive = np.sum((z1 > 0) & (z2 > 0))

        if num_positive > max_positive_depth:
            max_positive_depth = num_positive
            best_points_3d = pts3d
            best_pose = (R, t)

    return best_points_3d, best_pose

# 以本次估得的 F 與兩台相機的內參計算 R、t（原本此處用寫死的舊 F/K 覆蓋，已移除）
intr_f = calib_store.get_intrinsic(front_sn) if front_sn else None
intr_s = calib_store.get_intrinsic(side_sn) if side_sn else None
if intr_f is None or intr_s is None:
    print("略過 R、t 計算：正面或側面相機尚未做內參校正（calib.py）")
else:
    K_F, K_S = intr_f[0], intr_s[0]
    pts1_inliers = pts1[inliers]
    pts2_inliers = pts2[inliers]
    best_points_3d, best_pose = triangulate_and_disambiguate(pts1_inliers, pts2_inliers, K_S, K_F, F)
    R, t = best_pose

    print("=====================================")
    print("相對旋轉矩陣 R (Rotation): \n", R)
    print("\n相對平移向量 t (Translation): \n", t)
    print("=====================================")