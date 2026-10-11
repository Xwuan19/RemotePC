<#
.SYNOPSIS
    Cai dat RemotePC len may Windows (can quyen Administrator).

.DESCRIPTION
    Script nay:
      1. Kiem tra quyen Administrator (tu nang quyen neu chay tu install.bat)
      2. Tim Python 3.10+ (hoac dung file .exe neu ban da dong goi)
      3. Neu chua co cau hinh -> chay `remotepc init` (hoi bot token, PIN, gio cam)
      4. Cai dat: copy file, tao 4 scheduled task, phan quyen, ap policy cho tai khoan con
      5. Kiem tra lai bang `remotepc doctor`

.EXAMPLE
    .\install.ps1                          # cai mac dinh (muc bao ve 2)
    .\install.ps1 -Level 1                 # chi an console + chay SYSTEM
    .\install.ps1 -Child Kid,EmBe          # chi dinh ro tai khoan cua con
    .\install.ps1 -SkipInit                # bo qua buoc hoi, dung config co san
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet(0, 1, 2)]
    [int]$Level = 2,

    [string[]]$Child = @(),

    [string]$Python = "",

    [switch]$SkipInit,

    [switch]$NoStart
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

function Write-Step($msg)  { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "    [OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg) { Write-Host "    [!] $msg" -ForegroundColor Yellow }
function Write-Bad($msg)   { Write-Host "    [X] $msg" -ForegroundColor Red }

# --------------------------------------------------------------------------- #
# 0. Quyen admin
# --------------------------------------------------------------------------- #
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Bad "Can quyen Administrator. Hay chuot phai -> 'Run as administrator'."
    exit 1
}
Write-Ok "Dang chay voi quyen Administrator ($($identity.Name))"

# --------------------------------------------------------------------------- #
# 1. Tim Python hoac exe
# --------------------------------------------------------------------------- #
$root = Split-Path -Parent $PSScriptRoot
$exe = Get-ChildItem -Path $root -Filter "SysHealthAgent.exe" -Recurse -ErrorAction SilentlyContinue |
       Select-Object -First 1

if ($exe) {
    $runner = @($exe.FullName)
    Write-Ok "Dung ban dong goi: $($exe.FullName)"
} else {
    if (-not $Python) {
        foreach ($cand in @("python", "py -3", "python3")) {
            $parts = $cand.Split(" ")
            try {
                $v = & $parts[0] $parts[1..($parts.Length-1)] --version 2>&1
                if ($LASTEXITCODE -eq 0 -and $v -match "Python 3") { $Python = $cand; break }
            } catch {}
        }
    }
    if (-not $Python) {
        Write-Bad "Khong tim thay Python 3. Tai tai https://www.python.org/downloads/"
        Write-Bad "  (nho tich 'Add Python to PATH' khi cai), hooc chay scripts\build.bat"
        Write-Bad "  tren 1 may khac de tao file .exe roi copy sang may nay."
        exit 2
    }
    $runner = @()
    foreach ($p in $Python.Split(" ")) { $runner += $p }
    $runner += "-m", "remotepc"
    Write-Ok "Dung Python: $Python"
}

function Invoke-RemotePC {
    param([string[]]$RemoteArgs)
    $exe = $runner[0]
    $pre = @()
    if ($runner.Length -gt 1) { $pre = $runner[1..($runner.Length - 1)] }
    Push-Location $root
    try {
        & $exe @($pre + $RemoteArgs)
        return $LASTEXITCODE
    } finally { Pop-Location }
}

# --------------------------------------------------------------------------- #
# 2. Cau hinh ban dau
# --------------------------------------------------------------------------- #
if (-not $SkipInit) {
    Write-Step "Thiet lap cau hinh ban dau (bot token, PIN, gio gioi nghiem)"
    Invoke-RemotePC @("init")
    if ($LASTEXITCODE -ne 0) { Write-Bad "init that bai"; exit $LASTEXITCODE }
}

# --------------------------------------------------------------------------- #
# 3. Cai dat
# --------------------------------------------------------------------------- #
Write-Step "Cai dat agent (muc bao ve $Level)"
$installArgs = @("install", "--level", "$Level")
foreach ($c in $Child) { $installArgs += @("--child", $c) }
if ($NoStart) { $installArgs += "--no-start" }
Invoke-RemotePC $installArgs
$code = $LASTEXITCODE

# --------------------------------------------------------------------------- #
# 4. Kiem tra
# --------------------------------------------------------------------------- #
Write-Step "Kiem tra lai"
Invoke-RemotePC @("doctor")

Write-Host ""
if ($code -eq 0) {
    Write-Ok "Hoan tat. Mo Telegram va gui /status cho bot de thu."
    Write-Warn2 "Neu bot chua tra loi: chay `remotepc find-chat` roi them chat_id,"
    Write-Warn2 "hoac gui `/pair <PIN>` cho bot tu dien thoai cua bo/me."
} else {
    Write-Bad "Cai dat chua hoan tat (ma loi $code). Xem cac dong phia tren."
}
exit $code
