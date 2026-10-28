@echo off
setlocal
cd /d "%~dp0"
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo Python is not installed. Run run.bat first.
    pause
    exit /b 1
)
echo Python:
%PY% -c "import sys; print(sys.version); print(sys.executable)"
echo.
echo Checking modules...
%PY% -c "import PyQt5.QtWidgets, PyQt5.QtNetwork; print('PyQt5 OK')"
%PY% -c "import winrt.windows.media.control; print('winrt OK')"
%PY% -c "import pycaw.pycaw, psutil; print('pycaw + psutil OK')"
echo.
echo Starting YM Mini with console. Errors will appear below.
echo Close this window to stop it.
echo ------------------------------------------------------------
%PY% "%~dp0ym_mini.py"
echo ------------------------------------------------------------
echo YM Mini exited with code %errorlevel%.
pause
