@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM === Double-click: pick folder (COM), ask for FPS, UNC-safe, local venv ===

pushd "%~dp0" || (echo [ERR] Cannot change to script directory.& pause & exit /b 1)

REM Folder picker (COM Shell)
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command ^
  "$sh = New-Object -ComObject Shell.Application; " ^
  "$f = $sh.BrowseForFolder(0,'Select the root folder (NAS allowed)',0); " ^
  "if($f){[Console]::WriteLine($f.Self.Path)}" `) do (
  set "ROOT=%%I"
)

if not defined ROOT (
  echo [INFO] Cancelled.
  pause
  popd
  exit /b 0
)

REM Map ROOT to temp drive (handles UNC)
pushd "%ROOT%" || (echo [ERR] Cannot access: %ROOT% & pause & popd & exit /b 1)
set "ROOT_MAPPED=%CD%"
for %%I in ("%ROOT_MAPPED%") do set "LABEL=%%~nxI"
popd

REM === Ask FPS ===
set "FPS="
set /p FPS="Frames per second (e.g., 12 / 24 / 30 / 60) [default 24]: "
if "%FPS%"=="" set "FPS=24"

REM Other defaults
set "OUT=%ROOT_MAPPED%\_Timelapse"
set "WIDTH=1280"
set "CODEC=mp4v"

echo.
echo ROOT:   %ROOT%
echo MAPPED: %ROOT_MAPPED%
echo LABEL:  %LABEL%
echo OUT:    %OUT%
echo FPS/W:  %FPS% / %WIDTH%
echo CODEC:  %CODEC%
echo.

choice /C YN /N /M "Overwrite existing videos? [Y/N]"
if errorlevel 2 ( set "OVERWRITE=" ) else ( set "OVERWRITE=--overwrite" )

choice /C YN /N /M "Show timestamp overlay? [Y/N]"
if errorlevel 2 ( set "NO_TIME=--no-time" ) else ( set "NO_TIME=" )

REM Decide Python launcher
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  echo [ERR] Python 3.8+ not found.
  pause
  popd
  exit /b 1
)

REM Local venv path
set "VENV=%LocalAppData%\TimelapseTool\.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "VPIP=%VENV%\Scripts\pip.exe"

if not exist "%VPY%" (
  echo Creating local venv: %VENV%
  %PYEXE% -m venv "%VENV%" || (echo [ERR] Failed to create venv.& pause & popd & exit /b 1)
)

"%VPY%" -m pip install --upgrade pip >nul
"%VPIP%" install -r requirements.txt || (echo [ERR] pip install failed.& pause & popd & exit /b 1)

set "SCRIPT=%CD%\timelapse_from_folders.py"
if not exist "%SCRIPT%" (
  echo [ERR] timelapse_from_folders.py not found next to this file.
  pause
  popd
  exit /b 1
)

echo.
echo Generating videos...
"%VPY%" "%SCRIPT%" --root "%ROOT_MAPPED%" --out "%OUT%" --fps %FPS% --width %WIDTH% --label "%LABEL%" %NO_TIME% --codec %CODEC% %OVERWRITE%
set "EC=%ERRORLEVEL%"
echo.

if %EC% NEQ 0 (
  echo [ERR] Python exit code: %EC%
) else (
  echo Done! Output: %OUT%
)
pause
popd
