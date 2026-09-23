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

if not exist "dist\StockCompDashboard\StockCompDashboard.exe" (
    echo The known-working build was not found at:
    echo   %CD%\dist\StockCompDashboard\StockCompDashboard.exe
    echo This script will not recreate or overwrite that folder.
)

echo Installing desktop packaging dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements-desktop.txt
if errorlevel 1 exit /b 1

echo Building AI-enabled StockCompDashboard.exe into a separate folder...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --workpath "build\StockCompDashboardAI" StockCompDashboardAI.spec
if errorlevel 1 exit /b 1

echo.
echo Known-working build left in place:
echo   %CD%\dist\StockCompDashboard\StockCompDashboard.exe
echo Updated AI-enabled build:
echo   %CD%\dist\StockCompDashboardAI\StockCompDashboard.exe
echo Persistent data:
echo   %%LOCALAPPDATA%%\StockComp\paper_trading_data
echo Existing project paper_trading_data is copied with a backup and is not deleted.
endlocal
