@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

REM 检查pythonw是否存在
where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start "" pythonw main.py
    exit /b 0
)

REM 如果没有pythonw，用python后台启动
start "" python main.py
exit /b 0
