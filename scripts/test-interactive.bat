@echo off
rem ============================================================================
rem  RemotePC - KIEM TRA TREN WINDOWS (ban danh cho NGUOI click doi)
rem
rem  Khac scripts\test.bat: file nay
rem    - tu xin quyen Administrator (hop thoai UAC)
rem    - giu cua so lai khi chay xong de doc ket qua
rem
rem  AI agent / shell KHONG nen goi file nay (UAC can nguoi bam, va cua so
rem  nang quyen tach rieng nen khong bat duoc output). Agent dung:
rem      scripts\test.bat
rem
rem  Cach dung: click doi file nay, hoac  scripts\test-interactive.bat
rem             scripts\test-interactive.bat -SkipLive
rem ============================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

set "HERE=%~dp0"

rem ---------- Da co quyen Admin chua? ----------
net session >nul 2>&1
if %errorlevel% equ 0 goto :run

echo.
echo   Dang yeu cau quyen Administrator de kiem tra day du...
echo   (Bam "No" thi script van chay, nhung nhieu test se bi SKIP.)
echo.

rem Start-Process mo cua so MOI - chap nhan, vi day la duong danh cho nguoi that
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Khong nang duoc quyen - chay tiep voi quyen hien tai.
    goto :run
)
exit /b 0

:run
rem test.bat tu pause o cuoi khi RP_PAUSE=1, nen o day khong pause them lan nua
set "RP_PAUSE=1"
call "%HERE%test.bat" %*
exit /b %errorlevel%
