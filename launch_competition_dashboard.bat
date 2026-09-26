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

echo Starting the paper-trading dashboard.
echo Portfolio and saved plans live in: "%CD%\paper_trading_data"
echo Back up that folder to keep history. This app never places real orders.
".venv\Scripts\python.exe" -m streamlit run app\competition_dashboard.py
endlocal
