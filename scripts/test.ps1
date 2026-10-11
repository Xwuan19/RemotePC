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
# Uu tien `py -3` (launcher chuan tren Windows), roi moi den python/python3.
# Viet bang if/elseif thay vi duyet mang long, vi mang con rong @() de bi
# PowerShell lam sup khien @PyArgs splat ra $null.
$PyExe = $null
$PyArgs = @()
if (Get-Command "py" -ErrorAction SilentlyContinue) {
    $PyExe = "py"; $PyArgs = @("-3")
} elseif (Get-Command "python" -ErrorAction SilentlyContinue) {
    $PyExe = "python"; $PyArgs = @()
} elseif (Get-Command "python3" -ErrorAction SilentlyContinue) {
    $PyExe = "python3"; $PyArgs = @()
}
if (-not $PyExe) {
    Write-Host "`n KHONG TIM THAY PYTHON." -ForegroundColor Red
    Write-Host " Cai Python 3.9+ tu python.org (nho tick 'Add python.exe to PATH')" -ForegroundColor Red
    Write-Host " roi chay lai script nay.`n" -ForegroundColor Red
    exit 2
}

# Goi Python va ghi ca ra man hinh lan vao bao cao.
# Tra ve object {Code, Text} de phan tich rieng tung buoc - neu gop chung ca
# log roi moi regex thi so test se bi dem trung (buoc 1 va buoc 2 giao nhau).
function Invoke-Py {
    param([string[]]$PyRest, [string]$Label)
    Write-Both ""
    Write-Both "--- $Label ---" "DarkGray"
    $out = (& $PyExe @PyArgs @PyRest 2>&1 | Out-String)
    $code = $LASTEXITCODE
    foreach ($line in ($out.TrimEnd() -split "`r?`n")) {
        Write-Both ("  " + $line.TrimEnd())
    }
    return [pscustomobject]@{ Code = $code; Text = $out }
}

# Doc "Ran N tests" / "skipped=N" / "failures=N" / "errors=N" tu output 1 buoc.
function Read-TestCounts {
    param([string]$Text)
    $ran = 0; $skip = 0; $bad = 0
    $m = [regex]::Match($Text, "Ran (\d+) tests?")
    if ($m.Success) { $ran = [int]$m.Groups[1].Value }
    foreach ($mm in [regex]::Matches($Text, "skipped=(\d+)")) { $skip += [int]$mm.Groups[1].Value }
    foreach ($mm in [regex]::Matches($Text, "failures=(\d+)")) { $bad += [int]$mm.Groups[1].Value }
    foreach ($mm in [regex]::Matches($Text, "errors=(\d+)"))   { $bad += [int]$mm.Groups[1].Value }
    return [pscustomobject]@{ Ran = $ran; Skipped = $skip; Failed = $bad }
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
Write-Both "  Buoc nay bo qua test song de hop thoai/anh chup khong nhay 2 lan." "DarkGray"
$env:REMOTEPC_SKIP_LIVE = "1"
$r1 = Invoke-Py @("-m", "unittest", "discover", "-s", "tests", "-t", ".") `
                "python -m unittest discover -s tests -t ."
Remove-Item Env:\REMOTEPC_SKIP_LIVE -ErrorAction SilentlyContinue
$rc1 = $r1.Code
$c1 = Read-TestCounts $r1.Text

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

    $r2 = Invoke-Py @("-m", "unittest", "tests.test_windows_live", "-v") `
                    "python -m unittest tests.test_windows_live -v"
    Remove-Item Env:\REMOTEPC_SELFTEST -ErrorAction SilentlyContinue
    Remove-Item Env:\REMOTEPC_LIVE_POLICY -ErrorAction SilentlyContinue
    $rc2 = $r2.Code
    $c2 = Read-TestCounts $r2.Text
}

# ------------------------------------------------------------------ 3. Lint ----
Section "BUOC 3/3  KIEM TRA TINH SACH  (pyflakes)"
& $PyExe @PyArgs -c "import importlib.util as u,sys; sys.exit(0 if u.find_spec('pyflakes') else 1)"
$rc3 = 0
if ($LASTEXITCODE -eq 0) {
    $r3 = Invoke-Py @("-m", "pyflakes", "remotepc", "tests") "python -m pyflakes remotepc tests"
    $rc3 = $r3.Code
} else {
    Write-Both "  pyflakes chua duoc cai -> bo qua buoc nay." "DarkGray"
    Write-Both "  (Cai bang: python -m pip install pyflakes)" "DarkGray"
    $rc3 = 0
}

# --------------------------------------------------------------- Ket luan ----
Section "KET LUAN"

# Tinh rieng tung buoc. KHONG regex tren ca log: buoc 1 va buoc 2 giao nhau
# (buoc 1 co 37 test song bi skip, buoc 2 chay lai chinh 37 test do) nen
# cong don se ra con so sai.
if (-not $c2) { $c2 = [pscustomobject]@{ Ran = 0; Skipped = 0; Failed = 0 } }
$unitRun = $c1.Ran
$unitSkip = $c1.Skipped
$unitBad = $c1.Failed
$liveRun = $c2.Ran
$liveSkip = $c2.Skipped
$liveBad = $c2.Failed
$failed = $unitBad + $liveBad

Write-Both ("  Buoc 1  test don vi  : {0,-5}  {1} test, skip {2}, fail {3}" -f `
             $(if ($rc1 -eq 0) { "PASS" } else { "FAIL" }), $unitRun, $unitSkip, $unitBad) `
           $(if ($rc1 -eq 0) { "Green" } else { "Red" })

if ($SkipLive) {
    Write-Both "  Buoc 2  test song    : BO QUA (-SkipLive)" "Yellow"
} else {
    $liveTag = if ($rc2 -eq 0) { "PASS" } else { "FAIL" }
    Write-Both ("  Buoc 2  test song    : {0,-5}  {1} test, skip {2}, fail {3}" -f `
                 $liveTag, $liveRun, $liveSkip, $liveBad) `
               $(if ($rc2 -eq 0) { "Green" } else { "Yellow" })
    if ($liveSkip -gt 0) {
        Write-Both "        (skip o buoc nay thuong vi thieu quyen Admin hoac dang o session 0;" "DarkGray"
        Write-Both "         moi test skip deu in ly do - xem chi tiet o tren)" "DarkGray"
    }
}

Write-Both ("  Buoc 3  pyflakes     : {0}" -f $(if ($rc3 -eq 0) { "PASS" } else { "FAIL" })) `
           $(if ($rc3 -eq 0) { "Green" } else { "Red" })
Write-Both ""
Write-Both ("  Tong fail / error    : {0}" -f $failed) $(if ($failed) { "Red" } else { "Green" })

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
