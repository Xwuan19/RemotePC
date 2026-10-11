"""UIHOST - process chay trong session cua nguoi dung (con).

Nhiem vu duy nhat: nhung thu can "nhin thay duoc" tren man hinh
  * hien hop thoai / phu canh bao toan man hinh (lenh /msg cua bo me)
  * khoa man hinh, dang xuat
  * chup man hinh gui ve bot
  * bao ve guardian biet cua so nao dang mo (con dang choi game / hoc?)

UIHOST khong giu bot token, khong noi mang -> con co pha duoc cung khong lam
ro thong tin, va guardian (SYSTEM) se khoi dong lai no trong vai giay.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from . import actions, ipc, system_info
from .paths import Layout, data_dir, IS_WINDOWS

log = logging.getLogger("remotepc.uihost")

ALIVE_INTERVAL = 15.0
FOREGROUND_INTERVAL = 10.0
POLL_INTERVAL = 0.4


class UIHost:
    def __init__(self, layout: Layout):
        self.layout = layout.ensure()
        self.worker = ipc.JobWorker(layout, self.handle)
        self.inbox = layout.inbox_dir
        self.shots = layout.shots_dir
        self._last_alive = 0.0
        self._last_fg = 0.0
        self._stop = False
        self.pid = os.getpid()

    # ------------------------------------------------------------------ #

    def announce(self) -> None:
        """Bao guardian biet minh dang chay (de guardian gan quyen bao ve)."""
        user = os.environ.get("USERNAME") or system_info.collect(with_public_ip=False).user
        self._write_inbox("hello.json", {
            "pid": self.pid,
            "user": user,
            "session": system_info.current_session_id(),
            "exe": Path(sys.executable).name,
            "ts": time.time(),
        })

    def _write_inbox(self, name: str, payload: dict) -> None:
        try:
            self.inbox.mkdir(parents=True, exist_ok=True)
            tmp = self.inbox / f".{name}.{self.pid}.tmp"
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.inbox / name)
        except OSError as e:
            log.debug("ghi %s that bai: %s", name, e)

    # ---------------- xu ly job ---------------- #

    def handle(self, job: ipc.Job) -> tuple[bool, dict[str, Any], str]:
        kind = job.kind
        p = job.payload or {}
        log.debug("job %s: %s", kind, p)

        if kind == ipc.JOB_PING:
            return True, {"pid": self.pid, "user": os.environ.get("USERNAME", "")}, ""

        if kind == ipc.JOB_MESSAGE:
            ok, detail = actions.message_box(
                text=str(p.get("text") or ""),
                title=str(p.get("title") or "Thông báo từ bố/mẹ"),
                style=str(p.get("style") or "warn"),
                timeout=int(p.get("timeout") or 0),
                topmost=bool(p.get("topmost", True)),
                beep=bool(p.get("beep", True)),
            )
            return ok, {"detail": detail}, ("" if ok else detail)

        if kind == ipc.JOB_OVERLAY:
            ok, detail = actions.overlay(
                text=str(p.get("text") or ""),
                title=str(p.get("title") or "THÔNG BÁO QUAN TRỌNG"),
                duration=int(p.get("duration") or 60),
                bg=str(p.get("bg") or "#0b0b0b"),
                fg=str(p.get("fg") or "#ff5252"),
            )
            return ok, {"detail": detail}, ("" if ok else detail)

        if kind == ipc.JOB_LOCK:
            ok, detail = actions.lock_workstation()
            return ok, {"detail": detail}, ("" if ok else detail)

        if kind == ipc.JOB_LOGOFF:
            ok, detail = actions.logoff(force=bool(p.get("force", True)))
            return ok, {"detail": detail}, ("" if ok else detail)

        if kind == ipc.JOB_SCREENSHOT:
            name = str(p.get("name") or f"shot-{int(time.time())}.png")
            ok, path, detail = actions.screenshot(self.shots / name)
            if ok and path:
                return True, {"file": path.name, "size": path.stat().st_size}, ""
            return False, {"detail": detail}, detail

        if kind == ipc.JOB_FOREGROUND:
            win = system_info.get_active_window()
            return True, {"window": (win.label() if win else "")}, ""

        return False, {}, f"Loai job khong ho tro: {kind}"

    # ---------------- vong lap ---------------- #

    def tick(self) -> None:
        now = time.time()
        self.worker.process_once()

        if now - self._last_alive >= ALIVE_INTERVAL:
            self._last_alive = now
            self.announce()

        if now - self._last_fg >= FOREGROUND_INTERVAL:
            self._last_fg = now
            win = system_info.get_active_window()
            if win and (win.title or win.process):
                self._write_inbox("foreground.json", {
                    "title": win.title, "process": win.process, "pid": win.pid, "ts": now,
                    "idle": round(system_info.idle_seconds(), 1),
                })

    def run(self, once: bool = False) -> int:
        self.layout.ensure()
        self.announce()
        log.info("UIHOST san sang (pid=%s, user=%s)", self.pid, os.environ.get("USERNAME", "?"))
        try:
            while not self._stop:
                try:
                    self.tick()
                except Exception:                              # pragma: no cover
                    log.exception("tick loi")
                    time.sleep(2)
                if once:
                    break
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:                              # pragma: no cover
            pass
        return 0

    def stop(self) -> None:
        self._stop = True


# --------------------------------------------------------------------------- #
# Guardian doc cac bao cao tu inbox
# --------------------------------------------------------------------------- #


class InboxReader:
    """Phia guardian: doc hello/foreground tu uihost."""

    def __init__(self, layout: Layout):
        self.inbox = layout.inbox_dir
        self._hello_seen_pid = 0
        self._hello_seen_ts = 0.0

    def _read(self, name: str, delete: bool = False) -> Optional[dict]:
        p = self.inbox / name
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if delete:
            try:
                p.unlink()
            except OSError:
                pass
        return raw

    def take_hello(self) -> Optional[dict]:
        """Lay 'hello' moi (pid khac lan truoc hoac sau 60s) - dung de gan bao ve."""
        data = self._read("hello.json")
        if not data:
            return None
        pid = int(data.get("pid") or 0)
        ts = float(data.get("ts") or 0)
        if pid == self._hello_seen_pid and ts - self._hello_seen_ts < 60:
            return None
        self._hello_seen_pid, self._hello_seen_ts = pid, ts
        return data

    def foreground(self, max_age: float = 120.0) -> Optional[dict]:
        data = self._read("foreground.json")
        if not data:
            return None
        if time.time() - float(data.get("ts") or 0) > max_age:
            return None
        return data

    def uihost_alive(self, max_age: float = ALIVE_INTERVAL * 3) -> bool:
        data = self._read("hello.json")
        if not data:
            return False
        return (time.time() - float(data.get("ts") or 0)) <= max_age

    def uihost_pid(self) -> int:
        data = self._read("hello.json")
        return int((data or {}).get("pid") or 0)


def main(argv: Optional[list[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    argv = list(sys.argv[1:] if argv is None else argv)
    if IS_WINDOWS:
        from .stealth import hide_console
        hide_console()

    disguise = os.environ.get("REMOTEPC_NAME", "SysHealthAgent")
    base = Path(os.environ["REMOTEPC_DATA"]) if os.environ.get("REMOTEPC_DATA") else data_dir(disguise)
    layout = Layout(base)
    host = UIHost(layout)

    if "--once" in argv:
        return host.run(once=True)

    # Che do chay 1 lan roi thoat (dung khi guardian goi `schtasks /run` de hien 1 hop thoai)
    if "--job" in argv:
        i = argv.index("--job")
        try:
            spec = json.loads(argv[i + 1])
        except (IndexError, json.JSONDecodeError):
            return 2
        job = ipc.Job.from_dict(spec)
        ok, result, err = host.handle(job)
        host.worker.ack(job, ok, result, err)
        return 0 if ok else 1

    return host.run()


if __name__ == "__main__":
    raise SystemExit(main())
