# 專案工作區（AI 協作規範 + 問題追蹤 + 待辦）

> **這份文件給 AI 助手看**（Claude / GPT / 其他模型）。人類閱讀的白話說明請見 [DEVELOPMENT.md](DEVELOPMENT.md)。
> 最後更新：2026-10-09

---

## 【當前工作交接狀態】

> **接手的 AI 請先讀這一段。** 這裡記錄「上一位助手做到哪、下一步該做什麼」，每完成一個階段性步驟就會更新。
> 若此區塊顯示「無進行中任務」，代表上一段工作已告一段落，可直接從下方待辦清單挑選。

**更新時間**：2026-10-13

**【單一投手鎖定（2026-10-13）】**
- 使用者要求減少骨架跑到旁人，只偵測畫面中央的投手。主程式使用 `skeleton/detect_skeleton.py`，不是 `_new.py`。
- 根因：原本逐幀選最大框，且用當幀排列編號當人物 ID；無框仍呼叫 MMPose，而其空框預設為全圖推論。
- 已改為中央區域取得單人、依框位置與重疊持續追蹤、固定輸出 ID 1；漏偵測不輸出舊框或全圖骨架，連續遺失 15 幀後允許重新由中央取得目標。每個 estimator 各自保存狀態，reset 清除。
- 驗證：Pitcher 環境 `test_pitcher_target.py` 15 項、`test_pitch_replay.py` 16 項通過；涵蓋偵測順序改變、中央選人、跨步離開中央、短／長漏偵測、空幀不跑全圖推論、雙 estimator 狀態隔離及回放相容性。
- 下一步：現場確認投手與旁人重疊、快速跨步時的效果；中央範圍／失聯幀數可調 `PitcherTargetSelector` 建構參數。未修改未啟用的 `_new.py`，也未重寫既有錄影分析資料；目前 UI「重新分析」會重用已載入骨架，不會重新推論既有 JSON。

**【同學 PR #1 處理中（2026-10-11）】**
- PR 作者 ChocoLa-1484（naiyanyang@gmail.com）。他的 fork 沒有在歷史改寫後 Discard，而是在舊歷史上修改再合併新 main → PR 含 104 個 commit（102 個為舊歷史副本，含 Claude 標記與學長作者），直接合併會讓 Contributors 再出現 Claude、並帶回已刪除的 `Src/mmengine_main` 等 1586 個檔案。**不可直接按 GitHub 的 Merge**
- 處理方式：本機分支 `pr1-clean` = main + cherry-pick 他的 commit 3233eaf（作者保留為他），再加一個整理 commit（移除 `.vscode/settings.json`、`docs/bullpen.docx`，`.gitignore` 加 `.vscode/`）。`review-pr1` 為原始 PR 內容。兩者皆未推送
- 已驗證：他的測試 77 通過 2 略過（需他本機錄影）；主視窗含模型可正常建立；本 repo 先前的功能（曝光輸入框、179 FPS、校正快照、P1-006）合併後仍在
- 內容摘要：補上「右肩外旋」定義（P1-005 根因）；2D 讀回已存分析與重新分析／儲存（P1-003）；3D 分頁 PnP／8Points 棋盤外參與公制三角化、幀偏移預設 0、品質過濾；新校正工具 `calibrate.py`、十字架、螢光球、跨步量測與 `main_metric.py`；JSON 原子寫入；新增 5 份文件於 `docs/`
- 注意：3D 匯出拿掉 0.818 倍率（原本畫面沒乘、JSON 有乘，現已一致）；「回放比較」分頁 `video_widget_compare.py:860,871` 仍乘 0.818，待統一
- **PR #1 修正清單**（原 `MAIN_REPLAY_FIX.md`「修正內容」）：
  - 補回目前 `PoseEstimater` 遺漏的「右肩外旋」角度定義 `[10, 8, 8]`。2D 分析器使用右前臂相對手肘水平線的角度；原本缺少此項，MER 無法確認，BR 也無法開始偵測。此值是 2D 投影角度，不能當作解剖學上的真實三維肩外旋角。
  - 單人骨架資料不必先手動選人才能判斷腳踝穩定；多人時仍須選人。
  - FC／MER／BR 圖像場景由對應視窗持有，重畫時重用；缺少骨架疊圖時仍顯示原始關鍵幀影像。
  - 載入既有影片時讀回骨架與 `Keyframes`，不依賴是否存在 ROI 圖片。超出影片範圍的事件幀不顯示。
  - 播放／啟用分析不再重新載入並清除關鍵幀；第 0 幀會確實進入分析。
  - 一般雙影片分析及單影片掃描結束都會匯出。BR 靠近片尾、不足後七幀時，仍顯示已有畫面與軌跡。尚未確認的事件標為「候選」。
  - JSON 採暫存檔寫入後替換，NaN／Infinity 轉為 null，儲存失敗顯示提示並可重試。空的相機骨架不會清空已有檔案；部分重分析保留未重新分析的幀。
  - 離線重分析保留原有 ROI／OCR 與其他 metadata。全域 A／D／空白鍵僅作用於可見的 2D 頁，不攔截文字欄位輸入。
  - 修正縮放後回放圖像未重新適配大小，以及前一段影片的延遲結束回呼誤作用於新影片的問題。
- **實機測試（2026-10-11）**：使用者在 2D 回放載入 `20260505_1355_P03` 的 CS（單影片），按「重新分析」→ FC、MER（175.2°）、BR（手腕 26.4 m/s、出手距離 173 cm）**三個都偵測到**，確認 P1-005 已解決；載入時讀回既有分析也正常（P1-003）
- ⚠️ **「重新分析」會覆寫原始錄影資料夾的骨架檔**：上述測試把學長論文資料的 `CS_..._P03.json` 覆寫並新增 `CS_..._BRandRapsodo.json`；已從 `G:\...\Ms2026_Sports_ShihYiYing\Db\台鋼牛棚\` 還原並比對一致，測試結果另存本機 `local/pr1_test_20261011_P03/`。**待辦**：分析結果改為另存（或覆寫前自動備份），避免動到論文資料與台鋼正式資料
- **2D 影片選取待改善（使用者提出，給台鋼人員用要越簡單越好）**：交接文件第 110-111、245、248 段：選一支＝單影片（同一支放左右兩格）；選 CF+CS＝雙影片（左正面、右側面），分析數值都只用側面。建議先做「點 CF、CS 或該球資料夾都自動載入 CS 單影片」，避免只選 CF 的 P1-001；「錯開 2 幀」驗證後再考慮改自動雙影片
- **待辦**：(1) 使用者在 `pr1-clean` 實機操作 2D 回放（載入→分析→MER/BR 是否出現→切換影片後是否讀回）；(2) 通過後合併 `pr1-clean` 到 main 並推送，在 GitHub 關閉 PR #1 並說明；(3) 通知同學 Sync fork → Discard、本機 `git reset --hard origin/main`；(4) 他新增的 5 份文件之後考慮整併進 DEVELOPMENT.md（使用者偏好文件不要越加越多）
**當前任務**：
- 已修正（2026-10-09，P1-008）：校正取樣的 `numpy.AxisError`。`calib.py` 統一角點為 `(N, 1, 2)` 保存，比較時兩邊展為 `(N, 2)`，避免越界與二／三維混用造成的錯誤廣播；角點數改變時拒收並提示清空樣本。`calibrate.py` 重新掃描現在顯示偵測數、序號、零台與錯誤原因；新取像優先選連線相機，保留仍連線的角色選擇，勾選重用內參時保留離線序號。
- 驗證：`conda run -n Pitcher --no-capture-output python -m unittest discover -s Src/UI_Control/tests -v`，13 項通過；實際 SDK 與校正視窗的啟動／重掃均找到 SN25462481、SN25462483。未取實體棋盤格樣本，下一步由使用者重啟校正工具、確認正面／側面角色後實拍。進場前已有未提交的精靈、工作流程與文件變更，本輪保留，尚未合併提交。
- 筆電安裝包 `C:\Users\user\Downloads\Pitcher_筆電安裝包\` **已實測通過**（全新 conda 環境照「安裝順序.txt」安裝 → 版本與 Pitcher 環境逐一比對一致（僅刻意排除 mmcv-full）→ 實際載入 YOLO + ViTPose 模型與 PySpin 成功），測試環境已刪除。試裝過程修正 4 個問題：pip 用 cp950 讀 requirements（鎖定檔改純 ASCII）、原環境有刻意的依賴不一致（改 `--no-deps`）、mm 子專案 setup.py 需 torch（加 `--no-build-isolation`）、freeze 漏 setuptools（補 60.2.0）。另注意：未 `conda activate` 直接呼叫 python.exe 時，numpy 會載到 base 環境的 MKL 而當機，安裝與執行一律在已啟動的環境中進行
- 已完成：README 改寫（2026-10-07）。版本鎖定檔放進 repo 的 `environment\`（不用 `env\`：`.gitignore` 的 `ENV/` 在 Windows 不分大小寫會忽略它）。根目錄舊的 `requirements.txt`（numpy==1.23.5 等）已過時、README 不再引用，待決定是否刪除
- 已完成：UI_Control 資料檔整理（2026-10-07）。舊設備校正 → `Db\Calibration\old_rig_20260223\`（`side_camera_calib.npz` 改名 `front_camera_calib.npz`，內容其實是正面 50mm 內參 fx 7927.87）；輸出/截圖 → `Db\Archive\UI_Control_20261007\`；4 支舊工具改用 `calib_store.OLD_RIG_DIR` 並實測可讀。注意：舊的 `stereo_calib.json` 與 `stereo_calib.npz` 本來就是兩次不同校正（fx 1060.45 vs 1061.90），勿用 `extract_calib.py` 覆蓋 json
- `trt_cache\`（660MB，sm120 = RTX 50 系列的 TensorRT 引擎，僅 `utils/model_v1.py` 使用）：使用者要求**保留**，未來可能部署到 5090。評估時注意：TensorRT 引擎同時綁定 GPU 架構與 TensorRT/onnxruntime 版本，現行環境（torch 2.0.1+cu118、onnxruntime 1.8.0）不支援 sm120，換 5090 需整套環境升級，屆時快取很可能須重建
- 暫緩：3D 分頁讀取校正快照的實機驗證（使用者目前不使用 3D 分析；被 P1-007 擋住）
- 已完成（2026-10-08，c6e38f4，待使用者接相機實測）：2D 分頁加「曝光(us)」滑桿（100～5500），依正面/側面切換各自存回 `GH3_camera_config.yaml` / `_2.yaml`；原標「曝光」的滑桿其實是增益，已改名；修正切換相機時 int() 捨去造成設定檔數值越存越小。以模擬相機測過：切換 20 次設定不變、調側面只改側面檔。提醒使用者：兩台曝光不同會使曝光中點錯開（觸發同步的是曝光起點），建議曝光一致、亮度用增益補
- 注意：`pitch_ui.py` 有手動修改，與 `pitch_ui.ui` 重新產生的結果不同（754 vs 744 行），**不要用 pyuic 重新產生覆蓋**；改介面文字需兩檔同步改。使用者的 VS Code 會在 `.ui` 存檔時自動產生 `*_ui_ui.py`，屬多餘檔案可刪
- 待使用者決定（2026-10-08 提案，使用者暫緩先談協作）：(1) `GH3_camera_config.yaml`/`_2.yaml` 改名為 `GH3_front.yaml`/`GH3_side.yaml`（yaml 綁角色不綁序號；註解會被 yaml.dump 清掉，只能靠檔名）；(2) 開相機時印出「角色 SN ← yaml」；(3) `calib.py` 依所選相機的角色讀對應 yaml（目前固定讀正面）
- 已查明（2026-10-08）：yaml 中 `device_link_throughput_limit`、`exposure_auto`、`exposure_mode` **程式沒有讀**，改了無效。頻寬上限未設定到相機，可能與 P2-003（錄影僅 68 FPS）有關，調查時先看
- GitHub 協作（2026-10-08 定案，告一段落）：
  - repo **維持公開**；實驗室同學已 fork（公開 fork）；**無協作者**，別人只能 fork → PR，由使用者合併
  - 雲端硬碟已改為「限制」（只有擁有者），分享時逐一加 email
  - **git 歷史已改寫兩次並強制推送**（使用者親自執行 filter-branch 與 push；auto mode 擋下 AI 執行）：(1) 刪除 51 個 commit 的 `Co-Authored-By: Claude` 標記；(2) 2026-08-19 起 54 個 commit 作者改為 `ZhengMo <221846216+ZhengMo1220@users.noreply.github.com>`（兩個 "Restore ... from prior contributor" 與 2024～2025 的 47 個保留學長 chenboch）。**因此本文件中 2026-08-19 之後的 commit 編號（如 c6e38f4、2b1d1bc）都已失效**，以 commit 標題搜尋。本機備份分支：`backup-before-rewrite`、`backup-before-author`
  - 本機 git 身分已改為使用者（原為學長 chenbochen / a27879798@gmail.com）。**不得再加 Claude 共同作者標記**
  - 標籤 `v2026.10.07` 已隨改寫更新
  - 待同學在 fork 按 Sync fork → Discard commits；可選：main 分支保護禁止 force push（公開 repo 免費）
  - 已移除：`Fix_Env.bat`（會刪 libiomp5md.dll 弄壞 torch）、根目錄 `requirements.txt`、`Src/mmengine_main`、`Src/cython_bbox-0.1.3`、OCR debug 圖片；`start.bat` 改為自動尋找 Anaconda 與專案路徑
**實驗室演練設定**：正面 SN24380119（primary）、側面 SN24380117（secondary）

### 本輪已完成（皆已 commit 並 push）
1. 新增 `cv_utils/calib_store.py`：校正檔路徑與讀寫集中於此（`Db\Calibration\{camera_roles.json, intrinsic\, extrinsic\}`）
2. `calib.py`：輸出改到 `Db\Calibration\intrinsic\`；log 最新在上 + 時間戳；第 4 區改為「儲存角色」寫 `camera_roles.json`（原 `stereo_calib.json` 輸出移除，現行流程無人讀取）
3. `01`：輸出改到 `extrinsic\`；`02`：讀 `extrinsic\`、F 存 `fundamental.json`（含角色、內點數、門檻）、移除用寫死舊 F/K 覆蓋的段落，改用真實內參算 R、t（缺內參則略過）
4. `03_3d.py`：讀 `load_stereo_calibration()`，缺資料時印警告並退回寫死舊值
5. `cv_thread.py`：序號優先讀 `camera_roles.json`，舊 `camera_serials.json` 仍相容；example 檔移除
6. 資料搬移：`intrinsics_all.json`、`calib_SN24380119.npz` → `Db\Calibration\intrinsic\`；演練點位 → `extrinsic\`；舊設備點位備份 → `extrinsic\old_rig_backup_20261007\`；`Src\UI_Control\selected_points_*.json` 已從 git 移除（本機版已被 01 清空成 `{}`，原內容在 git 歷史）

7. **錄影校正快照（使用者選 (c)，分三個 commit）**：`calib_store.save_snapshot/load_snapshot`；`pitch_widget.saveCalibrationSnapshot()` 在手動與自動錄影建資料夾後寫 `calibration.json`（校正不齊或相機與角色不符時只印原因、不影響錄影）；`video_widget_2.applyCalibration()` 載入錄影時讀快照並同步更新兩個 `PoseAnalyzer.viewer3D` 的 K/F，無快照則用 `default_calibration`（原寫死值），舊錄影行為不變。已用假物件測試兩條路徑；**尚未實機錄影驗證**

### 其他結論
- **[已修正 2026-10-07] `calib.py` 重投影誤差算錯**：原寫法 `cv2.norm(L2)/N` 低估約 √N 倍，已改為 RMS，並加入自動品質判斷（RMS 門檻 0.5/1.0、單組異常剔除、3×3 覆蓋、最大傾斜 ≥20°、樣本 ≥15、重複樣本拒收）。以模擬資料驗證四種情境通過。既有結果：SN25462483 實際 RMS 3.93 px（不合格，需重做）；SN24380119 RMS 0.449 px（合格）
- **[已確認] SN24380119 為 8mm 鏡頭**（使用者 2026-10-07 確認）：fx 1411.8 × 5.86 µm ≈ 8.3 mm，內參合理。先前「兩台皆 6mm」的前提有誤；SN24380117 鏡頭尚未確認

### 其他已知事項
- `02` 內點 12/24：門檻 1.0 px 對手點過嚴（中位誤差 2.86 px；門檻 3/5/8 px 時內點 12/15/19），且未去畸變。RANSAC 無固定種子，每次 F 不同
- `findChessboardCornersSB`：使用者詢問過，結論現階段不改；正面長焦若常偵測失敗再換
- `FlirCameraSystem` 開啟時載入 Default user set（觸發關閉），`calib.py` 單台開啟不會卡同步

### 正式校正須知

**流程**：見 [CALIBRATION.md 第 2 節](CALIBRATION.md)（內參 → 指定角色 → 拍照 → 01 → 02 → 03 驗證）

**校正板規格差異（重要，來自論文 PPT slide 13）**

| 相機 | 棋盤格 | 每格邊長 | 張數 |
|---|---|---|---|
| 側面（8mm 廣角，近距離） | 10×7 | **10mm** | 25 |
| 正面（50mm 長焦，13m 遠） | 7×7 | **100mm** | 35 |

正面需要大 10 倍的校正板，因為 50mm 長焦架在 13 公尺外，小板子在畫面上佔比太小、角點間距不足，校正精度會嚴重劣化。**手邊的 8×11 / 10mm 板子適用側面，正面可能不夠**——現場判斷標準：板子需佔畫面 1/3 以上且能移到四個角落。

**已知限制**：外參拍照（「2D 相機」分頁）透過 `VideoCaptureThread`，因此必須接兩台 + GPIO 同步線。

### 外參校正流程待優化（2026-10-07 演練時使用者提出）

- [x] 輸出位置改到 `Db\Calibration\`（2026-10-07 完成）
- [ ] 01 每次執行從空白開始並整個覆寫 JSON；只想重點側面也會清空正面 → 改為載入既有點位、可指定只處理單一視角或單張
- [ ] 01 正面/側面分兩個視窗點，順序容易對錯 → 改為左右並排同時標註
- [ ] 校正桿是白色球，可自動偵測球心，人只負責確認順序
- [x] 02 結果存檔、移除寫死值（2026-10-07 完成）
- [x] `video_widget_2.py` 改為讀錄影資料夾的校正快照（2026-10-07 完成，同 P2-001）
- [ ] 02 內點門檻與去畸變
- [ ] 拍照、標註、計算整合為單一校正工具

### 外參校正改為自動找對應點（評估中）

手動點校正桿對應點費工且誤差大（中位 2.86 px），正在評估自動化方法（例如大棋盤格同時入鏡）。有確定的做法再更新此處。
- 學姊論文第 274-275 段的原方法：20 對同步照片、兩根校正桿、每對手點 8 點（共 160 點）、先去畸變、正規化 8 點法 + RANSAC 2000 次；內點門檻沒寫數值。**校正桿球距在論文、簡報、交接文件中都沒有記載**，需實際量測（`03_3d.py` 寫死的「第 1、4 點 340 mm」來源不明）
- 學姊論文第 225 段：兩台側面 8mm、正面（本壘板後）50mm。但 2026-10-08 牛棚校正的 SN24380115 fx=12463，以 5.86 µm 像素換算約 73mm，與「50mm」不符、與設備交接清單的 75mm 紀錄吻合 → **待確認該台現在的鏡頭**
> 給接手的 AI：使用者的個人研究細節放在本機 `docs/local/RESEARCH_NOTES.md`（不進 git），**研究內容不要寫進這份檔案或推上 GitHub**。

### 程式輸出統一放到 Db（2026-10-07 使用者提出，暫不處理）

- [ ] 盤點所有會寫檔的程式（3D 輸出、匯出、平板擷取、舊工具等），規劃放到 `Db\` 底下分類存放，避免散落在 `Src\UI_Control`

驗證基準：2026-10-07 演練資料 `Db\Record\Calibrate_Picture\`（4 組：01、02、04、05）及 `Db\Calibration\extrinsic\selected_points_cf/cs.json`。

---

**先前任務**：修復 2D 分頁問題
**狀態**：[P1-002] 已修復；[P1-003]、[P1-004]、[P1-005] 待處理

**已完成**：
- [P1-002] 單影片模式崩潰 — **已修復**（commit `6fb5aad`）。在 `detect_skeleton.py` 補上 `person_df_by_frame` 屬性（四處純新增），使用者實測通過
- 新舊架構差異分析完成（見 [P2-002]）
- **硬體資源量測完成** — 見 [HARDWARE_EVALUATION.md](HARDWARE_EVALUATION.md)。結論：顯存非瓶頸（8 路僅 2.9 GB），GPU 算力才是（單路 44 FPS，5 路共卡每路剩 8.8 FPS），但系統架構本為即時擷取/離線分析分離，不需即時分析多路
- 文件體系重整、README 補上 SDK 安裝流程

**下一步**（依使用者實測回報，2D 分頁還有三個新問題）：
1. [P1-003] 2D 分頁沒有讀取已分析 JSON 的功能，每次都重跑推論 → 仿 `video_widget_2.py` 的 `loadProcessedData()` 實作
2. [P1-004] 影片樹選取狀態被強制清除，重複點選會載入錯誤影片 → 三個候選解法待討論
3. [P1-005] MER/BR 關鍵幀常偵測不到（卡在 140° 門檻）→ 需先輸出實際角度值釐清原因，**勿直接調門檻**
4. [P1-001] 單影片模式選 CF 會用正面影片跑側面分析（仍未處理）

**注意事項**：
- 修改程式碼前必須先向使用者說明計畫並取得同意
- 2D 分頁單影片模式已可用，但選 CF 仍有 [P1-001] 的資料正確性問題，建議暫時只選 CS（側面）

---

## 第一部分：協作規範（AI 必讀）

### 專案背景

棒球投球動作分析系統。源自碩士論文《基於2D-to-3D Diffusion-Transformer網路之棒球投球骨架動作切分及動作分析》（施邑穎，成大人工智慧科技碩士學位學程）。程式碼由學長姐交接，**透過 USB 複製、非 git clone**，交接時有大量未提交的開發進度，存在多處未完成的重構痕跡。

目前維護者：ZhengMo，倉庫：`github.com/ZhengMo1220/Pitch-Skeleton-User-Interface`

### 工作原則

1. **先查證，不要猜測。** 這個程式碼庫有大量命名與實作不一致之處（見下方問題追蹤），任何關於「某段程式碼做什麼」的陳述都必須以實際讀過的程式碼為依據，並在回答中指出檔案與行號。如果推論有不確定性，明講。
2. **改動程式碼前先說明計畫並取得同意。** 使用者明確要求不要亂做。
3. **每次改動都要 commit**，commit message 用英文、說明「為什麼」而非只說「改了什麼」。
4. **發現新問題就登記到下方「問題追蹤」**，附日期、症狀、成因、影響範圍，不要只留在對話裡。
5. **修 bug 前先確認根因**，不要只讓錯誤訊息消失。這個專案已知有「兩套互相矛盾的相機參數」「新舊兩版 PoseEstimater」這類結構性問題，表面修補會累積更多技術債。
6. **重構時參考 code smell 原則**，但以「不破壞現有可運作功能」為最高優先，大範圍重構須先與使用者討論。
7. **每完成一個階段性步驟，立即更新本文件最上方的「當前工作交接狀態」區塊。** 不要等到任務全部完成、也不要等到對話快結束才寫——隨時可能因為用量耗盡、當機或使用者離開而中斷，文件必須永遠保持在「下一位接手者看得懂」的狀態。使用者也可隨時說「記錄交接狀態」要求立即更新。
   - 觸發時機：完成一次分析、做完一個決策、改完一個檔案、發現新問題
   - 必寫內容：當前任務、已完成什麼、下一步具體動作、待決策事項

### 環境

- conda 環境名稱：`Pitcher`（Python 3.8.20）
- 執行方式：**必須** `cd Src\UI_Control` 後再 `python main.py`（程式用相對路徑找設定檔，從專案根目錄執行會 `FileNotFoundError`）
- 環境建置的已知陷阱見 [DEVELOPMENT.md](DEVELOPMENT.md)

### GUI 分頁對應（重要，常被搞混）

| 分頁 | 程式檔 | 狀態 |
|---|---|---|
| 2D 相機 | `camera_widget.py` | 交接文件明載「入口未啟用」，是備用實作，非主線 |
| **2D** | **`pitch_widget.py`** | **主線**。即時雙相機擷取、投球錄影、回放與投球階段分析 |
| 3D | `video_widget_2.py` | 3D 骨架重建與回放 |
| 回放比較 | `video_widget_compare.py` | 多結果比對 |

---

## 第二部分：問題追蹤

狀態標記：`OPEN` 未處理 / `IN PROGRESS` 處理中 / `FIXED` 已修復 / `WONTFIX` 決定不處理

### [P1-001] 單影片模式會用錯視角做分析，且無任何警告

- **登記日期**：2026-09-21
- **狀態**：`FIXED`（2026-10-11）：在影片清單只點一個項目（CF、CS 或該球資料夾）時，自動改用該球的側面 CS 影片做單影片分析（`pitch_widget._find_side_video`，排除 `_Sk26`）；按住 Ctrl 同時選 CF+CS 仍為雙影片模式；只有 CF 沒有 CS 時在終端機提示。待使用者實機確認
- **優先序**：最高（會產出錯誤數據卻不報錯，比 crash 更危險）
- **症狀**：在 2D 分頁單獨選取一支 `CF_*.mp4`（正面）影片時，系統仍會執行側面專用的生物力學分析，產出的肩髖分離角、肩外旋角、跨步距離等數值在物理上無意義，但介面不會有任何提示。
- **成因**：`pitch_widget.py:1571-1573` 的分析路徑固定只跑 `pose_estimater_2`（程式碼原註解：「只對主視角（側面）進行分析」），讀的是 `frame_2`。而 `pitch_widget.py:1968-1971` 在單影片模式下執行 `video_paths = [video_paths[0], video_paths[0]]`，把選中的影片同時指派給 `video_path1` 與 `video_path2`——若選的是 CF，`frame_2` 就變成正面畫面，分析照跑不誤。
- **影響**：所有單影片模式 + 選 CF 的分析結果不可信。
- **可能解法（待討論）**：(a) 單影片模式下檢查檔名前綴，非 `CS_` 時跳出警告或拒絕分析；(b) 單影片模式強制只接受側面影片；(c) 允許使用者明確指定「這支是哪個視角」。

### [P1-002] 單影片模式分析結束時崩潰：`person_df_by_frame` 屬性不存在

- **登記日期**：2026-08-19（2026-09-17、2026-09-20 重複遇到）
- **狀態**：`FIXED`（2026-09-22，commit `6fb5aad`）
- **實際修法**：在 `detect_skeleton.py` 補上 `person_df_by_frame`，四處純新增（`__init__:42`、`mergePersonData:161`、`setProcessedData:498`、`reset:518`），未修改任何既有邏輯。已由使用者實測驗證。
- **未做的部分**：新版實作中，此字典同時用於讓 `getPersonDf` 變成 O(1) 查找（取代全表掃描）。本次只恢復正確性、未套用該效能優化，因為修改查詢路徑會影響即時模式與雙影片模式，風險不對等，應併入架構遷移一併處理。
- **優先序**：高
- **症狀**：2D 分頁單影片模式勾選「檢視骨架影片」，完整掃描跑完後跳出錯誤對話框「單影片分析失敗：'PoseEstimater' object has no attribute 'person_df_by_frame'」。
- **成因**：`pitch_widget.py:2203` 呼叫 `_mirror_single_video_analysis()`，該函式在 `pitch_widget.py:2144-2147` 存取 `self.pose_estimater_2.person_df_by_frame`。但 `pitch_widget.py` import 的是**舊版** `skeleton/detect_skeleton.py` 的 `PoseEstimater`，該類別沒有此屬性；有此屬性的是**新版** `skeleton/detect_skeleton_new.py`。屬於未完成的重構——呼叫端已改用新版 API，import 卻仍指向舊版。
- **影響**：單影片模式完全無法完成分析。雙影片模式不受影響（不走此路徑）。
- **可能解法（待討論）**：(a) 比對新舊版 `PoseEstimater` 差異，將 import 切換到新版並驗證相容性；(b) 將缺少的屬性/邏輯補回舊版；(c) 完成重構、移除舊版。需先做差異分析再決定。

### [P1-003] 2D 分頁沒有讀取已分析 JSON 的功能，每次載入都重跑推論

- **登記日期**：2026-09-22
- **狀態**：`FIXED`（2026-10-11，同學 PR #1：`pitch_widget._load_saved_analysis`、`save_current_analysis`、`restart_video_analysis`；待使用者實機確認）
- **優先序**：高（嚴重影響操作效率）
- **症狀**：在「回放」模式下載入影片時，即使該影片先前已分析過、資料夾內已有骨架 JSON，系統仍會從頭跑一次完整推論，等待時間長。
- **成因**：`pitch_widget.py` 完全沒有使用 `JsonLoader`——全檔搜尋顯示 `is_processed` 只在第 400 行被設為 `False`，之後從未被讀取；也沒有任何 `loadProcessedData()` 之類的方法。相對地，3D 分頁（`video_widget_2.py`）有兩條載入路徑：`loadVideo()`（重跑推論）與 `loadProcessedData()`（用 `JsonLoader` 讀既有結果）。**2D 分頁只實作了前者**。
- **判斷**：這不是壞掉，是功能未實作。`cv_utils/cv_control.py:346` 的 `JsonLoader` 已經存在可用。
- **可能解法**：仿照 `video_widget_2.py` 的做法，在 `play_video_from_item()` 中檢查對應 JSON 是否存在，有則走 `setProcessedData()` 路徑（該方法已存在於 `PoseEstimater`，且 2026-09-22 的修復已讓它一併重建 `person_df_by_frame`）。

### [P1-004] 影片樹選取狀態被強制清除，導致重複點選時載入錯誤影片

- **登記日期**：2026-09-22
- **狀態**：`OPEN`
- **優先序**：高
- **症狀**：點選另一支影片後，載入的仍是前一支影片。樹狀圖上可觀察到多個項目同時呈現反白狀態。
- **成因**：`pitch_widget.py:502` 將事件綁定在 `itemSelectionChanged`（選取狀態一變就觸發），而 `play_video_from_item()` 的結尾（約 1998-2000 行）又執行：
  ```python
  for item in self.tree.selectedItems():
      item.setSelected(False)
  ```
  形成遞迴觸發：點選 → 載入 → 清除選取 → 選取再次改變 → 再次觸發（此時 `selectedItems()` 為空，於 1920-1921 行提早返回）。清除後樹狀圖沒有任何「目前選中」項目，下次點選時選取狀態容易不一致；再配合 1936-1944 行「若沒選到影片節點就自動抓資料夾底下所有影片」的分支，可能抓到殘留項目。
- **可能解法（待討論）**：(a) 移除結尾的清除選取邏輯；(b) 清除時用 `blockSignals()` 避免遞迴觸發；(c) 改綁 `itemClicked` 或 `itemDoubleClicked`，語意上更符合「使用者主動選片」的操作意圖。

### [P1-005] MER（肩最大外旋）與 BR（球離手）關鍵幀經常偵測不到

- **登記日期**：2026-09-22
- **狀態**：`FIXED`（2026-10-11，同學 PR #1；待使用者實機確認）。**真正的根本原因不是 140° 門檻**：2D 分析（`pitch_widget.py`、`utils/analyze.py`、`utils/vis_image.py`）讀取「右肩外旋」角度，但實際使用的 `skeleton/detect_skeleton.py` 沒有定義它（只有未使用的 `detect_skeleton_new.py` 有 `'右肩外旋': [10, 8, 8]`，P2-002 未完成遷移的遺留）→ 角度永遠算不出、MER 無法確認、BR 也不會開始偵測。PR 補上定義。此角度是 2D 投影的「右前臂相對手肘水平線」，不是解剖學真實肩外旋
- **優先序**：中
- **症狀**：多支影片分析後，FC（前導腳著地）正常顯示且數值合理（跨步距離 127～129 cm），但「肩最大外旋」與「球離手」兩格始終空白。
- **成因**：`_track_max_shoulder_rotation()` 中，MER 需同時滿足兩個條件才會確認並顯示：
  ```python
  elif self.max_shoulder_angle_value > 140 and (峰值 - 當前值) > 3.0:
  ```
  即 **最大外旋角必須超過 140 度**，且已從峰值回落超過 3 度。若未達門檻則永不觸發。BR 的偵測通常依賴 MER 先確認，故一併失效。
- **待釐清**：需輸出實際追蹤到的角度值，判斷是 (a) 投球動作真的未達 140°、(b) 側面視角關節點估測精度不足、(c) 門檻設定過嚴。**在釐清前不應直接調整門檻值**，那會掩蓋真正的原因。

### [P1-006] 3D 分頁載入影片時崩潰：找不到 `CS_..._BRandRapsodo.json`

- **登記日期**：2026-10-07（使用者實機錄影後開 3D 分頁時發現）
- **狀態**：`FIXED`（2026-10-07，commit 2b1d1bc；以實際錄影資料夾測過檔案搜尋，待使用者在 GUI 確認）
- **優先序**：高（3D 分頁無法開啟新錄影，也擋住校正快照的實機驗證）
- **症狀**：`FileNotFoundError: ...\CS_20261007_1811_Pitcher01_P01_BRandRapsodo.json`，接著 `reset()` 中 `self.viewer3d.reset()` 報 `'NoneType' object has no attribute 'reset'`。
- **成因**：2D 分頁（`pitch_widget.py`）把球離手資料存成 **`CF_`**`..._BRandRapsodo.json`，3D 分頁（`video_widget_2.py:495`）卻用 `video_name_2`（**`CS_`**）組檔名，且 `display_roi_from_json` 沒有檔案存在檢查。第一次崩潰使 `viewer3d` 停在 `None`，之後的 `reset()` 再崩潰。`Db\Record` 內所有既有的 BRandRapsodo 檔皆為 `CF_` 開頭。非本輪校正修改造成（該行來自 234609e 前人程式碼）。`video_widget_compare.py:650,651,696` 有相同寫法。
- **建議修正**：先找 `CF_`、再找 `CS_`（與 `pitch_widget.py:2073` 一致），都沒有就略過 ROI 顯示；`reset()` 對 `viewer3d is None` 防呆。

### [P1-007] 3D 分頁載入影片時崩潰：Rapsodo 球速資料為空

- **登記日期**：2026-10-07（P1-006 修正後，使用者驗證校正快照時發現）
- **狀態**：`OPEN`（暫緩：使用者表示目前尚不使用 3D 分析）
- **優先序**：中（3D 分頁目前無法載入任何錄影，但 3D 分析暫未使用）
- **症狀**：`AttributeError: 'NoneType' object has no attribute 'get'`（`video_widget_2.py:638`）
- **成因**：`BRandRapsodo.json` 的 `metrics` 在所有錄影中皆為 `null`（球速轉速靠 Rapsodo 平板 OCR 取得，而 OCR 初始化一直失敗，見 P3-002）。`data.get("metrics", {})` 在鍵存在但值為 null 時回傳 None；即使有 dict，`'N/A'` 也無法套用 `:.1f` 格式
- **建議修正**：`metrics = data.get("metrics") or {}`，球速/轉速缺值時顯示 N/A（約 3 行）
- **附帶**：欄位為 `velocity_mph`，畫面標示卻是 `kph`，單位不一致，有資料後會差 1.6 倍
- **連帶待驗證**：錄影校正快照的「3D 分頁讀取」端尚未實機驗證（崩潰發生在 `applyCalibration` 之前）。錄影端已驗證：5 球皆有 `calibration.json`

### [P1-008] 校正取樣角點維度越界，重新掃描相機缺少結果提示

- **登記日期**：2026-10-09
- **狀態**：已修正；13 項回歸測試通過，實際重掃找到兩台相機，待現場棋盤格取樣驗證。
- **症狀**：`calibrate.py` 按空白鍵取樣，經 `Collector.capture_sample()` 進入 `calib.py` 第 328 行，出現 `numpy.AxisError: axis 2 is out of bounds for array of dimension 2`。使用者另回報重新掃描不能用。
- **成因**：重複樣本判斷固定用 `axis=2`，未統一角點的 `(N, 2)`／`(N, 1, 2)` 形狀；兩種形狀混用還會廣播成所有點兩兩比較，漏掉重複樣本。精靈掃描無成功／零台提示，並把舊校正序號與線上序號合併且保留舊選擇；本機 SDK 連續兩次掃描均成功，尚無 SDK 失效證據。
- **影響範圍**：單台 `calib.py` 與整合 `calibrate.py` 的棋盤格取樣；整合工具設定頁的相機重掃與角色預選。
- **修正**：統一角點形狀後逐點算位移，角點數不符時拒收；重掃顯示本次結果，連線序號優先，保留可用角色及離線匯入用途。測試見 `Src/UI_Control/tests/test_calibration_capture.py`。

### [P1-009] 骨架目標逐幀切換至旁人

- **登記日期**：2026-10-13
- **狀態**：`FIXED`（選人邏輯及回歸測試完成；待現場確認）
- **症狀／成因**：`detect_skeleton.py` 逐幀挑最大框、依偵測排序編號；手動選 ID 1 實際只保留當幀第一個候選，不能鎖定同一個人。空框仍送入 MMPose 全圖推論。
- **影響範圍**：使用主線 `PoseEstimater` 的即時與離線新骨架推論；已存 JSON 不受影響。
- **修正**：`pitcher_target.py` 起始只接受框中心 x=25–75%、y=15–85% 的人物，距畫面中心最近者優先；框面積至少全圖 0.5%、高度至少 10%。鎖定後以位移、IoU、面積變化配對，不再依排列編號或每幀最大框選人，允許跨步離開起始區。漏偵測輸出空結果，連續 15 幀後解除鎖定、下一幀再由中央取得目標；不推論舊框。只輸出一人，邏輯 ID 固定 1。
- **限制**：空間配對不是外觀辨識；兩人高度重疊仍可能混淆，低信心關節與動作模糊也需另行評估。

### [P2-003] 自動錄影實際只有約 66～69 FPS，不是 179 FPS

- **登記日期**：2026-10-07
- **狀態**：`OPEN`（待調查）
- **優先序**：中（影響時間解析度：球離手、速度計算）
- **症狀**：相機回報 179 FPS，但 `[AutoRecord] Actual recorded FPS` 為 66～69，存檔影片 FPS 同為 66～69。
- **已確認非新問題**：2026-05-05、08-17、09-10 的既有錄影也都是 66～69 FPS（2025-08 為 60）。先前文件中「自動錄影保持 179 FPS」的說法有誤。
- **成因已查明（2026-10-08）**：
  1. `cv_thread.py` 擷取迴圈 `count % 3 == 0` 才 emit `frame_ready`，而自動錄影 `cv_control.buffer_frame` 就是接這個訊號 → 錄影只拿到 1/3 幀，實際約 59.7 FPS。`cv_control.py` 註解「自動錄影不受採樣影響，每幀都存」與事實不符
  2. `stop_auto_recording` 寫死預錄 30 幀，實際 `pre_frames` 為 45 幀 → FPS 高估成 66～69。**已修正**（commit「Fix auto-recording FPS: subtract the real pre-roll count」），模擬驗證 60.0
- **影響**：既有錄影標示 66～69 FPS、實際約 60 → 時間被壓縮約 12%。2D 速度（`analyze.py` 用影片 FPS）與 3D 速度（`analyze_3d.py` 寫死 dt=0.014 即 69 FPS）都高估約 10～15%
- **待辦**：(a) 接相機跑量測腳本（scratchpad `measure_fps.py`：只取像／取像+轉色／取像+轉色+複製，各 10 秒），確認同步下實際上限；(b) 錄影改為每幀都存、顯示仍 1/3，建議存原始 Bayer（2 MB/幀）寫檔時再轉色。本機 127 GB RAM 足夠（一球約 3.5 秒：彩色約 7.8 GB、Bayer 約 2.6 GB），筆電需另評估；(c) `analyze_3d.py` 的 dt 改讀影片 FPS
- `cv_control_compare.py` 有同樣的寫死 30 幀，但沒有任何程式 import 它，未修改
- **進度（2026-10-08）**：
  - 實測相機上限（scratchpad `measure_fps.py`）：只取像 179.5、取像+轉色 179.4、取像+轉色+複製彩色 172.0 FPS → 存原始 Bayer 可錄滿
  - **已完成全幀率錄影**（commit「Add optional full-rate (179 FPS) auto recording」）：2D 分頁「相機設定」新增「錄影幀率」選單（預設 60）；179 模式由擷取執行緒保留每一幀原始 Bayer（預錄 135 幀），寫檔時轉彩色；停止條件改為顯示幀數（兩模式皆約 2.25 秒）。真相機測試：60 模式 180 幀；179 模式 541 幀 @ 179.3 FPS，顏色一致。使用者在主程式實錄 `Db\Record\20261008_Pitcher01\20261008_2249_P01`：530 幀 @ 179，寫檔約 15 秒
  - 60 模式算出 62.2 FPS（理論 59.7），推測為 Qt 訊號佇列延遲造成計時誤差，影響小，未處理
  - **未完成**：(1) `analyze_3d.py` dt 寫死 0.014 改讀影片 FPS —— **使用者決定暫緩**（目前不用 3D）。影響：速度顯示變正確，但 `video_widget_2._detect_foot_contact` 的「腳踝速度 ≤ 3.0 m/s」門檻是在偏高 19% 的速度下調的，改 dt 時要一併驗證門檻；179 FPS 影片在改之前速度只有實際約 4 成；(2) 播放時「正面/側面錯開 2 幀」（`pitch_widget.py` 雙影片模式 `frame_offset=2`、`video_widget_2.py` `setFrameOffset(-2)`）是否正確**尚未查明**：錄影是成對寫入、硬體同步，理論上應為 0。20261008_2249_P01 只有緩慢手部動作，互相關在 ±8 幀內無明顯峰值，無法判定；需重錄瞬間事件（拍手、球落地、敲桌）再分析。**不要直接把 2 乘以 3**
    - 2026-10-08 再測：`20261008_2258_P02`（60 FPS）手碰滑鼠，兩台**同在第 26 幀**接觸 → 幀差約 0（±1），支持「錯開 2 幀是錯的」。`20261008_2303_P01`（179 FPS）無瞬間事件，無法判定
    - 179 FPS 影片每約 3 幀有明暗起伏，推測為室內燈光 120 Hz 閃爍（179−120≈59 Hz），會干擾以影格差為基礎的自動分析
    - 牛棚資料（`N:\20261008 pitcher videos\Db\Record\`，60 FPS，CF 為正面、CS 為投手後方）`20261008_Pitcher02\20261008_1050_P02`：兩台**同在第 121 幀手臂伸直**、122 幀正面可見球離手 → 幀差約 0。與實驗室結果一致
    - **交接文件《交接程式操作文件_Ui_20260828》第 154 段只寫「雙影片預設 offset 為 2；單影片 offset 為 0」，沒有說明原因**；程式註解為「補償相機延遲」；git 歷史無紀錄（隨 2026-08-19 補存前人程式一起進來）
    - **推論的根本原因（未驗證）**：`FlirCameraSystem.get_grayscale_image` 在第一次取像時才 `BeginAcquisition`，`DualFlirSystem.get_grayscale_images` 先取正面再取側面 → 正面（自由拍攝、送觸發）先開始，側面（等觸發）較晚就緒，錯過的觸發讓正面暫存區多出 K 張，之後永久錯位 K 張，且 **K 每次開相機可能不同**。另外取像失敗時只重啟該台相機的擷取（line ~105），也可能中途改變錯位
    - **驗證方法**：scratchpad `startup_offset.py`（照主程式方式開相機 5 次，讀前 6 對 FrameID 比對）。2026-10-08 使用者已無接相機，待下次在實驗室執行
    - 若推論成立，正解是**先讓側面開始擷取、再讓正面開始**，而不是調 offset 數值
    - **影響範圍**：2D 生物力學數值只用側面影片，不受影響；靜態校正不受影響；**只影響 3D 配對**。使用者目前不用 3D → 暫不處理
- 「錄影幀率」選單重開程式會回到 60，使用者表示暫不需要記住設定
- **【更正 2026-10-09】60 FPS 是學姊刻意的設計**：論文（`Ms2026_Sports_ShihYiYing/Doc/Thesis/..._ThesisMs_20260824_V1B3_En_施邑穎.docx`）第 118 段「相機 180 fps，後續姿態估計以 30 fps 進行，先重新取樣以降低計算成本」；口試簡報（`TSG_Bullpen/..._20260707_V5B3_施邑穎_Oral_J.pptx`）第 5 頁：觸發後錄 180 frames (60fps)、FIFO 預錄 60 frames。原設計預錄 60 張（程式現為 45）。以下「本意是全幀率」的推論**是錯的**，保留供參考：
- （已推翻）`cv_control.py` 原註解「自动录影（不受采样影响，每帧都存）」→ 曾推論前人本意是全幀率；交接文件第 85 段寫「前 60 幀緩衝」但程式為 45、FPS 計算寫 30，三處不一致；第 269 段「錄影時無法顯示骨架，以降低錄影處理負載」→ 前人有考慮處理負載。未找到「模型只能吃 60 FPS」之類說明
- **179 FPS 改為預設前必須先驗證**（使用者要求：先尊重原設計，測試證明較好才換）：用真實投球的 179 FPS 影片測 (1) 一球分析時間；(2) 前腳著地／最大外旋／球離手是否都偵測得到、位置合理（分析程式有以「幀數」為單位、在 60 FPS 下調的門檻）；(3) 與同一球 60 FPS 比較能否抓到 60 漏掉的瞬間。任一項不行就維持預設 60
- 2D 分頁「更新速率」選單（`pitch_ui.comboBox`）是前人留下的介面，**沒有任何程式讀取它**，目前無作用

### [P3-002] 平板擷取 OCR 初始化失敗

- **登記日期**：2026-10-07
- **狀態**：`OPEN`
- **優先序**：低（不影響錄影與分析）
- **症狀**：每次錄完都印 `[TabletCapture] error: OCR init failed: 'type' object is not subscriptable`。
- **推測成因**：此訊息通常是 Python 3.9+ 型別寫法（如 `list[str]`）在 Python 3.8 執行所致，待查是 `capture_tablet_images.py` 還是其相依套件。
- **功能說明（2026-10-11 查證）**：錄完一球、影片載入回放時（`pitch_widget.checkVideoLoad` → `startTabletCapture(enable_ocr=True)`），從鏡頭（`tablet_source="0"`，例如 DroidCam）擷取 Rapsodo 平板畫面，裁出固定 ROI 做 OCR，結果顯示在 2D 分頁 Rapsodo 區並寫入 `*_BRandRapsodo.json`（學姊論文第 253 段）。
- **影響**：因 OCR 一直失敗，所有錄影的 `metrics`（球速、轉速、旋轉方向）**從來沒有讀到**，Rapsodo 整合功能實際上沒有運作，也連帶造成 P1-007
- `Db\Record\TabletCapture\` 永遠是空的：建立資料夾的程式還在，但存裁切圖的 `_save_crop` 呼叫已被註解（`capture_tablet_images.py:342`），可忽略或刪除
- **優先序待使用者決定**：取決於牛棚是否需要 Rapsodo 數據

### [P2-002] 未完成的架構遷移：新舊兩套推論後端並存

- **登記日期**：2026-09-22（分析 P1-002 時發現）
- **狀態**：`OPEN`
- **優先序**：中（現行舊架構可運作，新架構是效能優化性質）

學長姐曾進行一次**完整的推論後端更換**，四個組件中三個已完成、一個缺件，整體未啟用：

| 角色 | 現行（使用中） | 新版（已寫好但未啟用） |
|---|---|---|
| Model | `utils/model.py` | `utils/model_v1.py` |
| PoseEstimater | `skeleton/detect_skeleton.py`（505 行） | `skeleton/detect_skeleton_new.py`（1040 行） |
| 人物偵測 | mmdet `inference_detector` | ultralytics YOLO `.track()` |
| 姿態推論 | mmpose `inference_topdown`（PyTorch） | ONNX Runtime + TensorRT |
| 模型檔 | `Db/pretrain/vitpose_Sk26.pth` | `Db/pretrain/ViTPose_26kpts_fixed.onnx` ← **缺件** |

**注意命名陷阱**：這裡的 `model_v1.py` 是**新**版、`model.py` 是**舊**版，與 `UI_Control_v1`（舊版）的命名邏輯相反。

**新版對 Model 的額外要求**（現行 `model.py` 不滿足）：`run_pose()`、`pose_input_shape`、`detector.track()`。這些只有 `model_v1.py` 有。

**證據顯示新版曾實際運作過**：`Src/UI_Control/trt_cache/` 內有兩個約 330MB 的 TensorRT engine 檔（`*_sm120.engine`），是編譯後的推論引擎快取，代表 ONNX 模型確實被載入執行過。

**關於缺件**：`ViTPose_26kpts_fixed.onnx` 是姿態估測模型（26 關節點，對應 HALPE-26）。它應是由 `vitpose_Sk26.pth`（1.18GB，仍在）**轉檔**產生，而非另外訓練——專案內有 `Src/UI_Control/pth2onnx.py` 這支轉檔工具。因此若要啟用新架構，是「重新轉檔」而非「重新訓練」，成本可控。

**待評估**：是否要完成這次遷移。效益是推論速度（ONNX+TensorRT 通常顯著快於 PyTorch），成本是需重新轉檔、驗證分析結果一致性、並確認 ultralytics 追蹤行為與現行 mmdet 是否等價。

### [P2-001] 系統存在兩套互相矛盾的相機校正參數

- **登記日期**：2026-09-20
- **狀態**：`FIXED`（2026-10-07，待實機驗證）：3D 分頁改讀每筆錄影的 `calibration.json` 快照；無快照的舊錄影仍用寫死值
- **優先序**：中（影響 3D 重建精度，但 2D 分析不受影響）
- **症狀**：`stereo_calib.json` 與 `video_widget_2.py` 寫死的參數數值差異極大（正面內參 fx：1060.45 vs 7927.87，約 7.5 倍）。
- **成因**：`video_widget_2.py:193-233` 將 `K_F`、`K_S`、`F` 直接寫死在建構子中，且保留 5 組被註解的歷史版本 `F` 矩陣（僅 `#0505` 那組生效）。3D 分頁實際使用的是寫死的這套，不是 JSON 檔。
- **附帶發現**：產生 `stereo_calib.json` 內參的原始校正程式碼**已確認不存在**——搜尋過 git 全歷史（含已刪除的 `UI_Control_v1`）與 Google Drive 交接資料夾，均無任何 `stereoCalibrate` / `calibrateCamera` 呼叫。
- **可能解法**：與下方「內參重新校正」待辦一併處理，校正完成後改為統一從單一設定檔讀取。

### [P3-001] 正面攝影機畫面全黑

- **登記日期**：2026-09-10
- **狀態**：`OPEN`
- **優先序**：低（疑為硬體/場地因素，非程式問題）
- **症狀**：即時模式下正面攝影機（camera1, SN 25462483）畫面全黑。
- **推測成因**：50mm 長焦鏡頭視角窄，室內測試環境距離不足導致對焦/取景範圍不符。待實際場地測試確認。

### 已解決紀錄

| 編號 | 日期 | 問題 | 解法 |
|---|---|---|---|
| — | 2026-08-19 | 環境建置多項失敗（SSL 憑證、DLL、opencv 版本） | 見 [DEVELOPMENT.md](DEVELOPMENT.md) |
| — | 2026-09-10 | `main.py` 相機分頁呼叫參數不匹配 | 移除多餘的 `self.model` 參數 |
| — | 2026-09-10 | 雙相機無法同步擷取 | 確認需接 GPIO 同步線，接上後正常 |
| — | 2026-09-10 | `UI_Control_v1` 造成混淆 | 確認為實驗性沙盒且無引用，已移除 |

---

## 第三部分：待辦清單

### 進行中：2D 分頁問題修復（目前焦點）

- [ ] 修復 [P1-001] 單影片模式視角錯誤
- [ ] 修復 [P1-002] `person_df_by_frame` 崩潰
- [ ] 比對 `detect_skeleton.py` 與 `detect_skeleton_new.py` 差異，決定重構方向

### 程式碼健檢

- [ ] 檢查 `video_widget_2.py`、`video_widget_compare.py` 是否有類似的未完成重構痕跡
- [ ] 確認「人體骨架」「人物框」等 UI 選項是否真的有對應功能
- [ ] `protobuf` 版本衝突確認（`onnx` 要求 `>=3.20.2`，實裝 `3.20.1`），用到 `pth2onnx.py` 前需驗證

### 相機與校正

- [ ] 重新規劃並實作內參校正流程。2026-09-17 台鋼場勘確認新架構為 **8 台相機、4 種焦距**（8mm×4、6mm×2、35mm×1、25mm×1 備案），與舊的 3 台架構（50mm 正面 + 8mm 側面）完全不同。內參與鏡頭綁定，舊校正資料不適用，需重做並建立可追溯的流程。
- [ ] 解決 [P2-001] 參數來源統一問題
- [ ] 評估第二支側面相機整合（現有程式僅支援 `SN1`/`SN2` 兩台）

### GB10（MSI EdgeXpert / ARM64）— 已降級為次要任務

**2026-09-21 定位變更**：GB10 只作為「監看 5 支攝影機畫面」之用，**不跑骨架分析**。分析主力維持 Windows + RTX 4090（未來可能換 5090）。因此原本規劃的「整套 GUI 系統移植到 ARM64」不再需要，相關測試計畫文件已由使用者移出專案。

- [x] 確認 FLIR 有 ARM64 Linux 版 Spinnaker SDK — 廠商已提供 `spinnaker-4.4.0.246-noble-arm64-pkg.tar.gz`
- [ ] （暫停）其餘 GB10 相關驗證，待實際需要監看功能時再啟動

### 台鋼場地架設（未來，非當前工作）

場勘圖：`docs/TSG_Bullpen_Carmera_Positionpicture.jpg`

- [ ] **相機介面選型待決策**：場勘規劃線長 8～32 公尺（編號 1-3 至收線箱 1：30m/25m/22m；編號 4-8 至收線箱 2：32m/24m/19m/21m/8m），遠超過 USB3 被動線材約 3 公尺的極限。若改用 GigE 網路型相機，`camera_objects/single_camera/flir_camera_system.py` 需調整（GigE 的初始化參數、頻寬設定與同步機制皆與 USB3 不同，GPIO 實體線同步可能改為 PTP 網路時間同步）。若沿用 USB3 則需驗證主動式延長線或光纖延長方案在 179fps 下的穩定性。

---

## 附錄：硬體規格速查

**舊架構（論文，3 台）**
- 相機：FLIR Grasshopper3 GS3-U3-23S6C，1920×1084，179fps，BayerRG8
- 鏡頭：側面 KOWA LM8HC 8mm F/1.8 ×2；正面 KOWA LM50HC 50mm F/1.4 ×1
- 同步：GPIO 硬體觸發（camera1 Primary 輸出 `ExposureActive`，camera2 Secondary 由 `Line3` 接收）
- 序號：`SN1=25462483`（正面 camera1）、`SN2=25462481`（側面 camera2），寫死於 `cv_utils/cv_thread.py:68-69`

**新架構（2026-09-17 台鋼場勘，8 台）**
- 投手區：左右各 1 台 6mm、正上方 1 台 8mm、正後方 1 台 8mm、正前方 1 台 25mm（備案 35mm）
- 打擊區：左右各 1 台 8mm、正前方 1 台 35mm

**畫面左右對應**（`pitch_widget.py` → `FrameView` / `FrameView_2`）
- 左 = `FrameView` = `frame` = camera1 = **正面**
- 右 = `FrameView_2` = `frame_2` = camera2 = **側面** ← 分析只用這一路
