@echo off
rem ============================================================================
rem  RemotePC - Go cai dat (tu nang quyen Administrator)
rem  Cu click doi, hoac:  scripts\uninstall.bat
rem             scripts\uninstall.bat keepdata     -> giu lai nhat ky
rem             scripts\uninstall.bat force        -> bo qua PIN (chu may)
rem ============================================================================
setlocal EnableDelayedExpansion

set "MODE=%~1"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  Dang yeu cau quyen Administrator...
    echo.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%MODE%' -Verb RunAs"
    exit /b
)

set "ARGS="
if /i "%MODE%"=="keepdata" set "ARGS=-KeepData"
if /i "%MODE%"=="force"    set "ARGS=-Force"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall.ps1" %ARGS%

echo.
pause
endlocal
