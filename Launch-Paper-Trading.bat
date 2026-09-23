@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo Could not find the project virtual environment at "%PYTHON%".
    echo Create it from this folder with: python -m venv .venv
    exit /b 1
)
"%PYTHON%" -m streamlit run "%~dp0app\competition_dashboard.py"
