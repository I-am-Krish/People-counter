@echo off
title People Counter — Launcher
color 0A

echo.
echo  ╔══════════════════════════════════════════════════╗
echo  ║          PEOPLE COUNTER — STARTING UP           ║
echo  ╚══════════════════════════════════════════════════╝
echo.

REM ── Check Python ──────────────────────────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo  [ERROR] Python not found. Please install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

REM ── Create / activate virtual environment ─────────────────────────────────
if not exist "venv\Scripts\activate.bat" (
    echo  [SETUP] Creating virtual environment...
    python -m venv venv
)

echo  [SETUP] Activating virtual environment...
call venv\Scripts\activate.bat

REM ── Install / upgrade dependencies ────────────────────────────────────────
echo  [SETUP] Installing dependencies...
pip install -q -r requirements.txt

REM ── Start both servers ────────────────────────────────────────────────────
echo.
echo  [INFO]  Starting Analytics Dashboard on http://127.0.0.1:7003
start "Analytics Dashboard" cmd /k "call venv\Scripts\activate.bat && python analytics_server.py"

timeout /t 2 /nobreak >nul

echo  [INFO]  Starting People Counter stream on http://127.0.0.1:7004
echo.
echo  ┌─────────────────────────────────────────────────┐
echo  │  Live feed   → http://127.0.0.1:7004            │
echo  │  Dashboard   → http://127.0.0.1:7003            │
echo  │                                                  │
echo  │  Press Ctrl+C in this window to stop counting.  │
echo  └─────────────────────────────────────────────────┘
echo.

python people_counter.py

pause
