@echo off
setlocal enabledelayedexpansion
title Testing Toolkit Agent - Installation
cd /d "%~dp0"

:: ============================================================
:: Testing Toolkit - Local Compute Agent Installer (Windows)
:: Silent, zero-admin, portable. Double-click and forget.
:: ============================================================

set "INSTALL_DIR=%USERPROFILE%\TestingToolkit\agent"
set "PYTHON_DIR=%INSTALL_DIR%\python"
set "PYTHON_VER=3.12.9"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VER%/python-%PYTHON_VER%-embed-amd64.zip"
set "AGENT_PORT=7842"

echo [INFO] Installing Testing Toolkit Agent...
echo [INFO] Install directory: %INSTALL_DIR%

:: Create install directory
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"

:: Download portable Python if not present
if not exist "%PYTHON_DIR%\python.exe" (
    echo [INFO] Downloading portable Python %PYTHON_VER%...
    curl -sL "%PYTHON_URL%" -o "%INSTALL_DIR%\python.zip"
    if errorlevel 1 (
        echo [ERROR] Failed to download Python. Check internet connection.
        pause
        exit /b 1
    )
    echo [INFO] Extracting Python...
    tar -xf "%INSTALL_DIR%\python.zip" -C "%PYTHON_DIR%"
    del "%INSTALL_DIR%\python.zip"

    :: Enable pip in embedded Python
    for %%f in ("%PYTHON_DIR%\python*._pth") do (
        echo import site>> "%%f"
    )

    :: Install pip
    echo [INFO] Installing pip...
    curl -sL "https://bootstrap.pypa.io/get-pip.py" -o "%INSTALL_DIR%\get-pip.py"
    "%PYTHON_DIR%\python.exe" "%INSTALL_DIR%\get-pip.py" --quiet
    del "%INSTALL_DIR%\get-pip.py"
)

:: Install/upgrade agent dependencies
echo [INFO] Installing agent dependencies...
"%PYTHON_DIR%\python.exe" -m pip install --quiet --upgrade ^
    fastapi uvicorn httpx keyring ^
    fastembed numpy openpyxl pypdf reportlab Pillow ^
    python-docx python-pptx selectolax striprtf ^
    2>nul

:: Copy agent source (from the repo or from a Vercel-hosted archive)
:: For now, assume the src/ folder is alongside this script or we fetch it
echo [INFO] Agent dependencies installed.

:: Register auto-start via Task Scheduler (no admin required for current user)
echo [INFO] Registering auto-start...
schtasks /create /tn "TestingToolkitAgent" /tr "\"%PYTHON_DIR%\python.exe\" -m agent" ^
    /sc onlogon /rl limited /f >nul 2>&1

:: Start the agent now
echo [INFO] Starting agent on localhost:%AGENT_PORT%...
start /b "" "%PYTHON_DIR%\python.exe" -m agent

:: Wait for agent to become healthy
echo [INFO] Waiting for agent to start...
set /a attempts=0
:wait_loop
if !attempts! geq 30 (
    echo [WARN] Agent did not start within 30 seconds. Check logs.
    goto done
)
curl -s "http://127.0.0.1:%AGENT_PORT%/health" >nul 2>&1
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    set /a attempts+=1
    goto wait_loop
)

echo [SUCCESS] Agent is running on localhost:%AGENT_PORT%

:done
echo.
echo [INFO] Installation complete. You can close this window.
echo [INFO] The agent will auto-start on every login.
timeout /t 3 /nobreak >nul
