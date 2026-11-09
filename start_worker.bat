@echo off
title AUTO-POSTER Background Worker
color 0e

set VENV_PYTHON=.venv\Scripts\python.exe

if not exist "%VENV_PYTHON%" (
    python main.py
) else (
    "%VENV_PYTHON%" main.py
)

pause

