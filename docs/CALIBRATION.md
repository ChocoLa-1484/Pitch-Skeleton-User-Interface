# 相機校正與量測

給開發者看的校正說明：主流程、分步工具，以及各種校正方法與量測工具。使用者只需要 README 的「相機校正」一節。

> 2026-10-11 由以下文件整併而成，內容未改寫：`DEVELOPMENT.md`「相機校正流程」一節、README 的校正細節、`CHESSBOARD_STRIDE.md`、`CROSS_CALIBRATION.md`、`FLUORESCENT_BALL_EXTRACTION.md`、`MAIN_METRIC.md`（第 3～6 節原作者：ChocoLa-1484）。

## 目錄

1. 整合校正工具 `calibrate.py`（校正桿 + F 矩陣）
2. 分步工具與 `Db\Calibration` 檔案結構
3. 棋盤格內參、PnP 與 8-point 真實距離量測
4. 8 顆螢光粉球十字架：HSV 偵測與公制 PnP 外參
5. 獨立螢光粉球提取
6. `main_metric.py` 公制量測入口

## 1. 整合校正工具 `calibrate.py`（校正桿 + F 矩陣）

建議使用新的整合入口（在專案根目錄執行）：

```powershell
conda activate Pitcher
python .\Src\UI_Control\calibrate.py
```

按「開始校正」後，在同一視窗依序完成：正面內參 → 側面內參 → 雙機拍校正桿 → 左右標註 →「計算並儲存校正」。棋盤格每台至少擷取 15 組，校正桿至少 4 組，左右每張依相同球心順序標註 6 點。仍需人工移動校正板／桿及標註球心，不需另開 `main.py`、`01`、`02`。

已校正內參且鏡頭、焦距及影像尺寸／裁切未變時，可勾選「沿用這兩台已有內參」。外參照片也可匯入包含 `cf`、`cs` 的資料夾，兩邊檔名必須相同。完整通過後才更新目前校正檔；每次的原始照片、點位、內參樣本、誤差報告與被取代檔案的備份保存在 `Db\Calibration\sessions\<時間>_wand\`，供之後與棋盤格外參方法比較。這版先實作校正桿方法。

**8 顆粉紅球十字架的自動 HSV／PnP 外參**：執行 `python Src/UI_Control/calibrate.py --cross`。預設讀取 `intrinsic_20261008` 與 `Calibrate_Picture_Auto`，使用正面 SN24380115、側面 SN21055356，輸出毫米制外參、球號標註與品質報告到新的 `Db/Calibration/cross_pnp/<時間>/`。此功能不覆蓋現行 F 校正；世界座標、拍攝建議、量測限制與本批外參和現場位置的差異見 本文件第 4 節〈8 顆螢光粉球十字架〉。

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

獨立離線流程，依賴現有環境的 OpenCV、NumPy、SciPy，不需要相機 SDK、Qt 或骨架推論模型，不改動現行十字架校正與 `fundamental.json`。

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

## 4. 8 顆螢光粉球十字架：HSV 偵測與公制 PnP 外參

7×7 內角點、每格 100 mm 的棋盤內參，以及 PnP／8-point 兩條影片距離量測流程，另見 本文件第 3 節〈棋盤格內參、PnP 與 8-point 真實距離量測〉。

在專案根目錄，以已安裝 OpenCV、NumPy 的 `Pitcher` 環境執行：

```powershell
conda activate Pitcher
python Src/UI_Control/calibrate.py --cross
```

也可以執行 `python Src/calibration/calibrate_cross.py`。`--help` 列出所有參數；離線功能不需要相機 SDK、Qt 或骨架模型。原本 `calibrate.py` 不加參數仍開啟原校正精靈。

只需提取螢光粉球時，使用獨立入口 `python Src/calibration/extract_fluorescent_balls.py`。單張／資料夾輸入、HSV 參數與球心輸出見 本文件第 5 節〈獨立螢光粉球提取〉；此入口不讀內參，也不執行 PnP。

本次使用者確認的預設值：

| 項目 | 設定 |
| --- | --- |
| 內參 | `Db/Calibration/intrinsic_20261008/intrinsics_all.json` |
| 照片 | `Db/Record/Calibrate_Picture_Chessboard`，cf、cs 各 10 張同名照片 |
| cf 正面 | SN24380115 |
| cs 側面 | SN21055356 |
| 照片大小 | 1920 × 1084，必須和內參一致 |
| 裝置與相機 | 十組之間固定不動 |
| 桿的前後差 | 面向 ball6 在左、ball8 在右的裝置正面，橫桿在直桿後方 15 mm |

### 世界座標與量測定義

以 **ball3 球心為原點**，X 軸由 ball6 指向 ball8，Z 軸由 ball5 指向 ball1（裝置正確鉛直時就是向上），Y 軸依右手定則 `Y = Z × X`，指向裝置背面。所有三維數值及平移量均為 **毫米**。

| 球 | X (mm) | Y (mm) | Z (mm) |
| --- | ---: | ---: | ---: |
| ball1 | 0 | 0 | 150 |
| ball2 | 0 | 0 | 100 |
| ball3 | 0 | 0 | 0 |
| ball4 | 0 | 0 | -150 |
| ball5 | 0 | 0 | -350 |
| ball6 | -350 | 15 | 0 |
| ball7 | -150 | 15 | 0 |
| ball8 | 100 | 15 | 0 |

這裡的 15 mm 是**前後深度差，沒有將橫桿降低 15 mm**。ball3 是直桿上的唯一中心球；橫桿在 `(0, 15, 0)` 處的投影交點沒有額外一顆球，因此共 8 顆。

模型將所述 20、15、10 cm 解讀為沿橫桿 X 方向的球心位置差；例如 ball7 到 ball3 的三維直線距離是 `sqrt(150² + 15²)`，不是恰好 150 mm。請實測**球心到球心**位置、兩組球心平面的深度差，而非桿面間距或兩球表面間隙。球的黏貼厚度、大小及安裝偏移都會影響外參。若間隔實際是三維斜距，應修改 `object_points()` 的實測座標。

ball3 不是地面點。若量到 ball3 球心離指定地面平面 H mm，則地面為 `Z = -H`，執行時提供：

```powershell
# 500 僅是範例；必須改成現場量到的球心高度
python Src/UI_Control/calibrate.py --cross --ball3-height-mm 500
```

若希望以投手板前緣中央的地面點作為正式原點，請量出該點在上述座標中的三維位置，再平移世界座標；若要 Y 軸代表「往本壘」，也要現場對準或測量旋轉。不能因裝置放在投手板上，就假設 ball3 位於板中心正上方、Y 一定指向本壘，或 ball5 位於地面。

跨步量測需先定義事件與點位，例如「前腳落地時，前腳接觸點到投手板指定基準點的水平距離」。水平距離為 `norm((B-A)[:2])`；沿投球方向的跨步長則應將位移投影到實測的本壘方向。骨架踝關節不是鞋底接觸點，兩者定義不可混用。

### 十字架放置位置會影響什麼？

**會影響外參的座標定義與估計品質，不只影響驗證（verify）。** 外參是兩個座標系之間的旋轉、平移；本程式把裝置局部座標直接當成世界座標，因此 ball3 的位置與桿的方向都參與定義。

| 情況 | 影響 |
| --- | --- |
| 保持相機不動，移動／旋轉十字架，再以 ball3 為原點校正 | 世界座標跟著裝置變動，所以每台輸出的 R、t 會改變；不是相機真的移動 |
| 固定投手板／場地世界座標，移動十字架且量得每個位置的裝置到世界變換 | 可以把各次結果轉回相同場地座標；理想情況下固定相機的世界外參一致，實際會有估計誤差 |
| 相機不動，只比較兩台相機彼此的相對 R、t | 理想情況不隨十字架擺位改變；兩台必須看到同一個裝置姿態並正確對應球號 |
| 裝置更遠、更小、靠近影像邊緣，或其中一台幾乎沿著桿看 | 球心定位、畸變模型誤差、遮擋與幾何條件改變，外參估計精度也會變化 |
| 校正完成後，在不同位置放已知距離的標記做驗證 | 使用已固定的外參重建，才能檢查該量測區域的精度；不是把每個驗證位置重新設原點再校正 |

若場地點 `X_world = R_world_from_device @ X_device + t_world_from_device`，先用裝置點求出的 PnP 是 `T_camera_from_device`，則換到場地座標的外參為：

```text
T_camera_from_world = T_camera_from_device @ inverse(T_world_from_device)
```

此處皆為 4×4 齊次變換。這由 [OpenCV PnP 的物體／世界到相機座標慣例](https://docs.opencv.org/4.13.0/d5/d1f/calib3d_solvePnP.html)推得；目前工具沒有自動量測或套用各擺位的裝置到場地變換，不能直接混合多個位置共同解算。

**校正完成後可以移走十字架。** 只要相機、鏡頭設定及所採用的世界座標基準不變，移走標定物不會使已保存的外參失效；改變的是「在新擺位重新以 ball3 為原點校正」時的結果。

**下一次收集可這樣安排：** 先選一個兩台都能清楚看見全部球的位置，設剛性底座、量球心幾何、校正鉛直，記錄 ball3 相對投手板的位置與裝置方向；固定裝置拍一組作校正。保持相機不動，再到投手板、預期落腳區及不同高度拍獨立已知距離作驗證。不同擺位請分資料夾記錄；本程式的共同 PnP 解算只接受同一固定擺位的重複照片。

單純變更座標原點或剛性旋轉不會改變兩個三維點的歐氏距離；但要把 XY 距離當作水平跨步，Z 必須確實鉛直。要量「相對投手板」的位置或「朝本壘方向」的跨步，還必須建立裝置座標與場地基準的關係。

### 偵測、配對與解算

1. 將原始 BGR 影像轉成 HSV。預設 H=135–168、S≥105、V≥50，OpenCV 8-bit 的 H 範圍是 0–179。可用 `--h-min`、`--h-max`、`--s-min`、`--v-min` 調整，支援跨 H=0 的範圍。
2. 小幅形態學開運算去除雜點，再以距離轉換峰值分割黏連的粉紅球，用各區域重心取得次像素座標。這些是影像輪廓重心；遮擋、陰影、球體透視仍會造成球心偏差。
3. 用去畸變後的候選中心找出五點直桿、5:10:15:20 的間距特徵；橫桿三個獨立球另行配對。依三維幾何評分，不直接用畫面的左右順序，也不任取最大八個色塊。
4. 以**原始像素 + K + 畸變係數**執行 SQPnP；使用 IPPE 的平面近似提供另一組初始值，再以真正包含 15 mm 深度差的三維點執行 LM 最佳化。輸出正深度解、替代姿態及模糊性資訊，不把近似平面結果當成最終模型。
5. 必須有完整八點，且每點都參與誤差檢查。預設每張 RMS≤2 px、最大誤差≤4 px，球號的最佳與次佳排列若差距不足 0.2 px，則拒絕。缺球、過多背景色塊或幾何不符會保留失敗原因與預覽，不會補造球心。
6. 固定拍攝模式下，與逐球中位座標偏移超過 3 px 的整張影像不參與共同解算。每台至少 3 張，且至少 60% 的合格影像需穩定；剩餘所有八點觀測共同求一個 R、t，**不平均旋轉向量**。此檢查只偵測像素移動，無法證明場景真的完全靜止。

HSV 全畫面出現過多候選時，可限制搜尋區域；座標仍輸出為原始全圖像素：

```powershell
python Src/UI_Control/calibrate.py --cross --roi-cf 800 650 350 350 --roi-cs 850 650 250 330
python Src/UI_Control/calibrate.py --cross --detect-only
```

ROI 範例針對本批照片的位置；之後拍攝必須重新檢查。少於 8 顆、半徑小於 3 px、太接近側視而嚴重重疊，都可能需要重拍，不能只放寬誤差門檻。

### 輸出與後續重建

每次寫到新的 `Db/Calibration/cross_pnp/<時間>/`，亦可 `--output <尚不存在的目錄>`：

- `report.json`：每張候選球、ball1–ball8 像素位置、各點誤差、接受／拒絕原因、共同解算、內參快照、相機序號、世界定義及警告。
- `extrinsics.json`：去掉逐張偵測細節，保留兩台外參、投影矩陣、內參和量測限制。
- `summary.txt`：簡短摘要。
- `cf/`、`cs/`：各張 HSV 遮罩、球號標註及放大局部圖。黃色圈為偵測中心，綠色十字為 PnP 重投影。

外參採 OpenCV 方向：`X_camera = R_world_to_camera @ X_world + t_world_to_camera_mm`。**t 不是相機的世界位置**；相機世界位置是 `C = -R.T @ t`。相機座標 X 向右、Y 向下、Z 向前。

`P_undistorted_pixels = K @ [R | t]` 只能搭配 `cv2.undistortPoints(..., P=K)` 得到的去畸變像素。另有 `triangulate_world()`，會先去畸變成正規化座標，再用兩台 `[R | t]` 三角化，直接回傳上述世界座標的毫米值：

```python
# 從專案根目錄使用
import json
import numpy as np
from Src.calibration.cross_calibration import triangulate_world, ground_distance_mm

with open("Db/Calibration/cross_pnp/<本次時間>/extrinsics.json", encoding="utf-8") as f:
    result = json.load(f)
# 使用前必須處理 warnings，並完成獨立實距驗證。
front, side = result["cameras"]["cf"], result["cameras"]["cs"]
f, s = result["intrinsics"]["cf"], result["intrinsics"]["cs"]
points_mm = triangulate_world(
    front_pixels, side_pixels, front, side,  # 同步、同一物理點的 Nx2 原始像素
    np.array(f["intrinsic_matrix"]), np.array(f["distortion_coefficients"]),
    np.array(s["intrinsic_matrix"]), np.array(s["distortion_coefficients"]))
distance_mm = ground_distance_mm(points_mm[0], points_mm[1])
```

單台相機不能從一個像素恢復任意三維位置。只有已知點在某平面上時，才可用 `pixel_to_plane(pixel, pose, K, dist, plane_z_mm)` 求射線和平面的交點；踝關節、膝蓋與抬起的腳不應直接投到地面冒充真實位置。土坡不是單一平面時，也不能將所有接觸點套用同一 Z。

本功能**不覆蓋現行 `fundamental.json` 或內參**。舊流程由 F/recoverPose 取得的 t 只有方向、沒有公制尺度；新輸出的 F 也只適用於去畸變像素，不能直接放入舊的原始像素 F 流程。後續骨架程式要改用本檔保存的兩台公制 R、t，並將此校正快照綁定錄影，才能維持尺度與座標定義。本次已提供可重用的三角化函式，尚未修改骨架播放器或錄影快照流程。

### 本批照片的實測結果（2026-10-10）

預設參數下，20 張照片均找到 8 顆球並完成編號。共同解算排除球心偏移較大的影像：

| 相機 | 個別成功 | 共同解算張數 | 共同 RMS | 最大像素誤差 | 推估到 ball3 距離 |
| --- | ---: | ---: | ---: | ---: | ---: |
| cf / SN24380115 | 10/10 | 8 | 1.681 px | 3.466 px | 31.871 m |
| cs / SN21055356 | 10/10 | 7 | 0.837 px | 1.962 px | 3.941 m |

cf 排除 01、10；cs 排除 01、03、05。排除依據是影像座標偏移，不代表已判定裝置確實移動；分割、手部遮擋、球心偏差也可能造成差異。

**此組外參仍需查驗，不能直接宣稱可以準確測量跨步距離。** 正面指定內參 fx≈12462.87 px、fy≈12474.94 px，PnP 推得相機距離約 31.9 m，球心原點以上高度約 10.0 m。長焦、遠距離拍攝可以有很大的像素焦距，不能單憑 fx 大就判定內參錯誤；應以現場位置與獨立實距驗證。程式照指定資料計算，不自行替換成其他序號。

使用者已確認：**正面 SN24380115** 水平距離約 22 m、離地約 2.4 m；**70 cm 指裝置底部，不是 ball3 球心**。因此不能使用 `--ball3-height-mm 700`。若底部到 ball3 球心的鉛直高度為 h mm，則 ball3 離地高度為 `H = 700 + h` mm；兩個高度必須基於同一水平基準。不能直接假設底部就是 ball5 球心。

目前正面 PnP 推得水平距離 30.25 m、相機高於 ball3 10.02 m，直線距離 31.87 m；水平距離仍與現場約 22 m 不符。ball3 實際高度尚未量得，因此移除先前以 70 cm 當成球心高度計算的地面高度、直線距離與角度比較。另一個 PnP 解推得相機在 ball3 下方約 9.93 m，也須排查。使用者將重新收集資料，原校正輸出保留供比較。

量得 H 後再用 `--ball3-height-mm H` 記錄地面 `Z=-H`。此參數只設定地面位置，**不會改變以 ball3 為原點的 PnP R、t，也不會修正距離差異**。不能只選像素誤差較小的一解，或把 t 直接縮放到 22 m。

正面還存在兩個差異很大但重投影誤差相近的解，RMS 約 1.681 與 1.708 px，分張姿態差可達約 50°。這正說明小像素誤差不等於外參可信。15 mm 深度差、幾乎平面的十字架和錯配／不適用的內參，都需要排查。

同一校正裝置再三角化的三維球心 RMS 約 4.19 mm、最大約 7.75 mm；這只是**擬合資料的自我一致性**，不是獨立精度測試，更不能外推到整個投球活動範圍。所有報告標記 `independently_validated_for_measurement=false`，完整解算後狀態仍為 `review_required`；退出碼 0 只表示程式完成，不表示量測品質通過。

### 拍攝與精度建議

1. **先確認內參適用。** 相機序號、鏡頭、焦距／對焦、解析度、裁切 ROI 與拍內參時一致；固定曝光、白平衡及增益，避免粉紅球過曝失去飽和度。相機或鏡頭一動，須重新估外參。
2. **固定裝置並保持真正鉛直。** 用剛性底座、水平儀及鉛垂線，避免手持球或遮住球面；本批側面橫桿幾乎端視，中央幾顆球相互重疊、也有手部遮擋，應改善觀測角度。
3. **增加各相機都看得到的深度結構。** 15 mm 的厚度相對數公尺距離太小，建議另設有明確球號、較大前後差的固定標記或立體校正架；新增點後要把實測三維座標一起更新。球在兩台畫面都應有足夠直徑和清楚間隔，依現場試拍確認，不以數到八個色塊為合格標準。
4. **重複固定照片只能減少部分隨機誤差。** 十張同位置影像不能消除錯誤內參、錯誤球距或幾何退化。可另拍不同位置與角度檢查，但移動裝置後不可將球號的相同局部座標當成同一世界座標直接合併；需要各位置的已知世界變換或多視角聯合校正。
5. **在量測範圍內做獨立驗證。** 在投手板、前腳落點及不同高度放置未參與校正、已實測距離的標記，檢查水平距離、垂直高度與左右方向；將可接受誤差依用途訂為 cm 或 mm，不能只用 2 px 判斷。同步雙機拍攝投球，否則快動作的兩台點位不屬於同一瞬間。

參考：[OpenCV PnP 與外參方向](https://docs.opencv.org/4.13.0/d5/d1f/calib3d_solvePnP.html)、[OpenCV HSV inRange](https://docs.opencv.org/4.13.0/da/d97/tutorial_threshold_inRange.html)。

測試：

```powershell
conda run --no-capture-output -n Pitcher python -m unittest discover -s Src/UI_Control/tests -v
```

## 5. 獨立螢光粉球提取

入口：`Src/calibration/extract_fluorescent_balls.py`。
核心：`Src/calibration/fluorescent_balls.py`。

只需要 OpenCV 與 NumPy，不讀內參，不計算 PnP，不載入 Qt、相機 SDK 或骨架模型。現有 `calibrate_cross.py` 共用同一個偵測核心，避免兩套 HSV 方法產生不同結果。

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

**c1、c2… 是每張圖內按 y 再 x 排序的候選 ID，不是實體 ball1～ball8，也不是跨影格追蹤 ID。** 找到八個候選只代表數量符合，不能證明沒有誤檢或球號正確。實體球號與幾何檢查仍由 `calibrate_cross.py` 進行。

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
