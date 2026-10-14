@echo off
REM Double-click to open the RoMa extrinsic calibration window (testing).
REM Requires the "Roma" conda environment (see docs/CALIBRATION.md section 7).

set "CONDA_ACTIVATE="
for %%p in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\miniconda3" "%LOCALAPPDATA%\anaconda3") do (
    if not defined CONDA_ACTIVATE if exist "%%~p\Scripts\activate.bat" set "CONDA_ACTIVATE=%%~p\Scripts\activate.bat"
)
if not defined CONDA_ACTIVATE (
    echo Anaconda not found. Open "Anaconda Prompt" and run:
    echo     conda activate Roma
    echo     python Src\calibration\roma\roma_extrinsic_gui.py
    pause
    exit /b 1
)

call "%CONDA_ACTIVATE%" Roma
cd /d "%~dp0"
python Src\calibration\roma\roma_extrinsic_gui.py
pause
