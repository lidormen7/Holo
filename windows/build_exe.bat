@echo off
REM Builds a single-file dist\Holo.exe that runs on any Windows 10/11 PC without Python.
REM Uses pip only (your pip.ini / internal server, or a "wheels" folder).
cd /d "%~dp0"
set PY=python
where py >nul 2>nul
if not errorlevel 1 set PY=py -3

if exist ".venv\Scripts\python.exe" goto :install
%PY% -m venv .venv
if errorlevel 1 goto :nopython

:install
set PIPARGS=
if exist "wheels\" set PIPARGS=--no-index --find-links wheels
".venv\Scripts\python.exe" -m pip install %PIPARGS% -r requirements.txt pyinstaller
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --onefile --windowed --name Holo --icon holo\assets\holo.ico --add-data "holo\assets;holo\assets" --hidden-import pystray._win32 --collect-data sv_ttk launcher.py
if errorlevel 1 goto :fail
".venv\Scripts\python.exe" -c "import subprocess,sys; sys.exit(subprocess.call([r'dist\Holo.exe','--self-test','selftest.txt']))"
type selftest.txt
echo.
echo Built dist\Holo.exe
pause
exit /b 0

:nopython
echo Python 3.10 or newer is required to BUILD the exe.
pause
exit /b 1

:fail
echo Build failed. See the messages above.
pause
exit /b 1
