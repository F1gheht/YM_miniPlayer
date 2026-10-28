@echo off
setlocal
cd /d "%~dp0"

rem --- find a real Python (not the Microsoft Store stub)
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY goto nopython

echo Installing dependencies...
%PY% -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto piperror

for /f "delims=" %%i in ('%PY% -c "import sys; print(sys.exec_prefix)"') do set "PYDIR=%%i"
if exist "%PYDIR%\pythonw.exe" (
    start "" "%PYDIR%\pythonw.exe" "%~dp0ym_mini.py" --background
) else (
    start "" %PY% "%~dp0ym_mini.py" --background
)
echo Done. YM Mini runs in the background, open it with Ctrl+Alt+M.
timeout /t 3 >nul
exit /b 0

:nopython
echo.
echo Python is not installed.
echo   1 - install Python 3.12 automatically with winget
echo   2 - open python.org and install it manually
choice /c 12 /n /m "Press 1 or 2: "
if errorlevel 2 goto manual
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
echo.
echo When the install finishes, CLOSE this window and run run.bat again.
pause
exit /b 0

:manual
echo In the installer, tick "Add python.exe to PATH".
start "" https://www.python.org/downloads/
pause
exit /b 1

:piperror
echo.
echo Failed to install dependencies. Copy the text above and send it to me.
pause
exit /b 1
