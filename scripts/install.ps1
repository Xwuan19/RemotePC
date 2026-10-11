<#
.SYNOPSIS
    Cai dat RemotePC len may Windows (can quyen Administrator).

.DESCRIPTION
    Script nay:
      1. Kiem tra quyen Administrator (tu nang quyen neu chay tu install.bat hoac setup.bat)
      2. Tim Python 3.10+ (neu chua co -> TU DONG TAI VA CAI DAT PYTHON)
      3. Kiem tra va cai dat thu vien phu thuoc (mss)
      4. Huong dan nhap Bot Token Telegram (neu chua co)
      5. Cai dat he thong: copy file, tao 4 scheduled task, phan quyen, ap policy cho tai khoan con
      6. Kiem tra lai bang `remotepc doctor` va huong dan ghep noi qua dien thoai

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

    [string]$Token = "",

    [string]$Pin = "",

    [switch]$SkipInit,

    [switch]$NoStart,

    [switch]$NonInteractive
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
# Helper tim va cai dat Python
# --------------------------------------------------------------------------- #
function Find-PythonInstalled {
    # 1. Thu cac lenh trong PATH
    foreach ($cand in @("python", "py -3", "python3")) {
        $parts = $cand.Split(" ")
        try {
            $cmd = Get-Command $parts[0] -ErrorAction SilentlyContinue
            if ($cmd) {
                $subArgs = if ($parts.Length -gt 1) { $parts[1..($parts.Length - 1)] } else { @() }
                $v = & $parts[0] @($subArgs + @("--version")) 2>&1
                if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(1[0-9]|[2-9][0-9])") {
                    return $cand
                }
            }
        } catch {}
    }

    # 2. Thu cac duong dan mac dinh
    $common = @(
        "C:\Program Files\Python311\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe"
    )
    foreach ($p in $common) {
        if (Test-Path $p) {
            try {
                $v = & $p --version 2>&1
                if ($LASTEXITCODE -eq 0 -and $v -match "Python 3\.(1[0-9]|[2-9][0-9])") {
                    return $p
                }
            } catch {}
        }
    }
    return $null
}

function Install-PythonAuto {
    Write-Step "May chua co Python 3 (3.10+). Bat dau tu dong cai dat Python..."

    # 1. Thu winget truoc (neu co)
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget) {
        Write-Host "    [+] Dang cai dat Python qua winget..." -ForegroundColor Yellow
        try {
            $proc = Start-Process -FilePath "winget" -ArgumentList "install --id Python.Python.3.11 --exact --source winget --silent --accept-package-agreements --accept-source-agreements" -Wait -PassThru -NoNewWindow
            if ($proc.ExitCode -eq 0) {
                Write-Ok "Da cai dat Python qua winget."
            }
        } catch {
            Write-Warn2 "Winget gap su co, chuyen sang tai truc tiep tu python.org..."
        }
        $found = Find-PythonInstalled
        if ($found) { return $found }
    }

    # 2. Tai installer truc tiep tu python.org
    $is64 = [Environment]::Is64BitOperatingSystem
    $pyUrl = if ($is64) {
        "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    } else {
        "https://www.python.org/ftp/python/3.11.9/python-3.11.9.exe"
    }
    $installerPath = "$env:TEMP\python-installer.exe"

    Write-Host "    [+] Dang tai Python 3.11 tu python.org..." -ForegroundColor Yellow
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $pyUrl -OutFile $installerPath -UseBasicParsing
    } catch {
        if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
            & curl.exe -fsSL -o $installerPath $pyUrl
        } else {
            Write-Bad "Khong tai duoc file cai dat Python: $_"
            return $null
        }
    }

    if (-not (Test-Path $installerPath)) {
        Write-Bad "Khong tim thay file cai dat Python da tai ve."
        return $null
    }

    Write-Host "    [+] Dang cai dat Python che do im lang (Include_tcltk, pip, PATH)..." -ForegroundColor Yellow
    $installArgs = "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_tcltk=1 Include_pip=1"
    $proc = Start-Process -FilePath $installerPath -ArgumentList $installArgs -Wait -PassThru
    Remove-Item $installerPath -Force -ErrorAction SilentlyContinue

    # Lam moi bien moi truong PATH
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:PATH = "$machinePath;$userPath"

    $found = Find-PythonInstalled
    return $found
}

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
        $Python = Find-PythonInstalled
        if (-not $Python) {
            $Python = Install-PythonAuto
        }
    }
    if (-not $Python) {
        Write-Bad "Khong tim thay hoac khong the tu dong cai dat Python 3."
        Write-Bad "Hay tai va cai dat thu cong tu https://www.python.org/downloads/ (nho tich 'Add Python to PATH')."
        exit 2
    }
    $runner = @()
    foreach ($p in $Python.Split(" ")) { $runner += $p }
    $runner += "-m", "remotepc"
    Write-Ok "Dung Python: $Python"

    # Cai dat thu vien phu thuoc (mss cho screenshot)
    try {
        $pyCmd = $Python.Split(" ")[0]
        $pySubArgs = if ($Python.Split(" ").Length -gt 1) { $Python.Split(" ")[1..($Python.Split(" ").Length - 1)] } else { @() }
        $checkMss = & $pyCmd @($pySubArgs + @("-c", "import mss; print('ok')")) 2>$null
        if ($checkMss -ne "ok") {
            Write-Host "    [+] Dang cai dat thu vien ho tro chup man hinh (mss)..." -ForegroundColor Yellow
            & $pyCmd @($pySubArgs + @("-m", "pip", "install", "--quiet", "mss>=9.0.1")) 2>$null
            Write-Ok "Da cai dat mss."
        } else {
            Write-Ok "Thu vien chup man hinh (mss): Da co san."
        }
    } catch {
        Write-Warn2 "Khong the cai mss qua pip, tool se dung co che chup man hinh mac dinh cua Windows."
    }
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
# 2. Cau hinh ban dau (Bot Token & PIN)
# --------------------------------------------------------------------------- #
if (-not $SkipInit) {
    # Kiem tra file config.json xem da co token chua
    $hasToken = $false
    $cfgPath = "C:\ProgramData\SysHealthAgent\config.json"
    if (Test-Path $cfgPath) {
        try {
            $rawJson = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($rawJson.telegram.bot_token) { $hasToken = $true }
        } catch {}
    }

    if (-not $hasToken -and -not $Token -and -not $NonInteractive) {
        Write-Host ""
        Write-Host "==========================================================================" -ForegroundColor Cyan
        Write-Host "  THIET LAP BOT TELEGRAM DE QUAN LY MAY CUA CON" -ForegroundColor Yellow
        Write-Host "==========================================================================" -ForegroundColor Cyan
        Write-Host "  De quan ly may tinh tu xa qua dien thoai, ban can 1 bot Telegram mien phi:"
        Write-Host "    1. Mo Telegram tren dien thoai, tim @BotFather"
        Write-Host "    2. Gui lenh /newbot va lam theo huong dan de dat ten cho bot"
        Write-Host "    3. Copy doan HTTP API Token (vi du: 7123456789:AAHk...)"
        Write-Host "--------------------------------------------------------------------------"
        $Token = Read-Host "  -> Dan Bot Token vao day (hoac Enter neu muon nhap sau)"
        $Token = $Token.Trim()

        if ($Token) {
            $Pin = Read-Host "  -> Dat ma PIN bao ve lenh tat may (Enter de dung mac dinh: 1234)"
            $Pin = $Pin.Trim()
            if (-not $Pin) { $Pin = "1234" }
        }
    }

    Write-Step "Luu cau hinh agent"
    $initArgs = @("init", "--defaults")
    if ($Token) { $initArgs += @("--token", $Token) }
    if ($Pin)   { $initArgs += @("--pin", $Pin) }
    Invoke-RemotePC $initArgs
}

# --------------------------------------------------------------------------- #
# 3. Cai dat tien trinh he thong
# --------------------------------------------------------------------------- #
Write-Step "Cai dat agent (muc bao ve $Level)"
$installArgs = @("install", "--level", "$Level")
foreach ($c in $Child) { $installArgs += @("--child", $c) }
if ($NoStart) { $installArgs += "--no-start" }
Invoke-RemotePC $installArgs
$code = $LASTEXITCODE

# --------------------------------------------------------------------------- #
# 4. Kiem tra lai
# --------------------------------------------------------------------------- #
Write-Step "Kiem tra lai he thong (doctor)"
Invoke-RemotePC @("doctor")

# --------------------------------------------------------------------------- #
# 5. Ket luan & Huong dan
# --------------------------------------------------------------------------- #
Write-Host ""
if ($code -eq 0) {
    Write-Host "==========================================================================" -ForegroundColor Green
    Write-Host "  [OK] CAI DAT HOAN TAT VA SAN SANG HOAT DONG!" -ForegroundColor Green
    Write-Host "==========================================================================" -ForegroundColor Green
    Write-Host "  BUOC TIEP THEO - GHEP NOI DIEN THOAI CUA BO/ME:" -ForegroundColor Yellow
    Write-Host "    1. Mo app Telegram tren dien thoai cua ban."
    Write-Host "    2. Tim bot ma ban da tao va bam START (hoac gui /start)."
    Write-Host "    3. Gui lenh sau cho bot de nhan dien dien thoai la Admin:"
    if ($Pin) {
        Write-Host "          /pair $Pin" -ForegroundColor Cyan
    } else {
        Write-Host "          /pair 1234" -ForegroundColor Cyan
    }
    Write-Host "       (1234 la ma PIN mac dinh, ban co the doi bat cu luc nao bang /pin)"
    Write-Host ""
    Write-Host "  CAC LENH CO BAN DE QUAN LY MAY CON:" -ForegroundColor Yellow
    Write-Host "    /status      - Xem thoi gian con dung may va app dang chay"
    Write-Host "    /screenshot  - Chup man hinh may cua con"
    Write-Host "    /msg <loi>   - Gui thong bao nhac nho len man hinh con"
    Write-Host "    /lock        - Khoa man hinh ngay lap tuc"
    Write-Host "    /shutdown    - Tat may con (co dem nguoc 45s)"
    Write-Host "==========================================================================" -ForegroundColor Green
} else {
    Write-Bad "Cai dat chua hoan tat (ma loi $code). Xem cac dong phia tren."
}
exit $code
