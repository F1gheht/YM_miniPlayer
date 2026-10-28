@echo off
setlocal
cd /d "%~dp0"

rem ============================================================
rem  Builds YMMini-Setup.exe (one installer file) into Output\
rem ============================================================

set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY (
    echo Python is not installed. Run run.bat first - it will offer to install Python.
    pause
    exit /b 1
)
set /p VER=<VERSION

echo [1/3] Installing build tools...
%PY% -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller
if errorlevel 1 goto fail

echo [2/3] Building YMMini.exe...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
%PY% -m PyInstaller --noconfirm --clean --onedir --windowed --name YMMini --icon icon.ico ^
  --collect-all winrt --hidden-import comtypes.stream --hidden-import websocket ym_mini.py
if errorlevel 1 goto fail

echo [3/3] Packing installer...
call :find_iscc
if not defined ISCC (
    echo Inno Setup is not installed - installing it with winget...
    winget install -e --id JRSoftware.InnoSetup --scope user --accept-package-agreements --accept-source-agreements
    call :find_iscc
)
if not defined ISCC (
    winget install -e --id JRSoftware.InnoSetup --accept-package-agreements --accept-source-agreements
    call :find_iscc
)
if not defined ISCC (
    echo Could not find Inno Setup. Install it from https://jrsoftware.org/isdl.php and run build.bat again.
    pause
    exit /b 1
)
"%ISCC%" /Q /DMyAppVersion=%VER% installer.iss
if errorlevel 1 goto fail

echo.
echo Done: Output\YMMini-Setup.exe
explorer /select,"%~dp0Output\YMMini-Setup.exe"
pause
exit /b 0

:find_iscc
set "ISCC="
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
exit /b 0

:fail
echo.
echo Build failed. Copy the text above and send it to me.
pause
exit /b 1
