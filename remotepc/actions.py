"""Cac hanh dong thuc thi tren may: nguon dien, hop thoai, man hinh canh bao,
chup man hinh, liet ke ung dung.

Luu y ve session (Windows):
  * shutdown/restart : chay duoc tu SYSTEM
  * lock/logoff/hop thoai/chup anh : PHAI chay trong session cua nguoi dung
    -> guardian se day qua uihost (xem ipc.py / uihost.py)
Moi ham tra ve `(ok: bool, detail: str)` ho du lieu, va khong bao gio nem loi
ra ngoai de vong bot khong chet.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Optional

from .paths import IS_WINDOWS

log = logging.getLogger("remotepc.actions")

Result = tuple[bool, str]

# ---------------- MessageBox constants ---------------- #
MB_OK = 0x00000000
MB_ICONERROR = 0x00000010
MB_ICONQUESTION = 0x00000020
MB_ICONWARNING = 0x00000030
MB_ICONINFORMATION = 0x00000040
MB_SYSTEMMODAL = 0x00001000
MB_SETFOREGROUND = 0x00010000
MB_TOPMOST = 0x00040000

STYLE_FLAGS = {
    "info": MB_ICONINFORMATION,
    "warn": MB_ICONWARNING,
    "warning": MB_ICONWARNING,
    "error": MB_ICONERROR,
    "critical": MB_ICONERROR,
    "question": MB_ICONQUESTION,
}


def _popen(cmd: list[str], timeout: int = 20, **kw: Any) -> subprocess.CompletedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace", creationflags=flags, **kw)


# --------------------------------------------------------------------------- #
# NGUON DIEN
# --------------------------------------------------------------------------- #


def shutdown(delay: int = 30, message: str = "", force: bool = True, restart: bool = False) -> Result:
    """Tat (hoac khoi dong lai) may sau `delay` giay."""
    delay = max(0, min(int(delay), 10 * 60 * 60))
    message = (message or "").strip()[:512] or "May se tat theo yeu cau cua bo me."
    shutdown_bin = os.environ.get("REMOTEPC_SHUTDOWN_BIN") or "shutdown"
    if IS_WINDOWS:
        args = [shutdown_bin, "/r" if restart else "/s", "/t", str(delay), "/c", message]
        if force:
            args.append("/f")
    elif sys.platform == "darwin":
        args = [shutdown_bin, "-r" if restart else "-h", f"+{max(1, delay // 60)}", message]
    else:
        # Linux: shutdown can +/- phut; dung delay bang `sleep` neu < 60s
        mins = max(0, round(delay / 60))
        args = [shutdown_bin, "-r" if restart else "-h", (f"+{mins}" if mins else "now"), message]
    try:
        p = _popen(args, timeout=30)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        if p.returncode == 0:
            return True, f"Da dat lenh {'khoi dong lai' if restart else 'tat may'} sau {delay}s."
        return False, out or f"Lenh tra ve ma {p.returncode}"
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def abort_shutdown() -> Result:
    """Huy lenh tat may dang dem nguoc (`shutdown /a`)."""
    shutdown_bin = os.environ.get("REMOTEPC_SHUTDOWN_BIN") or "shutdown"
    try:
        if IS_WINDOWS:
            p = _popen([shutdown_bin, "/a"], timeout=15)
        else:
            p = _popen([shutdown_bin, "-c"], timeout=15)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        if p.returncode == 0:
            return True, "Da huy lich tat may."
        return False, out or "Khong co lich tat may nao de huy."
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def lock_workstation() -> Result:
    """Khoa man hinh (chi co tac dung trong session nguoi dung)."""
    if IS_WINDOWS:
        try:
            import ctypes

            # rundll32 cach cu; goi truc tiep user32 dang tin cay hon
            res = ctypes.windll.user32.LockWorkStation()   # type: ignore[attr-defined]
            if res:
                return True, "Da khoa man hinh."
            return False, "LockWorkStation tra ve 0 (co the dang chay ngoai session nguoi dung)."
        except Exception as e:                              # pragma: no cover
            return False, f"{type(e).__name__}: {e}"
    if sys.platform == "darwin":
        ok, detail = _shell("pmset displaysleepnow")
        return ok, detail or "Da khoa man hinh."
    for cmd in ("loginctl lock-session", "xdg-screensaver lock", "gnome-screensaver-command -l"):
        ok, detail = _shell(cmd)
        if ok:
            return True, "Da khoa man hinh."
    return False, "Khong tim duoc lenh khoa man hinh phu hop."


def logoff(force: bool = True) -> Result:
    """Dang xuat nguoi dung hien tai."""
    if IS_WINDOWS:
        # `shutdown /l` khong nhan /t hay /f
        try:
            p = _popen(["shutdown", "/l"], timeout=20)
            if p.returncode == 0:
                return True, "Da gui lenh dang xuat."
            return False, ((p.stdout or "") + (p.stderr or "")).strip() or "Dang xuat that bai."
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"{type(e).__name__}: {e}"
    ok, detail = _shell("loginctl terminate-user " + os.environ.get("USER", ""))
    if ok:
        return True, "Da dang xuat."
    return False, detail


def _shell(cmd: str, timeout: int = 20) -> Result:
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return (p.returncode == 0), ((p.stdout or "") + (p.stderr or "")).strip()
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# HOP THOAI / MAN HINH CANH BAO   (chay trong session nguoi dung)
# --------------------------------------------------------------------------- #


def message_box(text: str, title: str = "Tao nhắc mày", style: str = "warn",
                timeout: int = 0, topmost: bool = True, beep: bool = True) -> Result:
    """Hop thoai Windows. timeout=0 -> cho den khi bam OK."""
    text = (text or "").strip()
    if not text:
        return False, "Noi dung rong."
    if not IS_WINDOWS:
        return _message_box_unix(text, title, timeout)
    try:
        import ctypes

        flags = MB_OK | STYLE_FLAGS.get(style.lower(), MB_ICONWARNING)
        if topmost:
            flags |= MB_TOPMOST | MB_SETFOREGROUND | MB_SYSTEMMODAL
        if beep:
            try:
                ctypes.windll.user32.MessageBeep(0xFFFFFFFF)   # type: ignore[attr-defined]
            except Exception:
                pass
        user32 = ctypes.windll.user32                          # type: ignore[attr-defined]
        if timeout and timeout > 0:
            # MessageBoxTimeoutW: export khong chinh thuc nhung co san tu Win95 den Win11
            try:
                box = user32.MessageBoxTimeoutW
            except AttributeError:
                box = None
            if box is not None:
                box.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                ctypes.c_uint, ctypes.c_ushort, ctypes.c_uint]
                box.restype = ctypes.c_int
                rc = box(None, text, title, flags, 0, int(timeout * 1000))
                return True, f"Hop thoai da dong (ma {rc})."
            # fallback: chay trong thread co hen gio
            t = threading.Thread(target=user32.MessageBoxW, args=(None, text, title, flags), daemon=True)
            t.start()
            time.sleep(min(timeout, 600))
            return True, "Hop thoai da hien (khong tu dong dong duoc)."
        # timeout=0: hien hop thoai trong thread rieng de khong lam treo vong bot
        t = threading.Thread(target=user32.MessageBoxW, args=(None, text, title, flags), daemon=True)
        t.start()
        return True, "Hop thoai da hien len man hinh."
    except Exception as e:                                      # pragma: no cover
        return False, f"{type(e).__name__}: {e}"


def _message_box_unix(text: str, title: str, timeout: int) -> Result:
    """Fallback Linux/macOS khi dev/test."""
    for cmd in (
        ["zenity", "--warning", "--title", title, "--text", text, f"--timeout={timeout or 30}"],
        ["notify-send", title, text],
        ["osascript", "-e", f'display dialog "{text}" with title "{title}"'],
    ):
        if shutil.which(cmd[0]):
            ok, detail = _shell(" ".join(f'"{c}"' if " " in c else c for c in cmd),
                                timeout=(timeout + 5) if timeout else 35)
            if ok:
                return True, f"Da hien thong bao bang {cmd[0]}."
    log.info("[DEV] Thong bao: %s | %s", title, text)
    return True, "(che do dev) Da in ra log thay vi hien hop thoai."


def overlay(text: str, title: str = "THÔNG BÁO QUAN TRỌNG", duration: int = 60,
            bg: str = "#0b0b0b", fg: str = "#ff5252", font_size: int = 0) -> Result:
    """Phu toan man hinh mot canh bao (topmost, khong co thanh tieu de).

    dung `duration=0` -> giu den khi nguoi dung bam nut "Con da hieu".
    """
    text = (text or "").strip()
    if not text:
        return False, "Noi dung rong."
    try:
        import tkinter as tk
    except Exception:
        return message_box(text, title, "warn", timeout=duration)
    try:
        return _overlay_tk(tk, text, title, duration, bg, fg, font_size)
    except Exception as e:                                      # pragma: no cover
        log.warning("overlay tkinter loi (%s), quay ve hop thoai", e)
        return message_box(text, title, "warn", timeout=duration or 30)


def _overlay_tk(tk, text: str, title: str, duration: int, bg: str, fg: str, font_size: int) -> Result:
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=bg)

    vx, vy = 0, 0
    sw = root.winfo_screenwidth() or 1920
    sh = root.winfo_screenheight() or 1080
    if IS_WINDOWS:
        try:
            import ctypes
            u32 = ctypes.windll.user32
            vw = u32.GetSystemMetrics(78)  # SM_CXVIRTUALSCREEN
            vh = u32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
            if vw and vh:
                vx = u32.GetSystemMetrics(76)  # SM_XVIRTUALSCREEN
                vy = u32.GetSystemMetrics(77)  # SM_YVIRTUALSCREEN
                sw = vw
                sh = vh
            else:
                sw = u32.GetSystemMetrics(0) or sw
                sh = u32.GetSystemMetrics(1) or sh
        except Exception:
            pass

    root.geometry(f"{sw}x{sh}+{vx}+{vy}")

    # Co chu theo man hinh: man hinh ngang/hoc sinh dung laptop nho van doc ro
    size = font_size or max(20, min(56, int(min(sw / 26, sh / 14))))

    frame = tk.Frame(root, bg=bg)
    frame.place(relx=0.5, rely=0.5, anchor="center")
    tk.Label(frame, text=title, bg=bg, fg="#ffffff", font=("Segoe UI", int(size * 0.55), "bold"),
              wraplength=int(sw * 0.85)).pack(pady=(0, 24))
    tk.Label(frame, text=text, bg=bg, fg=fg, font=("Segoe UI", size, "bold"),
              wraplength=int(sw * 0.85), justify="center").pack()
    countdown = tk.Label(frame, text="", bg=bg, fg="#9e9e9e", font=("Segoe UI", int(size * 0.45)))
    countdown.pack(pady=(28, 0))

    closed = {"v": False}

    def close(_=None):
        if closed["v"]:
            return
        closed["v"] = True
        try:
            root.destroy()
        except Exception:
            pass

    if duration and duration > 0:
        def tick(remain: int):
            if closed["v"]:
                return
            if remain <= 0:
                close()
                return
            countdown.config(text=f"Tự động đóng sau {remain}s")
            root.after(1000, tick, remain - 1)
        root.after(100, tick, int(duration))
    else:
        tk.Button(frame, text="Biết rồi", command=close, font=("Segoe UI", int(size * 0.45)),
                   bg="#1f1f1f", fg="#ffffff", activebackground="#333333", padx=24, pady=8,
                   relief="flat").pack(pady=(32, 0))
    root.bind("<Escape>", close)
    root.protocol("WM_DELETE_WINDOW", close)
    try:
        root.lift()
        root.focus_force()
    except tk.TclError:
        pass
    root.mainloop()
    return True, f"Da hien canh bao toan man hinh ({duration}s)."


# --------------------------------------------------------------------------- #
# CHUP MAN HINH (trong session nguoi dung)
# --------------------------------------------------------------------------- #

_PS_SHOT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bmp.Save($args[0], [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "OK"
"""


def screenshot(path: Optional[Path] = None, quality_jpeg: int = 80) -> tuple[bool, Optional[Path], str]:
    """Chup man hinh chinh, tra ve (ok, duong dan, thong bao)."""
    path = Path(path) if path else Path(tempfile.gettempdir()) / f"rp-{int(time.time())}.png"
    path.parent.mkdir(parents=True, exist_ok=True)

    # 1) mss + Pillow neu co
    try:
        import mss                                     # type: ignore
        import mss.tools                               # type: ignore

        sct_cls = getattr(mss, "MSS", mss.mss)
        with sct_cls() as sct:
            shot = sct.grab(sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0])
            png = mss.tools.to_png(shot.rgb, shot.size)
        path.write_bytes(png)
        return True, path, "OK (mss)"
    except Exception as e:
        log.debug("mss khong dung duoc: %s", e)

    # 2) Windows: PowerShell + System.Drawing (khong can cai them gi)
    if IS_WINDOWS:
        ps = shutil.which("powershell") or shutil.which("pwsh") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        script = Path(tempfile.gettempdir()) / f"rp-shot-{os.getpid()}.ps1"
        try:
            script.write_text(_PS_SHOT, encoding="utf-8")
            p = _popen([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                        str(path)], timeout=45)
            out = ((p.stdout or "") + (p.stderr or "")).strip()
            if path.exists() and path.stat().st_size > 100:
                return True, path, "OK (powershell)"
            return False, None, out[:300] or "PowerShell khong tao duoc anh."
        except (subprocess.SubprocessError, OSError) as e:
            return False, None, f"{type(e).__name__}: {e}"
        finally:
            try:
                script.unlink(missing_ok=True)
            except OSError:
                pass

    # 3) Unix: cac cong cu co san
    for cmd in (["gnome-screenshot", "-f", str(path)], ["scrot", str(path)],
                ["import", "-window", "root", str(path)],
                ["screencapture", "-x", str(path)]):
        if shutil.which(cmd[0]):
            ok, detail = _shell(" ".join(f'"{c}"' if " " in str(c) else str(c) for c in cmd), timeout=30)
            if ok and path.exists():
                return True, path, f"OK ({cmd[0]})"
    return False, None, "Khong co cong cu chup man hinh (cai `pip install mss` neu can)."


# --------------------------------------------------------------------------- #
# UNG DUNG / TIEN TRINH
# --------------------------------------------------------------------------- #


def list_processes(top: int = 20, sort_by: str = "mem") -> list[dict[str, Any]]:
    """Danh sach tien trinh. Windows: tasklist CSV; Unix: ps."""
    rows: list[dict[str, Any]] = []
    if IS_WINDOWS:
        raw = ""
        try:
            p = _popen(["tasklist", "/fo", "csv", "/nh"], timeout=20)
            raw = p.stdout or ""
        except (subprocess.SubprocessError, OSError):
            raw = ""
        for line in io.StringIO(raw):
            line = line.strip()
            if not line:
                continue
            try:
                rec = next(csv.reader([line]))
            except (csv.Error, StopIteration):
                continue
            if len(rec) < 5:
                continue
            mem = rec[4].replace(".", "").replace(",", "").replace(" K", "").replace("K", "").strip()
            try:
                mem_kb = int(re.sub(r"\D", "", mem) or 0)
            except ValueError:
                mem_kb = 0
            rows.append({"name": rec[0], "pid": int(rec[1]) if rec[1].isdigit() else 0,
                         "session": rec[2], "mem_kb": mem_kb})
    else:
        raw = _shell("ps -eo pid,comm,rss --sort=-rss 2>/dev/null || ps -eo pid,comm,rss")[1]
        for line in raw.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 2 or not parts[0].isdigit():
                continue
            try:
                mem_kb = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            except ValueError:
                mem_kb = 0
            rows.append({"pid": int(parts[0]), "name": Path(parts[1]).name, "mem_kb": mem_kb})
    rows.sort(key=lambda r: r.get("mem_kb", 0), reverse=(sort_by == "mem"))
    return rows[: max(1, top)]


def kill_process(name_or_pid: str, force: bool = True) -> Result:
    """Tat 1 ung dung cua con (vd game) - dung can than."""
    if IS_WINDOWS:
        arg = str(name_or_pid)
        args = ["taskkill", "/IM", arg, "/F"] if not arg.isdigit() else ["taskkill", "/PID", arg, "/F"]
        if not force and not arg.isdigit():
            args = ["taskkill", "/IM", arg]
        try:
            p = _popen(args, timeout=20)
            out = ((p.stdout or "") + (p.stderr or "")).strip()
            return (p.returncode == 0), out[:300]
        except (subprocess.SubprocessError, OSError) as e:
            return False, f"{type(e).__name__}: {e}"
    ok, detail = _shell(f"pkill {'-9 ' if force else ''}{name_or_pid}")
    return ok, detail or "Da gui tin hieu."

