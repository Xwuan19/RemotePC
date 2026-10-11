@echo off
rem ============================================================================
rem  RemotePC - Setup 1-Click
rem ============================================================================
setlocal

rem Kiem tra quyen Administrator
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [!] Dang yeu cau quyen Administrator [UAC]...
    echo.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)

rem Chay bo cai dat PowerShell
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
if %errorlevel% neq 0 (
    echo.
    echo [X] Cai dat chua hoan tat [ma loi: %errorlevel%].
)

echo.
pause
endlocal
