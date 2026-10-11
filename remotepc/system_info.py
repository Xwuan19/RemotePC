"""Thong tin he thong: may, nguoi dung, uptime, IP, cua so dang mo, thoi gian idle.

Windows dung ctypes (khong can pywin32). Linux/macOS dung lenh shell tuong ung
de tool con chay/test duoc tren may dev.
"""

from __future__ import annotations

import ctypes
import logging
import os
import platform
import re
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .paths import IS_WINDOWS

log = logging.getLogger("remotepc.sysinfo")


def _run(cmd: list[str] | str, timeout: int = 10, shell: bool = False) -> str:
    """Chay lenh, tra ve stdout; loi -> ''."""
    try:
        flags = 0
        if IS_WINDOWS and not shell:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        p = subprocess.run(
            cmd, shell=shell, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace", creationflags=flags,
        )
        return (p.stdout or "").strip()
    except (subprocess.SubprocessError, OSError) as e:
        log.debug("Chay %s that bai: %s", cmd, e)
        return ""


@dataclass
class ActiveWindow:
    title: str = ""
    process: str = ""
    pid: int = 0
    at: float = field(default_factory=time.time)

    def label(self) -> str:
        proc = self.process or "?"
        title = self.title or "(khong co tieu de)"
        return f"{proc} — {title}"


@dataclass
class SystemInfo:
    hostname: str = ""
    user: str = ""
    os_name: str = ""
    os_version: str = ""
    arch: str = ""
    boot_time: float = 0.0
    uptime_seconds: float = 0.0
    lan_ip: str = ""
    public_ip: str = ""
    cpu_percent: float = -1.0
    mem_percent: float = -1.0
    mem_used_gb: float = 0.0
    mem_total_gb: float = 0.0
    disk_percent: float = -1.0
    disk_free_gb: float = 0.0
    disk_total_gb: float = 0.0
    sessions: list[str] = field(default_factory=list)
    is_admin: bool = False
    is_system: bool = False
    session_id: int = -1

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in vars(self).items()}


# --------------------------------------------------------------------------- #
# Windows: ctypes
# --------------------------------------------------------------------------- #

if IS_WINDOWS:
    user32 = ctypes.windll.user32            # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32        # type: ignore[attr-defined]
    advapi32 = ctypes.windll.advapi32        # type: ignore[attr-defined]
    dwmapi = ctypes.windll.dwmapi            # type: ignore[attr-defined]


# Struct Win32 dinh nghia o module level, dung type co do rong tuong minh
# (DWORD = c_uint32). Ly do: `c_ulong` la 4 byte tren Windows (mo hinh LLP64)
# nhung 8 byte tren Linux/macOS (LP64) -> struct se lech kich thuoc tuy he dieu
# hanh va khong the kiem chung layout bang test o may dev.
DWORD = ctypes.c_uint32
UINT32 = ctypes.c_uint32
ULONG64 = ctypes.c_ulonglong


class MEMORYSTATUSEX(ctypes.Structure):
    """typedef struct _MEMORYSTATUSEX (sysinfoapi.h) - 64 byte tren ca x86 lan x64."""
    _fields_ = [
        ("dwLength", DWORD),
        ("dwMemoryLoad", DWORD),
        ("ullTotalPhys", ULONG64),
        ("ullAvailPhys", ULONG64),
        ("ullTotalPageFile", ULONG64),
        ("ullAvailPageFile", ULONG64),
        ("ullTotalVirtual", ULONG64),
        ("ullAvailVirtual", ULONG64),
        ("ullAvailExtendedVirtual", ULONG64),
    ]


class LASTINPUTINFO(ctypes.Structure):
    """typedef struct tagLASTINPUTINFO (winuser.h) - 8 byte."""
    _fields_ = [("cbSize", UINT32), ("dwTime", DWORD)]


def get_active_window() -> Optional[ActiveWindow]:
    if not IS_WINDOWS:
        return _active_window_unix()
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        pid = ctypes.c_ulong(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = process_name(pid.value)
        return ActiveWindow(title=buf.value, process=proc, pid=int(pid.value))
    except Exception as e:                      # pragma: no cover
        log.debug("get_active_window loi: %s", e)
        return None


def process_name(pid: int) -> str:
    if not IS_WINDOWS or not pid:
        return ""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    try:
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not h:
            return ""
        try:
            size = ctypes.c_ulong(512)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
            if ok:
                return Path(buf.value).name
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        pass
    return ""


def get_visible_apps() -> list[dict[str, Any]]:
    """Liệt kê các ứng dụng có cửa sổ hiển thị (nhóm Apps trong Task Manager)."""
    if not IS_WINDOWS:
        return []
    try:
        desk = user32.OpenInputDesktop(0, False, 0x01FF)
        if desk:
            user32.SetThreadDesktop(desk)
    except Exception:
        pass

    apps: list[dict[str, Any]] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def _enum_cb(hwnd: Any, lp: Any) -> bool:
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if not title or title == "Program Manager":
                return True

            cloaked = DWORD(0)
            DWMWA_CLOAKED = 14
            dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value != 0:
                return True

            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if ex & WS_EX_TOOLWINDOW:
                return True

            WS_EX_APPWINDOW = 0x00040000
            GW_OWNER = 4
            owner = user32.GetWindow(hwnd, GW_OWNER)
            if owner and not (ex & WS_EX_APPWINDOW):
                return True

            pid = DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pid_val = int(pid.value)
            if not pid_val:
                return True

            for existing in apps:
                if existing["pid"] == pid_val:
                    if title not in existing["title"]:
                        existing["title"] = f"{existing['title']}, {title}"
                    return True

            pname = process_name(pid_val)
            apps.append({
                "pid": pid_val,
                "name": pname or f"PID-{pid_val}",
                "title": title,
            })
        except Exception as e:
            log.debug("enum_cb loi: %s", e)
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
    except Exception as e:
        log.debug("EnumWindows loi: %s", e)
    return apps


def idle_seconds() -> float:
    """So giay ke tu lan cuoi ban phim/chuot duoc dung."""
    if not IS_WINDOWS:
        return 0.0

    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetLastInputInfo(ctypes.byref(info)):
            tick = kernel32.GetTickCount()
            return max(0.0, (tick - info.dwTime) / 1000.0)
    except Exception:
        pass
    return 0.0


def uptime_seconds() -> float:
    if IS_WINDOWS:
        try:
            return kernel32.GetTickCount64() / 1000.0
        except Exception:
            pass
    try:
        return time.time() - os.stat("/proc/1").st_mtime
    except OSError:
        pass
    out = _run(["uptime"])
    m = re.search(r"up\s+(?:(\d+)\s+days?,\s*)?(\d+):(\d+)", out)
    if m:
        days = int(m.group(1) or 0)
        return days * 86400 + int(m.group(2)) * 3600 + int(m.group(3)) * 60
    return 0.0


def lan_ip() -> str:
    """IP LAN (khong can mang that: dung socket UDP trick)."""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1.0)
        s.connect(("8.8.8.8", 53))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return ""
    finally:
        if s:
            s.close()


def public_ip(timeout: float = 4.0) -> str:
    """IP cong khai (best effort, co the rong neu khong co mang)."""
    import urllib.request

    for url in ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                ip = r.read().decode("utf-8", errors="replace").strip()
            if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip) or ":" in ip:
                return ip
        except Exception:
            continue
    return ""


def is_admin() -> bool:
    if IS_WINDOWS:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:
            return False
    return os.geteuid() == 0 if hasattr(os, "geteuid") else False


def is_system_account() -> bool:
    """True khi process dang chay duoi tai khoan SYSTEM (session 0)."""
    if not IS_WINDOWS:
        return False
    return os.environ.get("USERNAME", "").upper() in {"SYSTEM", "LOCAL SERVICE", "NETWORK SERVICE"} or \
        os.environ.get("USERDOMAIN", "").upper() in {"NT AUTHORITY", "WINDOWS AUTHORITY"}


def is_session_zero() -> bool:
    """True khi process dang o session 0 (service/SYSTEM) -> KHONG hien thi duoc
    hop thoai len man hinh nguoi dung, phai day qua uihost."""
    if not IS_WINDOWS:
        return False
    try:
        sid = ctypes.c_uint32()
        k32 = getattr(ctypes.windll, "kernel32", None)
        if k32 and k32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid)):
            return sid.value == 0
    except Exception:
        pass
    if os.environ.get("SESSIONNAME", "").lower() == "services":
        return True
    return is_system_account()


def current_session_id() -> int:
    if not IS_WINDOWS:
        return -1
    try:
        return int(kernel32.WTSGetActiveConsoleSessionId())
    except Exception:
        return -1


def active_sessions() -> list[str]:
    """Danh sach phien dang nhap (Windows: `query user`)."""
    if not IS_WINDOWS:
        out = _run(["who"])
        return [line.strip() for line in out.splitlines() if line.strip()]
    raw = _run(["query", "user"]) or _run(["qwinsta"])
    lines = []
    for line in raw.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and "Active" in line:
            lines.append(line.strip())
    return lines or [raw.strip()] if raw.strip() else []


def logged_on_users() -> list[str]:
    """Cac tai khoan dang dang nhap (bo qua SYSTEM/SERVICE)."""
    users = []
    for line in active_sessions():
        token = line.split()[0] if line.split() else ""
        token = token.lstrip(">").strip()
        if token and token.lower() not in ("system", "services", "local", "dich"):
            users.append(token)
    if not users:
        env_user = os.environ.get("USERNAME", "")
        if env_user and env_user.upper() not in {"SYSTEM", "LOCAL SERVICE"}:
            users.append(env_user)
    return users


def local_accounts() -> list[dict[str, str]]:
    """Danh sach tai khoan cuc bo + co phai admin khong (Windows: `net localgroup`)."""
    if not IS_WINDOWS:
        return []
    admins_raw = _run(["net", "localgroup", "Administrators"])
    admins = {l.strip() for l in admins_raw.splitlines() if l.strip() and not l.startswith(
        ("Alias name", "Comment", "Members", "---", "The command"))}
    out = []
    raw = _run(["net", "user"])
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith(("User accounts for", "---", "The command")):
            continue
        for name in line.split():
            name = name.strip()
            if not name or name.endswith("$"):
                continue
            out.append({"name": name, "admin": "yes" if name in admins else "no"})
    return out


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", DWORD), ("dwHighDateTime", DWORD)]


_last_cpu_times: list[int] = []


def cpu_percent() -> float:
    """Trả về CPU % tức thời hoặc từ lần đo trước."""
    if not IS_WINDOWS:
        return -1.0
    global _last_cpu_times
    try:
        def to_u64(ft: FILETIME) -> int:
            return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
            return -1.0
        cur = [to_u64(idle), to_u64(kernel), to_u64(user)]
        if not _last_cpu_times:
            _last_cpu_times = cur
            time.sleep(0.05)
            if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
                return -1.0
            cur = [to_u64(idle), to_u64(kernel), to_u64(user)]

        idle_diff = cur[0] - _last_cpu_times[0]
        total_diff = (cur[1] - _last_cpu_times[1]) + (cur[2] - _last_cpu_times[2])
        _last_cpu_times = cur

        if total_diff > 0:
            pct = 100.0 * (total_diff - idle_diff) / total_diff
            return round(max(0.0, min(100.0, pct)), 1)
    except Exception:
        pass
    return -1.0


def memory_details() -> tuple[float, float, float]:
    """Trả về (percent, used_gb, total_gb)."""
    if not IS_WINDOWS:
        pct = memory_percent()
        return pct, 0.0, 0.0
    try:
        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        if kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            total_gb = round(stat.ullTotalPhys / (1024**3), 1)
            avail_gb = round(stat.ullAvailPhys / (1024**3), 1)
            used_gb = round(total_gb - avail_gb, 1)
            return float(stat.dwMemoryLoad), used_gb, total_gb
    except Exception:
        pass
    return -1.0, 0.0, 0.0


def disk_details(path: str = "C:\\" if IS_WINDOWS else "/") -> tuple[float, float, float]:
    """Trả về (percent, free_gb, total_gb)."""
    import shutil
    try:
        total, used, free = shutil.disk_usage(path)
        pct = round(used / total * 100, 1) if total else 0.0
        free_gb = round(free / (1024**3), 1)
        total_gb = round(total / (1024**3), 1)
        return pct, free_gb, total_gb
    except Exception:
        return -1.0, 0.0, 0.0


def memory_percent() -> float:
    pct, _, _ = memory_details()
    return pct


def collect(tag: str = "pc1", with_public_ip: bool = True) -> SystemInfo:
    """Lay thong tin may hien tai."""
    up = uptime_seconds()
    mem_pct, mem_used, mem_total = memory_details()
    disk_pct, disk_free, disk_total = disk_details()
    cpu_pct = cpu_percent()
    info = SystemInfo(
        hostname=socket.gethostname(),
        user=", ".join(logged_on_users()) or os.environ.get("USERNAME", "?"),
        os_name=platform.system(),
        os_version=platform.release() if IS_WINDOWS else f"{platform.release()} {platform.version()}",
        arch=platform.machine(),
        boot_time=time.time() - up if up else time.time(),
        uptime_seconds=up,
        lan_ip=lan_ip(),
        cpu_percent=cpu_pct,
        mem_percent=mem_pct,
        mem_used_gb=mem_used,
        mem_total_gb=mem_total,
        disk_percent=disk_pct,
        disk_free_gb=disk_free,
        disk_total_gb=disk_total,
        sessions=active_sessions()[:3],
        is_admin=is_admin(),
        is_system=is_system_account(),
        session_id=current_session_id(),
    )
    if IS_WINDOWS:
        # Windows 10/11: lay ban build day du
        try:
            info.os_version = f"{platform.release()} (build {platform.version().split('.')[-1]})"
        except Exception:
            pass
    if with_public_ip:
        info.public_ip = public_ip()
    return info


def format_shutdown_reason(raw: str) -> str:
    """Chuyen thong bao Event 1074 tieng Anh thanh tieng Viet ngan gon, de hieu."""
    if not raw:
        return "Không rõ nguyên nhân"
    import re
    if "aborted" in raw.lower() or "abort" in raw.lower():
        user_m = re.search(r"by user\s+(.*?)(?:\.|$)", raw, re.IGNORECASE)
        user = user_m.group(1).strip() if user_m else ""
        user_name = user.split("\\")[-1] if user else ""
        actor = "Lệnh từ xa (Bot Telegram)" if "system" in user.lower() else (user_name or "Người dùng")
        return f"Đã huỷ tắt máy bởi: *{actor}*"

    user_m = re.search(r"on behalf of user\s+(.*?)\s+for the following reason", raw, re.IGNORECASE)
    user = user_m.group(1).strip() if user_m else ""

    proc_m = re.search(r"The process\s+(.*?)\s*(?:\(|$)", raw, re.IGNORECASE)
    proc = Path(proc_m.group(1).strip()).name if proc_m else ""

    comment_m = re.search(r"Comment:\s*(.*)", raw, re.IGNORECASE)
    comment = comment_m.group(1).strip() if comment_m else ""

    type_m = re.search(r"Shutdown Type:\s*([^\s]+)", raw, re.IGNORECASE)
    stype = type_m.group(1).lower() if type_m else "shutdown"

    if not user and not proc:
        return raw[:300]

    action_vn = "Khởi động lại" if "restart" in stype else "Tắt máy"
    user_name = user.split("\\")[-1] if user else ""
    if "system" in user.lower():
        actor = "Lệnh từ xa (Bot Telegram)"
    elif "explorer" in proc.lower():
        actor = f"Người dùng bấm trực tiếp ({user_name})"
    else:
        actor = user_name or user

    parts = [f"{action_vn} bởi: *{actor}*"]
    if comment and comment.lower() != "no title for this reason could be found":
        parts.append(f"Lời nhắn: “{comment}”")
    return " · ".join(parts)


def last_shutdown_event() -> str:
    """Doc su kien 1074 (System log): ai/tien trinh nao vua ra lenh tat may, ly do gi."""
    if not IS_WINDOWS:
        return ""
    ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    script = (
        "$e = Get-WinEvent -FilterHashtable @{LogName='System'; Id=1074} -MaxEvents 1 -ErrorAction SilentlyContinue; "
        "if ($e) { ($e.TimeCreated.ToString('HH:mm:ss dd/MM/yyyy') + ' | ' + $e.Message) }"
    )
    out = _run([ps, "-NoProfile", "-Command", script], timeout=25)
    text = " ".join(out.split())
    return format_shutdown_reason(text)


def os_full_name() -> str:
    if not IS_WINDOWS:
        return f"{platform.system()} {platform.release()}"
    try:
        raw = _run(["cmd", "/c", "ver"])
        m = re.search(r"\[Version ([^\]]+)\]", raw)
        if m:
            return f"Windows {platform.release()} ({m.group(1)})"
    except Exception:
        pass
    return f"Windows {platform.release()}"


def _active_window_unix() -> Optional[ActiveWindow]:
    """Fallback cho Linux (dev/test): tra ve process hien tai."""
    return ActiveWindow(title="", process=Path(os.readlink("/proc/self/exe")).name
                        if Path("/proc/self/exe").exists() else "python")
