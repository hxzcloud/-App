@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

echo ========================================
echo    FloatTranslator - Start
echo ========================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.7+
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [INFO] Python detected
python --version
echo.

REM Check dependencies
echo [INFO] Checking dependencies...
python -c "import PyQt5, pynput, requests" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Missing dependencies, installing...
    python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if %errorlevel% neq 0 (
        echo [ERROR] Install failed. Run manually: python -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo [INFO] Dependencies installed
) else (
    echo [INFO] Dependencies ready
)

echo.
echo [INFO] Starting FloatTranslator...
echo [TIP] Press Ctrl+Alt+T to show/hide window
echo [TIP] Close window minimizes to system tray
echo.

python main.py

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Program error
    pause
)
