@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Create the project virtual environment first:
    echo   python -m venv .venv
    echo   .\.venv\Scripts\Activate.ps1
    echo   pip install -r requirements.txt
    exit /b 1
)

echo Installing desktop packaging dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements-desktop.txt
if errorlevel 1 exit /b 1

echo Building StockCompDashboard.exe...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean StockCompDashboard.spec
if errorlevel 1 exit /b 1

echo.
echo Built:
echo   %CD%\dist\StockCompDashboard\StockCompDashboard.exe
echo Persistent data:
echo   %%LOCALAPPDATA%%\StockComp\paper_trading_data
echo Existing project paper_trading_data is copied with a backup and is not deleted.
endlocal
