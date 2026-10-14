@echo off
REM Double-click to open the camera calibration wizard (intrinsics, or intrinsics + wand extrinsics).
REM Requires Anaconda/Miniconda and the "Pitcher" environment (see README).

set "CONDA_ACTIVATE="
for %%p in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\miniconda3" "%LOCALAPPDATA%\anaconda3") do (
    if not defined CONDA_ACTIVATE if exist "%%~p\Scripts\activate.bat" set "CONDA_ACTIVATE=%%~p\Scripts\activate.bat"
)
if not defined CONDA_ACTIVATE (
    echo Anaconda not found. Open "Anaconda Prompt" and run:
    echo     conda activate Pitcher
    echo     python Src\calibration\calibrate.py
    pause
    exit /b 1
)

call "%CONDA_ACTIVATE%" Pitcher
cd /d "%~dp0"
python Src\calibration\calibrate.py
pause
