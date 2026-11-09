@echo off
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
    echo Install uv first: python -m pip install uv==0.12.19
    exit /b 1
)
uv sync --locked
if errorlevel 1 exit /b %errorlevel%
uv run --locked --no-sync python -m streamlit run streamlit_app.py %*
