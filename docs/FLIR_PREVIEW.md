# FLIR 多相機即時預覽

雙擊專案根目錄的 `start_flir_preview.bat`，或在專案根目錄執行：

```bat
conda activate Pitcher
python -m Src.UI_Control.main_flir_preview
```

需安裝此專案既有的 PyQt5、NumPy、Spinnaker SDK 與相符版本的 PySpin。這個入口不載入骨架模型，也不需要模型權重。

- 接上 1 台顯示 1 格，最多顯示 5 台；1 台滿版、2–4 台兩欄、5 台三欄。
- 每 2 秒自動偵測；可按「重新偵測」立即重掃。超過 5 台時保留已開啟的相機，空位依序號補入。
- 各台獨立擷取，關閉 TriggerMode，使用連續取像；不需要同步線。未寫入相機 UserSet 或專案設定檔。
- 每次連線在開始擷取前將相機 ROI 設為 **1920×1080**，並讀回確認；原有 ROI 偏移在新尺寸允許範圍內保留，必要時調整至合法範圍。不支援此尺寸或節點不可寫時顯示錯誤。
- 接收端全速逐幀取像，以 `OldestFirst` 讀取 SDK 緩衝，不因預覽 30 FPS 主動跳過接收。另一執行緒只轉換抽樣影格，畫面更新上限約 30 FPS（介面計時器 34 ms），實際流暢度仍取決於接收與轉色速度，不錄影。
- **接收 FPS**：最近 2 秒收到的完整影格數／實際測量秒數；**平均 FPS**：本次連線完整幀數／經過秒數。開始前 2 秒為暖機區間。
- **完整幀數／不完整幀數／取像錯誤**分開計數；**跳號**為相鄰收到的 Frame ID 中間缺少的編號數，不把預覽抽樣計入。跳號可能來自傳輸或主機 SDK 緩衝溢出，不能單憑此數字認定是 hub 故障；不完整和跳號也不相加當作精確丟幀率。
- **設定 FPS**只讀取相機現有 AcquisitionFrameRate，並非實際收到的速率。此工具不自動改成 180、不改曝光。若原設定為 60 FPS，須先在相機設定工具設好所需速率。設定 180 也不保證實收 180。
- hub 測試建議：先接一台，等暖機後觀察至少 30 秒，再逐台加入；每台接收與平均應接近 180 FPS，30 秒約收到 5,400 個完整影格，同時查看不完整／跳號／錯誤是否增加。重連會重設該台影格統計，但保留重連次數與上次中斷原因（滑鼠停留在統計區查看）。
- 拔除相機會移除該格；接回會重新開啟。錯誤顯示在各格下方，完整訊息可停留滑鼠查看；其他相機繼續擷取。
- 若無畫面，先關閉占用相機的 SpinView 或主程式，再檢查 USB／網路、電源與 SDK。多台實際效能取決於解析度、USB 控制器或網路頻寬。

取像和 RGB 轉換依 [FLIR PySpin 官方指南](https://softwareservices.flir.com/Spinnaker/4.4/guides/python-programmer-guide.html) 的 API 模式實作；Frame ID 和預覽深複製參考 [Image API](https://softwareservices.flir.com/Spinnaker/latest/api/pyspin_ref/classes/PySpin.Image.html)，並核對本機 PySpin 4.0 的方法。此專案既有環境仍使用 Spinnaker 4.0，無需為此功能升級。

離線驗證：`python -m unittest Src.UI_Control.tests.test_flir_preview`。實際 1–5 台同接與拔插仍需現場硬體驗證。
