@echo off
setlocal

set "SCRIPT_FILE=%~dp0upgrade_vs_collector.ps1"
set "LOG_FILE=%~dp0upgrade_vs_collector.log"

where powershell.exe >nul 2>nul
if errorlevel 1 (
    echo [upgrade] PowerShell is not available.
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_FILE%" >nul 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

if "%EXIT_CODE%"=="0" (
    echo [upgrade] Upgrade completed successfully.
) else (
    echo [upgrade] Upgrade failed. Exit code: %EXIT_CODE%. See upgrade_vs_collector.log.
)

exit /b %EXIT_CODE%
