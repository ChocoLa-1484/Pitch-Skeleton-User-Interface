# Rapsodo Pitching OCR

從 Rapsodo PRO 2.0 的 iPad 畫面擷取投手數據並輸出 JSON：球速（mph 或 kph）、總旋轉數（RPM）、旋轉方向（HH:MM）、旋轉效率（%）、垂直位移 V-BREAK（inch 或 cm）、水平位移 H-BREAK（inch 或 cm）。支援兩種來源：事後處理 mp4 影片，或透過擷取卡即時讀取。

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

### 影片模式（處理 mp4 檔）

```bash
python tools/video_player.py
```

啟動後開啟 PyQt5 GUI：
1. 點「開啟影片」
2. 依序選擇 **source 球速單位**（Rapsodo 畫面顯示的單位）、**output 球速單位**（JSON 要寫的單位）
3. 依序選擇 **source V/H 位移單位**、**output V/H 位移單位**
4. 選擇 mp4 檔案 → 輸入球員姓名 → 開始處理
5. 每球辨識完畢會即時寫入 JSON，GUI 右側顯示當前球、History 區列出累積紀錄

**關於單位**：source 必須和 Rapsodo 畫面顯示一致，否則 OCR 值會被範圍守門擋掉或誤寫入。output 如果和 source 不同，系統會自動換算（NIST 標準常數）。

### 即時擷取卡模式

```bash
# 預設 mph + inch：Rapsodo 顯示、JSON 都是 mph + in
python main.py

# 台灣 Rapsodo 顯示 kph + cm，JSON 也要 kph + cm（不換算）
python main.py --source-velocity-unit kph --source-break-unit cm

# 台灣 Rapsodo 顯示 kph + cm，但 JSON 要輸出 mph + in（自動換算）
python main.py --source-velocity-unit kph --output-velocity-unit mph \
               --source-break-unit cm --output-break-unit in
```

確認擷取卡已接好並能看到 Rapsodo 畫面。程式會開預覽視窗，按 `n` 切換下一位球員、`q` 結束。

## 運作方式

程式以 Rapsodo 左上角的球數計數器（例 `3/5`）作為觸發點：計數器變化 → 等畫面穩定 → 擷取 3 張連續穩定幀 → 對 6 個數據欄位（球速 / 總旋轉數 / 旋轉方向 / 旋轉效率 / V-BREAK / H-BREAK）並行 OCR → majority vote → 寫入 JSON。以 299 秒影片為例，「最快速」模式約 4 分 26 秒，「1x realtime」約 5 分 55 秒（時間隨 GPU / CPU 規格變動）。

OCR 使用 Tesseract + EasyOCR 雙引擎交叉驗證。只要兩邊信心不足或互相衝突，該欄位會寫 `null` — 寧願空值也不會輸出錯誤數值。

單位處理拆為兩層：
- **source** = Rapsodo iPad UI 顯示單位、OCR 讀到的原始數字 → 決定 VALIDATION 範圍守門
- **output** = JSON 寫出單位 → 決定 JSON key 尾綴（`velocity_mph` ↔ `velocity_kph`、`v_break_in` ↔ `v_break_cm`、H 同理）
- 若 source = output，直接寫入（byte-identical）；若不同，DataWriter 寫入前用 NIST 換算常數乘一次

## 輸出格式

每次執行會產生一個新 session，檔名 `rapsodo_data_pitching_<timestamp>.json`：

```json
{
  "session_id": "20260424_012341",
  "mode": "pitching",
  "source": "video",
  "video_file": "Rapsodo_Pitcher_20260412_V1.mp4",
  "last_updated": "2026-04-24T01:27:52",
  "current_player": "1",
  "entries": [
    {
      "entry_id": 1,
      "player": "1",
      "timestamp": "2026-04-24T01:23:47",
      "velocity_mph(球速)": 69.8,
      "total_spin_rpm(旋轉數)": 1795,
      "spin_direction(旋轉方向)": "12:58",
      "spin_efficiency_pct(旋轉效率)": 76.1,
      "v_break_in(垂直位移)": -5.9,
      "h_break_in(水平位移)": 17.0
    }
  ]
}
```

欄位：

- `velocity_mph(球速)` 或 `velocity_kph(球速)` — float 或 null，一位小數
- `total_spin_rpm(旋轉數)` — int 或 null，RPM
- `spin_direction(旋轉方向)` — string 或 null，時鐘格式 `HH:MM`
- `spin_efficiency_pct(旋轉效率)` — float 或 null，百分比，一位小數
- `v_break_in(垂直位移)` 或 `v_break_cm(垂直位移)` — float 或 null，一位小數，帶正負號
- `h_break_in(水平位移)` 或 `h_break_cm(水平位移)` — float 或 null，一位小數，帶正負號

null 代表 Rapsodo 本身未顯示（例如變化球 spin 偶爾空白）或 OCR 雙引擎互不同意。

輸出路徑：影片模式 `C:\rapsodo-metrics-ocr\rapsodo_data_pitching_<timestamp>.json`、即時模式 `C:\rapsodo-metrics-ocr\rapsodo_data_pitching.json`。可在 [config.py](config.py) 的 `OUTPUT_JSON_PITCHING` 調整。

## 專案結構

- [main.py](main.py) — 即時擷取卡模式入口
- [config.py](config.py) — ROI、驗證範圍、輸出路徑等常數
- [models.py](models.py) — BaseMetrics / RelativeROI dataclass
- [requirements.txt](requirements.txt) — pip 相依套件
- [core/capture.py](core/capture.py) — 擷取卡串流封裝
- [core/change_detector.py](core/change_detector.py) — 球數計數器變化偵測
- [core/data_writer.py](core/data_writer.py) — JSON 原子寫入 + 單位換算
- [core/screen_detector.py](core/screen_detector.py) — iPad 螢幕角點偵測 + 透視校正
- [core/session_manager.py](core/session_manager.py) — 多球員 session 管理
- [modes/base_ocr_reader.py](modes/base_ocr_reader.py) — Tesseract + EasyOCR 雙引擎 pipeline
- [modes/pitching.py](modes/pitching.py) — 投手 OCR reader
- [tools/video_player.py](tools/video_player.py) — 影片模式 PyQt5 GUI
- [tools/headless_driver.py](tools/headless_driver.py) — 無 UI benchmark driver
- [tools/detect_devices.py](tools/detect_devices.py) — 列出擷取卡裝置
