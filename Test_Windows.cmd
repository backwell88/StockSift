@echo off
setlocal
cd /d "%~dp0"
if not exist "build\temp" mkdir "build\temp"
set "TEMP=%CD%\build\temp"
set "TMP=%TEMP%"
set "TMPDIR=%TEMP%"
set "PYTHONPATH=%CD%\function"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m unittest discover -s function\tests -v
  goto finished
)
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    py -3 -m unittest discover -s function\tests -v
    goto finished
  )
)
python -m unittest discover -s function\tests -v
:finished
set "stockreport_test_exit=%errorlevel%"
pause
exit /b %stockreport_test_exit%
