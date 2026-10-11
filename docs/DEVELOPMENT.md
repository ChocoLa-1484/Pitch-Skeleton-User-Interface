# 開發者筆記

給要重建開發環境、除錯、或接手維護這個專案的人看。使用者安裝步驟請見 [README.md](../README.md)；AI 助手的協作規範與問題追蹤表請見 [TODO.md](TODO.md)。這份文件記錄「為什麼」——每個已知問題的成因與排查過程，方便日後遇到類似狀況時參考。

最後更新：2026-10-07

---

## 目前狀況一覽（2026-09-21）

**可以正常使用的功能**
- 環境已建置完成（conda 環境 `Pitcher`），程式可正常啟動
- 雙相機即時擷取（需接 GPIO 同步線）、自動投球偵測、自動錄影
- 2D 分頁「雙影片模式」回放分析（同時選 CF + CS 兩支影片）

**目前不能用 / 有風險的功能**
- 2D 分頁單獨選「正面 CF」影片：系統會拿正面影片去做側面專用的分析，**算出來的角度數據沒有意義，但畫面不會有任何警告**（P1-001）→ 暫時請只選 CS（側面）影片
- **回放模式每次都會重跑分析**，即使該影片先前已分析過、資料夾裡已有 JSON。原因是 2D 分頁根本沒有實作「讀取已存結果」的功能（3D 分頁有）（P1-003）
- **點選另一支影片，載入的可能還是前一支**。原因是選取狀態在載入後被強制清除，造成狀態混亂（P1-004）
- **「肩最大外旋」與「球離手」兩個關鍵幀常常空白**。程式要求最大外旋角超過 140 度才會顯示，未達門檻就不觸發。尚未釐清是動作真的沒到、還是估測不準（P1-005）
- 3D 分頁的 3D 重建：舊錄影使用程式內建的舊設備參數，精度存疑；2026-10-07 起新錄影會改用錄影當下的校正值（P2-001）

**硬體評估**
2026-09-22 已完成 GPU 資源量測，詳見 [HARDWARE_EVALUATION.md](HARDWARE_EVALUATION.md)。簡言之：顯存綽綽有餘（8 路只要 2.9 GB），但單路推論 44 FPS、5 路共用一張卡每路只剩 8.8 FPS——不過系統本來就設計成「即時只錄影、分析事後跑」，所以這不影響實際使用。

**最近修好的**
- 2026-09-22：2D 分頁「單影片模式」跑完分析會跳錯誤視窗的問題已修復（問題編號 P1-002）。現在單選一支影片也能完成分析、正常偵測關鍵幀。

**2D 分頁該怎麼操作（暫行建議）**
1. 執行 `cd Src\UI_Control` 然後 `python main.py`
2. 切到「2D」分頁，右側選「回放」頁籤
3. 在影片樹狀清單中選取影片。兩種方式都可以：
   - **同時選 CF + CS 兩支**（按住 Ctrl 點選）→ 雙影片模式，左右各顯示正面/側面，分析用側面那支
   - **只選一支**（單影片模式）→ **請務必選 `CS_` 開頭的側面影片**。選 CF（正面）雖然能跑完，但分析結果無意義（見 P1-001）
4. 勾選「檢視骨架影片」開始分析
5. 分析完成後，下方「前導腳著地 / 肩最大外旋 / 球離手」三個按鈕會變成可點擊，按下可跳到對應畫面
6. 播放控制：空白鍵播放/暫停、A 上一幀、D 下一幀

> 為什麼一定要選兩支？因為系統的生物力學分析（肩髖分離角、肩外旋角等）設計上只從**側面**視角計算。選兩支時系統會自動用 CS（側面）那支做分析；只選一支時系統會把它同時當成正面和側面用，若選到 CF 就會分析錯對象。

**接下來的工作順序**
1. 修好 2D 分頁的兩個問題（P1-001、P1-002）
2. 比對新舊兩版骨架偵測程式（`detect_skeleton.py` vs `detect_skeleton_new.py`），決定要怎麼收拾這個未完成的重構
3. 相機校正：流程已整理完成、輸出統一存到 `Db\Calibration`（2026-10-07），待正式現場校正
4. GB10 主機的相機連接測試（等實體機器到手）

---

## 環境建置已知問題

### SSL 憑證驗證失敗（`CERTIFICATE_VERIFY_FAILED`）

**症狀**：`pip install` / `conda install` 全部失敗，錯誤訊息包含 `unable to get local issuer certificate`。

**成因**：部分防毒軟體（例如 Avast）的「網頁守衛 / HTTPS 掃描」功能會攔截所有 HTTPS 連線，用自己生成的根憑證重新簽發，藉此掃描流量內容。這張憑證會被加進 Windows 系統信任庫（瀏覽器、`curl` 因此不受影響），但 Python 生態系（`pip`、`conda`）預設只信任 `certifi` 套件內建、與系統信任庫完全獨立的公開憑證清單，不認得防毒軟體臨時生成的憑證，因而拒絕連線。

**解法**：到防毒軟體設定裡關閉 HTTPS/SSL 掃描（Avast 路徑：主畫面齒輪圖示 →「設定」→「隱私」→「網頁守衛」→ 關閉「啟用 HTTPS 掃描」）。這是最徹底的解法，關閉後所有 Python 工具鏈都會恢復正常，不需要額外的憑證合併或環境變數設定。

### `libiomp5md.dll` 不可刪除

**症狀**：`ImportError` / `OSError: Error loading "...\nvfuser_codegen.dll"`，torch 完全無法 import。

**成因**：舊版 README 曾指示刪除 `anaconda3\envs\<env>\Lib\site-packages\torch\lib\libiomp5md.dll` 以消除一則 OpenMP 重複載入的 warning。這個指示可能是針對更早期的 torch 版本寫的——當時 torch 內部沒有用到 nvfuser，刪除該檔案只會消除警告。但目前使用的 torch 2.0.1 版本中，`nvfuser_codegen.dll` 依賴這個檔案，刪除後會導致 torch 整個 import 失敗。

**解法**：不要刪除這個檔案。若已誤刪，執行 `pip install torch==2.0.1 --index-url https://download.pytorch.org/whl/cu118 --force-reinstall --no-deps` 重新安裝即可恢復。

### `opencv-python-headless` 覆蓋 GUI 版 opencv

**症狀**：`cv2.imshow`、`cv2.namedWindow` 等視窗顯示功能消失或報錯，但 `import cv2` 本身不會出錯。

**成因**：`mmpretrain` 的可選相依套件 `albumentations`（資料增強函式庫）依賴 `opencv-python-headless`（無 GUI 功能的精簡版），pip 安裝時會用它覆蓋掉先前裝好的 `opencv-python`（完整版）。這套系統的 GUI（`cv_utils/cv_thread.py`、`pitch_widget.py` 等）需要完整版才能正常運作，且核心功能不依賴 `albumentations`。

**解法**：
```
pip uninstall -y opencv-python-headless
pip install --force-reinstall --no-deps opencv-python==4.8.1.78
```

### vispy / PySpin DLL 衝突

**症狀**：`RuntimeError: Could not import backend "PyQt5": DLL load failed while importing QtOpenGL`。

**成因**：若在 `import PySpin`（FLIR 相機 SDK）之後才呼叫 `vispy.use('pyqt5')` 鎖定 Qt 後端，PySpin 附帶的第三方 DLL 會污染 Windows 的 DLL 搜尋路徑，導致 Qt 之後載入 `QtOpenGL` 時抓到不相容的版本。已透過二分法排查確認：問題不是 PySpin 本身，而是「PyQt5 已載入 + PySpin 載入」這個組合疊加時才會觸發。

**解法**：`vispy.use('pyqt5')` 必須在檔案最上方、任何會匯入 PySpin 的模組之前執行。目前 `main.py` 已依此順序修正。若新增其他程式進入點（entry point）也需要遵循相同順序。

### 執行路徑錯誤導致 `FileNotFoundError`

**症狀**：`FileNotFoundError: [Errno 2] No such file or directory: '..\mmyolo_main\configs\...'`

**成因**：`utils\model.py` 中的模型設定檔路徑使用相對路徑（如 `..\mmyolo_main\configs\...`），這些路徑假設程式是從 `Src\UI_Control` 目錄本身執行的。若從專案根目錄執行 `python Src\UI_Control\main.py`，相對路徑解析基準不同，會找不到檔案。

**解法**：務必先 `cd Src\UI_Control` 再執行 `python main.py`。

### FLIR SDK（PySpin）為隱性必要相依套件

`camera_objects/__init__.py` 設計上會在沒有 PySpin 時優雅跳過 FLIR 相關類別的匯出，但 `cv_utils/cv_thread.py` 目前無條件匯入 `FlirCameraSystem`，因此實務上 PySpin 是必要相依套件，即使不接 FLIR 相機也需要安裝，否則整個 GUI 無法啟動。

若沒有官方安裝檔，且電腦上其他 conda 環境已裝過 PySpin，可直接複製該環境的 `Lib\site-packages\PySpin\` 與對應的 `spinnaker_python-*.dist-info` 資料夾過來，前提是兩邊 Python 版本一致。

## 相機硬體相關

### 相機校正

校正流程、各種校正方法與量測工具，統一整理在 [CALIBRATION.md](CALIBRATION.md)。

### `camera_widget.py` 的 `model` 參數已移除

`PoseCameraTabControl.__init__` 目前簽名為 `def __init__(self, parent=None)`，不再接受 `model` 參數（第 17-18 行殘留了被註解掉的 `self.model = model` 與 `self.init_pose_estimater()`，是重構未完成的痕跡）。目前相機分頁純粹負責畫面預覽與錄影，不涉及即時骨架推論，呼叫端（`main.py`）需對應使用 `PoseCameraTabControl()`（不帶參數）。

### 相機規格參考（`camera_config/*.yaml`）

- 解析度 1920×1084，179fps，`BayerRG8` 原始格式（需程式端做色彩還原，對應 UI 上的曝光/紅色/藍色調色滑桿）
- 透過 GPIO 硬體訊號線做多相機同步觸發：`camera1`（正面）在 `DualFlirSystem.__init__` 中被設為 Primary（`configure_gpio_primary()`，輸出 `ExposureActive` 訊號），`camera2`（側面）被設為 Secondary（`configure_gpio_secondary()`，由 `Line3` 接收觸發），非純軟體時間戳記同步
- **已實測驗證**：僅接 USB 資料線、未接 GPIO 同步線時，Secondary 相機會因等不到觸發訊號而逾時（`Failed waiting for EventData on NEW_BUFFER_DATA event`）；接上同步線後雙機可正常同步擷取畫面
- 目前程式僅支援兩台相機（正面 + 側面各一），無第二支側面相機（論文原始架構為 2 側面 + 1 正面）的處理邏輯

## 背景：系統設計依據

此系統的演算法設計對應學長姐碩士論文：《基於2D-to-3D Diffusion-Transformer網路之棒球投球骨架動作切分及動作分析》（施邑穎，國立成功大學人工智慧科技碩士學位學程，指導教授連震杰）。

- 原始硬體架設：3 部高速工業相機（2 側面 + 1 正面）+ Rapsodo Pro 2.0 投球追蹤系統（獨立設備，量測結果未納入本系統演算法）
- 核心流程：雙攝影機校正（Zhang's method）→ YOLOv11+ViTPose 2D 骨架偵測 → 三角測量重建 3D 骨架 → 投球四階段切分（Foot Contact / Maximum External Rotation / Ball Release）→ 生物力學參數計算（肩髖分離角、肩外旋角、手腕速度等）
- 系統架構採「即時擷取（Live）」與「離線分析（Replay）」分離設計，避免即時運算拖慢影像擷取效能

### 鏡頭規格（論文表 22）

| 位置 | 型號 | 焦距 | 光圈 | 用途 |
|---|---|---|---|---|
| c1, c2（側面 ×2） | KOWA LM8HC | 8mm | F/1.8 | 視角較大，完整涵蓋投手從抬腿到跟隨動作的整個過程 |
| c3（正面） | KOWA LM50HC | 50mm | F/1.4 | 較高人體解析度，提升關節辨識品質 |

> 曾誤判正面鏡頭為 75mm——那是設備交接清單裡的庫存/歷史紀錄，非論文正式採用規格。查證鏡頭焦距等硬體規格時，以論文本文為準，交接清單僅供財產追蹤參考。

### GUI 分頁與即時操作的實際對應關係

依交接文件《交接程式操作文件_Ui》第 10 節：**`Src/UI_Control/camera_widget.py`（「2D 相機」分頁）目前入口未啟用**，是獨立於主流程之外的備用實作。真正的雙攝影機即時操作、自動投球偵測、自動錄影，是整合在 **「2D」分頁**（`PosePitchTabControl`，即 `pitch_widget.py`）裡的 `cameraCheckBox`（開啟相機）/`startPitchCheckBox`（開始投球）等控制項。

雙視角畫面的左右對應（追蹤自 `cv_thread.py` → `DualFlirSystem.get_grayscale_images()` → `pitch_widget.py updateFrame()`，並經論文原文「介面左上方提供正面攝影機與側面攝影機之同步播放畫面」交叉驗證）：

| 畫面位置 | UI 元件 | 對應相機 | 序號 |
|---|---|---|---|
| 左邊 | `FrameView` | 正面攝影機（camera1） | `25462483` |
| 右邊 | `FrameView_2` | 側面攝影機（camera2） | `25462481` |

## Git 歷史說明

本專案原始 git 歷史（47 個 commit，2024-07-30 ～ 2024-10-12）為前一位貢獻者所建立，commit 訊息多為概略性的 `fix bug`。約 2024-10 之後至此份程式碼被複製交接為止的開發進度未曾提交版本控制，是透過 USB 直接複製取得，因此這段時間的變更沒有逐次的 commit 紀錄可供追溯。

## 2D／3D 回放、分析 JSON 與 3D 公制模式（2026-10-11）

> 原為 `MAIN_REPLAY_FIX.md`（作者 ChocoLa-1484），內容未改寫。該次的修正清單移到 `TODO.md` 的 PR #1 紀錄。

從專案根目錄執行：

```powershell
conda activate Pitcher
python Src/UI_Control/main.py
```

### 輸出位置

2D 分析播放到最後一幀，或單影片掃描完成後，結果寫在**影片所在資料夾**：

- `<影片檔名>_BRandRapsodo.json`：FC／MER／BR、原有 ROI／OCR，以及分析 FPS、幀偏移與分析幀數。
- `<影片檔名>.json`：該相機實際已有的骨架。2D 流程主要分析側面，正面沒有資料時不會產生假的完整骨架。

介面顯示「分析 JSON 已儲存」表示完成；「JSON 儲存失敗」可將游標放在提示上查看原因。

播放列新增「重新分析」與「儲存 JSON」。重新分析會保留已有骨架、重算關鍵幀，播完自動儲存；儲存按鈕也可立即寫出目前已有的部分分析。載入舊事件檔後，若要使用修正後的 MER／BR 偵測，請按「重新分析」。

骨架 `frame_number` 沿用原有回放時間軸；新事件檔的 `Analysis.pose_frame_number_space` 與 `frame_offset` 明確記錄對應方式。棋盤量測讀取新格式時會換回原始影片幀。舊資料沒有這段 metadata，使用前仍需確認同步。

### 驗證

```powershell
conda run --no-capture-output -n Pitcher python -m unittest discover -s Src/UI_Control/tests -p test_pitch_replay.py -v
```

以 P05 既有側面骨架重跑：準備幀 50、FC 候選 95、MER 124、BR 138；這是驗證分析流程可運行，並非人工確認事件時間。既有 FC 95 仍可能早於真正落地，校正量測應逐幀目視核對。測試匯出使用暫存資料夾，不改寫原始影片資料。

此次修正沒有把原 2D 的固定像素倍率改成棋盤公制量測。校正後的同一後腳位移請使用 [CALIBRATION.md 第 6 節〈main_metric.py 公制量測入口〉](CALIBRATION.md)。

### 3D 回放與棋盤外參

3D 頁上方新增 **PnP 棋盤校正／8Points 棋盤尺度／舊版估算**。預設 PnP，檔案分別為：

```text
Db/Calibration/chessboard/pnp/stereo.json
Db/Calibration/chessboard/8point/stereo.json
```

1. 載入 CF／CS 兩段影片。檔名必須能識別相機，影片尺寸須符合各自內參，兩片 FPS 須相同。
2. 選擇方法；可按「選擇外參」換成另一組棋盤流程產生的 `stereo.json`。相機位置、鏡頭設定須與校正時相同。
3. 確認「第二片偏移」、初始站立幀與左右後腳，再按「套用校正」。偏移定義是 **第二影片實際幀 = 第一影片幀 − 偏移**，預設 0，並非自動同步。影片順序可 CF／CS 或 CS／CF，外參方向會按檔名處理。
4. 按「處理和儲存」從第 0 幀分析。播放到最後一幀自動儲存，也能中途按「儲存分析 JSON」。切換校正或同步設定會清除先前重建與事件，避免混用結果。
5. 回放確認 FC／MER／BR；需要修正時，移到正確幀並按「設為 FC／MER／BR」，再儲存。這些按鈕不會把沒有雙目骨架的幀變成有效公制量測；若缺骨架，需重新處理原始影片。

「載入處理過的影片」會先讀取所選方法的骨架輸出，否則讀取原始 `<影片名>.json`。有同步 metadata 時會轉換回來源幀，再轉成目前時間軸。缺少 metadata 的舊骨架按來源幀解讀，須人工核對同步。只有校正內容、影片來源／修改時間、偏移、FPS、初始幀與後腳設定相同，才恢復儲存的 3D 結果。

MER／BR 圖像場景現在由對應視窗持有；沒有可用 3D 數值或疊圖失敗時仍顯示原影片。片尾 BR 不需要等後七幀，未確認的候選也會保留，JSON 的 `mer_confirmed`／`br_confirmed` 記錄是否確認。

#### 尺度與量測定義

- PnP／8Points 讀取校正的 **K、畸變、R、毫米 t**，去畸變後三角化；3D 座標保存為公尺，以 CF 相機光心為原點。不再套用舊的 21.556／21.56 倍率。
- FC 顯示你確認的「初始後腳腳跟 → FC 時同一後腳腳跟」**三維直線位移**；右腳 HALPE 25、左腳 24。沒有地面標定，因此不是地面投影距離。
- BR 的距離是「初始後腳腳跟 → BR 右手腕」三維直線距離，不是投球方向的水平 extension。手腕速度用連續兩個有效幀的三維差分乘上實際 FPS，缺幀不跨越計速；骨架抖動仍會放大速度誤差。
- MER 面板顯示「手臂平面法向量與軀幹軸夾角」，不是臨床肩外旋角。肩髖分離角改用垂直於軀幹軸的平面，避免把相機 XZ 平面誤當地面。
- 每點須兩相機信心值至少 0.5、重投影誤差 ≤ 3 px、兩相機正深度、交會角 ≥ 1°。不合格點輸出 `null`，相關距離顯示 `--`，不會用預期 60–70 cm 調整尺度。
- 自動事件時刻使用 CS 影像的右投手啟發式：抬膝、左踝穩定、前臂角度與手腕影像速度峰值。它與 3D 數值有效性分開，不是球體離手偵測，也未經事件精度驗證；左投手目前請手動指定事件。所有事件都建議逐幀確認。

目前校正仍需同場景實尺檢核，低重投影誤差本身不能保證絕對尺度準確。相機移動、同步誤差、腳跟遮擋及骨架誤認都可能造成偏差。需要人工標記四張原圖或地面距離時，使用 `main_metric.py` 的獨立量測頁。

#### 3D 輸出

輸出位於影片資料夾；方法後綴為 `pnp`／`8point`／`legacy`，不同方法互不覆蓋：

| 檔案 | 內容 |
| --- | --- |
| `<第一影片名>_<方法>_3D.json` | 公制模式：`metric_pose_3d_v1`，逐幀公尺座標、各點品質、內外參快照與 SHA-256、同步資訊。舊版模式保留座標 record list。 |
| `<第一影片名>_<方法>_analysis.json` | 每幀角度／速度與分析覆蓋幀號、來源及量測定義。 |
| `<第一影片名>_<方法>_keyframes.json` | FC／MER／BR 與畫面一致的 cm、m/s、角度；已移除原本額外的 0.818 倍率。 |
| `<各相機影片名>_<方法>_poses.json` | 2D 骨架及其播放時間軸 metadata；不覆寫 2D 頁的原骨架。 |

公制 3D JSON 是含 metadata 的物件，與舊版 list schema 不同；舊「回放比較」頁尚未接入這個格式。每個檔案皆採暫存後替換，非有限數值轉為 `null`。載入失敗會顯示原因，不能改用舊倍率冒充公制結果；儲存失敗可按按鈕重試。

#### 3D 回歸驗證

```powershell
conda run --no-capture-output -n Pitcher python -m unittest discover -s Src/UI_Control/tests -p test_video_3d_replay.py -v
```

測試涵蓋畸變、CF／CS 順序、尺度、逐點品質淘汰、缺幀速度、片尾 BR、場景生命週期、自動匯出、儲存重試、校正切換與結果恢復。合成投影驗證演算法與單位；不代表實際相機已達同等量測精度。Qt 測試使用 offscreen，無法驗證 Windows 畫面上的 OpenGL 3D 骨架渲染。

2026-10-11 驗證：14 項 3D、16 項 2D 回放、15 項棋盤幾何、10 項公制量測測試通過。P05 既有骨架的 3D 頁候選為 FC 95、MER 124、BR 138，三張原圖皆可回放並匯出。因既有 CF 骨架稀疏，FC 距離保持 `null`；請載入原始影片重新處理，且目視確認 FC，不能把候選幀視為真實落地時刻。
