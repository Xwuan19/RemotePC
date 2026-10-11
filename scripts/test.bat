@echo off
rem ============================================================================
rem  RemotePC - KIEM TRA TU DONG TREN WINDOWS
rem
rem  Day la cong vao DETERMINISTIC: khong pause, khong tu nang quyen, khong
rem  mo cua so moi. AI agent va shell goi truc tiep file nay, exit code va
rem  stdout bat duoc binh thuong.
rem
rem  Nguoi dung muon click doi -> dung  scripts\test-interactive.bat
rem  (file do moi xin UAC va giu cua so lai de doc ket qua)
rem
rem  Cach dung:  scripts\test.bat
rem              scripts\test.bat -SkipLive     (bo qua phan goi Win32 that)
rem              scripts\test.bat -Policy       (them test ghi policy HKCU)
rem
rem  Bien moi truong:
rem    RP_PAUSE=1   giu cua so lai khi chay xong (mac dinh: 0)
rem
rem  Script KHONG tat may, KHONG cai dat, KHONG sua gi ngoai thu muc tam.
rem ============================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

set "HERE=%~dp0"
set "REPO=%HERE%.."
cd /d "%REPO%"

rem ---------- Bao cho nguoi dung biet dang co quyen gi ----------
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo   [!] Dang chay KHONG co quyen Administrator.
    echo       Test ve chan End Task / schtasks / ACL se tu SKIP kem ly do.
    echo       Muon kiem tra day du:  scripts\test-interactive.bat
    echo.
)

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

if "%RP_PAUSE%"=="1" pause
exit /b %RC%
