# Pitch-Skeleton-User-Interface

棒球投手骨架分析系統：雙台 FLIR 高速相機同步錄影，以 YOLO + ViTPose 偵測 2D 骨架，並以雙相機三角測量重建 3D 骨架。

> 開發者／維護者請另外參考 [DEVELOPMENT.md](docs/DEVELOPMENT.md)（環境問題排查、硬體細節、系統設計背景），相機校正細節見 [CALIBRATION.md](docs/CALIBRATION.md)。

程式已依介面、校正、攝影機、骨架與分析分資料夾，舊版工具保留於 `Src/legacy/`；位置與新指令見[DEVELOPMENT.md〈程式目錄與攝影機設定〉](docs/DEVELOPMENT.md)。主程式工具列新增「攝影機設定」，開啟相機後可分別調曝光、增益、白平衡，以及硬體支援的焦距／對焦。

## 安裝

以下步驟會建立與實驗室主機**版本完全一致**的環境（2026-10-07 已在全新環境實測通過）。

### 0. 事先準備

| 項目 | 說明 |
|---|---|
| 作業系統 | Windows 10/11 64-bit |
| 顯示卡 | NVIDIA，驅動版本 **≥ 520**（需支援 CUDA 11.8）。實驗室為 RTX 4090／驅動 560.94。**RTX 50 系列不支援**此環境 |
| 軟體 | [Anaconda](https://www.anaconda.com/download)、[Git](https://git-scm.com/)、[Visual Studio 2022 Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)（勾選「使用 C++ 的桌面開發」，部分套件需現場編譯） |
| 不在 git 裡的檔案 | **模型權重**與 **Spinnaker SDK** 打包為 `Pitcher_筆電安裝包.zip`（約 3 GB），從[雲端硬碟](https://drive.google.com/drive/folders/118_mVMTLW6fZl6LcJljtjAKD-0s6X4_6?usp=sharing)下載（需維護者授權）。解壓縮後內含 `安裝順序.txt`、`01_Spinnaker_4.0.0.116\`、`02_模型權重_Db_pretrain\` |

CUDA Toolkit 不必另外安裝：PyTorch 與 mmcv 使用的是自帶 CUDA 11.8 的預編譯版本。

### 1. 取得程式碼與模型權重

```
git clone https://github.com/ZhengMo1220/Pitch-Skeleton-User-Interface.git
```

把安裝包 `02_模型權重_Db_pretrain\` 裡的 `.pth` 檔全部複製到 `Db\pretrain\`：

```
Db\pretrain\
├─ epoch_210.pth                                                   ← 骨架模型（程式預設使用）
├─ yolov8_s_syncbn_fast_8xb16-500e_coco_20230117_180101-5aa5f0f1.pth ← 人物偵測（程式預設使用）
└─ 其他 .pth（備用模型）
```

### 2. 建立 conda 環境

以下指令**一律在「Anaconda Prompt」中、於專案根目錄執行**。

```
conda create -n Pitcher --file environment\conda_explicit.txt -y
conda activate Pitcher
```

### 3. 安裝 Python 套件

```
pip install --no-deps -r environment\requirements-lock.txt
pip install --no-build-isolation cython_bbox==0.1.5
```

> **一定要加 `--no-deps`**：照清單原樣安裝才會與實驗室環境一致。實驗室環境有兩處刻意保留的版本不一致（刻意不裝 `opencv-python-headless`，因為它會蓋掉有視窗功能的 opencv；`protobuf` 比 onnx 要求的低一版但可正常運作），讓 pip 自動解析依賴會直接報錯中止。

### 4. 安裝專案內的三個 OpenMMLab 子專案

```
cd Src\mmpose_main
pip install --no-deps --no-build-isolation -e .
cd ..\mmyolo_main
pip install --no-deps --no-build-isolation -e .
cd ..\mmpretrain_main
pip install --no-deps --no-build-isolation -e .
cd ..\..
```

### 5. 安裝 FLIR 相機 SDK（Spinnaker 4.0.0.116）

**必須使用 4.0.0.116**，不要到官網下載最新版（原因見本節最後）。

a. 關閉所有相機程式、拔除相機，以系統管理員身分**依序**安裝安裝包 `01_Spinnaker_4.0.0.116\` 內的檔案：

`TeledyneCommonComponentsSetup.exe` → `VCRedist_v140_x64.msi` → `Spinnaker_GenICam_v140_x64.msi` → `Spinnaker_Binaries_v140_x64.msi` → `Spinnaker_GenTL_v140_x64.msi` → `Spinnaker_Drivers_x64.msi`

（若顯示已安裝，選 `Repair` 或略過；詢問是否安裝驅動程式時選允許。安裝包內另有選用的說明文件、Python 範例，以及 GigE 網路相機才需要的 `GigeVisionInterface.exe`，本專案用 USB3 相機不必安裝）

b. **重新啟動 Windows**

c. 安裝 Python 版 SDK：

```
conda activate Pitcher
pip install "<安裝包路徑>\01_Spinnaker_4.0.0.116\spinnaker_python-4.0.0.116-cp38-cp38-win_amd64.whl"
```

> **為什麼鎖定 4.0.0.116**：PySpin 與 Spinnaker SDK 版本必須完全一致，混搭會出現 `DLL load failed while importing PySpin`。PySpin 的 wheel 也綁定 Python 版本（`cp38` = Python 3.8），而本專案的 OpenMMLab 套件建構於 Python 3.8，較新的 Spinnaker 已不提供 cp38 版本，升級 SDK 等於要整套環境一起升級。

### 6. 驗證

```
conda activate Pitcher
python -c "import torch, mmcv, mmdet, mmpose, mmyolo, PySpin; print(torch.__version__, torch.cuda.is_available(), mmcv.__version__)"
```

預期輸出：`2.0.1+cu118 True 2.0.1`

接上相機後確認偵測得到（若顯示 0 台，先關閉 SpinView 等佔用相機的程式）：

```
python -c "import PySpin; s=PySpin.System.GetInstance(); cs=s.GetCameras(); print('Camera count:', cs.GetSize()); cs.Clear(); s.ReleaseInstance()"
```

> 安裝或執行時遇到錯誤，請先查閱 [DEVELOPMENT.md](docs/DEVELOPMENT.md) 的「環境建置已知問題」。注意：**務必先 `conda activate Pitcher` 再執行任何程式**，直接呼叫環境裡的 `python.exe` 可能載入到其他環境的數學函式庫而當機。

## 執行

在專案根目錄執行（也可沿用進入 `Src\UI_Control` 後執行 `python main.py`）：

```
conda activate Pitcher
python Src/UI_Control/main.py
```

也可以直接雙擊專案根目錄的 `start.bat`，它會自動啟動 Pitcher 環境並切換到正確目錄。

| 分頁 | 用途 |
|---|---|
| 2D | 雙相機即時畫面、自動偵測投球並錄影、回放與投球階段分析（主要功能） |
| 2D 相機 | 雙相機同步拍照（用於外參校正） |
| 3D | 3D 骨架重建與回放 |
| 回放比較 | 多筆結果比對 |

雙相機錄影需接 **GPIO 同步線**（正面相機為 primary），且兩台相機需接在**不同的 USB 控制器**上（單台 179 FPS 約佔 3 Gbps）。

## 相機校正

新架設或移動相機後需要重新校正，結果自動存到 `Db\Calibration\`，錄影時會把當下的校正值一起存進該筆錄影資料夾。

雙擊專案根目錄的 **`start_calibrate.bat`** 開啟「校正中心」，所有校正方法都在這個視窗裡點選：

| 項目 | 方法 |
|---|---|
| 內參（每台相機各做一次） | 即時拍攝棋盤格；或讀取已拍好的照片資料夾 |
| 外參（選一種） | **RoMa（不需道具，用當天錄影，研究測試中）**；校正桿手點（學姊方法）；校正桿 3D 驗證（學姊 03）；棋盤格 PnP／8Points；粉紅球十字架 |

拍內參時板子要上下、左右、斜向都傾斜；只朝同一方向轉時程式會警告（fx 會算不準）。

**用 RoMa 做外參**（第一次使用前需安裝 `Roma` 環境，見 [docs/CALIBRATION.md 第 7 節](docs/CALIBRATION.md)）：

1. 校正中心選「即時拍攝棋盤格」→「開啟」，勾選「只做內參」，完成兩台內參。
2. 校正中心選「RoMa」，按「加入錄影資料夾…」選當天的錄影（例如 `Db\Record\<日期>_Pitcher01`），勾選「存成目前的外參」，按「開始計算」。

其他校正方法（棋盤格、螢光粉球十字架）、舊的分步工具與詳細說明，見 [docs/CALIBRATION.md](docs/CALIBRATION.md)。

## 資料夾結構

```
Db\                      ← 不在 git 裡
├─ pretrain\             模型權重
├─ Calibration\          相機校正結果
└─ Record\               錄影與分析結果
    └─ <日期>_Pitcher<編號>\<時間>_P<球數>\
        ├─ CF_*.mp4 / CS_*.mp4        正面／側面原始影片
        ├─ CF_*.json / CS_*.json      骨架偵測結果
        ├─ *_BRandRapsodo.json        球離手與 Rapsodo 資料
        └─ calibration.json           錄影當下的校正值
environment\             環境版本鎖定檔
Src\UI_Control\          主程式視窗
Src\calibration\         校正工具（chessboard_cross：棋盤格／十字架、roma：RoMa 外參）
docs\                    開發文件
```
