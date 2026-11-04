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
- FPS 是此預覽程式取得並轉換的影格速率，預覽約上限 30 FPS，不是相機完整硬體幀率。只保留最新影格，不錄影。
- 拔除相機會移除該格；接回會重新開啟。錯誤顯示在各格下方，完整訊息可停留滑鼠查看；其他相機繼續擷取。
- 若無畫面，先關閉占用相機的 SpinView 或主程式，再檢查 USB／網路、電源與 SDK。多台實際效能取決於解析度、USB 控制器或網路頻寬。

取像和 RGB 轉換依 [FLIR PySpin 官方指南](https://softwareservices.flir.com/Spinnaker/4.4/guides/python-programmer-guide.html) 的 API 模式實作；此專案既有環境仍使用 Spinnaker 4.0，無需為此功能升級。

離線驗證：`python -m unittest Src.UI_Control.tests.test_flir_preview`。實際 1–5 台同接與拔插仍需現場硬體驗證。
