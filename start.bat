@echo off
title AUTO-POSTER Mission Control
color 0b

echo ============================================================
echo   AUTO-POSTER - Mission Control & Automation Engine
echo ============================================================
echo.

set VENV_PYTHON=.venv\Scripts\python.exe

if not exist "%VENV_PYTHON%" (
    echo [ERROR] Virtual environment not found at .venv!
    echo Please run: python -m venv .venv
    pause
    exit /b 1
)

echo [*] Starting Live Web Dashboard on http://localhost:5050 ...
start "AUTO-POSTER Dashboard" cmd /k "%VENV_PYTHON% dashboard.py"

echo [*] Launching Automation Pipeline & Autopilot Worker ...
"%VENV_PYTHON%" main.py

pause

