@echo off
rem ============================================================================
rem  RemotePC - Cài đặt 1-Click trên máy của con
rem
rem  Chỉ cần click đúp vào file này:
rem    1. Tự động xin quyền Administrator (hộp thoại UAC)
rem    2. Tự động tải và cài đặt Python nếu máy con chưa có
rem    3. Tự động cài đặt thư viện cần thiết (mss)
rem    4. Hướng dẫn nhập Bot Token và cài đặt agent chạy ngầm
rem ============================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

rem Kiểm tra quyền Administrator
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [!] Dang yeu cau quyen Administrator (UAC)...
    echo.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)

rem Chạy bộ cài đặt PowerShell
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*

if %errorlevel% neq 0 (
    echo.
    echo [X] Cai dat chua hoan tat (ma loi: %errorlevel%).
)

echo.
pause
endlocal
