@echo off
title SubKeep
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -m subkeep %*) || (python -m subkeep %*)
if errorlevel 1 (
  echo.
  echo SubKeep needs Python 3.9 or newer. Get it free from https://www.python.org/downloads/
  pause
)
