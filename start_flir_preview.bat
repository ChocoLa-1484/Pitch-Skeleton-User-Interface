@echo off
REM Independent FLIR preview, without pose models.
set "CONDA_ACTIVATE="
for %%p in ("%USERPROFILE%\anaconda3" "%USERPROFILE%\miniconda3" "%ProgramData%\anaconda3" "%ProgramData%\miniconda3" "%LOCALAPPDATA%\anaconda3") do (
    if not defined CONDA_ACTIVATE if exist "%%~p\Scripts\activate.bat" set "CONDA_ACTIVATE=%%~p\Scripts\activate.bat"
)
if not defined CONDA_ACTIVATE (
    echo Anaconda not found. Run in your camera Python environment:
    echo python -m Src.UI_Control.main_flir_preview
    pause
    exit /b 1
)
call "%CONDA_ACTIVATE%" Pitcher
if errorlevel 1 exit /b 1
cd /d "%~dp0"
python -m Src.UI_Control.main_flir_preview
if errorlevel 1 pause
