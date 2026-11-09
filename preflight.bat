@echo off
title AUTO-POSTER Preflight Health Check
color 0b

set VENV_PYTHON=.venv\Scripts\python.exe

if not exist "%VENV_PYTHON%" (
    python -m shorts_bot.preflight
) else (
    "%VENV_PYTHON%" -m shorts_bot.preflight
)

pause

