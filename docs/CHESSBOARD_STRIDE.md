# 棋盤內參、PnP 與 8-point 真實距離量測

獨立離線流程，依賴現有環境的 OpenCV、NumPy、SciPy，不需要相機 SDK、Qt 或骨架推論模型，不改動現行十字架校正與 `fundamental.json`。

## 本次設定

- 照片：`Db/Record/Calibrate_Picture_Chessboard/{cf,cs}`，各 142 張。
- 棋盤：**7×7 內角點，每格 100×100 mm**。首尾內角點有 6 個間隔，即 600 mm。
- 影片：`Db/Record/20261008_Pitcher01/20261008_1130_P05`。檔名實際是 `*_Pitcher02_P05.mp4`，依 CF/CS 前綴尋找。
- 兩影片均為 1920×1084、181 幀、67 fps。
- 使用者確認的量：**最初站立時的後腳腳跟，到前腳落地時同一隻原支撐後腳的腳跟**。本片預設右腳，HALPE-26 索引 25，左腳索引 24。
- 60–70 cm 僅在量測後比較，從未用於內參、外參或尺度擬合。

## 三個程式

在專案根目錄執行：

```powershell
conda activate Pitcher

# 1. 內參、畸變與角點檢測
python Src/UI_Control/calibrate_chessboard.py

# 2. 棋盤 PnP 與固定內參雙目精修
python Src/UI_Control/measure_stride_pnp.py

# 3. 8-point、三角測量與棋盤毫米尺度
python Src/UI_Control/measure_stride_8point.py
```

亦可用 `conda run --no-capture-output -n Pitcher python ...`。本機直接呼叫環境內 `python.exe` 而未啟用 Conda，可能在 OpenCV PnP 遇到 DLL 延遲載入錯誤；上述兩種方式已驗證。

這些命令完成校正並輸出品質報告、影片幀圖。沒有量測點時，`run_status.json` 為 `needs_annotations`，不輸出假造的跨步距離。

## 跨幀選點與量距離

```powershell
# 115 是目視檢查起始值，可在視窗逐幀調整精確落地時刻
python Src/UI_Control/measure_stride_pnp.py --annotate --reference-frame 0 --frame 115 --heel right --ground-plane

# 完全相同的選點用另一條路徑比較
python Src/UI_Control/measure_stride_8point.py --annotations Db/Calibration/chessboard/pnp/annotations.json
```

視窗依序要求：

1. 在最初站立幀，點 CF 後腳腳跟，再點 CS 同一腳跟。
2. 自動切到事件幀，再點 CF、CS 同一隻後腳腳跟。
3. 有 `--ground-plane` 時，再點三個地面點，每點先 CF 再 CS。它們必須在選定的同一水平地面、分散且不共線，不可跨不同高度的平台或斜坡。
4. Enter 儲存並計算。

`j/l` 前後一幀，`a/d` 前後十幀，`u` 撤銷一點，`r` 重選，Esc 取消。最初兩點未完成時，切換的是起始幀；之後切換的是事件幀。請選腳跟，不能用腳踝替代。

顯示影像會縮小，儲存時還原成原始像素。相機必須維持校正時的位置、焦距、焦點和裁切；跨幀量測時也不能移動相機。CS 對應幀為 `CF frame + --cs-frame-offset`，預設 0；相同幀號不保證曝光完全同步，請目視檢查。

有地面點才輸出 `ground_distance_cm`；未提供時只算三維直線距離 `distance_3d_cm`，地面距離和 60–70 cm 的地面驗證結果為 null。棋盤為手持直立且每張會移動，不能把棋盤 XY 或相機 XY 當地面。

## 這組影片發現的問題

既有事件 JSON 的 `Keyframes.FC.frame` 是 **95**，不指定 `--frame` 時程式讀取它。但目視第 95 幀前腳仍離地；約第 110–120 幀才接近落地，需確認精確接觸時刻。程式沒有自動偵測或驗證落地事件。

CF 骨架 JSON 只有 **0、138** 幀，CS 才有 0–180。不得用 JSON 陣列下標配對、把 CF 138 當成 95，或把插值當真實腳跟。

CS 既有骨架的核對線索如下，單位為原始像素，**不是厘米量測**：

| 幀 | 右腳跟 (x, y) | 左腳跟 (x, y) |
| --- | --- | --- |
| 0 | (845.02, 797.35) | (879.07, 796.39) |
| 95 | (845.50, 801.77) | (1039.32, 764.49) |
| 115 | (842.89, 799.57) | (1068.66, 813.14) |
| 120 | (842.99, 799.90) | (1062.24, 823.24) |

原支撐右腳此時幾乎原地不動。**同一後腳前後位移，與初始後腳到落地前腳的跨步，是不同的量**；已確認的定義可能與 60–70 cm 不一致。程式保留使用者指定的同一後腳定義，沒有更換終點去符合預期數值。

有完整骨架時可執行：

```powershell
python Src/UI_Control/measure_stride_pnp.py --pose-annotations --reference-frame 0 --frame 115 --heel right
```

它讀取確切 `frame_number`、HALPE-26 腳跟索引、信心值 ≥0.5，同幀只能一人。這組資料 CF 缺 115 幀，會明確拒絕，請用 `--annotate`。模型腳跟與鞋底實際接觸點仍可能有誤差。

## 算法

內參用 `findChessboardCornersSB` 偵測 49 點，各相機獨立校正。預設等距抽樣最多 60 個有效視角，依每張重投影 RMS 排除明顯異常；k3 固定 0，其餘徑向／切向係數和 fx、fy、cx、cy 估計。輸出逐張 RMS、內參標準差、覆蓋率、失敗名單和角點預覽。

PnP 路徑對每對棋盤求兩個 `solvePnP` 位姿，利用固定相機幾何的一致性，解決方形棋盤的八種角點編號方向；再以 `stereoCalibrate(CALIB_FIX_INTRINSIC)` 聯合精修共同雙目位姿。棋盤 100 mm 物件座標直接提供尺度。

8-point 路徑共用已對齊角點。PnP **只用於辨識點序**，此路徑 R、t、尺度並未使用 PnP 的數值：

1. 去畸變至像素座標，RANSAC 篩選對應點，明確用 `FM_8POINT` 擬合 F。
2. 需多個不同位置／深度的棋盤；單一平面不足以唯一決定 F，程式會檢查 homography 支配率並拒絕退化資料。
3. `E = K_cs.T @ F @ K_cf`，修正奇異值，枚舉四個 R/t 解並檢查正深度。
4. 只用影像對應點精修 R 與單位 t 的極線誤差，避免修正 E 奇異值後極線偏移。
5. 用去畸變正規化座標三角測量，以相鄰棋盤邊長中位數求 `scale = 100 mm / reconstructed_edge`，把 t 與三維點恢復成毫米。

每五對棋盤保留一對，不參與雙目 R/t 和尺度擬合，檢查邊長、重投影、正深度和視差。這些照片可能參與過內參校正，相鄰照片也相似，所以是自洽性檢查，**不是獨立實尺驗證**。

三維輸出為 CF 相機座標，`X_cs = R_cs_from_cf @ X_cf + t_cs_from_cf_mm`。內部距離單位 mm，cm 輸出才除以 10。量測點重投影 >3 px、深度為負或射線夾角 <1° 時拒絕量測。

API 定義見 [OpenCV 相機校正與三維重建文件](https://docs.opencv.org/4.12.0/d9/d0c/group__calib3d.html)。

## 本批實測（2026-10-10）

| 內參 | CF | CS |
| --- | ---: | ---: |
| 偵測角點 | 117 / 142 | 121 / 142 |
| 採用內參照片 | 59 | 59 |
| RMS | 0.3828 px | 0.5073 px |
| fx | 9428.18 px | 1451.46 px |
| fy | 8597.88 px | 1464.07 px |

| 雙目流程（預設自由 fx/fy） | PnP | 8-point |
| --- | ---: | ---: |
| 訓練 / 保留 / 排除對數 | 28 / 7 / 5 | 28 / 7 / 5 |
| 相機間基線 | 23.077 m | 22.917 m |
| 保留棋盤邊長 RMSE 中位數 | 1.433 mm | 3.169 mm |

CF 的 fx/fy 約 1.097，且部分雙目重投影超過 3 px，已記錄警告。小的單目 RMS 不足以保證毫米尺度正確。

另做**假設像素正方形、固定 fx/fy=1** 的敏感度試算，保存在 `Db/Calibration/chessboard_aspect1/`，未覆蓋預設：PnP / 8-point 基線約 21.284 / 21.184 m，保留棋盤邊長 RMSE 中位數約 0.849 / 1.533 mm。雖然雙目檢查較好，仍需確認相機規格與是否做過非等比例縮放；不能只挑較符合預期跨步長度的模型。

若確認 fx/fy 比例，可明確指定：

```powershell
python Src/UI_Control/calibrate_chessboard.py --fix-aspect-ratio 1 --output Db/Calibration/chessboard_square_pixels
python Src/UI_Control/measure_stride_pnp.py --calibration Db/Calibration/chessboard_square_pixels --annotate --reference-frame 0 --frame 115 --ground-plane
```

同批檢測重擬合內參可加 `--reuse-detections`，僅限影像內容與 `--pattern` 未變。`--max-views` 控制內參取樣，`--max-pairs` 控制雙目取樣，`--preview-every 0` 關閉角點預覽。

## 輸出

預設寫入 `Db/Calibration/chessboard/`：

- `intrinsics.json`：K、畸變、照片與品質資料。
- `detections.json`、`corners/`：角點與預覽。
- `pnp/stereo.json`、`8point/stereo.json`：各自毫米外參、尺度來源和驗證。
- 各方法的 `video_audit.json`、`pose_motion_audit.json`、起始／事件幀圖：影片核對。
- `annotations.template.json`：手填原始像素的格式，缺點會拒絕量測。
- 選點後的 `annotations.json`：保存兩時刻、兩相機像素，可供另一算法重用。
- 量測後的 `measurement.json`、`measurement.csv`、`*_initial_measurement.jpg`、`*_contact_measurement.jpg`。
- `summary.txt`、`run_status.json`：本次狀態。開始先設 incomplete，失敗時不把舊結果標成功。以本次 `measurement` 欄位為準；null 表示沒有新量測，目錄內先前的量測檔不是本次結果。

`--output` 可指定輸出目錄，重跑更新程式生成的報告，原始照片／影片保持不變。所有輸出仍標 `independently_validated_for_measurement=false`，須以同一端點、同一事件的實尺距離驗證。`Db` 已被 `.gitignore` 排除，實測輸出需另行保存。

測試：

```powershell
conda run --no-capture-output -n Pitcher python -m unittest discover -s Src/UI_Control/tests -p test_chessboard_metric.py -v
```

涵蓋已知幾何、去畸變、PnP 基線、8-point 獨立尺度、平面退化、棋盤點序、地面投影、跨幀欄位，以及缺點、錯點和影像尺寸不符時的拒絕行為。
