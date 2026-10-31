@echo off
rem Copies the ACTrainerLink CSP app (records other cars' laps) into Assetto Corsa.
rem Usage: install_csp_app.bat ["C:\path\to\assettocorsa"]
setlocal
cd /d "%~dp0"

set "AC=%~1"
if "%AC%"=="" set "AC=%ProgramFiles(x86)%\Steam\steamapps\common\assettocorsa"

if not exist "%AC%\acs.exe" (
    echo Could not find Assetto Corsa in "%AC%".
    echo Run this again with your install folder, e.g.:
    echo     install_csp_app.bat "D:\SteamLibrary\steamapps\common\assettocorsa"
    echo Content Manager shows the folder under Settings - Assetto Corsa - Root folder.
    pause
    exit /b 1
)

xcopy /E /I /Y "csp_app\ACTrainerLink" "%AC%\apps\lua\ACTrainerLink" >nul
if errorlevel 1 (
    echo Copy failed - try running as administrator.
    pause
    exit /b 1
)
echo Installed to "%AC%\apps\lua\ACTrainerLink".
echo It starts recording by itself in every session (needs Custom Shaders Patch).
pause
