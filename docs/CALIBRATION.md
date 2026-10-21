# 相機校正與量測

給開發者看的校正說明：主流程、分步工具，以及各種校正方法與量測工具。使用者只需要 README 的「相機校正」一節。

> 2026-10-11 由以下文件整併而成；2026-10-20 將舊十字架章節更新為木框與手動座標流程：`DEVELOPMENT.md`「相機校正流程」一節、README 的校正細節、`CHESSBOARD_STRIDE.md`、`CROSS_CALIBRATION.md`、`FLUORESCENT_BALL_EXTRACTION.md`、`MAIN_METRIC.md`（第 3～6 節原作者：ChocoLa-1484）。

## 目錄

1. 整合校正工具 `calibrate.py`（校正桿 + F 矩陣）
2. 分步工具與 `Db\Calibration` 檔案結構
3. 棋盤格內參、PnP 與 8-point 真實距離量測
4. 木框 PnP 外參與手動座標標註
5. 獨立螢光粉球提取
6. `main_metric.py` 公制量測入口
7. 雙棒八球 8-point 拍照與外參 UI

## 1. 整合校正工具 `calibrate.py`（校正桿 + F 矩陣）

建議使用新的整合入口（在專案根目錄執行）：

```powershell
conda activate Pitcher
python .\Src\UI_Control\calibrate.py
```

按「開始校正」後，在同一視窗依序完成：正面內參 → 側面內參 → 雙機拍校正桿 → 左右標註 →「計算並儲存校正」。棋盤格每台至少擷取 15 組，校正桿至少 4 組，左右每張依相同球心順序標註 6 點。仍需人工移動校正板／桿及標註球心，不需另開 `main.py`、`01`、`02`。

已校正內參且鏡頭、焦距及影像尺寸／裁切未變時，可勾選「沿用這兩台已有內參」。外參照片也可匯入包含 `cf`、`cs` 的資料夾，兩邊檔名必須相同。完整通過後才更新目前校正檔；每次的原始照片、點位、內參樣本、誤差報告與被取代檔案的備份保存在 `Db\Calibration\sessions\<時間>_wand\`，供之後與棋盤格外參方法比較。這版先實作校正桿方法。

**木框 PnP 外參**：執行 `python Src/UI_Control/calibrate.py --wood-frame`，引導收集約 15 組照片並儲存最佳一組。**手動原點／XYZ 軸／尺度**：執行 `python Src/UI_Control/calibrate.py --manual-coordinates`，選擇正面及側面圖片後標註並匯出 JSON。兩種視窗的操作與限制見第 4 節。

**只提取螢光粉球**：執行 `python Src/calibration/extract_fluorescent_balls.py`，不需要內參或 PnP。可用 `--input` 指定單張或資料夾，輸出遮罩、彩色前景、候選球心 JSON 與預覽圖；詳見 本文件第 5 節〈獨立螢光粉球提取〉。

新入口以程式位置解析路徑，可從任意工作目錄啟動。舊有分步工具仍可使用：

```
python Src/UI_Control/calib.py                        # 1. 每台相機內參，並指定角色
python Src/UI_Control/main.py                         # 2.「2D 相機」分頁拍校正桿照片
python Src/legacy/calibration/01_ball_detect_manual.py # 3. 舊分步工具：標註
python Src/legacy/calibration/02_ransac_8points.py      # 4. 舊分步工具：F 矩陣
python Src/legacy/calibration/03_3d.py                 # 5.（選用）舊尺度驗證
```

詳細步驟與注意事項見本文件第 2 節。

## 2. 分步工具與 `Db\Calibration` 檔案結構

所有校正結果都存在 `Db\Calibration\`（不進 git），程式會自動讀取，不必再手動把數字貼進程式碼。

**整合入口（2026-10-09）**：專案根目錄執行 `python Src/UI_Control/calibrate.py`。以校正精靈整合下面的 1–5 步，不載入骨架模型；內參重用 `calib.CalibWindow` 的取樣、角點與品質評估，計算放到背景執行，結果先暫存於本次 session。兩台內參及外參都成功後，才一起寫入現用校正檔。

- 全新內參每台至少 15 組，RMS 須 ≤ 1 px；覆蓋區域、傾斜角度等建議列在紀錄。保留原始棋盤格照片與 NPZ 角點，方便重算。
- 外參照片至少 4 組、每張 6 點，正面與側面必須同名；拍照與標註都在同一視窗。左右 1–6 必須對應同一實體球心。滾輪縮放、Ctrl＋拖曳平移、右鍵撤銷。最後一張不必再切換即可保存。
- `Src/calibration/calibration_workflow.py` 提供驗證、RANSAC、內點重估、誤差與發布處理。F 方向維持 `x_front^T F x_side = 0`，固定隨機種子；至少 12 內點且比例 ≥ 50% 才接受。門檻預設 3 px、可調，**誤差採左右兩側點到對極線距離的較大值**，與舊 `02` 的單側 1 px 門檻不同，比較報告時須使用一致門檻與資料。
- 目前 3D 消費端使用原始像素；本工具也維持 `raw_pixels` 的 F，未單獨對外參點去畸變，以免座標系不一致。若日後改成去畸變的棋盤格外參，須同步修改重建端。擬合誤差不能代替獨立尺寸／3D 精度驗證；這版未自動完成舊步驟 6 的 340 mm 尺度驗證。
- `sessions/<時間>_wand/` 包含 `session.json`（角色、內參快照、棋盤格規格、狀態）、`cf/`、`cs/`、`selected_points_cf.json`、`selected_points_cs.json`、`intrinsic/`、`report.json`。點位逐次保存為草稿，中途取消不改現用值。草稿保留供檢查，尚未提供重開精靈後的自動續作。
- 發布前備份被取代的檔案到 session 的 `previous_calibration/`，逐檔以暫存檔取代；遇一般寫入例外會回復本次已取代檔案。這不是跨檔案的斷電交易，請勿同時開多個校正工具寫入。既有錄影快照不受影響。
- 雙機取像沿用現有 `DualFlirSystem` 的 GPIO 設定，取像／釋放在同一工作執行緒；仍需現場驗證同步線、曝光與影像配對。

回歸測試（使用合成幾何、模擬相機，不會修改現用校正資料）：

```powershell
conda activate Pitcher
python -m unittest discover -s Src/UI_Control/tests -v
```

以下為原本仍保留的分步工具流程：

**`calib.py` 直接使用棋盤格照片（2026-10-13）**：不必連接相機。在第 2 區填好內角點寬／高與每格邊長（mm），於「從照片校正」填入照片所屬相機的序號，再按「讀取照片資料夾」。每次選單台相機的資料夾，例如 `cf` 或 `cs`，不會遞迴混入其他相機照片。支援 JPG、JPEG、PNG、BMP、TIFF 與中文路徑；無角點、尺寸不符、壞檔及與前一張有效照片幾乎相同的影像會略過並記錄原因。載入成功會取代原樣本，取消或整批失敗則保留原樣本。

確認有效照片至少 10 張（建議 15–20 張、不同位置與傾斜角）後，按「執行校正」。結果沿用既有品質檢查，存至 `Db/Calibration/intrinsic/intrinsics_all.json` 與 `calib_SN<序號>.npz`，另記錄實際採用的照片路徑。同一序號會更新其校正結果。兩台相機請分別載入與計算；改棋盤格規格後需重新讀取照片。整合精靈 `calibrate.py` 仍沿用原本的即時取樣流程。

| 步驟 | 執行 | 要接什麼 | 產出 |
|---|---|---|---|
| 1. 內參（每台各做一次） | `python Src/UI_Control/calib.py`，選相機 → 連接 → 擷取 15~20 組 → 執行校正 | 只要 USB，同步線可接可不接 | `intrinsic\intrinsics_all.json`、`calib_SN<序號>.npz` |
| 2. 指定正面/側面 | `calib.py` 第 4 區 → 儲存角色 | — | `camera_roles.json` |
| 3. 拍外參照片 | `python Src/UI_Control/main.py` →「2D 相機」分頁 →「拍照」 | 兩台 + GPIO 同步線 | `Db\Record\Calibrate_Picture\cf\`、`cs\` |
| 4. 標註對應點 | `python Src/legacy/calibration/01_ball_detect_manual.py`（先正面再側面，每張 6 點，滾輪換張） | — | `extrinsic\selected_points_cf.json`、`_cs.json` |
| 5. 算 F 矩陣 | `python Src/legacy/calibration/02_ransac_8points.py` | — | `extrinsic\fundamental.json` |
| 6. 驗證 | `python Src/legacy/calibration/03_3d.py`（用第 1、4 點實距 340 mm 換算尺度，畫出 3D 點） | — | — |

**注意事項**
- 上方命令從專案根目錄執行；舊分步工具已移到 `Src/legacy/calibration/`。目錄對照見 [PROJECT_LAYOUT.md](PROJECT_LAYOUT.md)。
- 相機序號不再寫死：`cv_thread.py` 依序採用「呼叫端指定 → `camera_roles.json` → 舊版 `camera_serials.json` → 自動偵測 → 預設值」
- `01` 每次執行會從空白開始並覆蓋 JSON。若只是打開看看就關掉，會把結果清空（2026-10-07 演練時發生過）
- `02` 每次跑出的 F 會略有不同（RANSAC 隨機抽樣，未固定種子）
- **錄影時會把當下的校正值存進該筆錄影資料夾**（`calibration.json`），3D 分頁開啟錄影時用這份快照重建。所以重新校正不會影響舊錄影；沒有快照的舊錄影則沿用程式內建的舊設備參數。校正尚未完成（兩台內參 + F + 角色）就錄影的話，終端機會印出「未存校正快照」與原因

**2026-10-07 實驗室演練結果**（正面 SN24380119 為 8mm 鏡頭，僅為熟悉流程）：外參 4 組照片 × 6 點 = 24 點，內點 12/24。原因是內點門檻 1 px 對手點球心太嚴（實際中位誤差 2.86 px），加上沒有做去畸變。

## 3. 棋盤格內參、PnP 與 8-point 真實距離量測

獨立離線流程，依賴現有環境的 OpenCV、NumPy、SciPy，不需要相機 SDK、Qt 或骨架推論模型，不改動木框校正與 `fundamental.json`。

### 本次設定

- 照片：`Db/Record/Calibrate_Picture_Chessboard/{cf,cs}`，各 142 張。
- 棋盤：**7×7 內角點，每格 100×100 mm**。首尾內角點有 6 個間隔，即 600 mm。
- 影片：`Db/Record/20261008_Pitcher01/20261008_1130_P05`。檔名實際是 `*_Pitcher02_P05.mp4`，依 CF/CS 前綴尋找。
- 兩影片均為 1920×1084、181 幀、67 fps。
- 使用者確認的量：**最初站立時的後腳腳跟，到前腳落地時同一隻原支撐後腳的腳跟**。本片預設右腳，HALPE-26 索引 25，左腳索引 24。
- 60–70 cm 僅在量測後比較，從未用於內參、外參或尺度擬合。

### 三個程式

在專案根目錄執行：

```powershell
conda activate Pitcher

# 1. 只算內參、畸變與角點檢測
python Src/calibration/calibrate_chessboard.py

# 2. 只算外參：讀取上一步內參，分別輸出 PnP 與 8-point
python Src/calibration/calibrate_chessboard_extrinsic.py --method both

# 3. 量測：讀取已存的外參，不再重算校正
python Src/tools/measure_stride_pnp.py
python Src/tools/measure_stride_8point.py
```

亦可用 `conda run --no-capture-output -n Pitcher python ...`。本機直接呼叫環境內 `python.exe` 而未啟用 Conda，可能在 OpenCV PnP 遇到 DLL 延遲載入錯誤；上述兩種方式已驗證。

內參入口只寫 `intrinsics.json`、`detections.json` 與角點預覽。獨立外參入口不需要投球影片，固定內參後計算 R／t 與尺度，寫 `pnp/stereo.json`、`8point/stereo.json` 及各自的 `calibration_summary.txt`；可用 `--method pnp` 或 `--method 8point` 只算一種。JSON 格式維持 `metric_stereo_v1`，原 3D 回放及公制量測入口可繼續讀取。

外參預設沿用內參資料夾的角點；也可另指定**同棋盤規格、相同相機／鏡頭設定與解析度**的同步照片，左右放在 `cf`、`cs` 並以同名配對。相機位置須與待分析影片一致，多個棋盤位置需保持兩台相機不動：

```powershell
python Src/calibration/calibrate_chessboard_extrinsic.py --calibration Db/Calibration/chessboard --images "D:/外參棋盤照片" --output Db/Calibration/chessboard_new_rig --method both
python Src/tools/measure_stride_pnp.py --stereo Db/Calibration/chessboard_new_rig/pnp/stereo.json --annotate
```

外參的 `--output` 是輸出根目錄，其下依方法建立 `pnp`／`8point`；`--images` 產生的角點另存 `extrinsic_detections.json`，不覆寫內參角點。量測的 `--stereo` 可直接指定外參檔，省略時讀取 `--calibration` 下的 `<方法>/stereo.json`；缺檔會提示先執行外參命令。量測才會輸出影片幀圖，沒有量測點時 `run_status.json` 為 `needs_annotations`。

### 跨幀選點與量距離

```powershell
# 115 是目視檢查起始值，可在視窗逐幀調整精確落地時刻
python Src/tools/measure_stride_pnp.py --annotate --reference-frame 0 --frame 115 --heel right --ground-plane

# 完全相同的選點用另一條路徑比較
python Src/tools/measure_stride_8point.py --annotations Db/Calibration/chessboard/pnp/annotations.json
```

視窗依序要求：

1. 在最初站立幀，點 CF 後腳腳跟，再點 CS 同一腳跟。
2. 自動切到事件幀，再點 CF、CS 同一隻後腳腳跟。
3. 有 `--ground-plane` 時，再點三個地面點，每點先 CF 再 CS。它們必須在選定的同一水平地面、分散且不共線，不可跨不同高度的平台或斜坡。
4. Enter 儲存並計算。

`j/l` 前後一幀，`a/d` 前後十幀，`u` 撤銷一點，`r` 重選，Esc 取消。最初兩點未完成時，切換的是起始幀；之後切換的是事件幀。請選腳跟，不能用腳踝替代。

顯示影像會縮小，儲存時還原成原始像素。相機必須維持校正時的位置、焦距、焦點和裁切；跨幀量測時也不能移動相機。CS 對應幀為 `CF frame + --cs-frame-offset`，預設 0；相同幀號不保證曝光完全同步，請目視檢查。

有地面點才輸出 `ground_distance_cm`；未提供時只算三維直線距離 `distance_3d_cm`，地面距離和 60–70 cm 的地面驗證結果為 null。棋盤為手持直立且每張會移動，不能把棋盤 XY 或相機 XY 當地面。

### 這組影片發現的問題

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
python Src/tools/measure_stride_pnp.py --pose-annotations --reference-frame 0 --frame 115 --heel right
```

它讀取確切 `frame_number`、HALPE-26 腳跟索引、信心值 ≥0.5，同幀只能一人。這組資料 CF 缺 115 幀，會明確拒絕，請用 `--annotate`。模型腳跟與鞋底實際接觸點仍可能有誤差。

### 算法

內參用 `findChessboardCornersSB` 偵測 49 點，各相機獨立校正。預設等距抽樣最多 60 個有效視角，依每張重投影 RMS 排除明顯異常；k3 固定 0，其餘徑向／切向係數和 fx、fy、cx、cy 估計。輸出逐張 RMS、內參標準差、覆蓋率、失敗名單和角點預覽。

2026-10-13 修正 SB 旗標傳法：明確傳入 `flags=CALIB_CB_NORMALIZE_IMAGE`，失敗時加 `CALIB_CB_EXHAUSTIVE` 重試；兩次皆失敗才列入失敗名單。成功視角的 `detector`／`detection_flags` 記錄實際方法與旗標。第一次套用修正時請不要加 `--reuse-detections`，否則會沿用舊角點與漏偵測結果；如需保留原校正，請以 `--output` 指定新的輸出資料夾。

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

### 本批實測（2026-10-10）

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
python Src/calibration/calibrate_chessboard.py --fix-aspect-ratio 1 --output Db/Calibration/chessboard_square_pixels
python Src/calibration/calibrate_chessboard_extrinsic.py --calibration Db/Calibration/chessboard_square_pixels --method both
python Src/tools/measure_stride_pnp.py --calibration Db/Calibration/chessboard_square_pixels --annotate --reference-frame 0 --frame 115 --ground-plane
```

同批檢測重擬合內參可加 `--reuse-detections`，僅限影像內容與 `--pattern` 未變。內參命令的 `--max-views` 控制內參取樣，外參命令的 `--max-pairs` 控制雙目取樣；量測命令不再接受 `--max-pairs`。`--preview-every 0` 關閉角點預覽。

### 輸出

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

## 4. 木框 PnP 外參與手動座標標註

木框外參使用既有相機內參及已確認的固定木框三維點位，不需在 UI 輸入尺寸。舊十字架計算程式與專用測試已移除，既有 `Db/` 照片及校正資料保留。

### 木框 PnP 視窗

```powershell
conda activate Pitcher
python Src/UI_Control/calibrate.py --wood-frame
# 亦可直接啟動
python Src/UI_Control/calibrate_wood_frame.py
```

1. 選擇正面 CF、側面 CS 的既有內參，核對相機角色、序號及影像尺寸。鏡頭、焦距／對焦、影像裁切或解析度改變後，需先重做適用的內參。
2. 核對固定木框點位表：正面依 F1→F4，側面依 S1→S4 標註。紙板厚度已確認為 `0.3 cm = 3 mm`，已反映在下表座標，不能再次加上厚度。
3. 依拍攝指引收集約 15 組正面／側面照片（目標可調整為 5–30 組），也可匯入含 `cf/`、`cs/` 且兩邊檔名相同的既有照片。匯入在背景執行、顯示進度並可取消，單次可只補一組；取消或失敗會保留既有照片與標記。每組兩張必須是同一木框姿態，兩台相機保持固定；選擇清楚、可見且有足夠間隔的標記點。
4. 在兩張圖上分別依序標註四點；模糊、遮擋或誤拍的照片可取消「採用這組」略過，原始照片仍保留。點位與略過設定自動保存到 `Db/Calibration/sessions/<時間>_wood_frame/session.json`；可在設定頁載入工作階段接續標註。至少需完成並採用 5 組才能計算，建議收集約 15 組不同姿態。尺寸與標記錯誤不能靠增加照片數修正。
5. 計算每組 PnP 並檢查其他姿態的雙機一致性，依評分選擇最佳一組。結果頁顯示最佳組、雙機基線、各組 RMS 與未採用原因，也可返回檢視最佳組照片。計算結果先存至該工作階段的 `best_stereo.json`；按「儲存最佳一組外參」可匯出至預設 `Db/Calibration/wood_frame/stereo.json` 或自選位置，供既有公制校正讀取器使用。

兩組點使用同一木框世界座標，單位全部為 mm：

| 視角／點位 | X | Y | Z |
|---|---:|---:|---:|
| 正面 F1 | 80 | 320 | -3 |
| 正面 F2 | 320 | 320 | -3 |
| 正面 F3 | 320 | 80 | -3 |
| 正面 F4 | 80 | 80 | -3 |
| 側面 S1 | 403 | 320 | 30 |
| 側面 S2 | 403 | 320 | 270 |
| 側面 S3 | 403 | 80 | 270 |
| 側面 S4 | 403 | 80 | 30 |

正面與側面標註的是不同平面上的各自四點，各平面仍可能有姿態歧義，需檢查品質結果與實際相機位置。拍攝時若移動木框，各組外參以該組木框座標為準，不能直接平均不同原點／方向的世界外參。需要固定場地座標時，應把木框放在已量測且可重現的基準位置，或另行提供木框到場地的剛體變換。

外參沿用 OpenCV 方向：`X_camera = R_world_to_camera @ X_world + t_world_to_camera_mm`。`t` 是世界原點在相機座標中的位置；相機的世界位置是 `C = -R.T @ t`。像素重投影誤差用於挑選照片，但不等於整個投球範圍的實際量測精度；使用前仍需以未參與校正的已知距離核對。

輸出使用 `metric_stereo_v1`，頂層 `R_cs_from_cf`／`t_cs_from_cf_mm` 為 CF→CS 相對外參，供既有三角化流程讀取。`world.cameras.cf/cs` 則保存兩台相機相對於最佳組木框的外參；檔案也保留內參快照、最佳組 ID、全部來源點位、各組診斷與評分。其他照片用於選擇相機間一致的候選解，沒有平均不同擺位的木框世界外參。

在主程式「3D」頁的「校正設定」選擇 PnP，再選取木框匯出的 `stereo.json` 並套用。`main_metric.py` 可用 `--calibration Db/Calibration/wood_frame/stereo.json` 指定。匯出木框外參不會自動取代舊校正桿的 `fundamental.json` 或舊錄影快照。

```python
from Src.calibration.wood_frame import load_wood_frame_rig

rig = load_wood_frame_rig("Db/Calibration/wood_frame/stereo.json")
front_pose = rig["world"]["cameras"]["cf"]
rotation = front_pose["R_camera_from_world"]
translation_mm = front_pose["t_camera_from_world_mm"]
```

### 手動原點、XYZ 方向與尺度視窗

```powershell
python Src/UI_Control/calibrate.py --manual-coordinates
# 亦可直接啟動
python Src/UI_Control/manual_coordinates.py
```

1. 分別選擇正面與側面圖片；可使用與木框校正不同的一組照片。
2. 在每個視角標記原點，以及 X、Y、Z 正方向的影像端點。兩張圖須表達相同的實體原點與軸方向，像素位置則各自記錄。
3. 在各視角的「真實尺度」面板另選照片，標出已知距離的 A、B 兩端，輸入真實長度與 mm／cm／m 單位。座標與尺度照片可以不同，但同一視角的相機、解析度及拍攝設定應相同，參考線段需盡量與量測物位於同一深度。
4. 按「匯出結果 JSON」選擇位置（預設 `Db/Calibration/wood_frame/manual_coordinates.json`）；之後可按「讀取標記 JSON」接續修改。輸出 `manual_coordinates_v1` 保留影像路徑及尺寸、原始像素點位、軸方向、尺度端點、真實長度與局部 `mm_per_pixel`。

程式讀取時使用驗證器，它會檢查版本並重新計算尺度，將相對影像路徑解析到 JSON 所在資料夾：

```python
from Src.calibration.manual_coordinates import load_document

annotations = load_document("Db/Calibration/wood_frame/manual_coordinates.json")
front = annotations["views"]["CF"]
origin_px = front["origin_px"]
x_direction = front["axis_unit_directions_px"]["X"]
scale_mm_per_pixel = front["scale"]["mm_per_pixel"]
```

影像座標原點為左上角，x 向右、y 向下，顯示縮放不改變保存的原圖像素。尺度線段提供該段位置／方向的像素與真實長度關係；透視影像中，不同深度與方向不保證共用同一個比例。這份標註 JSON 用於保存人工定義，**不是由兩張照片的 2D 軸與單條尺度就得到完整透視 3D 外參**。完整公制 3D 重建仍需內參、足夠的已知三維對應點或其他可解的幾何約束。

## 5. 獨立螢光粉球提取

入口：`Src/calibration/extract_fluorescent_balls.py`。
核心：`Src/calibration/fluorescent_balls.py`。

只需要 OpenCV 與 NumPy，不讀內參，不計算 PnP，不載入 Qt、相機 SDK 或骨架模型。此獨立工具保留，不依賴已移除的十字架幾何計算。

### 執行

在專案根目錄執行，下列預設會處理 `Db/Record/Calibrate_Picture_Auto` 內所有照片：

```powershell
conda activate Pitcher
python Src/calibration/extract_fluorescent_balls.py
```

單張照片、單一相機或其他資料夾皆可，不要求 cf/cs 配對。資料夾會遞迴搜尋；保留來源子目錄結構，檔名包含原始副檔名，避免同名 JPG/PNG 衝突。

```powershell
python Src/calibration/extract_fluorescent_balls.py --input Db/Record/Calibrate_Picture_Auto/cf/01.jpg
python Src/calibration/extract_fluorescent_balls.py --input Db/Record/Calibrate_Picture_Auto/cs
python Src/calibration/extract_fluorescent_balls.py --input "D:/新照片" --output "D:/提取結果/run01"
```

預設輸出至新的 `Db/Record/Ball_Extraction/<時間>/`。自訂輸出目錄必須尚不存在，且不能放在輸入資料夾內，以免下次把遮罩和標註圖當成來源照片。

### 調整 HSV 與 ROI

```powershell
python Src/calibration/extract_fluorescent_balls.py --input Db/Record/Calibrate_Picture_Auto/cf --roi 800 650 350 350 --h-min 135 --h-max 168 --s-min 105 --v-min 50
```

- OpenCV 8-bit HSV：H=0–179，S/V=0–255。預設 H=135–168、S≥105、V≥50 是從現有照片調出的起點，新光源與曝光下應查看遮罩再調整。
- `--roi X Y W H` 指全圖搜尋範圍，同次執行會套用到每張照片。不同相機要用不同 ROI 時，請分開執行。範圍超過某張影像時，該張會記錄錯誤。
- `--min-radius-px`、`--min-area-px`、`--max-area-px` 可調整球大小的篩選條件。
- `--expected-count 8` 只檢查候選數量，不會刪成八顆或補造缺球。其他裝置可改數量，`--expected-count 0` 關閉數量檢查。

處理順序為 BGR → HSV → 顏色門檻 → 小幅開運算 → 距離轉換分割黏連色塊 → 各區域重心。完全遮蔽或嚴重重疊的球仍可能無法分開。

### 輸出

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

**c1、c2… 是每張圖內按 y 再 x 排序的候選 ID，不是實體 ball1～ball8，也不是跨影格追蹤 ID。** 找到八個候選只代表數量符合，不能證明沒有誤檢或球號正確。此工具不進行實體球號配對與三維幾何檢查。

數量不足或過多會保留結果並標示 `count mismatch`；讀圖等錯誤會記錄於報告，繼續處理其餘照片。退出碼 0 表示全部照片處理完成，數量不一定符合；有讀圖／處理錯誤則為 2。

### 從其他程式呼叫

從專案根目錄（`Src` 可被 Python import 的環境）使用：

```python
from Src.calibration.fluorescent_balls import DetectionConfig, detect_balls, read_image

image = read_image("照片.png")
config = DetectionConfig(hue_min=135, hue_max=168, saturation_min=105, value_min=50)
candidates, mask = detect_balls(image, config, roi=None)
centers = [candidate["center_px"] for candidate in candidates]
```

這個核心函式也能直接處理相機提供的 NumPy BGR 影像陣列。CLI 本身只讀照片，不啟動相機。

## 6. `main_metric.py` 公制量測入口

```powershell
conda activate Pitcher
python Src/UI_Control/main_metric.py
```

保留 `main.py` 的相機、2D、3D、回放比較頁，新增「校正量測（後腳）」。3D 頁現在也能選擇 PnP／8Points 棋盤外參重建，詳見 [3D 回放與校正說明](MAIN_REPLAY_FIX.md#3d-回放與棋盤外參)。2D 與舊回放比較仍使用原算法；新增量測頁提供四張原圖的人工點選、品質檢查與獨立報告。

預設載入 `Db/Calibration/chessboard/pnp/stereo.json` 和 P05 影片。量測定義固定為**初始後腳腳跟 → 前腳落地時同一後腳腳跟**，預設右腳。

1. 確認校正、影片資料夾、初始幀、落地幀及 `CS = CF + offset`。預設 FC 來自既有事件檔，須目視確認；本片可先從 115 幀附近檢查。
2. 按「載入／重設選點」。依序選 `reference`、`heel`，各在 CF／CS 圖上點同一實體腳跟。滾輪放大、拖曳平移、雙擊還原。變更幀或校正設定後須重新選點。
3. 勾選地面距離時，再選三組分散且不共線、位於同一水平面的地面對應點。沒有地面點時取消勾選，僅量三維直線位移。
4. 可用「讀取腳跟 JSON」帶入既有骨架，或在完整入口用「重新辨識 4 張影像」，再人工核對。缺幀、低信心、多個人物或幾何品質不合格均拒絕量測。
5. 按「計算後腳位移」，可選另一份 8-point `stereo.json` 以完全相同選點比較。兩模型相近不代表已驗證實際精度。
6. 匯出至影片資料夾下的 `metric_measurements/<時間>/`，包含 `measurement.json/csv`、`annotations.json`、校正快照，以及四張選點影像。結果記錄校正雜湊、幀號、腳跟定義、品質數值與目視確認狀態。

原程式內的影片可透過頂端「量測目前影片」帶入；CS／CF 的相機順序與幀偏移會轉換成量測頁的定義。

只使用量測介面、不載入 GPU 模型及相機 SDK：

```powershell
python Src/UI_Control/main_metric.py --measurement-only --frame 115
```

命令列檢查（`--check` 缺資料／品質不合格回傳 2，距離為 null）：

```powershell
python Src/UI_Control/main_metric.py --check --frame 115
python Src/UI_Control/main_metric.py --check --frame 115 --annotations path/to/annotations.json --output path/to/new_report
```

這組原始 CF 骨架只有 0、138 幀，直接讀 JSON 不能量 115 幀；需人工選點或重新辨識。棋盤內參有條件性警告，目前尚無獨立實尺精度驗證；不應宣稱已達厘米級精度，60–70 cm 也不參與任何尺度擬合。

本機四張原圖重新辨識流程已執行。預設 PnP 校正搭配自動辨識的第 0／115 幀腳跟，最大重投影約 25.25 px，超過 3 px 門檻，因此程式拒絕輸出距離；仍需核對左右腳、對應點、同步與相機設定，不能將這次自動結果視為準確量測。

校正算法、影像條件與限制見 本文件第 3 節〈棋盤格內參、PnP 與 8-point 真實距離量測〉。

## 7. 雙棒八球 8-point 拍照與外參 UI

```powershell
conda activate Pitcher
python Src/UI_Control/calibrate.py --two-rods
# 獨立入口
python Src/UI_Control/calibrate_two_rods.py
```

兩根棒子各有四顆直徑 50 mm 的螢光粉球。使用者已確認以下為**球心到球心**間距，不加球半徑或直徑：

| 棒子 | 實體上端至下端 | 相鄰球心距離 (mm) | 第一顆到各顆累積距離 (mm) |
|---|---|---|---|
| A | A1、A2、A3、A4 | 200、120、180 | 0、200、320、500 |
| B | B1、B2、B3、B4 | 100、140、210 | 0、100、240、450 |

1. 選取兩台既有內參與實體相機序號。相機、鏡頭、解析度與裁切固定，接好 GPIO 同步線後連接雙機；兩棒都停穩再拍。空白鍵拍一組，預設 15 組，可增加至 200 組。
2. 或匯入含 `cf/`、`cs/` 的資料夾，兩側檔名一一對應。**15 組代表每台至少 15 張、合計至少 30 張**。可分批補照片、單次只補一組，但同一工作階段的相機擺位必須相同。背景匯入提供進度與取消按鈕；完成尺寸驗證及保存後才提取球心。取消、讀圖或儲存失敗會清理本次匯入照片，保留既有照片與標記。
3. 提取沿用 `extract_fluorescent_balls` 使用的 `fluorescent_balls.detect_balls`，不需先執行 CLI 或產生提取 JSON。設定頁可調整 HSV、最小半徑與面積；標記頁可按「調整提取參數」重試本組。
4. 以去畸變球心的共線性與四點交比提出 A/B 分組。四點交比不能識別整根棒子的倒序，**必須核對兩張照片的實體 A/B 棒與上下編號**；A1/B1 始終指同一個實體上端，不能隨照片上的位置重新編號。可反轉指定視角某根棒子的順序，或選球號後手動點選球心。每組按「確認本組兩視角球號」；修改或重新提取後須再次確認。
5. 補拍提示依已收集、未略過的球心計算：各影像的棒子中心九宮格涵蓋、直立／斜向／接近水平投影角度，以及同一根棒在不同照片中的影像長度變化。A/B 棒在單張照片中的大小差異不算遠近變化。請在共同可見的量測區改變位置、方向、距離與前後傾斜，避免球互相遮擋。影像大小變化也可能受傾斜影響，不能當作真實深度或三維角度的量測。涵蓋提示不強制要求拍到兩相機無法共同看見的區域。
6. 至少完成並確認 15 組不同姿態才可計算。計算前會檢查重複姿態：若兩視角對應球心的 RMS 位移都不超過 1 px，即使檔名不同仍拒收，避免重複照片同時進入擬合與保留驗證。依提示取消重複組的「採用這組」，再改變棒子位置或角度補拍。原圖與草稿保存於 `Db/Calibration/sessions/<時間>_two_rods/session.json`，可從設定頁讀回。
7. 約 80% 組別用於外參與尺度擬合，約 20%（每第 5 組）保留檢查。RANSAC 選內點，normalized 8-point 估計 F，再求 E、判定正深度及極線最佳化；以每根棒子內的已知距離恢復毫米尺度。**不假設兩根棒子間距固定，也不使用 PnP。** 結果列出各組重投影及棒長誤差；退化幾何、錯配、尺度不一致或保留組驗證不佳會拒絕輸出。
8. 工作階段自動保存 `stereo.json`；另按「匯出 8-point 外參」可存至預設 `Db/Calibration/two_rods/stereo.json`。主程式「3D → 校正設定」選 **8Points**，選取此檔後套用。此工具不改寫舊 F 矩陣或錄影快照。

結果為 `metric_stereo_v1`、`method=8point`、單位 mm，世界座標基準為正面相機。輸出包含內參、CF→CS 的 R/t、雙棒規格、原始點位、訓練／保留組別與診斷，可用既有 `Src.calibration.chessboard_extrinsic.load_rig(path)` 讀取。球的影像區域中心近似球心投影，近距離及畫面邊緣仍可能有偏差，正式使用需以額外已知距離驗證精度。
