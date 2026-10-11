# ============================================================================
#  RemotePC - Bo kiem tra tu dong tren Windows
#
#  Chay:   scripts\test.bat            (khuyen nghi - tu nang quyen Admin)
#     hoac powershell -ExecutionPolicy Bypass -File scripts\test.ps1
#
#  Tuy chon:
#    -SkipLive           chi chay test don vi (khong goi Win32 that)
#    -Policy             chay them test ghi policy HKCU (co rui ro, xem muc 7
#                        trong docs/WINDOWS_TEST_PLAN.md)
#    -Report <duong dan> ghi bao cao ra file (mac dinh: test-report.txt)
#
#  Script KHONG BAO GIO tat/restart may, khong cai dat, khong xoa bat cu thu
#  gi ngoai nhung thu no tu tao ra trong thu muc tam.
# ============================================================================
[CmdletBinding()]
param(
    [switch]$SkipLive,
    [switch]$Policy,
    [string]$Report = ""
)

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
if (-not $Report) { $Report = Join-Path $RepoRoot "test-report.txt" }

$script:LogLines = New-Object System.Collections.Generic.List[string]

function Write-Both {
    param([string]$Text, [string]$Color = "Gray")
    Write-Host $Text -ForegroundColor $Color
    $script:LogLines.Add($Text)
}

function Section {
    param([string]$Title)
    Write-Both ""
    Write-Both ("=" * 74) "DarkCyan"
    Write-Both "  $Title" "Cyan"
    Write-Both ("=" * 74) "DarkCyan"
}

# ------------------------------------------------------------- Tim Python ----
$PyExe = $null
$PyArgs = @()
foreach ($cand in @(@("py", @("-3")), @("python", @()), @("python3", @()))) {
    if (Get-Command $cand[0] -ErrorAction SilentlyContinue) {
        $PyExe = $cand[0]; $PyArgs = $cand[1]; break
    }
}
if (-not $PyExe) {
    Write-Host "`n KHONG TIM THAY PYTHON." -ForegroundColor Red
    Write-Host " Cai Python 3.9+ tu python.org (nho tick 'Add python.exe to PATH')" -ForegroundColor Red
    Write-Host " roi chay lai script nay.`n" -ForegroundColor Red
    exit 2
}

# Goi Python va ghi ca ra man hinh lan vao bao cao. Tra ve exit code.
function Invoke-Py {
    param([string[]]$PyRest, [string]$Label)
    Write-Both ""
    Write-Both "--- $Label ---" "DarkGray"
    $out = (& $PyExe @PyArgs @PyRest 2>&1 | Out-String)
    $code = $LASTEXITCODE
    foreach ($line in ($out.TrimEnd() -split "`r?`n")) {
        Write-Both ("  " + $line.TrimEnd())
    }
    return $code
}

$Admin = ([Security.Principal.WindowsPrincipal] `
          [Security.Principal.WindowsIdentity]::GetCurrent()
         ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

# ------------------------------------------------------------- Moi truong ----
Section "MOI TRUONG"
$OsCaption = (Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue).Caption
if (-not $OsCaption) { $OsCaption = [System.Environment]::OSVersion.VersionString }
Write-Both ("  He dieu hanh : {0}" -f $OsCaption)
Write-Both ("  Build        : {0}" -f [System.Environment]::OSVersion.Version)
Write-Both ("  Kien truc    : {0}" -f $env:PROCESSOR_ARCHITECTURE)
Write-Both ("  Python       : {0} {1}" -f $PyExe, ($PyArgs -join " "))

Invoke-Py @("-c", "import sys; print('  Phien ban    : ' + sys.version.split()[0] + ('  (64-bit)' if sys.maxsize > 2**32 else '  (32-bit)'))") "Thong tin Python" | Out-Null
Invoke-Py @("-c", "import importlib.util as u; print('  tkinter      : ' + ('co' if u.find_spec('tkinter') else 'KHONG -> overlay /msg --full se bao loi'))") "Kiem tra tkinter" | Out-Null
Invoke-Py @("-c", "import importlib.util as u; print('  mss          : ' + ('co' if u.find_spec('mss') else 'khong -> chup man hinh dung fallback PowerShell'))") "Kiem tra mss" | Out-Null

Write-Both ("  Administrator: {0}" -f $(if ($Admin) { "CO" } else { "KHONG - nhieu test se SKIP" })) `
           $(if ($Admin) { "Green" } else { "Yellow" })
Write-Both ("  Thu muc repo : {0}" -f $RepoRoot)

# ------------------------------------------- 1. Test don vi (moi he dieu hanh) ----
Section "BUOC 1/3  TEST DON VI  (chay duoc o moi he dieu hanh)"
$rc1 = Invoke-Py @("-m", "unittest", "discover", "-s", "tests", "-t", ".") `
                 "python -m unittest discover -s tests -t ."

# ------------------------------------------------- 2. Test song tren Windows ----
$rc2 = 0
if ($SkipLive) {
    Section "BUOC 2/3  TEST SONG TREN WINDOWS - DA BO QUA (-SkipLive)"
} else {
    Section "BUOC 2/3  TEST SONG TREN WINDOWS  (goi Win32 / schtasks / icacls that)"
    if (-not $Admin) {
        Write-Both "  Dang chay KHONG co quyen Admin." "Yellow"
        Write-Both "  Test ve chan End Task, schtasks, ACL thu muc se SKIP." "Yellow"
        Write-Both "  Muon kiem tra day du: dung scripts\test.bat (tu nang quyen)," "Yellow"
        Write-Both "  hoac chuot phai PowerShell -> Run as administrator." "Yellow"
    }
    Write-Both "  Luu y: co the co 1 hop thoai va 1 anh chup man hinh - do la test." "DarkGray"

    $env:REMOTEPC_SELFTEST = "1"
    if ($Policy) {
        $env:REMOTEPC_LIVE_POLICY = "1"
        Write-Both "  -Policy: se ghi DisableTaskMgr vao HKCU cua tai khoan hien tai" "Yellow"
        Write-Both "           roi go bo ngay. Neu test crash, xem muc 7 trong ke hoach." "Yellow"
    } else {
        Remove-Item Env:\REMOTEPC_LIVE_POLICY -ErrorAction SilentlyContinue
    }

    $rc2 = Invoke-Py @("-m", "unittest", "tests.test_windows_live", "-v") `
                     "python -m unittest tests.test_windows_live -v"
    Remove-Item Env:\REMOTEPC_SELFTEST -ErrorAction SilentlyContinue
    Remove-Item Env:\REMOTEPC_LIVE_POLICY -ErrorAction SilentlyContinue
}

# ------------------------------------------------------------------ 3. Lint ----
Section "BUOC 3/3  KIEM TRA TINH SACH  (pyflakes)"
& $PyExe @PyArgs -c "import importlib.util as u,sys; sys.exit(0 if u.find_spec('pyflakes') else 1)"
if ($LASTEXITCODE -eq 0) {
    $rc3 = Invoke-Py @("-m", "pyflakes", "remotepc", "tests") "python -m pyflakes remotepc tests"
} else {
    Write-Both "  pyflakes chua duoc cai -> bo qua buoc nay." "DarkGray"
    Write-Both "  (Cai bang: python -m pip install pyflakes)" "DarkGray"
    $rc3 = 0
}

# --------------------------------------------------------------- Ket luan ----
Section "KET LUAN"
$blob = $script:LogLines -join "`n"

$total = 0
foreach ($m in [regex]::Matches($blob, "Ran (\d+) tests?")) { $total += [int]$m.Groups[1].Value }
$skipped = 0
foreach ($m in [regex]::Matches($blob, "skipped=(\d+)")) { $skipped += [int]$m.Groups[1].Value }
$failed = 0
foreach ($m in [regex]::Matches($blob, "(?:failures|errors)=(\d+)")) { $failed += [int]$m.Groups[1].Value }

Write-Both ("  Tong so test da chay : {0}" -f $total)
Write-Both ("  Skip (khong ap dung) : {0}" -f $skipped)
Write-Both ("  Fail / Error         : {0}" -f $failed) $(if ($failed) { "Red" } else { "Green" })
Write-Both ""
Write-Both ("  Buoc 1  test don vi  : {0}" -f $(if ($rc1 -eq 0) { "PASS" } else { "FAIL" })) `
           $(if ($rc1 -eq 0) { "Green" } else { "Red" })
if ($SkipLive) {
    Write-Both "  Buoc 2  test song    : BO QUA" "Yellow"
} else {
    Write-Both ("  Buoc 2  test song    : {0}" -f $(if ($rc2 -eq 0) { "PASS" } else { "CO MUC FAIL/SKIP" })) `
               $(if ($rc2 -eq 0) { "Green" } else { "Yellow" })
}
Write-Both ("  Buoc 3  pyflakes     : {0}" -f $(if ($rc3 -eq 0) { "PASS" } else { "FAIL" })) `
           $(if ($rc3 -eq 0) { "Green" } else { "Red" })

$script:LogLines | Set-Content -Path $Report -Encoding UTF8
Write-Both ""
Write-Both ("  Bao cao day du da ghi ra: {0}" -f $Report) "DarkGray"

$exit = 0
if ($rc1 -ne 0 -or $rc3 -ne 0) { $exit = 1 }
if (-not $SkipLive -and $rc2 -ne 0) { $exit = 1 }

Write-Both ""
if ($exit -eq 0) {
    Write-Both "  >>> TAT CA XANH. Tool san sang de cai dat that. <<<" "Green"
} else {
    Write-Both "  >>> CO MUC CHUA DAT. Gui noi dung file bao cao de xu ly tiep. <<<" "Red"
}
exit $exit
