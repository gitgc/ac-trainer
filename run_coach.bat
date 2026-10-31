@echo off
rem AC Trainer - double-click to start the coach, then launch a session from Content Manager.
rem Extra arguments are passed through, e.g.:  run_coach.bat demo   /   run_coach.bat voices
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up, this takes a minute...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if not exist ".venv\Scripts\python.exe" (
        echo Python 3.11 or newer is needed - get it from https://www.python.org/downloads/
        echo and tick "Add python.exe to PATH" during the install.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -c "import sys; sys.exit(sys.version_info < (3, 11))"
    if errorlevel 1 (
        echo Python 3.11 or newer is needed - this one is too old.
        rmdir /s /q .venv
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -e .
)

".venv\Scripts\python.exe" -m actrainer %*
pause
