@echo off
REM Double-click to open the calibration center (all intrinsic and extrinsic methods in one window).
REM Requires Anaconda/Miniconda and the "Pitcher" environment (see README).
REM The console window hides itself: output goes to Db\Logs\start_calibrate.log, and a
REM message box appears if the program cannot start or exits with an error.

if /i not "%~1"=="--hidden" (
    powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Start-Process -FilePath '%~f0' -ArgumentList '--hidden' -WindowStyle Hidden"
    exit /b
)

set "LOG_DIR=%~dp0Db\Logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG=%LOG_DIR%\start_calibrate.log"
REM Redirected output is not a console; force UTF-8 so Chinese/symbol prints cannot crash Python.
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"

set "CONDA_ACTIVATE="
for %%p in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\miniconda3" "%LOCALAPPDATA%\anaconda3") do (
    if not defined CONDA_ACTIVATE if exist "%%~p\Scripts\activate.bat" set "CONDA_ACTIVATE=%%~p\Scripts\activate.bat"
)
if not defined CONDA_ACTIVATE (
    call :fail "Anaconda not found. Open Anaconda Prompt and run:`n    conda activate Pitcher`n    python Src\calibration\calibration_center.py"
    exit /b 1
)

call "%CONDA_ACTIVATE%" Pitcher > "%LOG%" 2>&1
if errorlevel 1 (
    call :fail "Cannot activate the Pitcher conda environment.`nSee %LOG%"
    exit /b 1
)
cd /d "%~dp0"
python Src\calibration\calibration_center.py >> "%LOG%" 2>&1
if errorlevel 1 call :fail "The calibration center exited with an error.`nSee %LOG%"
exit /b

:fail
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show(\"%~1\", 'Pitcher', 'OK', 'Error') | Out-Null"
exit /b 1
