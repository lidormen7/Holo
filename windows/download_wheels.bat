@echo off
REM Run this on a PC that can reach your pip server (or the internet), with the same
REM Python version as the target PC. It saves every package Holo needs into "wheels".
REM Copy the whole Holo folder to the offline PC: run_holo.bat installs from it with no network.
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% -m pip download -r requirements.txt -d wheels --only-binary=:all: || (pause & exit /b 1)
%PY% -m pip download pyinstaller -d wheels --only-binary=:all:
echo Saved to the wheels folder.
pause
