@echo off
setlocal
cd /d "%~dp0"
if not exist "build\temp" mkdir "build\temp"
if not exist "build\pyinstaller-config" mkdir "build\pyinstaller-config"
set "TEMP=%CD%\build\temp"
set "TMP=%TEMP%"
set "TMPDIR=%TEMP%"
set "PYINSTALLER_CONFIG_DIR=%CD%\build\pyinstaller-config"
set "PIP_NO_CACHE_DIR=1"
if exist ".venv\Scripts\python.exe" goto install
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import venv" >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv
    goto created
  )
)
python -c "import venv" >nul 2>nul
if errorlevel 1 goto missing
python -m venv .venv
:created
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" function\tools\build_windows.py
if errorlevel 1 goto fail
echo.
echo Build complete. The versioned EXE path is printed above under dist\vVERSION\StockReport.
echo Copy that WHOLE StockReport folder, not only the EXE.
pause
exit /b 0
:missing
echo.
echo Python 3.10+ with venv and Tcl/Tk was not found. Install it, then retry.
pause
exit /b 1
:fail
echo.
echo Build failed. Read the error above and README.md.
pause
exit /b 1
