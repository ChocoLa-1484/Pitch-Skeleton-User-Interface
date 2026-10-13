import numpy as np
import cv2
import json

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))   # Src/UI_Control：讓 cv_utils 等模組可匯入
from cv_utils import calib_store
# scale of 0505: 21556.837 mm/px
# === Step 1: 載入資料 ===
with open(calib_store.POINTS_JSON["cs"], "r") as f:
    fdata = json.load(f)
with open(calib_store.POINTS_JSON["cf"], "r") as f:
    data = json.load(f)

pts_left = np.array(fdata["01.jpg"])    # 左相機
pts_right = np.array(data["01.jpg"])   # 右相機

# 轉為 (2, N) 的格式供 triangulate 使用
pts1 = pts_left.T
pts2 = pts_right.T

# === Step 2: 相機內參 ===
# 下方寫死的是舊設備的數值，只在讀不到 Db/Calibration 的校正檔時使用
K_R = np.array([
    [7.92787455e+03, 0.0, 8.69870446e+02],
    [0.0, 8.03959073e+03, 7.16810004e+02],
    [0.0, 0.0, 1.0]
])

K_L = np.array([
    [1.40583815e+03, 0.0, 9.64222231e+02],
    [0.0, 1.40662233e+03, 5.75695704e+02],
    [0.0, 0.0, 1.0]
])

# K_L = np.array([
#     [11229.920949545698, 0.0, 937.199020465423],
#     [0.0, 11240.535783693984, 586.8641683267341],
#     [0.0, 0.0, 1.0]
# ])
# K_R = np.array([
#     [1063.0766604691114, 0.0, 962.1115766023166],
#     [0.0, 1061.6146093201994, 570.8085693424371],
#     [0.0, 0.0, 1.0]
# ])

# === Step 3: 已估得的基本矩陣 F（請替換成你自己的）===
# 這裡為範例，請改為你前面算出來的 F
# F = np.array([
#     [-1.96209267e-06, -1.92549701e-06,  1.66946234e-03],
#     [4.94448469e-06, -4.90271503e-07, -1.99725264e-03 ],
#     [-1.14286140e-03, -1.18824175e-03,  1.00000000e+00]
# ])
# F = np.array([[-6.14357015e-08,  2.22505318e-06, -8.55658761e-04],
#             [7.93662298e-07,  8.14124289e-08, -4.02123194e-03],
#             [-2.90068577e-04,  8.68353278e-04,  1.00000000e+00]])  #0321
F = np.array([
        [-1.00177788e-07,  2.16557273e-06, -7.90363312e-04],
        [ 1.58917237e-07,  2.44307598e-07, -3.48771760e-03],
        [-9.75476979e-05,  7.35402506e-04,  1.00000000e+00]
    ]) 

# 優先使用 Db/Calibration 的校正結果（L = 側面 cs，R = 正面 cf）
calib, reason = calib_store.load_stereo_calibration()
if calib is not None:
    K_L, K_R, F = calib["K_S"], calib["K_F"], calib["F"]
    print(f"使用校正檔：正面 SN{calib['front']}、側面 SN{calib['side']}")
else:
    print(f"警告：{reason}")
    print("      改用程式內寫死的舊設備數值，結果不代表目前的相機")


# === Step 4: 計算本質矩陣與分解為 R, t ===
def compute_essential_matrix(F, K_L, K_R):
    E = K_R.T @ F @ K_L
    U, S, Vt = np.linalg.svd(E)
    S = [1, 1, 0]
    return U @ np.diag(S) @ Vt

def decompose_essential_matrix(E):
    U, _, Vt = np.linalg.svd(E)
    if np.linalg.det(U @ Vt) < 0:
        Vt = -Vt
    W = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    R1 = U @ W @ Vt
    R2 = U @ W.T @ Vt
    t = U[:, 2]
    return [(R1, t), (R1, -t), (R2, t), (R2, -t)]

E = compute_essential_matrix(F, K_L, K_R)
pose_candidates = decompose_essential_matrix(E)

# === Step 5: Triangulate & 選最合理的 R,t ===
P1 = K_L @ np.hstack((np.eye(3), np.zeros((3, 1))))
best_3d = None
best_scale = None
best_pose = None
max_in_front = -1

for R, t in pose_candidates:
    P2 = K_R @ np.hstack((R, t.reshape(3, 1)))
    pts4d_h = cv2.triangulatePoints(P1, P2, pts1, pts2)
    pts3d = (pts4d_h[:3] / pts4d_h[3]).T  # shape (8, 3)

    z1 = pts3d[:, 2]
    z2 = (R @ pts3d.T + t.reshape(3,1))[2, :]
    n_in_front = np.sum((z1 > 0) & (z2 > 0))

    if n_in_front > max_in_front:
        best_3d = pts3d
        best_pose = (R, t)
        max_in_front = n_in_front

# === Step 6: 以第 1、4 點之間的實際距離估算 scale ===
# 假設第 1,4 點實際距離為 15 mm
known_distance_mm = 340
d_2d = np.linalg.norm(pts1[:, 0] - pts1[:, 3])
d = np.linalg.norm(best_3d[0] - best_3d[3])
scale = known_distance_mm / d
scale_2d = known_distance_mm / d_2d
points3d_scaled = best_3d * scale

# === Step 7: 輸出或視覺化 ===
# print(f"估得的尺度係數：{scale:.3f}")
# print("轉換為實際世界單位的 3D 點：")
# for i, pt in enumerate(points3d_scaled):
#     print(f"點 {i+1}: {pt} (單位：mm)")
pt1 = points3d_scaled[0]
pt2 = points3d_scaled[1]
distance = np.linalg.norm(pt1 - pt2)
print(f"2d 距離：{d_2d}")
print(f"3d 距離：{d}")
print(f"估得的尺度係數：{scale:.3f}")
print(f"2D 尺度係數：{scale_2d:.3f}")
print(f"\n第 1 點與第 2 點之間的實際距離：約 {distance:.3f} mm")

# 選配視覺化
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D

fig = plt.figure()
ax = fig.add_subplot(111, projection='3d')
ax.scatter(points3d_scaled[:,0], points3d_scaled[:,1], points3d_scaled[:,2], s=50, c='blue')
for i, pt in enumerate(points3d_scaled):
    ax.text(pt[0], pt[1], pt[2], f'{i+1}', fontsize=10)
ax.set_title("實際尺度 3D 點（單位：mm）")
ax.set_xlabel("X (mm)")
ax.set_ylabel("Y (mm)")
ax.set_zlabel("Z (mm)")
plt.tight_layout()
plt.show()
