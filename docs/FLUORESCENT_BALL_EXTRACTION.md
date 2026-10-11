# 獨立螢光粉球提取

入口：`Src/UI_Control/extract_fluorescent_balls.py`。
核心：`Src/UI_Control/cv_utils/fluorescent_balls.py`。

只需要 OpenCV 與 NumPy，不讀內參，不計算 PnP，不載入 Qt、相機 SDK 或骨架模型。現有 `calibrate_cross.py` 共用同一個偵測核心，避免兩套 HSV 方法產生不同結果。

## 執行

在專案根目錄執行，下列預設會處理 `Db/Record/Calibrate_Picture_Auto` 內所有照片：

```powershell
conda activate Pitcher
python Src/UI_Control/extract_fluorescent_balls.py
```

單張照片、單一相機或其他資料夾皆可，不要求 cf/cs 配對。資料夾會遞迴搜尋；保留來源子目錄結構，檔名包含原始副檔名，避免同名 JPG/PNG 衝突。

```powershell
python Src/UI_Control/extract_fluorescent_balls.py --input Db/Record/Calibrate_Picture_Auto/cf/01.jpg
python Src/UI_Control/extract_fluorescent_balls.py --input Db/Record/Calibrate_Picture_Auto/cs
python Src/UI_Control/extract_fluorescent_balls.py --input "D:/新照片" --output "D:/提取結果/run01"
```

預設輸出至新的 `Db/Record/Ball_Extraction/<時間>/`。自訂輸出目錄必須尚不存在，且不能放在輸入資料夾內，以免下次把遮罩和標註圖當成來源照片。

## 調整 HSV 與 ROI

```powershell
python Src/UI_Control/extract_fluorescent_balls.py --input Db/Record/Calibrate_Picture_Auto/cf --roi 800 650 350 350 --h-min 135 --h-max 168 --s-min 105 --v-min 50
```

- OpenCV 8-bit HSV：H=0–179，S/V=0–255。預設 H=135–168、S≥105、V≥50 是從現有照片調出的起點，新光源與曝光下應查看遮罩再調整。
- `--roi X Y W H` 指全圖搜尋範圍，同次執行會套用到每張照片。不同相機要用不同 ROI 時，請分開執行。範圍超過某張影像時，該張會記錄錯誤。
- `--min-radius-px`、`--min-area-px`、`--max-area-px` 可調整球大小的篩選條件。
- `--expected-count 8` 只檢查候選數量，不會刪成八顆或補造缺球。其他裝置可改數量，`--expected-count 0` 關閉數量檢查。

處理順序為 BGR → HSV → 顏色門檻 → 小幅開運算 → 距離轉換分割黏連色塊 → 各區域重心。完全遮蔽或嚴重重疊的球仍可能無法分開。

## 輸出

每張原始照片（例如 `cf/01.jpg`）產生：

| 檔案 | 內容 |
| --- | --- |
| `cf/01.jpg.mask.png` | HSV 與開運算後的二值遮罩 |
| `cf/01.jpg.foreground.png` | 用上述遮罩提取的原始彩色前景，背景為黑色；可能仍含被球形／面積篩選排除的色塊 |
| `cf/01.jpg.annotated.jpg` | 全圖候選球心、候選編號與數量檢查 |
| `cf/01.jpg.detail.jpg` | 候選球所在區域的 2 倍放大圖；無候選時不產生 |
| `cf/01.jpg.centers.json` | 候選中心 `[x,y]`、區域面積、距離峰半徑、是否從黏連區分割，以及錯誤資訊 |
| `detections.json` | 全批設定、來源、逐張結果及統計 |

球心使用**原始全圖像素**，原點在左上角，x 向右、y 向下。使用 ROI 後也不會變成裁切圖座標，尚未去畸變。

**c1、c2… 是每張圖內按 y 再 x 排序的候選 ID，不是實體 ball1～ball8，也不是跨影格追蹤 ID。** 找到八個候選只代表數量符合，不能證明沒有誤檢或球號正確。實體球號與幾何檢查仍由 `calibrate_cross.py` 進行。

數量不足或過多會保留結果並標示 `count mismatch`；讀圖等錯誤會記錄於報告，繼續處理其餘照片。退出碼 0 表示全部照片處理完成，數量不一定符合；有讀圖／處理錯誤則為 2。

## 從其他程式呼叫

在 `Src/UI_Control` 可被 Python import 的環境中：

```python
from cv_utils.fluorescent_balls import DetectionConfig, detect_balls, read_image

image = read_image("照片.png")
config = DetectionConfig(hue_min=135, hue_max=168, saturation_min=105, value_min=50)
candidates, mask = detect_balls(image, config, roi=None)
centers = [candidate["center_px"] for candidate in candidates]
```

這個核心函式也能直接處理相機提供的 NumPy BGR 影像陣列。CLI 本身只讀照片，不啟動相機。
