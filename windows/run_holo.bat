@echo off
REM Starts Holo. The first run creates a private Python environment in .venv, and
REM packages are reinstalled automatically whenever requirements.txt changes.
REM Installation only uses pip, so it works with an internal/offline pip server
REM (pip.ini) or, with no network at all, from a "wheels" folder next to this file.
cd /d "%~dp0"
set PY=python
where py >nul 2>nul
if not errorlevel 1 set PY=py -3

if exist ".venv\Scripts\pythonw.exe" goto :check_packages
echo Setting up Holo for the first time...
%PY% -m venv .venv
if errorlevel 1 goto :nopython

:check_packages
fc /b requirements.txt ".venv\holo-requirements.txt" >nul 2>nul
if not errorlevel 1 goto :launch
echo Installing Holo's Python packages...
set PIPARGS=
if exist "wheels\" set PIPARGS=--no-index --find-links wheels
".venv\Scripts\python.exe" -m pip install %PIPARGS% -r requirements.txt
if errorlevel 1 goto :fail
copy /y requirements.txt ".venv\holo-requirements.txt" >nul

:launch
start "" ".venv\Scripts\pythonw.exe" launcher.py %*
exit /b 0

:nopython
echo Python 3.10 or newer is required. Install it, tick "Add python.exe to PATH", then run this file again.
pause
exit /b 1

:fail
echo Installing the Python packages failed. See the messages above.
echo Check that pip can reach your package server, or put the packages in a "wheels" folder.
pause
exit /b 1
