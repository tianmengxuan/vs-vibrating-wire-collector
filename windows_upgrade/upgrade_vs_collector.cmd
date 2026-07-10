@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo [升级] 未找到 powershell.exe，无法执行升级。
    pause
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0upgrade_vs_collector.ps1"
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo [升级] 升级流程完成。
) else (
    echo [升级] 升级流程未完成，退出码：%EXIT_CODE%
)

pause
exit /b %EXIT_CODE%
