@echo off
rem ============================================================================
rem  RemotePC - Dong goi thanh 2 file .exe (khong can cai Python tren may con)
rem
rem    dist\SysHealthAgent.exe   guardian  (chay SYSTEM, nhan lenh Telegram)
rem    dist\SysHealthUI.exe      uihost    (chay trong session cua con, hien thong bao)
rem
rem  Chay xong: copy ca thu muc dist\ sang may cua con, roi chay
rem               SysHealthAgent.exe install
rem ============================================================================
setlocal
chcp 65001 >nul
cd /d "%~dp0.."

where python >nul 2>&1
if errorlevel 1 (
    echo [X] Khong tim thay Python trong PATH.
    exit /b 1
)

echo.
echo ==^> Cai PyInstaller
python -m pip install --upgrade pip pyinstaller
if errorlevel 1 (
    echo [X] Cai PyInstaller that bai.
    exit /b 1
)

echo.
echo ==^> Dong goi
python -m remotepc build
if errorlevel 1 (
    echo [X] Dong goi that bai.
    exit /b 1
)

echo.
echo [OK] Xong. File nam trong thu muc dist\
echo      Copy ca 2 file .exe sang may cua con roi chay:
echo          SysHealthAgent.exe install
echo.
pause
endlocal
