@echo off
rem ============================================================================
rem  RemotePC - KIEM TRA TỰ ĐỘNG TRÊN WINDOWS (một lệnh duy nhất)
rem
rem  Cach dung:  scripts\test.bat
rem              scripts\test.bat -SkipLive     (bo qua phan goi Win32 that)
rem              scripts\test.bat -Policy       (them test ghi policy HKCU)
rem
rem  File nay tu xin quyen Administrator vi nhieu test can no:
rem    - gan DENY ACE de chung minh con khong End Task duoc
rem    - schtasks /create de chung minh Task Scheduler chap nhan XML cua tool
rem  Neu ban tu choi UAC, script van chay nhung cac test do se SKIP.
rem
rem  Script KHONG tat may, KHONG cai dat, KHONG sua gi ngoai thu muc tam.
rem ============================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

set "HERE=%~dp0"
set "REPO=%HERE%.."

rem ---------- Da co quyen Admin chua? ----------
net session >nul 2>&1
if %errorlevel% equ 0 goto :run

echo.
echo   Dang yeu cau quyen Administrator de kiem tra day du...
echo   (Neu ban bam "No", script van chay nhung nhieu test se bi SKIP.)
echo.

rem Tu nang quyen: goi lai chinh file nay trong mot cua so Administrator
set "ARGS=%*"
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%ARGS%' -Verb RunAs" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Khong nang duoc quyen. Chay tiep voi quyen hien tai...
    goto :run
)
exit /b 0

:run
cd /d "%REPO%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%test.ps1" %*
set "RC=%errorlevel%"

echo.
echo --------------------------------------------------------------------------
if "%RC%"=="0" (
    echo   KET QUA: TAT CA XANH  ^(exit code 0^)
) else (
    echo   KET QUA: CO MUC CHUA DAT  ^(exit code %RC%^)
    echo   Bao cao chi tiet: "%REPO%\test-report.txt"
)
echo --------------------------------------------------------------------------
echo.

rem Neu dang o cua so UAC tu nang quyen thi giu lai de nguoi dung doc ket qua
if not "%RP_NO_PAUSE%"=="1" pause
exit /b %RC%
