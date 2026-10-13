import cv2
import matplotlib.pyplot as plt
import numpy as np
import os
import json

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))   # Src/UI_Control：讓 cv_utils 等模組可匯入
from cv_utils import calib_store

# === 設定圖像資料夾路徑清單 ===
_PICTURES = os.path.join(os.path.dirname(calib_store.CALIB_DIR), 'Record', 'Calibrate_Picture')
img_folders = [
    ('cf', os.path.join(_PICTURES, 'cf')),
    ('cs', os.path.join(_PICTURES, 'cs'))
]

def process_folder(folder_name, img_folder):
    img_list = sorted([f for f in os.listdir(img_folder) if f.endswith(('.jpg', '.png'))])
    img_paths = [os.path.join(img_folder, f) for f in img_list]
    all_points = {}  # {'圖檔名': [[x1,y1], ...]}

    current_idx = 0
    clicked_points = []

    fig, ax = plt.subplots()
    plt.title(f'[{folder_name}] 請點選 6 個點（右鍵刪除），滾輪切換圖像')
    img_display = None

    def show_image():
        ax.clear()
        img = cv2.imread(img_paths[current_idx])
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        ax.imshow(img_rgb)
        ax.set_title(f"{os.path.basename(img_paths[current_idx])}  |  點選 {len(clicked_points)} / 6")

        for coll in list(ax.collections):
            coll.remove()
        for txt in list(ax.texts):
            txt.remove()

        if clicked_points:
            clicked = np.array(clicked_points)
            ax.scatter(clicked[:, 0], clicked[:, 1], c='red')
            for i, (x, y) in enumerate(clicked_points):
                ax.text(x + 6, y, str(i), color='green', fontsize=12)

        fig.canvas.draw_idle()

    def on_click(event):
        if event.button == 1 and event.inaxes:
            if len(clicked_points) < 6:
                clicked_points.append([event.xdata, event.ydata])
        elif event.button == 3 and event.inaxes:
            if clicked_points:
                clicked_points.pop()
        show_image()

    def on_scroll(event):
        nonlocal current_idx, clicked_points
        if len(clicked_points) == 6:
            filename = os.path.basename(img_paths[current_idx])
            all_points[filename] = clicked_points.copy()
            print(f"✅ 儲存 {filename} 點位：{clicked_points}")
        else:
            print(f"⚠️ 尚未完成 6 點標註：{len(clicked_points)} 點")

        if event.button == 'up':
            current_idx = (current_idx - 1) % len(img_paths)
        elif event.button == 'down':
            current_idx = (current_idx + 1) % len(img_paths)

        filename = os.path.basename(img_paths[current_idx])
        clicked_points.clear()
        if filename in all_points:
            clicked_points.extend(all_points[filename])
        show_image()

    fig.canvas.mpl_connect('button_press_event', on_click)
    fig.canvas.mpl_connect('scroll_event', on_scroll)

    show_image()
    plt.show()

    # 儲存點位（Db/Calibration/extrinsic/）
    out_json = calib_store.POINTS_JSON[folder_name]
    os.makedirs(os.path.dirname(out_json), exist_ok=True)
    with open(out_json, 'w') as f:
        json.dump(all_points, f, indent=2)
    print(f"📝 所有點位已儲存至 {out_json}")

if __name__ == '__main__':
    for folder_name, img_folder in img_folders:
        print(f"\n=== 開始標註資料夾：{img_folder} ===")
        process_folder(folder_name, img_folder)
