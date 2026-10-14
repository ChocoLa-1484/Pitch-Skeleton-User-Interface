# Rapsodo Pitching OCR

從 Rapsodo PRO 2.0 的 iPad 畫面擷取投手數據並輸出 JSON：球速（MPH）、總旋轉數（RPM）、旋轉方向（HH:MM）、旋轉效率（%）。支援兩種來源：事後處理 mp4 影片，或透過擷取卡即時讀取。

## 環境需求

- Windows 10 / 11，Python 3.10 以上
- Tesseract OCR（<https://github.com/UB-Mannheim/tesseract/wiki>），確認 `C:\Program Files\Tesseract-OCR\tesseract.exe` 存在，或將其加入 PATH
- 建議 NVIDIA GPU（EasyOCR 會自動使用 CUDA 加速，沒有 GPU 也能跑，只是較慢）
- 即時模式需要擷取卡（開發環境使用 AVerMedia BU110）

## 安裝

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
tesseract --version
```

## 執行

影片模式，處理 mp4 檔：

```bash
python tools/video_player.py
```

啟動後開啟 PyQt5 GUI：選擇影片 → 輸入球員名稱 → 開始處理，每球辨識完畢會即時寫入 JSON。

即時擷取卡模式：

```bash
python main.py --mode pitching
```

確認擷取卡已接好並能看到 Rapsodo 畫面。程式會開預覽視窗，按 `n` 切換下一位球員、`q` 結束。

## 運作方式

程式以 Rapsodo 左上角的球數計數器（例 `3/5`）作為觸發點：計數器變化 → 等畫面穩定 → 擷取 3 張連續穩定幀 → 對 4 個數據欄位並行 OCR → majority vote → 寫入 JSON。以 299 秒影片為例，「最快速」模式約 4 分 26 秒，「1x realtime」約 5 分 55 秒（時間隨 GPU / CPU 規格變動）。

OCR 使用 Tesseract + EasyOCR 雙引擎交叉驗證。只要兩邊信心不足或互相衝突，該欄位會寫 `null` — 寧願空值也不會輸出錯誤數值。

## 輸出格式

每次執行會產生一個新 session，檔名 `rapsodo_data_pitching_<timestamp>.json`：

```json
{
  "session_id": "20260417_012341",
  "mode": "pitching",
  "source": "video",
  "video_file": "Rapsodo_Pitcher_20260412_V1.mp4",
  "last_updated": "2026-04-17T01:27:52",
  "current_player": "1",
  "entries": [
    {
      "entry_id": 1,
      "player": "1",
      "timestamp": "2026-04-17T01:23:47",
      "velocity_mph": 69.8,
      "total_spin_rpm": 1795,
      "spin_direction": "12:58",
      "spin_efficiency_pct": 76.1
    }
  ]
}
```

欄位：

- `velocity_mph` — float 或 null，MPH，一位小數
- `total_spin_rpm` — int 或 null，RPM
- `spin_direction` — string 或 null，時鐘格式 `HH:MM`
- `spin_efficiency_pct` — float 或 null，百分比，一位小數

null 代表 Rapsodo 本身未顯示（例如變化球 spin 偶爾空白）或 OCR 雙引擎互不同意。

輸出路徑：影片模式 `C:\rapsodo-metrics-ocr\rapsodo_data_pitching_<timestamp>.json`、即時模式 `C:\rapsodo-metrics-ocr\rapsodo_data_pitching.json`。可在 [config.py](config.py) 的 `OUTPUT_JSON_PITCHING` 調整。

## 專案結構

- [main.py](main.py) — 即時擷取卡模式入口
- [config.py](config.py) — ROI、驗證範圍、輸出路徑等常數
- [models.py](models.py) — BaseMetrics / RelativeROI dataclass
- [requirements.txt](requirements.txt) — pip 相依套件
- [core/capture.py](core/capture.py) — 擷取卡串流封裝
- [core/change_detector.py](core/change_detector.py) — 球數計數器變化偵測
- [core/data_writer.py](core/data_writer.py) — JSON 原子寫入
- [core/screen_detector.py](core/screen_detector.py) — iPad 螢幕角點偵測 + 透視校正
- [core/session_manager.py](core/session_manager.py) — 多球員 session 管理
- [modes/base_ocr_reader.py](modes/base_ocr_reader.py) — Tesseract + EasyOCR 雙引擎 pipeline
- [modes/pitching.py](modes/pitching.py) — 投手 OCR reader
- [tools/video_player.py](tools/video_player.py) — 影片模式 PyQt5 GUI
- [tools/detect_devices.py](tools/detect_devices.py) — 列出擷取卡裝置
