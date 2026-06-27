@echo off
setlocal enabledelayedexpansion
title Testing Toolkit Agent - Installation
cd /d "%~dp0"

:: ============================================================
:: Testing Toolkit - Local Compute Agent Installer (Windows)
:: Downloads the agent bundle from GitHub, installs offline.
:: Silent, zero-admin, portable. Double-click and forget.
:: ============================================================

set "REPO_OWNER=nrcharanvignesh"
set "REPO_NAME=Testing-Toolkit"
set "BRANCH=main"
set "INSTALL_DIR=%USERPROFILE%\TestingToolkit"
set "AGENT_DIR=%INSTALL_DIR%\agent"
set "PYTHON_DIR=%INSTALL_DIR%\python"
set "PYTHON_VER=3.12.9"
set "PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VER%/python-%PYTHON_VER%-embed-amd64.zip"
set "BUNDLE_URL=https://github.com/%REPO_OWNER%/%REPO_NAME%/archive/refs/heads/%BRANCH%.zip"
set "AGENT_PORT=7842"

echo [INFO] ============================================
echo [INFO] Testing Toolkit Agent Installer
echo [INFO] ============================================
echo [INFO] Install directory: %INSTALL_DIR%
echo.

:: Create install directories
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if not exist "%AGENT_DIR%" mkdir "%AGENT_DIR%"
if not exist "%PYTHON_DIR%" mkdir "%PYTHON_DIR%"

:: ---- Step 1: Download portable Python if not present ----
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

    :: Enable pip in embedded Python (uncomment import site)
    for %%f in ("%PYTHON_DIR%\python*._pth") do (
        echo import site>> "%%f"
    )

    :: Install pip
    echo [INFO] Installing pip...
    curl -sL "https://bootstrap.pypa.io/get-pip.py" -o "%INSTALL_DIR%\get-pip.py"
    "%PYTHON_DIR%\python.exe" "%INSTALL_DIR%\get-pip.py" --quiet
    del "%INSTALL_DIR%\get-pip.py"
)

:: ---- Step 2: Download the agent bundle from GitHub ----
echo [INFO] Downloading agent bundle from GitHub...
curl -sL "%BUNDLE_URL%" -o "%INSTALL_DIR%\bundle.zip"
if errorlevel 1 (
    echo [ERROR] Failed to download agent bundle. Check internet/proxy.
    pause
    exit /b 1
)

echo [INFO] Extracting agent bundle...
tar -xf "%INSTALL_DIR%\bundle.zip" -C "%INSTALL_DIR%"
del "%INSTALL_DIR%\bundle.zip"

:: GitHub zips extract into a folder named <repo>-<branch>/
set "EXTRACTED=%INSTALL_DIR%\%REPO_NAME%-%BRANCH%"

:: ---- Step 3: Copy agent source code ----
echo [INFO] Installing agent source...
if exist "%EXTRACTED%\agent-bundle\src" (
    xcopy /E /Y /Q "%EXTRACTED%\agent-bundle\src\*" "%AGENT_DIR%\src\" >nul 2>&1
)

:: ---- Step 4: Copy ONNX models ----
echo [INFO] Installing ONNX models...
if exist "%EXTRACTED%\agent-bundle\models" (
    xcopy /E /Y /Q "%EXTRACTED%\agent-bundle\models\*" "%AGENT_DIR%\models\" >nul 2>&1
)

:: ---- Step 5: Install Python packages from bundled wheelhouse (OFFLINE) ----
echo [INFO] Installing Python packages (offline from bundled wheels)...
if exist "%EXTRACTED%\agent-bundle\wheelhouse" (
    "%PYTHON_DIR%\python.exe" -m pip install --quiet --no-index ^
        --find-links="%EXTRACTED%\agent-bundle\wheelhouse" ^
        -r "%EXTRACTED%\agent-bundle\requirements.txt" ^
        2>nul
    if errorlevel 1 (
        echo [WARN] Some packages may have failed. Trying online fallback...
        "%PYTHON_DIR%\python.exe" -m pip install --quiet ^
            -r "%EXTRACTED%\agent-bundle\requirements.txt" ^
            2>nul
    )
) else (
    echo [WARN] No wheelhouse found, installing online...
    "%PYTHON_DIR%\python.exe" -m pip install --quiet ^
        fastapi uvicorn httpx keyring ^
        fastembed numpy openpyxl pypdf reportlab Pillow ^
        python-docx python-pptx selectolax striprtf ^
        onnxruntime lancedb rapidocr-onnxruntime PyMuPDF ^
        2>nul
)

:: ---- Step 6: Set up models path environment ----
:: Agent will look for models in AGENT_DIR\models
set "MODELS_DIR=%AGENT_DIR%\models"

:: ---- Step 7: Cleanup extracted zip contents ----
echo [INFO] Cleaning up...
rmdir /S /Q "%EXTRACTED%" >nul 2>&1

:: ---- Step 8: Register auto-start via Task Scheduler ----
echo [INFO] Registering auto-start...
schtasks /create /tn "TestingToolkitAgent" ^
    /tr "\"%PYTHON_DIR%\python.exe\" -m agent" ^
    /sc onlogon /rl limited /f >nul 2>&1

:: ---- Step 9: Start the agent now ----
echo [INFO] Starting agent on localhost:%AGENT_PORT%...
pushd "%AGENT_DIR%\src"
start /b "" "%PYTHON_DIR%\python.exe" -m agent
popd

:: ---- Step 10: Wait for agent to become healthy ----
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

echo.
echo [SUCCESS] Agent is running on localhost:%AGENT_PORT%
echo [SUCCESS] Return to your browser - the app will connect automatically.

:done
echo.
echo [INFO] Installation complete. You can close this window.
echo [INFO] The agent will auto-start on every login.
timeout /t 5 /nobreak >nul
