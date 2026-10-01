@echo off
setlocal
cd /d "%~dp0"
if not exist "build\temp" mkdir "build\temp"
set "TEMP=%CD%\build\temp"
set "TMP=%TEMP%"
set "TMPDIR=%TEMP%"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" launch.py
  goto finished
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import tkinter" >nul 2>nul
  if not errorlevel 1 (
    py -3 launch.py
    goto finished
  )
)
python -c "import tkinter" >nul 2>nul
if errorlevel 1 goto missing
python launch.py
:finished
set "stockreport_exit=%errorlevel%"
if not "%stockreport_exit%"=="0" (
  echo.
  echo Launch failed. Read the error above, then retry.
  pause
)
exit /b %stockreport_exit%
:missing
echo.
echo Python 3.10+ with Tcl/Tk was not found. Install it, then retry.
pause
exit /b 1
