# 程式目錄與攝影機設定

2026-10-14 起，介面與運算／硬體模組分開。資料、錄影與既有校正仍存於 `Db/`。

| 目錄 | 用途 |
|---|---|
| `Src/UI_Control/` | `main.py`、`main_metric.py`、`calib.py`、`calibrate.py` 等 GUI 入口 |
| `Src/UI_Control/widgets/` | 相機、2D／3D 回放、量測、攝影機設定視窗 |
| `Src/UI_Control/forms/` | Qt Designer `.ui` 與對應 Python 介面 |
| `Src/UI_Control/rendering/`、`viewers/`、`controllers/` | 圖像繪製、3D 檢視、互動控制 |
| `Src/calibration/` | 內參／外參計算、校正資料儲存、棋盤與螢光球 CLI |
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
python Src/calibration/calibrate_chessboard.py
python Src/calibration/calibrate_chessboard_extrinsic.py --method both
python Src/tools/measure_stride_pnp.py
python Src/tools/measure_stride_8point.py
```

也支援 `python -m Src.UI_Control.main` 等模組入口。舊棋盤 CLI 路徑已搬移，請更新 IDE 的啟動設定；倉庫內的 imports、命令提示與文件已改用新位置。內部使用 `Src.*` 完整匯入，避免與第三方的 `utils`、`models` 混用。`main.py` 保持先載入 Qt／vispy 與模型，再載入攝影機 SDK 的順序，避免 Windows DLL 衝突。

## 曝光、焦距與顏色

1. 在「2D 相機」或「2D」分頁開啟相機；同一時間使用一個分頁擷取。
2. 按主視窗工具列的「攝影機設定（曝光／焦距／顏色）」，或從「攝影機」選單開啟。
3. 選正面 CF／側面 CS，核對序號，調整該列數值後按「套用」。視窗可與即時預覽並排使用。

曝光以微秒表示；上限同時受硬體範圍及目前設定 FPS 限制。顏色使用紅／藍白平衡，另提供增益。控制方式依據 [Spinnaker 官方 Python 指南](https://softwareservices.flir.com/Spinnaker/4.4/guides/python-programmer-guide.html)，範圍與可寫狀態由目前相機回報。

對焦位置與光學焦距是不同項目。只有鏡頭提供可寫的 `Focus`／`FocusPos`、`FocalLength` 節點時才開放，否則顯示不支援或唯讀，需手動調鏡頭環。程式不會改內參矩陣來模擬焦距。改焦距或對焦後請重新校正內參。

套用後會讀回硬體值，儲存到 `Src/cameras/config/GH3_camera_config.yaml`（正面）或 `GH3_camera_config_2.yaml`（側面）。鏡頭設定另外記錄序號，僅還原到相同相機。讀取／切換相機不寫入設定；存檔失敗會嘗試還原硬體值並顯示錯誤。錄影中停用調整，斷線後停止套用。原 2D 分頁的快速調色欄位共用相同後端。

## 驗證

```powershell
conda run -n Pitcher --no-capture-output python -m unittest discover -s Src/UI_Control/tests -p test_camera_settings.py -v
conda run -n Pitcher --no-capture-output python -m unittest discover -s Src/UI_Control/tests -p test_pitch_replay.py -v
```

相機設定測試使用模擬節點與暫存 YAML，不會改實體相機。實際鏡頭的電子對焦支援仍以連線後顯示為準。

本機測試請以 `-p test_*.py` 的具體檔名逐檔執行；將所有 Qt／OpenCV／torch 測試合併在同一 Python 程序，可能因 Windows DLL 載入順序發生 `WinError 182`。P05 實錄回歸另需搭配正確的錄影校正檔；目前本機影片為 1920×1084、現用棋盤內參為 1920×1080，會被尺寸檢查拒收。
