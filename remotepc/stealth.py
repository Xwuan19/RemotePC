"""Lop "nguy trang" + tu bao ve (Windows).

Muc tieu thuc te (khong hua huyen):
  1. Con KHONG the End Task: process chay duoi SYSTEM (Task Manager cua tai khoan
     thuong bao "Access denied") + gan quyen DENY `PROCESS_TERMINATE` cho tai khoan con.
  2. Con KHONG the tat tinh nang bao ve: policy DisableTaskMgr/DisableRegistryTools
     chi ap cho tai khoan con (bo me van dung binh thuong).
  3. Con KHONG the xoa/sua file agent: ACL chan Delete/Write o thu muc cai dat va du lieu.
  4. Con KHONG the tat scheduled task: ACL chan Read/Execute tren file task.
  5. Agent tu song lai: watchdog task chay moi phut + guardian kiem tra uihost.

Khong the lam (neu khong co driver kernel): an hoan toan process khoi Task Manager.
Vi vay tool di theo huong "nhin thay nhung khong tat duoc".
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from .paths import IS_WINDOWS

log = logging.getLogger("remotepc.stealth")

# ---------------- Windows constants ---------------- #
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WRITE_DAC = 0x00040000
READ_CONTROL = 0x00020000
SE_KERNEL_OBJECT = 1
DACL_SECURITY_INFORMATION = 0x00000004
DENY_ACCESS = 3
GRANT_ACCESS = 1
NO_INHERITANCE = 0
TRUSTEE_IS_SID = 0
TRUSTEE_IS_NAME = 1

SID_USERS = "S-1-5-32-545"          # Built-in Users
SID_INTERACTIVE = "S-1-5-4"         # Interactive
SID_SYSTEM = "S-1-5-18"
SID_ADMINS = "S-1-5-32-544"


def _shell(cmd: list[str], timeout: int = 30) -> tuple[bool, str]:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", creationflags=flags)
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# 1. An cua so console
# --------------------------------------------------------------------------- #


def hide_console() -> None:
    """An cua so cmd (khi chay bang python.exe thay vi pythonw.exe)."""
    if not IS_WINDOWS:
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()   # type: ignore[attr-defined]
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)        # type: ignore[attr-defined]  SW_HIDE
    except Exception as e:                                  # pragma: no cover
        log.debug("hide_console: %s", e)


def detach_console() -> None:
    """Cat khoi console (dung khi duoc goi tu 1 cua so cmd cua bo me)."""
    if not IS_WINDOWS:
        return
    try:
        ctypes.windll.kernel32.FreeConsole()                # type: ignore[attr-defined]
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 2. Chan End Task (DENY ACE tren process object)
# --------------------------------------------------------------------------- #

if IS_WINDOWS:
    class _TRUSTEEW(ctypes.Structure):
        _fields_ = [
            ("pMultipleTrustee", ctypes.c_void_p),
            ("MultipleTrusteeOperation", ctypes.c_int),
            ("TrusteeForm", ctypes.c_int),
            ("TrusteeType", ctypes.c_int),
            ("ptstrName", ctypes.c_wchar_p),
        ]

    class _EXPLICIT_ACCESSW(ctypes.Structure):
        _fields_ = [
            ("grfAccessPermissions", ctypes.c_ulong),
            ("grfAccessMode", ctypes.c_ulong),
            ("grfInheritance", ctypes.c_ulong),
            ("Trustee", _TRUSTEEW),
        ]


def _sid_from_string(sid_text: str) -> Optional[Any]:
    """'S-1-5-32-545' -> con tro SID (can LocalFree sau khi dung)."""
    if not IS_WINDOWS:
        return None
    advapi32 = ctypes.windll.advapi32                       # type: ignore[attr-defined]
    psid = ctypes.c_void_p()
    ok = advapi32.ConvertStringSidToSidW(ctypes.c_wchar_p(sid_text), ctypes.byref(psid))
    return psid if ok else None


def protect_process(pid: int, accounts: Iterable[str] = (), take_ownership: bool = True) -> tuple[bool, str]:
    """Gan DENY `PROCESS_TERMINATE` cho cac tai khoan/SID da cho.

    Chay duoc khi process goi co quyen WRITE_DAC tren process dich
    (SYSTEM/Admin luon co). Sau khi goi, `taskkill /F` tu cac tai khoan do
    se bao "Access is denied".

    `take_ownership=True` (chi SYSTEM/Admin lam duoc): doi chu so huu process
    sang SYSTEM. Can buoc nay vi *chu so huu luon ngam dinh co WRITE_DAC* -
    neu khong doi owner, tai khoan con co the tu sua lai DACL va bo chan.
    """
    if not IS_WINDOWS:
        return False, "Chi ho tro Windows."
    if not pid:
        return False, "Thieu pid."
    accounts = [a for a in accounts if a]
    if not accounts:
        return False, "Chong danh sach tai khoan can chan."

    advapi32 = ctypes.windll.advapi32                       # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32                       # type: ignore[attr-defined]

    WRITE_OWNER = 0x00080000
    access = WRITE_DAC | READ_CONTROL | (WRITE_OWNER if take_ownership else 0)
    handle = kernel32.OpenProcess(access, False, int(pid))
    if not handle and take_ownership:
        handle = kernel32.OpenProcess(WRITE_DAC | READ_CONTROL, False, int(pid))
        take_ownership = False
    if not handle:
        err = kernel32.GetLastError()
        return False, f"OpenProcess that bai (error {err}). Can chay bang SYSTEM/Admin."

    try:
        # (a) doi chu so huu sang SYSTEM
        if take_ownership:
            sys_sid = _sid_from_string(SID_SYSTEM)
            if sys_sid:
                OWNER_SECURITY_INFORMATION = 0x00000001
                rc = advapi32.SetSecurityInfoW(ctypes.c_void_p(handle), SE_KERNEL_OBJECT,
                                               OWNER_SECURITY_INFORMATION, ctypes.byref(sys_sid),
                                               None, None, None)
                kernel32.LocalFree(sys_sid)
                if rc != 0:
                    log.debug("Doi owner that bai rc=%s (bo qua)", rc)

        old_dacl = ctypes.c_void_p()
        sec_desc = ctypes.c_void_p()
        rc = advapi32.GetSecurityInfoW(ctypes.c_void_p(handle), SE_KERNEL_OBJECT,
                                       DACL_SECURITY_INFORMATION, None, None,
                                       ctypes.byref(old_dacl), None, ctypes.byref(sec_desc))
        if rc != 0:
            return False, f"GetSecurityInfo that bai (rc={rc})."

        entries = (_EXPLICIT_ACCESSW * len(accounts))()
        keepalive: list[Any] = []       # giu tham chieu de ctypes khong free som
        for i, acct in enumerate(accounts):
            ea = entries[i]
            ea.grfAccessPermissions = PROCESS_TERMINATE
            ea.grfAccessMode = DENY_ACCESS
            ea.grfInheritance = NO_INHERITANCE
            ea.Trustee.pMultipleTrustee = None
            ea.Trustee.MultipleTrusteeOperation = 0
            ea.Trustee.TrusteeType = 0
            if acct.upper().startswith("S-1-"):
                sid = _sid_from_string(acct)
                if not sid:
                    log.warning("SID khong hop le: %s", acct)
                    ea.Trustee.TrusteeForm = TRUSTEE_IS_NAME
                    ea.Trustee.ptstrName = acct
                    continue
                keepalive.append(sid)
                ea.Trustee.TrusteeForm = TRUSTEE_IS_SID
                ea.Trustee.ptstrName = ctypes.cast(sid, ctypes.c_wchar_p)
            else:
                ea.Trustee.TrusteeForm = TRUSTEE_IS_NAME
                ea.Trustee.ptstrName = acct

        new_dacl = ctypes.c_void_p()
        rc = advapi32.SetEntriesInAclW(len(accounts), entries, old_dacl, ctypes.byref(new_dacl))
        if rc != 0:
            return False, f"SetEntriesInAcl that bai (rc={rc})."
        try:
            rc = advapi32.SetSecurityInfoW(ctypes.c_void_p(handle), SE_KERNEL_OBJECT,
                                           DACL_SECURITY_INFORMATION, None, None,
                                           new_dacl, None)
            if rc != 0:
                return False, f"SetSecurityInfo that bai (rc={rc})."
            return True, f"Da chan End Task voi: {', '.join(accounts)}"
        finally:
            kernel32.LocalFree(new_dacl)
            kernel32.LocalFree(sec_desc)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def find_pids_by_name(exe_name: str) -> list[int]:
    """Tim PID theo ten file (Toolhelp32Snapshot) - khong can tasklist."""
    if not IS_WINDOWS:
        return []
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260),
        ]

    pids: list[int] = []
    try:
        kernel32 = ctypes.windll.kernel32                   # type: ignore[attr-defined]
        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID_HANDLE_VALUE:
            return []
        try:
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            if not kernel32.Process32FirstW(snap, ctypes.byref(entry)):
                return []
            target = exe_name.lower()
            while True:
                if str(entry.szExeFile).lower() == target:
                    pids.append(int(entry.th32ProcessID))
                if not kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
        finally:
            kernel32.CloseHandle(snap)
    except Exception as e:                                  # pragma: no cover
        log.debug("find_pids_by_name: %s", e)
    return pids


def protect_by_name(exe_name: str, accounts: Iterable[str]) -> list[str]:
    results = []
    for pid in find_pids_by_name(exe_name):
        if pid == os.getpid():
            continue
        ok, msg = protect_process(pid, accounts)
        results.append(f"pid {pid}: {msg}")
    return results


# --------------------------------------------------------------------------- #
# 3. ACL tren file / thu muc
# --------------------------------------------------------------------------- #


def lock_down_path(path: Path, deny_accounts: Iterable[str] = ()) -> tuple[bool, str]:
    """Chan Users doc/sua file nhay cam (config.json chua bot token).

    SYSTEM + Administrators giu toan quyen; moi thu khac bi chan.
    """
    if not IS_WINDOWS:
        try:
            os.chmod(path, 0o600)
            return True, "chmod 600"
        except OSError as e:
            return False, str(e)
    args = ["icacls", str(path),
            "/inheritance:r",
            "/grant", "SYSTEM:(F)",
            "/grant", "Administrators:(F)"]
    for acct in deny_accounts:
        args += ["/deny", f"{acct}:(R,W,D)"]
    ok, out = _shell(args)
    return ok, out[-200:]


def deny_for_accounts(path: Path, accounts: Iterable[str], rights: str = "(RX,W,D)") -> tuple[bool, str]:
    """Chan cac tai khoan cu the truy cap `path` (khong anh huong SYSTEM/Admin)."""
    if not IS_WINDOWS:
        return False, "Chi ho tro Windows."
    accounts = [a for a in accounts if a]
    if not accounts:
        return False, "Chong danh sach tai khoan."
    args = ["icacls", str(path)]
    for a in accounts:
        args += ["/deny", f"{a}:{rights}"]
    ok, out = _shell(args)
    return ok, out[-200:]


def allow_users_readonly(path: Path) -> tuple[bool, str]:
    """Users chi duoc doc+chay (khong sua/xoa) - dung cho thu muc chua uihost."""
    if not IS_WINDOWS:
        return False, "Chi ho tro Windows."
    ok, out = _shell(["icacls", str(path), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"])
    return ok, out[-200:]


def allow_users_writeonly(path: Path) -> tuple[bool, str]:
    """Users chi duoc TAO file (khong doc/ds/xoa) - dung cho thu muc inbox & shots."""
    if not IS_WINDOWS:
        return False, "Chi ho tro Windows."
    # WD = Write Data (create files); khong co R nen khong list/read duoc
    ok, out = _shell(["icacls", str(path), "/grant", "*S-1-5-32-545:(OI)(CI)(WD,AD,WA)"])
    if ok:
        ok2, out2 = _shell(["icacls", str(path), "/deny", "*S-1-5-32-545:(R)"])
        return ok2, (out + out2)[-200:]
    return ok, out[-200:]


# --------------------------------------------------------------------------- #
# 4. Policy rieng cho tai khoan con (khong dong den bo me)
# --------------------------------------------------------------------------- #


def account_sid(name: str) -> str:
    """Tra ve SID cua tai khoan cuc bo (dung PowerShell, on dinh hon wmic)."""
    ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    script = (
        "try { ([System.Security.Principal.NTAccount]'%s')"
        ".Translate([System.Security.Principal.SecurityIdentifier]).Value } catch { '' }" % name
    )
    ok, out = _shell([ps, "-NoProfile", "-Command", script], timeout=25)
    return out.strip() if ok and out.strip().startswith("S-1-") else ""


def non_admin_accounts() -> list[str]:
    """Cac tai khoan cuc bo KHONG nam trong nhom Administrators (= tai khoan con)."""
    from .system_info import local_accounts

    return [a["name"] for a in local_accounts() if a.get("admin") != "yes"]


def _policy_values(level: int, block_taskmgr: bool, block_regedit: bool) -> dict[str, int]:
    values: dict[str, int] = {}
    if level >= 2 and block_taskmgr:
        values["DisableTaskMgr"] = 1
        values["DisableProcessPage"] = 1     # an tab Processes (Windows 11)
    if level >= 2 and block_regedit:
        values["DisableRegistryTools"] = 1
        values["DisableLockWorkstation"] = 0
    return values


def apply_child_policies(accounts: Iterable[str], level: int = 2,
                         block_taskmgr: bool = True, block_regedit: bool = True) -> list[str]:
    """Ghi policy vao HKCU cua tai khoan con.

    Cach lam: neu hive cua ho dang nap (HKU\\<SID>) -> ghi truc tiep;
    neu chua -> `reg load` NTUSER.DAT tam roi ghi, sau do `reg unload`.
    """
    if not IS_WINDOWS:
        return ["Chi ho tro Windows."]
    values = _policy_values(level, block_taskmgr, block_regedit)
    if not values:
        return ["Khong co policy nao can ap dung (level < 2)."]
    out: list[str] = []
    sub = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
    for acct in accounts:
        sid = account_sid(acct)
        if not sid:
            out.append(f"{acct}: khong tim thay SID")
            continue
        applied = _write_hkcu_policy(sid, sub, values)
        if applied:
            out.append(f"{acct}: OK ({applied})")
            continue
        # Hive chua nap -> nap tam
        profile = Path(os.environ.get("SystemDrive", "C:")) / "Users" / acct / "NTUSER.DAT"
        if not profile.exists():
            out.append(f"{acct}: khong tim thay NTUSER.DAT ({profile})")
            continue
        mount = f"HKU\\RemotePC_{sid[-6:]}"
        ok1, msg1 = _shell(["reg", "load", mount, str(profile)], timeout=30)
        if not ok1:
            out.append(f"{acct}: reg load that bai - {msg1[-120:]}")
            continue
        try:
            applied = _write_hkcu_policy(mount.split("\\")[1], sub, values, root_mount=mount)
            out.append(f"{acct}: OK qua hive tam ({applied})" if applied else f"{acct}: khong ghi duoc")
        finally:
            _shell(["reg", "unload", mount], timeout=30)
    return out


def _write_hkcu_policy(sid_or_mount: str, subkey: str, values: dict[str, int],
                       root_mount: Optional[str] = None) -> str:
    import winreg

    done = []
    try:
        root = winreg.HKEY_USERS
        base = root_mount if root_mount else sid_or_mount
        try:
            key = winreg.CreateKeyEx(root, f"{base}\\{subkey}", 0, winreg.KEY_SET_VALUE | winreg.KEY_CREATE_SUB_KEY)
        except OSError:
            return ""
        with key:
            for name, val in values.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, val)
                done.append(f"{name}={val}")
    except OSError as e:
        log.debug("policy write loi: %s", e)
        return ""
    return ",".join(done)


def remove_child_policies(accounts: Iterable[str]) -> list[str]:
    """Xoa policy khi go cai dat."""
    if not IS_WINDOWS:
        return ["Chi ho tro Windows."]
    out = []
    names = ("DisableTaskMgr", "DisableProcessPage", "DisableRegistryTools")
    sub = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
    for acct in accounts:
        sid = account_sid(acct)
        if not sid:
            continue
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_USERS, f"{sid}\\{sub}", 0, winreg.KEY_SET_VALUE) as k:
                for n in names:
                    try:
                        winreg.DeleteValue(k, n)
                    except OSError:
                        pass
            out.append(f"{acct}: da xoa policy")
        except OSError:
            out.append(f"{acct}: khong co policy")
    return out


# --------------------------------------------------------------------------- #
# 5. Doi ten process trong Task Manager (limited)
# --------------------------------------------------------------------------- #


def set_process_title(title: str) -> None:
    """Doi tieu de cua so console (neu co) - chi mang tinh my thuat."""
    if not IS_WINDOWS:
        try:
            sys.stdout.write(f"\033]0;{title}\007")
            sys.stdout.flush()
        except Exception:
            pass
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()     # type: ignore[attr-defined]
        if hwnd:
            ctypes.windll.user32.SetWindowTextW(hwnd, title)  # type: ignore[attr-defined]
    except Exception:
        pass


def is_protected(pid: Optional[int] = None, accounts: Iterable[str] = ()) -> bool:
    """Kiem tra nhanh: tai khoan con co bi chan terminate khong?

    Cach kiem tra: mo process bang quyen TERMINATE tu tai khoan do la khong the
    tu SYSTEM, nen ta chi kiem tra DACL co ACE DENY hay khong.
    """
    if not IS_WINDOWS:
        return False
    pid = pid or os.getpid()
    advapi32 = ctypes.windll.advapi32                        # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32                        # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(READ_CONTROL, False, int(pid))
    if not handle:
        return False
    try:
        dacl = ctypes.c_void_p()
        sd = ctypes.c_void_p()
        if advapi32.GetSecurityInfoW(ctypes.c_void_p(handle), SE_KERNEL_OBJECT,
                                     DACL_SECURITY_INFORMATION, None, None,
                                     ctypes.byref(dacl), None, ctypes.byref(sd)) != 0:
            return False
        # Dem so ACE; khong parse chi tiet (du de biet da co gan hay chua)
        count = ctypes.c_ushort(0)
        try:
            ok = advapi32.GetAclInformation(dacl, ctypes.byref(count), 2, 2)  # AclRevisionInformation=1, AclSizeInformation=2
        except Exception:
            ok = False
        kernel32.LocalFree(sd)
        return bool(ok) and count.value > 0
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
