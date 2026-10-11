@echo off
rem ============================================================================
rem  RemotePC - Cai dat (tu nang quyen Administrator)
rem  Cu click doi file nay, hoac chay:  scripts\install.bat
rem  Tuy chon:  install.bat 1          -> muc bao ve 1
rem             install.bat 2 Kid      -> muc 2, tai khoan con la "Kid"
rem ============================================================================
setlocal EnableDelayedExpansion
chcp 65001 >nul

set "LEVEL=2"
set "CHILD="
if not "%~1"=="" set "LEVEL=%~1"
if not "%~2"=="" set "CHILD=%~2"

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  Dang yeu cau quyen Administrator...
    echo.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%LEVEL% %CHILD%' -Verb RunAs"
    exit /b
)

set "ARGS=-Level %LEVEL%"
if not "%CHILD%"=="" set "ARGS=%ARGS% -Child %CHILD%"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %ARGS%

echo.
pause
endlocal
