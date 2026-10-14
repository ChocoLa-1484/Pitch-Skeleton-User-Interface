import sys as _sys
from pathlib import Path as _Path
if __package__ in (None, ""):
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[3]))

import os
import numpy as np
import json
from Src.calibration import calib_store

# 加載校正參數
calib_data = np.load(os.path.join(calib_store.OLD_RIG_DIR, "stereo_calib.npz"))

print("=" * 60)
print("立體相機校正參數提取")
print("=" * 60)

# 提取內參矩陣
mtx_f = calib_data['mtx_f']  # 正面相機內參
mtx_s = calib_data['mtx_s']  # 側面相機內參
dist_f = calib_data['dist_f']  # 正面相機畸變係數
dist_s = calib_data['dist_s']  # 側面相機畸變係數
R = calib_data['R']  # 旋轉矩陣
T = calib_data['T']  # 平移向量

print("\n【正面相機內參矩陣 (Camera Matrix - Front)】")
print("mtx_f:")
print(mtx_f)

print("\n【側面相機內參矩陣 (Camera Matrix - Side)】")
print("mtx_s:")
print(mtx_s)

print("\n【正面相機畸變係數 (Distortion Coefficients - Front)】")
print("dist_f:")
print(dist_f)

print("\n【側面相機畸變係數 (Distortion Coefficients - Side)】")
print("dist_s:")
print(dist_s)

print("\n【兩台相機間旋轉矩陣 (Rotation Matrix)】")
print("R:")
print(R)

print("\n【兩台相機間平移向量 (Translation Vector) - 单位: mm】")
print("T:")
print(T)
print(f"相機間距離: {np.linalg.norm(T):.2f} mm")

# 保存為 JSON 格式供其他程式讀取
output_data = {
    "camera_front": {
        "intrinsic_matrix": mtx_f.tolist(),
        "distortion_coefficients": dist_f.tolist(),
        "fx": float(mtx_f[0, 0]),
        "fy": float(mtx_f[1, 1]),
        "cx": float(mtx_f[0, 2]),
        "cy": float(mtx_f[1, 2])
    },
    "camera_side": {
        "intrinsic_matrix": mtx_s.tolist(),
        "distortion_coefficients": dist_s.tolist(),
        "fx": float(mtx_s[0, 0]),
        "fy": float(mtx_s[1, 1]),
        "cx": float(mtx_s[0, 2]),
        "cy": float(mtx_s[1, 2])
    },
    "stereo": {
        "rotation_matrix": R.tolist(),
        "translation_vector": T.tolist(),
        "camera_distance_mm": float(np.linalg.norm(T))
    }
}

out_path = os.path.join(calib_store.OLD_RIG_DIR, "stereo_calib.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(output_data, f, indent=2)

print("\n" + "=" * 60)
print(f"✅ 校正參數已儲存至 {out_path}")
print("=" * 60)
