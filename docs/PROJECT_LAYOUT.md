# 程式目錄與攝影機設定

2026-10-14 起，介面與運算／硬體模組分開。資料、錄影與既有校正仍存於 `Db/`。

| 目錄 | 用途 |
|---|---|
| `Src/UI_Control/` | 主程式、內參／校正桿精靈、`calibrate_wood_frame.py` 木框 PnP、`manual_coordinates.py` 座標／尺度標註 |
| `Src/UI_Control/widgets/` | 相機、2D／3D 回放、量測、攝影機設定視窗 |
| `Src/UI_Control/forms/` | Qt Designer `.ui` 與對應 Python 介面 |
| `Src/UI_Control/rendering/`、`viewers/`、`controllers/` | 圖像繪製、3D 檢視、互動控制 |
| `Src/calibration/` | 內參／外參計算、木框 PnP、標註資料儲存、棋盤與獨立螢光球 CLI |
| `Src/cameras/` | FLIR 驅動、擷取／影片執行緒、硬體設定；YAML 位於 `config/` |
| `Src/pose/` | 現行模型、投手追蹤、骨架推論 |
| `Src/analysis/` | 動作分析、量測流程、分析結果讀寫 |
| `Src/rapsodo/` | 平板擷取、OCR V1／V2、好球帶工具 |
| `Src/tools/` | 抽幀、環境檢查、跨步量測 CLI |
| `Src/common/`、`Src/paths.py` | 共用計時／檔案工具、統一資源路徑 |
| `Src/legacy/` | 舊介面、重複校正工具與未啟用實驗；詳見[清單](../Src/legacy/README.md) |
| `Src/UI_Control/tests/` | 原有與新增回歸測試，位置保留 |

## 啟動與校正

在專案根目錄、啟用 `Pitcher` 環境後：

```powershell
python Src/UI_Control/main.py
python Src/UI_Control/calibrate.py
python Src/UI_Control/calibrate.py --wood-frame
python Src/UI_Control/calibrate.py --manual-coordinates
python Src/UI_Control/calibrate.py --two-rods
python Src/calibration/calibrate_chessboard.py
python Src/calibration/calibrate_chessboard_extrinsic.py --method both
python Src/tools/measure_stride_pnp.py
python Src/tools/measure_stride_8point.py
```

木框工具依拍攝指引收集約 15 組正面／側面照片，讀取既有內參，使用已確認的固定木框點位（紙板厚度 `0.3 cm = 3 mm`），計算各組品質並保存最佳外參。手動座標工具可另外選兩張照片、標記原點與 XYZ 正方向、標出尺度線段及輸入真實長度，再匯出／讀回 JSON。這些工具也可用各自的 `calibrate_wood_frame.py`、`manual_coordinates.py` 入口啟動；詳細資料格式與限制見 [CALIBRATION.md](CALIBRATION.md)。已移除舊十字架計算程式，獨立螢光球提取保留。

也支援 `python -m Src.UI_Control.main` 等模組入口。舊棋盤 CLI 路徑已搬移，請更新 IDE 的啟動設定；倉庫內的 imports、命令提示與文件已改用新位置。內部使用 `Src.*` 完整匯入，避免與第三方的 `utils`、`models` 混用。`main.py` 保持先載入 Qt／vispy 與模型，再載入攝影機 SDK 的順序，避免 Windows DLL 衝突。

## 曝光、焦距與顏色

1. 在「2D 相機」或「2D」分頁開啟相機；同一時間使用一個分頁擷取。
2. 按主視窗工具列的「攝影機設定（曝光／焦距／顏色）」，或從「攝影機」選單開啟。
3. 選正面 CF／側面 CS，核對序號，調整該列數值後按「套用」。視窗可與即時預覽並排使用。

曝光以微秒表示；上限同時受硬體範圍及目前設定 FPS 限制。顏色使用紅／藍白平衡，另提供增益。控制方式依據 [Spinnaker 官方 Python 指南](https://softwareservices.flir.com/Spinnaker/4.4/guides/python-programmer-guide.html)，範圍與可寫狀態由目前相機回報。

對焦位置與光學焦距是不同項目。只有鏡頭提供可寫的 `Focus`／`FocusPos`、`FocalLength` 節點時才開放，否則顯示不支援或唯讀，需手動調鏡頭環。程式不會改內參矩陣來模擬焦距。改焦距或對焦後請重新校正內參。

套用後會讀回硬體值，儲存到 `Src/cameras/config/GH3_camera_config.yaml`（正面）或 `GH3_camera_config_2.yaml`（側面）。鏡頭設定另外記錄序號，僅還原到相同相機。讀取／切換相機不寫入設定；存檔失敗會嘗試還原硬體值並顯示錯誤。錄影中停用調整，斷線後停止套用。原 2D 分頁的快速調色欄位共用相同後端。

## 驗證

2D 回放左側保留原始影片，右側預設顯示已儲存的人體骨架；「重新分析」完成後自動儲存 JSON，不需要另外儲存。兩頁均可點選 FC／MER／BR 標題按鈕或縮圖，暫停並跳到對應幀，影片框大小保持不變。3D 統一使用「載入影片」：選取同一次投球的 CF 或 CS 原始影片後，自動配對另一視角，沿用相同來源、校正、骨架與幀偏移的完整分析；找不到可用結果時逐幀分析並自動儲存，再開始回放。播放與拖曳不會觸發辨識或改寫 JSON。

3D 的「校正設定」可調整校正方式、校正檔與同步偏移；按「套用設定」後重新檢查快取。「關鍵幀」頁可把目前畫面設為 FC、MER 或 BR，修正後自動儲存，並檢查 FC < MER < BR。按「重新分析」會清除手動關鍵幀，以新判定結果寫入 JSON。

2D 與 3D 共用 `Src/analysis/pitch_events.py`：從 CS 側面骨架估計前腳支撐高度，FC 須接近地面且連續穩定；MER 只在抬臂、手腕高於肩部及手肘的後擺階段尋找最大值，排除低手後擺。左右投使用對應關節，閾值依腿長與 FPS 調整。MER／BR 是影像動作候選幀，不等同於直接量測肩關節旋轉或球離手。P02、P03、P04 作為本次回歸資料；P05 依使用者說明排除。

兩頁骨架共用繪圖方法與信心門檻。3D 優先讀取同一份 2D JSON；`pose_replay.py` 將已儲存的回放幀號轉回來源幀，再套用目前偏移。新分析會附帶每部影片的 `_pose_timeline.json`，避免單影片與雙影片的舊事件檔混淆骨架幀號。2D 骨架更新後，3D 快取會失效並重新分析。公制重建仍需要影片尺寸與校正內參一致，尺寸不同會顯示實際值並保留原有資料，不會自動拉伸影像套用校正。

```powershell
conda run -n Pitcher --no-capture-output python -m unittest discover -s Src/UI_Control/tests -p test_camera_settings.py -v
conda run -n Pitcher --no-capture-output python -m unittest discover -s Src/UI_Control/tests -p test_pitch_replay.py -v
conda run -n Pitcher --no-capture-output python -m unittest Src.UI_Control.tests.test_pitch_events Src.UI_Control.tests.test_video_3d_replay -v
```

相機設定測試使用模擬節點與暫存 YAML，不會改實體相機。實際鏡頭的電子對焦支援仍以連線後顯示為準。

本機測試請以 `-p test_*.py` 的具體檔名逐檔執行；將所有 Qt／OpenCV／torch 測試合併在同一 Python 程序，可能因 Windows DLL 載入順序發生 `WinError 182`。P05 實錄回歸另需搭配正確的錄影校正檔；目前本機影片為 1920×1084、現用棋盤內參為 1920×1080，會被尺寸檢查拒收。
