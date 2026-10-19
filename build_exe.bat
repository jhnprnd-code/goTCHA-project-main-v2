@echo off
title Build goTCHA.exe
cd /d "%~dp0"

:: Use the project's virtual environment when there is one.
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe

%PY% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    %PY% -m pip install pyinstaller || goto :fail
)

%PY% -m PyInstaller --noconfirm --clean --onefile --name goTCHA ^
    --distpath dist --workpath build --specpath build ^
    --paths backend --paths machine-learning ^
    --add-data "%~dp0frontend;frontend" ^
    --collect-submodules uvicorn ^
    --exclude-module matplotlib --exclude-module playwright --exclude-module tkinter ^
    gotcha_launcher.py || goto :fail

echo.
echo Built dist\goTCHA.exe. Study data and an optional model.joblib are read from its folder.
pause
exit /b 0

:fail
echo.
echo Build failed.
pause
exit /b 1
