@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Create the Python 3.14 environment and install requirements-app.txt first.
    exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run streamlit_app.py
