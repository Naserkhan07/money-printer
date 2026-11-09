@echo off
title AUTO-POSTER Live Dashboard
color 0a

set VENV_PYTHON=.venv\Scripts\python.exe

if not exist "%VENV_PYTHON%" (
    python dashboard.py
) else (
    "%VENV_PYTHON%" dashboard.py
)

pause

