# main_metric.py：棋盤校正量測入口

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

校正算法、影像條件與限制見 [CHESSBOARD_STRIDE.md](CHESSBOARD_STRIDE.md)。
