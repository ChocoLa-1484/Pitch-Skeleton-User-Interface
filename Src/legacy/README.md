# 舊版與實驗功能

這些檔案保留供比對與維護，主程式不會載入本資料夾。

| 位置 | 保留原因 | 現行入口／替代功能 |
|---|---|---|
| `calibration/01_ball_detect_manual.py`、`02_ransac_8points.py`、`03_3d.py` | 舊校正桿分步工具，仍可獨立執行 | `Src/calibration/calibrate.py` 整合精靈 |
| `ui/video_widget.py`、`video_ui.py`、`pitch_ui_2.py`、`cv_control_compare.py` | 無現行主程式引用的舊介面／控制器 | `Src/UI_Control/widgets/`、`forms/` |
| `ui/opencv/` | 舊 ArUco／OpenCV 介面；原本即依賴倉庫缺少的 `src.opencv_objects` | 保留參考，不宣稱可獨立啟動 |
| `experiments/` | 未啟用的骨架重構、ONNX／TensorRT 模型與匯出實驗 | `Src/pose/` 為主程式使用的版本 |
| `cameras/dual_realsense_system.py` | 未啟用且缺少單機 RealSense 實作 | `Src/cameras/` 的 FLIR 後端 |
| `rapsodo_integrated_extractor.py` | 舊 OCR 整合器 | `Src/rapsodo/capture_tablet_images.py` 使用 OCR V2 |

舊設備的校正／檢視工具（`calib_extrin.py`、`calib_from_folder.py`、`extract_calib.py`、`calibrated_3d_viewer.py`、`view_3d_animation.py`）主程式沒用到，已於 2026-10-13 刪除，需要時可從 git 歷史找回。

舊工具可能使用舊棋盤尺寸、舊尺度或固定範例資料，請先查看其參數。此輪只保留並更新匯入／資源路徑，未把實驗結果視為已驗證的現行功能。TensorRT 快取仍保留於原 `Src/UI_Control/trt_cache/`。
