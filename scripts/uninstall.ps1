<#
.SYNOPSIS
    Go cai dat RemotePC: dung task, xoa task, bo policy cua con, xoa file.

.EXAMPLE
    .\uninstall.ps1                 # hoi PIN, xoa het
    .\uninstall.ps1 -KeepData       # giu lai nhat ky / cau hinh
    .\uninstall.ps1 -Force          # bo qua PIN (chi dung khi ban la chu may
                                    #  va quen PIN - can Administrator)
#>
#Requires -Version 5.1
[CmdletBinding()]
param(
    [switch]$KeepData,
    [switch]$Force,
    [string]$Pin = ""
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[X] Can quyen Administrator." -ForegroundColor Red
    exit 1
}

$root = Split-Path -Parent $PSScriptRoot
$exe = Get-ChildItem -Path $root -Filter "SysHealthAgent.exe" -Recurse -ErrorAction SilentlyContinue |
       Select-Object -First 1
if ($exe) { $runner = @($exe.FullName) }
else      { $runner = @("python", "-m", "remotepc") }

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

# 1) Tat cac task truoc de process khong tu khoi dong lai giua chung
Write-Host "`n==> Dung cac scheduled task" -ForegroundColor Cyan
foreach ($name in @("SysHealthAgent", "SysHealthAgent UI", "SysHealthAgent Watchdog",
                    "SysHealthAgent Telemetry")) {
    schtasks /end /tn $name 2>&1 | Out-Null
}

# Lay dung ten task tu cau hinh (neu nguoi dung da doi ten nguy trang)
$cfg = Invoke-RemotePC @("config", "--show") | Out-String
if ($cfg -match '"display_name":\s*"([^"]+)"') {
    $display = $Matches[1]
    foreach ($suffix in @("", " UI", " Watchdog", " Telemetry")) {
        schtasks /end /tn "$display$suffix" 2>&1 | Out-Null
    }
}

# 2) Goi lenh go cua agent (tu xoa task, policy, file)
Write-Host "`n==> Go cai dat" -ForegroundColor Cyan
$args = @("uninstall")
if ($KeepData) { $args += "--keep-data" }
if ($Force) {
    Write-Host "    [!] Che do -Force: bo qua PIN (ban phai la chu may)." -ForegroundColor Yellow
    $args = @("uninstall", "--force")
} elseif ($Pin) {
    $args += @("--pin", $Pin)
}
Invoke-RemotePC $args

# 3) Don dep phan con sot (neu agent bi khoa boi ACL)
Write-Host "`n==> Don dep phan con sot" -ForegroundColor Cyan
$leftover = 0
foreach ($name in @("SysHealthAgent", "SysHealthAgent UI", "SysHealthAgent Watchdog",
                    "SysHealthAgent Telemetry")) {
    $q = schtasks /query /tn $name 2>&1
    if ($LASTEXITCODE -eq 0) {
        schtasks /delete /tn $name /f 2>&1 | Out-Null
        Write-Host "    Da xoa task con sot: $name" -ForegroundColor Yellow
        $leftover++
    }
}
if ($leftover -eq 0) { Write-Host "    [OK] Khong con task nao." -ForegroundColor Green }

Write-Host "`n[OK] Da go xong. Khoi dong lai may de chac chan moi thu tro ve binh thuong." -ForegroundColor Green
