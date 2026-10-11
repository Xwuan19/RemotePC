@echo off
rem ============================================================================
rem  RemotePC - KIEM TRA TU DONG TREN WINDOWS (mot lenh duy nhat)
rem
rem  Cach dung:  scripts\test.bat
rem              scripts\test.bat -SkipLive     (bo qua phan goi Win32 that)
rem              scripts\test.bat -Policy       (them test ghi policy HKCU)
rem
rem  CHAY DUOC CA HAI CACH:
rem    - Nguoi dung click doi  -> tu xin quyen Admin (UAC), giu cua so lai de doc
rem    - AI agent / tu shell   -> chay tai cho, khong popup, khong pause,
rem                               output bat duoc binh thuong
rem
rem  Script KHONG tat may, KHONG cai dat, KHONG sua gi ngoai thu muc tam.
rem ============================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

set "HERE=%~dp0"
set "REPO=%HERE%.."
cd /d "%REPO%"

rem ---------- Heu: co phai duoc chay bang cach CLICK DOI file nay khong? ----------
rem Khi click doi, Windows goi: cmd /c ""...\test.bat" "
rem Khi chay tu shell/agent thi %CMDLINE% khong chua duong dan cua chinh no.
set "INTERACTIVE=0"
echo %CMDLINE% | find /i "%~nx0" >nul 2>&1 && set "INTERACTIVE=1"

rem ---------- Da co quyen Admin chua? ----------
net session >nul 2>&1
if %errorlevel% equ 0 goto :run

echo.
echo   [!] Dang chay KHONG co quyen Administrator.
echo       Cac test ve chan End Task / schtasks / ACL se bi SKIP.
echo.

if "%INTERACTIVE%"=="0" goto :run

rem Chi tu nang quyen khi nguoi that click doi. Agent thi bo qua buoc nay vi
rem UAC can nguoi bam, va cua so moi se lam agent khong bat duoc output.
echo   Dang yeu cau quyen Administrator...
echo   (Bam "No" thi script van chay tiep voi quyen hien tai.)
echo.
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs" >nul 2>&1
if %errorlevel% neq 0 (
    echo   Khong nang duoc quyen - chay tiep voi quyen hien tai.
    goto :run
)
exit /b 0

:run
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

rem Chi pause khi nguoi that click doi - neu khong, AI agent se treo o day.
if "%INTERACTIVE%"=="1" pause
exit /b %RC%
