"""WATCHDOG - "nguoi giu cua" chay bang SYSTEM moi 1 phut.

Viec cua no:
  1. Guardian chet? -> day len (schtasks /run)
  2. Scheduled task bi xoa? -> tao lai tu ban XML luu trong ProgramData\\<Name>\\tasks\\
  3. Co nguoi dang nhap ma UI host chet? -> day len
  4. Bao ve bot neu phat hien ai do pha (co gioi han so lan bao de khoi spam)

Day la manh ghep quan trong nhat cua lop bao ve: du con co End Task duoc
guardian (thuc te la khong, vi process thuoc SYSTEM) thi 60 giay sau no van song lai.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import system_info
from .config import Config, load_config
from .installer import run, run_task, task_exists, task_names
from .paths import IS_WINDOWS, Layout
from .state import atomic_write_json

log = logging.getLogger("remotepc.watchdog")

MAX_ALERTS_PER_DAY = 6


class WatchdogState:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, Any] = {}
        try:
            self.data = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def save(self) -> None:
        atomic_write_json(self.path, self.data)

    def can_alert(self) -> bool:
        day = datetime.now().strftime("%Y-%m-%d")
        if self.get("alert_day") != day:
            self.set("alert_day", day)
            self.set("alert_count", 0)
        return int(self.get("alert_count", 0)) < MAX_ALERTS_PER_DAY

    def bump_alert(self) -> None:
        self.set("alert_count", int(self.get("alert_count", 0)) + 1)
        self.save()


def guardian_alive(layout: Layout) -> tuple[bool, int]:
    """Doc agent.pid va kiem tra process con song."""
    pid_file = layout.base / "agent.pid"
    try:
        info = json.loads(pid_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False, 0
    pid = int(info.get("pid") or 0)
    if not pid:
        return False, 0
    if pid == os.getpid():
        return False, pid
    if IS_WINDOWS:
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        try:
            kernel32 = ctypes.windll.kernel32                  # type: ignore[attr-defined]
            h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False, pid
            code = ctypes.c_ulong(0)
            ok = kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            kernel32.CloseHandle(h)
            return bool(ok and code.value == STILL_ACTIVE), pid
        except Exception:
            return False, pid
    try:
        os.kill(pid, 0)
        return True, pid
    except OSError:
        return False, pid


def restore_tasks(cfg: Config, layout: Layout) -> list[str]:
    """Tao lai task da bi xoa, dung ban XML luu luc install."""
    done: list[str] = []
    tasks_dir = layout.base / "tasks"
    names = task_names(cfg)
    for role, name in names.items():
        xml = tasks_dir / f"{role}.xml"
        if not xml.exists():
            continue
        if task_exists(name):
            continue
        ok, out = run(["schtasks", "/create", "/tn", name, "/xml", str(xml), "/f"], timeout=60)
        done.append(f"Đã tạo lại task `{name}`: {'OK' if ok else out[-100:]}")
    return done


def alert(cfg: Config, event: str, detail: str, state: WatchdogState) -> bool:
    """Gui canh bao ve Telegram (co gioi han so lan/ngay)."""
    if not cfg.telegram.bot_token or not cfg.telegram.allowed_chat_ids:
        return False
    if not state.can_alert():
        return False
    try:
        from . import messages as M
        from .telegram_client import TelegramClient, TelegramError

        tg = TelegramClient(cfg.telegram.bot_token, api_base=cfg.telegram.api_base, timeout=30)
        text = M.tamper_alert(event=event, detail=detail,
                              hostname=system_info.collect(with_public_ip=False).hostname,
                              at=f"{datetime.now():%H:%M:%S %d/%m/%Y}")
        for chat in cfg.telegram.allowed_chat_ids:
            try:
                tg.send_message(chat, text, parse_mode="Markdown")
            except TelegramError as e:
                log.warning("alert gui %s that bai: %s", chat, e)
        state.bump_alert()
        return True
    except Exception:
        log.exception("alert loi")
        return False


def run_once(cfg: Config, layout: Layout, verbose: bool = True) -> list[str]:
    """1 vong kiem tra. Tra ve danh sach viec da lam (de in khi chay tay)."""
    notes: list[str] = []
    state = WatchdogState(layout.base / "watchdog.json")
    names = task_names(cfg)

    if not IS_WINDOWS:
        alive, pid = guardian_alive(layout)
        if not alive:
            notes.append("(Không phải Windows: chỉ kiểm tra được pid)")
        state.save()
        return notes

    # 1) task bi xoa?
    restored = restore_tasks(cfg, layout)
    if restored:
        notes += restored
        alert(cfg, "Scheduled task bị xoá", " · ".join(restored)[:220], state)

    # 2) guardian chet?
    alive, pid = guardian_alive(layout)
    if not alive:
        ok, out = run_task(names["guardian"])
        notes.append(f"Guardian không chạy (pid cũ {pid}) → khởi động lại: "
                     f"{'OK' if ok else out[-100:]}")
        missing = [r for r, n in names.items() if not task_exists(n)]
        alert(cfg, "Agent bị tắt", f"Guardian không còn chạy (pid {pid}). "
                                  f"Đã tự khởi động lại."
                                  + (f" Thiếu task: {missing}" if missing else ""), state)
        state.set("guardian_down_count", int(state.get("guardian_down_count", 0)) + 1)
    else:
        if int(state.get("guardian_down_count", 0)):
            notes.append(f"Guardian đang chạy (pid {pid})")
        state.set("guardian_down_count", 0)

    # 3) uihost
    users = system_info.logged_on_users()
    ui_alive = False
    try:
        ui_info = json.loads((layout.inbox_dir / "hello.json").read_text(encoding="utf-8"))
        ui_alive = (time.time() - float(ui_info.get("ts") or 0)) < 60
    except (OSError, json.JSONDecodeError, ValueError):
        ui_alive = False
    if users and not ui_alive:
        ok, out = run_task(names["uihost"])
        notes.append(f"Có người đăng nhập ({', '.join(users)}) nhưng UI host chết → "
                     f"khởi động lại: {'OK' if ok else out[-100:]}")
        count = int(state.get("uihost_restart", 0)) + 1
        state.set("uihost_restart", count)
        if count in (3, 12, 30):
            alert(cfg, "Thành phần hiển thị bị tắt liên tục",
                  f"UI host đã phải khởi động lại {count} lần. "
                  f"Có thể con đang cố tắt thông báo của bố/mẹ.", state)
    elif not users:
        state.set("uihost_restart", 0)

    # 4) file cau hinh bi xoa/sua?
    if not layout.config_file.exists():
        alert(cfg, "File cấu hình bị xoá", str(layout.config_file), state)
        notes.append("Cấu hình bị xoá!")

    state.set("last_run", time.time())
    state.save()
    return notes


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stderr)
    cfg, cfg_path = load_config(os.environ.get("REMOTEPC_CONFIG"))
    base = Path(cfg.data_dir) if cfg.data_dir else Path(cfg_path).parent
    layout = Layout(base).ensure()
    if IS_WINDOWS:
        from .stealth import hide_console
        hide_console()
    verbose = "--verbose" in argv or "-v" in argv
    if "--loop" in argv:
        while True:
            try:
                for line in run_once(cfg, layout):
                    if verbose:
                        print(line)
            except Exception:
                log.exception("watchdog loop loi")
            time.sleep(60)
        return 0
    notes = run_once(cfg, layout, verbose=verbose)
    if verbose:
        for n in notes or ["Mọi thứ bình thường."]:
            print(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
