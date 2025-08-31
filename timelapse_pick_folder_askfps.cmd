@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM === Double-click: pick folder, ask FPS & time window (UNC-safe, local venv) ===

pushd "%~dp0" || (echo [ERR] Cannot change to script directory.& pause & exit /b 1)

REM Folder picker (COM Shell, ASCII-only)
for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command ^
  "$sh = New-Object -ComObject Shell.Application; " ^
  "$f = $sh.BrowseForFolder(0,'Select the root or single date folder (NAS allowed)',0); " ^
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

REM ==== Ask FPS & time window (no CHOICE) ====
set "FPS="
set /p FPS="Frames per second [default 24]: "
if "%FPS%"=="" set "FPS=24"

set "TSTART="
set /p TSTART="Time START HH:MM (e.g., 07:30) [blank = no limit]: "
set "TEND="
set /p TEND="Time END   HH:MM (e.g., 18:30) [blank = no limit]: "

REM Overwrite?
set "OVERWRITE="
set "ANS="
set /p ANS="Overwrite existing videos? [Y/N, default N]: "
if /I "%ANS%"=="Y" set "OVERWRITE=--overwrite"

REM Show timestamp?
set "NO_TIME="
set "ANS="
set /p ANS="Show timestamp overlay? [Y/N, default Y]: "
if /I "%ANS%"=="N" set "NO_TIME=--no-time"

REM ==== Defaults ====
set "OUT=%ROOT_MAPPED%\_Timelapse"
set "WIDTH=1280"
set "CODEC=mp4v"

echo.
echo ROOT:   %ROOT%
echo MAPPED: %ROOT_MAPPED%
echo LABEL:  %LABEL%
echo OUT:    %OUT%
echo FPS/W:  %FPS% / %WIDTH%
echo WINDOW: %TSTART% - %TEND%
echo CODEC:  %CODEC%
echo.

REM ==== Pick Python ====
set "PYEXE="
where py >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  echo [ERR] Python 3.8+ not found.
  pause
  popd
  exit /b 1
)

REM ==== Local venv ====
set "VENV=%LocalAppData%\TimelapseTool\.venv"
set "VPY=%VENV%\Scripts\python.exe"
set "VPIP=%VENV%\Scripts\pip.exe"

if not exist "%VPY%" (
  echo Creating local venv at: %VENV%
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
set "TSARG="
if not "%TSTART%"=="" set "TSARG=--time-start %TSTART%"
set "TEARG="
if not "%TEND%"=="" set "TEARG=--time-end %TEND%"

"%VPY%" "%SCRIPT%" --root "%ROOT_MAPPED%" --out "%OUT%" --fps %FPS% --width %WIDTH% --label "%LABEL%" %NO_TIME% --codec %CODEC% %OVERWRITE% %TSARG% %TEARG%
set "EC=%ERRORLEVEL%"
echo.

if %EC% NEQ 0 (
  echo [ERR] Python exit code: %EC%
) else (
  echo Done! Output: %OUT%
)
pause
popd
