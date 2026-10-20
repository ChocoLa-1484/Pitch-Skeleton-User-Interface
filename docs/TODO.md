# 專案工作區（AI 協作規範 + 問題追蹤 + 待辦）

> **這份文件給 AI 助手看**（Claude / GPT / 其他模型）。人類閱讀的白話說明請見 [DEVELOPMENT.md](DEVELOPMENT.md)。
> 最後更新：2026-10-09

---

## 【當前工作交接狀態】

> **接手的 AI 請先讀這一段。** 這裡記錄「上一位助手做到哪、下一步該做什麼」，每完成一個階段性步驟就會更新。
> 若此區塊顯示「無進行中任務」，代表上一段工作已告一段落，可直接從下方待辦清單挑選。

**更新時間**：2026-10-14

**【校正中心與 3D 座標修正（2026-10-20，使用者同意）】**
- 使用者目前主軸：**實作 RoMa 外參**。其他校正方法保留作為比較基準（學姊校正桿 01/02 已整合進 calibrate.py；03_3d.py 是 baseline 驗證，保留）。
- 新增 `Src/calibration/calibration_center.py`（`start_calibrate.bat` 開啟）：內參（即時拍攝／照片資料夾）與外參（RoMa、校正桿手點、03 驗證、棋盤格 PnP／8Points、十字架）在同一視窗點選；RoMa 在視窗內以 Roma 環境執行。`start_roma.bat` 與 tkinter 版 RoMa 視窗刪除（功能已在校正中心）。測試 `test_calibration_center.py`。
- 3D 舊版估算一律以側面相機為原點（PR #4 改成 CF 在前後原點變正面，視角差約 90°）：viewer 內部對調輸入，與學姊原本計算差異 0.0；舊快取自動重算。
- P1-012（關鍵幀判定成功率 35/77）：使用者同意交給同學調整，統計表在 `local/extrinsic/out/events_survey.csv`。

**【PR #4 合併（2026-10-20）】**
- 同學 ChocoLa-1484 的 PR #4（標題「fix main.py」，實際是 2D／3D／回放比較三頁重構）：新增 `Src/analysis/pitch_events.py`（2D、3D 共用的 FC／MER／BR 判定）、`pose_replay.py`（已存骨架的幀號換算）、比較頁選影片對話框、關鍵幀跳轉；3D 統一「載入影片」，CF 一律為第一支，舊版估算模式交換內參並轉置 F（數學正確）。
- 衝突兩處：`video_widget_2.py` 採用 PR #4 新版面並保留「預設舊版估算」與 `chessboard_cross` 新路徑；PR #4 對 `PROJECT_LAYOUT.md` 的補充併入 `DEVELOPMENT.md` 最後一節。
- 驗證：16 組測試逐檔全部通過（155 項，含 PR #4 新增的 4 組）；主程式無畫面模式可啟動。

**【PR #3 合併與校正整理定案（2026-10-14，使用者同意）】**
- 採用 PR #3 的分層架構；main 上 PR #3 之後的修改逐項移植：3D 分頁選 CS 或 CF 都自動載入兩支（`Src/cameras/video_control.py`）、內參鏡頭焦距檢查與長焦鎖畸變（`calib.py`／`calibrate.py`）、`calib_store.save_fundamental(extra=)`、RoMa 工具與視窗版、`start_roma.bat`、文件與 TODO。
- 校正程式依使用者要求集中到 `Src/calibration/`：`calibrate.py`、`calib.py`（原 PR #3 放在 `UI_Control/`）、共用 `calib_store.py`／`calibration_workflow.py`、`chessboard_cross/`（同學的棋盤格／十字架／螢光球）、`roma/`。5 支舊設備工具刪除；學姊 01／02／03 留在 `Src/legacy/calibration/`。
- `calibrate.py` 新增「只做內參」：兩台內參完成即發布內參與角色（`publish_intrinsics`，同樣先備份、失敗還原），不動既有外參；外參改用 RoMa。
- `docs/PROJECT_LAYOUT.md` 併入 `DEVELOPMENT.md`〈程式目錄與攝影機設定〉。逐檔測試全部通過（新增 publish_intrinsics 測試）；各 CLI 從專案根目錄 `--help` 正常、主程式無畫面模式可啟動、RoMa 視窗版實跑正常。
- 本機已用 10/08 牛棚 142 對棋盤照片產生 `Db/Calibration/chessboard/{pnp,8point}/stereo.json`（1920×1084），只適用 10/08 牛棚那組相機位置。
- 2026-10-14 使用者同意後完成：3D 分頁「3D 外參」預設改回學姊方法（舊版估算，讀 calibration.json）；內參工具在板子傾斜轉軸過度集中（集中度 > 0.8）時警告；新增 `start_calibrate.bat`。

**UI_Control 分層與主程式攝影機設定（2026-10-14，完成）**
- 使用者授權依功能分資料夾、更新路徑，將舊版／重複功能獨立保留，並在 main.py 加入曝光、焦距與顏色設定。
- 已遷移至 Src/{calibration,cameras,analysis,pose,rapsodo,common,tools,legacy}；UI 留下入口、widgets/forms/rendering/viewers/controllers。統一 Src.* 匯入與 Src/paths.py 資源路徑，舊目錄只剩快取的部分已清理，trt_cache 保留。
- 使用者原有 calibrate_cross.py、chessboard_metric.py 預設資料集與 CALIBRATION.md 照片表格修改保留；遷移前快照位於系統暫存 pitch_ui_migration。
- 共用硬體控制／主程式工具列及設定視窗已完成，10 項模擬硬體／Qt 測試通過；曝光、增益、白平衡讀回再保存，存檔失敗還原，鏡頭依可寫節點開放，錄影／斷線禁止套用。未改相機內參矩陣模擬焦距。
- 逐檔測試共 119 項：118 通過，1 項 P05 實錄仍因影片 1920×1084 與目前內參 1920×1080 不符而拒收（既有資料問題）。校正／照片／球偵測／量測、投手選人、2D 回放與 3D 合成測試通過。逐檔日誌在 local/layout_validation/；全套同程序執行會遇到本機 OpenCV／Qt／torch DLL 順序限制 WinError 182，應逐檔測試。
- 主視窗四頁與設定入口完成 smoke（模型／相機模擬，未推論或控制實體鏡頭）、main_metric 輕量模式啟動成功；9 個 CLI 以其他工作目錄啟動 --help 通過，活動模組 import／字型／設定資源路徑檢查通過。操作與新目錄見 docs/DEVELOPMENT.md〈程式目錄與攝影機設定〉，舊版與缺件清單見 Src/legacy/README.md。
- 使用者 2026-10-14 要求直接延續，沒有待核准事項。下一步由使用者連相機核對實際可寫控制；手動鏡頭不支援電子焦距／對焦，需手動調鏡頭環。實體距離量測仍需與錄影匹配的校正檔。

**【棋盤格內參／外參／量測拆分（2026-10-13）】**
- 使用者要求分開棋盤格內外參。內參保留 calibrate_chessboard.py；新增 calibrate_chessboard_extrinsic.py，提供 pnp／8point／both，讀取既有內參與角點，或用 --images 另讀同規格同步棋盤照片。輸出沿用 metric_stereo_v1，無需投球影片。
- 量測入口改為只讀既有 stereo.json，缺外參時提示先執行新命令，不再暗中重算。外參解算與 load_rig 抽至 cv_utils/chessboard_extrinsic.py；原 metric_session.load_rig 呼叫仍相容。
- 進場已有使用者修改的 calibrate_cross.py、chessboard_metric.py 預設路徑與 CALIBRATION.md 十字架照片路徑，保留。
- 驗證：test_chessboard_metric 16 項、test_chessboard_extrinsic 3 項、test_metric_session 12 項通過；新 CLI 合成幾何驗證兩種方法均恢復已知 R／t，不需影片，來源內參位元組保持不變。量測 CLI 用已存外參完成實際暫存影片／點位測試，不重算外參。--help 與 diff 檢查正常。
- test_video_3d_replay 13 項通過、1 項本機 P05 測試失敗：影片為 1920×1084，而現存 Db/Calibration/chessboard/pnp/stereo.json 的 CF 內參為 1920×1080；拒收發生在既有 video_widget_2 尺寸檢查，load_rig 搬移前後 AST 完全一致。未改校正值或影片以繞過檢查。
- 操作文件已更新為內參 → 獨立外參 → 量測。後續若分析 P05，須指定與該錄影相符的校正檔；本輪未重算正式校正資料。

**【2026-10-13 本輪完成（使用者同意）】**
- PR #2（同學：照片校正內參、SB 旗標修正、單一投手鎖定）已合併；`calib.py` 衝突保留雙方功能，同學的 P1-009 改編號為 P1-011。
- 3D 分頁選 CS 或 CF 都自動載入兩支，順序固定為 CS→CF（第一支一定當側面套 K_S）。
- 內參工具新增「鏡頭焦距」：fx 與焦距推算值差 15% 以上警告；≥ 25 mm 自動鎖畸變。
- 校正程式整理（2026-10-14 隨 PR #3 定案）：集中到 `Src/calibration/`（`calibrate.py`、`calib.py`、`chessboard_cross/`、`roma/`），5 支舊設備工具刪除，學姊 01／02／03 在 `Src/legacy/calibration/`。
- 新增 `Src/calibration/roma/roma_extrinsic.py`（使用者要求推上 GitHub，下週牛棚實測）：Roma 環境安裝與用法見 `docs/CALIBRATION.md` 第 7 節。研究實驗數據仍只記在本機 `local/`。
- 待使用者決定：3D 分頁「3D 外參」下拉選單預設改讀 `calibration.json`；校正工具加入「板子都朝同一方向轉」警告。

**【SB 偵測失敗原因查證（2026-10-13）】**
- 使用者確認修改後，已修正 SB 的 `flags=` 傳法並加入失敗時 EXHAUSTIVE 重試，成功視角記錄實際 detector／flags；未重算／覆寫現用內參。
- 根因見 P1-010：`chessboard_metric.detect_folder` 把旗標放在 SB 的第三個位置參數（corners），應明確傳 `flags=`。
- Pitcher 環境（本次 OpenCV 回報 5.0.0）實測 `Calibrate_Picture_Chessboard` 各 87 張：目前寫法 CF 80／CS 8；改用 `flags=CALIB_CB_NORMALIZE_IMAGE` 測試 CF 87／CS 85；傳統方法加 adaptive/normalize 均 87。角點成功率不代表已驗證內參／3D 精度。
- 已用正式 detect_folder 重驗整批：CF 87/87、CS 87/87（CS 有 2 張經 EXHAUSTIVE 重試成功）；16 項 test_chessboard_metric.py 測試通過，涵蓋旗標關鍵字、只重試失敗照片、兩次失敗才列入失敗名單。驗證不產出新內參或更新既有 detections.json；文件已提醒首次套用勿使用 --reuse-detections。

**【calib.py 從照片計算內參（2026-10-13）】**
- 使用者確認需求是讀取資料夾的棋盤格照片，再計算內參；不是載入既有 JSON／NPZ。
- 已加入單台照片資料夾匯入與相機序號欄位，沿用現有角點精修、品質評估及儲存流程。中文路徑可讀，尺寸不符／無角點／相鄰重複照片略過，取消或整批無效保留原樣本，成功才取代。即時相機與照片不可混用；改棋盤格規格後必須重取樣。
- 精靈沿用的 Collector 隱藏照片匯入，避免繞過其 session 原始影像紀錄。19 項 `test_calibration*.py` 測試通過，含合成棋盤圖的實際角點偵測、中文路徑、尺寸／壞檔／重複略過、取消保留樣本及離線寫出 JSON／NPZ。測試只寫暫存資料夾，現用校正檔未改動；照片校正精度仍依實際樣本品質決定。操作說明已補入 `docs/CALIBRATION.md`。

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

**2026-10-08 正面（SN24380115，50 mm）內參失準的原因分析（2026-10-12）**

- 結果：fx = 12463（50 mm 理論值約 8532，學姊舊值 7928）、k1 = 1.01、k2 = 33.5（50 mm 應接近 0）；RMS 0.17 仍被判「良好」→ RMS 檢查不出這種錯誤。同學十字架 PnP 換成理論 fx 後，相機距離由 31.9 m 變 21.8 m，與實測約 22 m 吻合。
- **已排除：正方形棋盤（7×7）不是原因。** 正方形棋盤的風險是角點順序轉向或鏡像，但那會讓該張誤差達數十 px；實際每張最大只有 0.43 px。
- 主因（2026-10-13 模擬驗證）：板子**只朝同一個方向轉**、且傾斜小（28 張中 23 張 < 20°）。使用者推測當時為了讓兩台相機都看到棋盤而刻意朝固定方向擺；合成 50 mm 資料重現：只繞單一軸傾斜 3–16°、角點雜訊 0.5 px 時 fx 誤差 +6%～+79%，多方向傾斜則多在 ±3% 內（理論上稱為「平行旋轉軸」退化）。鎖住畸變對 fx 幾乎沒有影響，但能避免 k2 = 33.5 這類不合理的畸變係數。
- 重新校正時：**內參與外參分開拍**，內參只顧這台相機，板子上下、左右、斜向都要轉，至少一半傾斜 30～45° 以上；長焦鏡頭填入焦距（≥ 25 mm 自動鎖畸變，fx 與焦距推算值差 15% 以上會警告）；用捲尺量的實際距離驗算。校正請用 `calibrate.py`（會保存原始照片），舊的 `calib.py` 不存照片。

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
> 給接手的 AI：使用者的個人研究細節放在本機 `local/RESEARCH_NOTES.md`（不進 git），**研究內容不要寫進這份檔案或推上 GitHub**。

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

### [P1-009] 2026-05-05 錄影的 3D 使用了相機移動前的 F

- **登記日期**：2026-10-12
- **狀態**：已修正（2026-10-12，使用者同意）：本機 `Db\Record\20260505_Pitcher01～03` 共 30 個錄影資料夾已寫入 `calibration.json`；程式預設值與學姊雲端原始資料未改。
- **症狀**：`video_widget_2.py` 內建的預設 F（標註 `#0505`）與 5/5 錄影的相機擺位不符；沒有 `calibration.json` 的舊錄影在 3D 分頁都使用這組 F。
- **查證**（校正照片 `N:\Bullpen_Data\TSG\Pitcher\TSGBullpen_Calibrate_Picture\`，20 對、每對 6 個手點）：
  - 照片分兩次拍：01–10 約 11:27、11–20 約 13:21。兩次之間 cs 相機背景位移約 290 px、cf 約 5–10 px（SIFT 背景單應比對），是兩種擺位。
  - 程式 F 依程式慣例（`E = K_Rᵀ F K_L`，L＝側面）換算後，在 01–10 手點上對極誤差中位數 1.6 px（吻合），在 11–20 上 54.7 px（不吻合）。
  - 5/5 的 Pitcher01～03 錄影（13:45–14:09）背景與 11–20 吻合（1–3 px），與 01–10 差約 300 px → 錄影是在相機移動後拍的，卻用了移動前的 F。
  - `fundamental matrix.txt`（內點 22/120）是兩次擺位的照片混在一起算的，也不能用。
- **修正方式**：用 11–20 的手點重算 F（樣本內 1.27 px、留一對交叉驗證 1.71 px），以 `calibration.json` 校正快照寫入 5/5 錄影資料夾（只動本機 `Db\Record` 工作複本，不改學姊雲端原始資料、不改程式預設值）。
- **修正後檢查**（7 筆有兩視角骨架的錄影）：2D 骨架點對 F 的對極誤差中位數 55.8 px → 14.6 px；3D 骨架內部形狀與關節角度變化很小（手肘、膝蓋角度差中位數 0.65°、90% 2.2°），影響主要在骨架整體方位。兩視角影格錯開 0 格時誤差最小，同步正常。
- **校正照片位置**：使用者已於 2026-10-12 從 Ncku 移到 `N:\Bullpen_Data\TSG\Pitcher\TSGBullpen_Calibrate_Picture\`。
- **附帶發現**：比對 F 時必須注意方向慣例（cf→cs 或 cs→cf），轉置錯誤會讓正確的 F 看起來差幾十 px。

### [P1-008] 校正取樣角點維度越界，重新掃描相機缺少結果提示

- **登記日期**：2026-10-09
- **狀態**：已修正；13 項回歸測試通過，實際重掃找到兩台相機，待現場棋盤格取樣驗證。
- **症狀**：`calibrate.py` 按空白鍵取樣，經 `Collector.capture_sample()` 進入 `calib.py` 第 328 行，出現 `numpy.AxisError: axis 2 is out of bounds for array of dimension 2`。使用者另回報重新掃描不能用。
- **成因**：重複樣本判斷固定用 `axis=2`，未統一角點的 `(N, 2)`／`(N, 1, 2)` 形狀；兩種形狀混用還會廣播成所有點兩兩比較，漏掉重複樣本。精靈掃描無成功／零台提示，並把舊校正序號與線上序號合併且保留舊選擇；本機 SDK 連續兩次掃描均成功，尚無 SDK 失效證據。
- **影響範圍**：單台 `calib.py` 與整合 `calibrate.py` 的棋盤格取樣；整合工具設定頁的相機重掃與角色預選。
- **修正**：統一角點形狀後逐點算位移，角點數不符時拒收；重掃顯示本次結果，連線序號優先，保留可用角色及離線匯入用途。測試見 `Src/UI_Control/tests/test_calibration_capture.py`。

### [P1-012] 投球事件判定（PR #4 的 pitch_events.py）在多數錄影找不到 FC／MER／BR

- **登記日期**：2026-10-20
- **狀態**：已查證，待決定由誰調整（演算法作者為同學 ChocoLa-1484，PR #4 只用 P02～P04 回歸）。
- **統計**（讀既有 CS 骨架 JSON、fps 取自影片；77 球）：

| 資料 | 全部找到 | 找不到抬腿 | 找不到 FC | 找不到 MER | 找不到 BR |
|---|---|---|---|---|---|
| 5/5 台鋼（30 球） | 24 | 0 | 5 | 0 | 1 |
| 3/27 台鋼（33 球） | 6 | 6 | 18 | 3 | 0 |
| 10/08 成大（14 球） | 5 | 8 | 1 | 0 | 0 |

- 失敗球的骨架檔都只有一人、無重複幀，排除資料格式問題。
- **FC 失敗例（5/5 P01）**：FC 要求前腳踝連續 3 幀晃動 < 腿長 4%（約 13 px），且只在第一個抬臂幀之前找；此投手前腳在側面畫面滑動到第 77 幀，抬臂也從第 77 幀開始，搜尋範圍在腳踩穩前就結束。MER、BR 必須先有 FC，所以三個都顯示「未找到」。
- 「找不到抬腿」：要求膝蓋抬到接近髖部高度（髖 y − 膝 y > −0.2 × 腿長），快投或抬腿較低的投法不符合。
- 錄影 fps 記錄為 64～78（P2-003），判定的時間門檻依 fps 換算。
- 逐球結果：本機 `local/extrinsic/out/events_survey.csv`；統計程式 `local/extrinsic/events_survey.py`。

### [P1-011] 骨架目標逐幀切換至旁人

- **登記日期**：2026-10-13
- **狀態**：`FIXED`（選人邏輯及回歸測試完成；待現場確認）
- **症狀／成因**：`detect_skeleton.py` 逐幀挑最大框、依偵測排序編號；手動選 ID 1 實際只保留當幀第一個候選，不能鎖定同一個人。空框仍送入 MMPose 全圖推論。
- **影響範圍**：使用主線 `PoseEstimater` 的即時與離線新骨架推論；已存 JSON 不受影響。
- **修正**：`pitcher_target.py` 起始只接受框中心 x=25–75%、y=15–85% 的人物，距畫面中心最近者優先；框面積至少全圖 0.5%、高度至少 10%。鎖定後以位移、IoU、面積變化配對，不再依排列編號或每幀最大框選人，允許跨步離開起始區。漏偵測輸出空結果，連續 15 幀後解除鎖定、下一幀再由中央取得目標；不推論舊框。只輸出一人，邏輯 ID 固定 1。
- **限制**：空間配對不是外觀辨識；兩人高度重疊仍可能混淆，低信心關節與動作模糊也需另行評估。

### [P1-010] SB 旗標誤傳到 corners 參數，造成大量棋盤格漏偵測

- **登記日期**：2026-10-13
- **狀態**：`FIXED`（16 項回歸測試通過，正式偵測流程 CF／CS 各 87/87 成功）
- **成因／影響**：`cv_utils/chessboard_metric.py` 的 `findChessboardCornersSB(gray, pattern, cv2.CALIB_CB_NORMALIZE_IMAGE)` 在目前 Python binding 中第三參數是輸出 corners，而非 flags，正規化未按預期啟用；影響 `calibrate_chessboard.py` 的照片篩選。`calib.py` 使用傳統角點偵測，不走此呼叫。
- **驗證**：同一批 CF／CS 各 87 張，在暫存診斷腳本中改成明確 `flags=` 後，SB 成功數由 80／8 提升到 87／85；剩餘兩張加 EXHAUSTIVE 均成功。未更新原 detections.json、intrinsics.json。

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
    - 牛棚資料（`N:\Bullpen_Data\Ncku\Baseball_pitcher_data\NckuBullpen_20261008_pitcher_videos\Record\`，60 FPS，CF 為正面、CS 為投手後方）`20261008_Pitcher02\20261008_1050_P02`：兩台**同在第 121 幀手臂伸直**、122 幀正面可見球離手 → 幀差約 0。與實驗室結果一致
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

**關於缺件**：`ViTPose_26kpts_fixed.onnx` 是姿態估測模型（26 關節點，對應 HALPE-26）。它應是由 `vitpose_Sk26.pth`（1.18GB，仍在）**轉檔**產生，而非另外訓練——專案內有 `Src/legacy/experiments/pth2onnx.py` 這支轉檔工具。因此若要啟用新架構，是「重新轉檔」而非「重新訓練」，成本可控。

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

### 顯卡升級至 RTX 50 系列（2026-10-11 使用者提出，之後再處理）

分析主機未來可能換 5090。**現行環境（Python 3.8 + PyTorch 2.0.1 + CUDA 11.8）無法在 50 系列執行**（Blackwell 需 CUDA 12.8 + PyTorch ≥ 2.7，未實機驗證）。40 系列以前的卡沿用現行環境即可。

- [ ] 升級牽動整套環境：PyTorch 2.7 不支援 Python 3.8 → 改 3.10；mmcv 無對應預編譯版本需自行編譯；PySpin 需換新版 Spinnaker 的 cp310 wheel
- [ ] 另開分支在新卡上重建環境並更新 `environment/` 鎖定檔；升級後須比對骨架、關鍵幀結果與舊環境一致

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
