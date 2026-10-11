"""AGENT (guardian) - vong lap chinh.

  * long-polling Telegram, nhan lenh tu bo me
  * chay Monitor (boot / login / idle / gio gioi nghiem)
  * watchdog: giu uihost song, gan lai quyen chong End Task dinh ky
  * thuc thi lenh: tat may, khoa may, hien thong bao, chup man hinh...

Guardian thuong chay duoi SYSTEM (session 0) nen moi thu can "hien len man hinh"
duoc day qua uihost (xem ipc.py).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from . import actions, ipc, messages as M, system_info
from .commands import ParsedCommand, format_duration, parse_duration, parse_message
from .config import Config, layout_for, load_config, save_config, set_by_path
from .curfew import format_min, parse_days, parse_hhmm
from .monitor import Monitor
from .paths import IS_WINDOWS, Layout, is_frozen, python_command_for
from .uihost import InboxReader
from .state import Store
from .telegram_client import TelegramClient, TelegramError, Update

log = logging.getLogger("remotepc.bot")

MONITOR_INTERVAL = 5.0
MONITOR_BOOT_DELAY = 8.0          # doi he thong on dinh sau khi may bat (REMOTEPC_BOOT_DELAY)
WATCHDOG_INTERVAL = 20.0
PROTECT_INTERVAL = 180.0
PIN_SESSION_MINUTES = 15

# Lenh bat buoc phai co PIN (tranh con muon dien thoai bo me ma nghich)
PIN_COMMANDS = {
    "shutdown", "restart", "logoff", "kill", "uninstall", "protect", "reload",
}
# `/abort` CO tinh khong doi PIN: khi lenh tat may chay roi thi phai huy duoc ngay
# /curfew va /config chi can PIN khi *doi* gia tri (xem thi khong)
CURFEW_VIEW_SUBS = {"", "show", "xem", "list"}

BOT_COMMAND_MENU = [
    ("status", "Trạng thái máy & giờ giới nghiêm"),
    ("msg", "Hiện thông báo trên màn hình con"),
    ("shutdown", "Tắt máy từ xa"),
    ("restart", "Khởi động lại"),
    ("lock", "Khoá màn hình"),
    ("screenshot", "Chụp màn hình gửi về"),
    ("curfew", "Xem/đổi giờ giới nghiêm"),
    ("apps", "Ứng dụng đang chạy"),
    ("log", "Nhật ký sự kiện"),
    ("abort", "Huỷ lệnh tắt máy"),
    ("help", "Hướng dẫn"),
]


def make_status_keyboard() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "📊 Trạng thái", "callback_data": "/status"},
                {"text": "📸 Chụp màn hình", "callback_data": "/screenshot"},
            ],
            [
                {"text": "🔒 Khoá máy", "callback_data": "/lock"},
                {"text": "🔴 Tắt máy (60s)", "callback_data": "/shutdown 60"},
            ]
        ]
    }


class Agent:
    """Toan bo vong doi cua agent tren 1 may."""

    def __init__(self, cfg: Config, cfg_path: Path, layout: Layout, stealth: bool = True):
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.layout = layout.ensure()
        self.stealth_enabled = stealth and cfg.stealth.level > 0
        self.store = Store(layout, max_events=cfg.monitoring.max_events,
                           keep_days=cfg.monitoring.keep_events_days)
        self.tg: Optional[TelegramClient] = None
        try:
            self.tg = TelegramClient(cfg.telegram.bot_token, api_base=cfg.telegram.api_base,
                                     timeout=cfg.telegram.poll_timeout + 15,
                                     proxy=cfg.telegram.proxy or None)
        except ValueError as e:
            log.warning("Chua cau hinh bot token: %s", e)
        self.queue = ipc.JobQueue(layout)
        self.inbox = InboxReader(layout)
        self.monitor = Monitor(
            cfg, self.store, notify=self.notify, do_action=self.do_action,
            foreground=self.current_app, screenshot=self.grab_screenshot,
        )
        self._stop = threading.Event()
        self._offset = 0
        self._offset_file = layout.base / "offset.dat"
        self._pid_file = layout.base / "agent.pid"
        self._last_protect = 0.0
        self._uihost_fail = 0
        self._threads: list[threading.Thread] = []
        self._session_start_time = time.time()
        self._load_offset()

    # ================================================================== #
    # VONG DOI
    # ================================================================== #

    def start(self) -> None:
        self._write_pid()
        self._setup_logging()
        log.info("Agent khoi dong (pid=%s, tag=%s, stealth=%s)", os.getpid(),
                 self.cfg.machine_tag, self.stealth_enabled)

        if self.stealth_enabled:
            from . import stealth as st
            if self.cfg.stealth.hide_console:
                st.hide_console()
            if self.cfg.stealth.randomize_process_title:
                st.set_process_title(self.cfg.stealth.description)

        self.protect_self(force=True)

        if not self.cfg.telegram.bot_token:
            log.error("Chua co bot_token - chay `remotepc init` truoc.")
        if not self.cfg.telegram.allowed_chat_ids:
            log.warning("Chua cap phep chat_id nao. Gui /pair <PIN> cho bot de ghep noi.")
        if not self.cfg.has_pin():
            log.warning("Chua dat PIN: lenh tat may/go cai dat khong can xac thuc.")
            self.notify(
                f"{M.EMOJI['warn']} *CHƯA ĐẶT MÃ PIN*\n"
                f"Máy `{self.cfg.machine_tag}` đang cho phép tắt máy / đổi giờ / gỡ tool "
                f"mà không cần xác thực.\n"
                f"Ai cầm được điện thoại có chat này là điều khiển được máy con.\n"
                f"👉 Gửi `/pin <mã 4 số trở lên>` để đặt mã ngay.")

        self._threads = [
            threading.Thread(target=self._monitor_loop, name="monitor", daemon=True),
            threading.Thread(target=self._watchdog_loop, name="watchdog", daemon=True),
        ]
        for t in self._threads:
            t.start()

        try:
            self._poll_loop()
        except KeyboardInterrupt:                        # pragma: no cover
            self.stop()

    def stop(self, reason: str = "") -> None:
        log.info("Agent dung (%s)", reason or "yeu cau")
        self._stop.set()
        self.store.save()
        self._pid_file.unlink(missing_ok=True)

    def _setup_logging(self) -> None:
        root = logging.getLogger("remotepc")
        root.setLevel(logging.INFO)
        if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
            try:
                fh = logging.FileHandler(self.layout.log_file, encoding="utf-8")
                fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
                root.addHandler(fh)
            except OSError:
                pass
        # Khong in ra console khi chay nen (tranh lo dien)
        if self.stealth_enabled:
            for h in list(root.handlers):
                if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                    root.removeHandler(h)

    def _write_pid(self) -> None:
        try:
            self._pid_file.write_text(json.dumps({
                "pid": os.getpid(), "started": time.time(),
                "exe": Path(sys.executable).name, "session0": system_info.is_session_zero(),
            }), encoding="utf-8")
        except OSError:
            pass

    # ================================================================== #
    # GUI TIN NHAN
    # ================================================================== #

    def notify(self, text: str, kind: str = "", photo: Optional[bytes] = None,
                silent: bool = False, reply_markup: Optional[dict] = None) -> bool:
        """Gui ve tat ca chat duoc cap phep."""
        if not text:
            return False
        chats = self.cfg.telegram.allowed_chat_ids
        if not self.tg:
            log.info("[chua co bot token] %s: %s", kind, text.replace("\n", " | ")[:160])
            return False
        if not chats:
            log.info("[khong co chat_id] %s: %s", kind, text.replace("\n", " | ")[:160])
            return False
        if reply_markup is None and kind in ("boot", "curfew_warn", "curfew_start"):
            reply_markup = make_status_keyboard()
        ok_any = False
        for chat_id in chats:
            try:
                if photo:
                    self.tg.send_photo(chat_id, photo, caption=text[:1000],
                                       filename=f"{self.cfg.machine_tag}.png", silent=silent)
                else:
                    self.tg.send_message(chat_id, text, parse_mode="Markdown", silent=silent,
                                         reply_markup=reply_markup)
                ok_any = True
            except TelegramError as e:
                log.error("Gui toi %s that bai: %s", chat_id, e)
            except Exception:
                log.exception("notify loi")
        if kind:
            self.store.log("notify", kind=kind, chars=len(text), delivered=ok_any)
        return ok_any

    def reply(self, upd: Update, text: str, silent: Optional[bool] = None,
              reply_markup: Optional[dict] = None) -> None:
        if not text or not self.tg:
            return
        try:
            self.tg.send_message(upd.chat_id, text, parse_mode="Markdown",
                                 silent=self.cfg.telegram.silent_replies if silent is None else silent,
                                 reply_markup=reply_markup)
        except TelegramError as e:
            log.error("reply that bai: %s", e)
            try:
                self.tg.send_message(upd.chat_id, text[:4000], reply_markup=reply_markup)
            except TelegramError:
                pass

    # ================================================================== #
    # VONG LAP TELEGRAM
    # ================================================================== #

    def _load_offset(self) -> None:
        try:
            self._offset = int(self._offset_file.read_text().strip() or 0)
        except (OSError, ValueError):
            self._offset = 0

    def _save_offset(self) -> None:
        try:
            self._offset_file.write_text(str(self._offset), encoding="utf-8")
        except OSError:
            pass

    def _poll_loop(self) -> None:
        backoff = 1
        first = True
        while not self._stop.is_set():
            if not self.tg:
                self._stop.wait(60)
                continue
            try:
                updates = self.tg.get_updates(self._offset, timeout=self.cfg.telegram.poll_timeout)
                backoff = 1
                if first:
                    first = False
                    self.tg.set_commands(BOT_COMMAND_MENU)
                for upd in updates:
                    self._offset = max(self._offset, upd.update_id + 1)
                    self._save_offset()
                    try:
                        if upd.raw.get("callback_query"):
                            cb_id = upd.raw["callback_query"].get("id")
                            if cb_id and hasattr(self.tg, "answer_callback_query"):
                                self.tg.answer_callback_query(cb_id)
                        self._handle_update(upd)
                    except Exception:
                        log.exception("Xu ly update loi")
                        try:
                            self.reply(upd, M.cmd_fail("LỆNH", traceback.format_exc(limit=2)))
                        except Exception:
                            pass
            except TelegramError as e:
                log.warning("getUpdates loi: %s", e)
                if e.code in (401, 404):
                    self.notify(f"{M.EMOJI['fail']} Bot token sai/hết hạn. Agent tạm dừng nghe lệnh.")
                    self._stop.wait(300)
                    continue
                self._stop.wait(min(backoff, 60))
                backoff = min(backoff * 2, 60)
            except Exception:
                log.exception("poll loop loi")
                self._stop.wait(min(backoff, 60))
                backoff = min(backoff * 2, 60)

    def _handle_update(self, upd: Update) -> None:
        if not upd.text.strip():
            return
        # ---- cap phep ----
        if not self.cfg.is_allowed_chat(upd.chat_id):
            cmd = parse_message(upd.text, self.cfg.machine_tag, False)
            if cmd.command == "pair":
                self._do_pair(upd, cmd)
                return
            log.warning("Tu choi chat %s (%s)", upd.chat_id, upd.from_name)
            # Chi tra loi 1 lan / chat de khong lo bot token
            if not self.store.state.extra.setdefault("rejected", {}).get(str(upd.chat_id)):
                self.store.state.extra["rejected"][str(upd.chat_id)] = time.time()
                self.store.save()
                self.reply(upd, M.unauthorized(upd.chat_id))
                for chat in self.cfg.telegram.allowed_chat_ids:
                    self.notify(f"{M.EMOJI['tamper']} Có chat lạ `{upd.chat_id}` "
                                f"({upd.from_name or '?'}) vừa nhắn cho bot của máy "
                                f"`{self.cfg.machine_tag}`.")
            return

        cmd = parse_message(upd.text, self.cfg.machine_tag, self.cfg.require_tag)
        if not cmd.ok:
            if cmd.other_machine:
                return                     # may khac se tra loi, minh im lang
            self.reply(upd, cmd.error or M.unknown_command(cmd.command))
            return

        # ---- kiem tra tuoi cua lenh (chong thuc thi lenh cu khi may vua bat lai) ----
        now = time.time()
        age = now - upd.date if upd.date else 0
        is_stale = (upd.date and upd.date < (self._session_start_time - 15)) or (age > 180)
        if is_stale and cmd.command in ("shutdown", "restart", "lock", "logoff", "kill", "uninstall", "msg"):
            log.warning("Bo qua lenh %s qua han (age=%ss, date=%s)", cmd.command, round(age, 1), upd.date)
            from datetime import datetime
            sent_time = datetime.fromtimestamp(upd.date).strftime("%H:%M:%S %d/%m/%Y") if upd.date else "trước đó"
            self.reply(upd, f"{M.EMOJI['warn']} Lệnh `/{cmd.command}` được gửi lúc `{sent_time}` "
                            f"(khi máy đang tắt hoặc đã gửi cách đây {int(age)}s).\n"
                            f"Agent đã tự động BỎ QUA lệnh này để tránh thao tác nhầm khi máy vừa bật lại.")
            return

        self.store.state.last_command_at = time.time()
        self.store.log("command", cmd=cmd.command, chat=upd.chat_id, who=upd.from_name,
                       args=cmd.text[:120])
        handler = self.HANDLERS.get(cmd.command)
        if handler is None:
            self.reply(upd, M.unknown_command(cmd.command))
            return

        # ---- kiem tra PIN ----
        if self.requires_pin(cmd) and not self._pin_ok(cmd, upd):
            self.reply(upd, M.need_pin(cmd.command))
            return

        try:
            answer = handler(self, cmd, upd)
        except Exception as e:
            log.exception("handler %s loi", cmd.command)
            answer = M.cmd_fail(cmd.command, f"{type(e).__name__}: {e}")
        if answer:
            markup = make_status_keyboard() if cmd.command in ("status", "ping", "help") else None
            self.reply(upd, answer, reply_markup=markup)
        if self.cfg.notify.on_command and cmd.command not in ("help", "status", "ping"):
            pass  # da tra loi truc tiep, khong gui trung

    def requires_pin(self, cmd: ParsedCommand) -> bool:
        """Lenh nay co bat buoc xac thuc PIN khong?"""
        if cmd.command in PIN_COMMANDS:
            return True
        sub = (cmd.args[0] if cmd.args else "").lower()
        if cmd.command == "curfew":
            return sub not in CURFEW_VIEW_SUBS
        if cmd.command == "config":
            return sub in ("set", "dat")
        return False

    def _pin_ok(self, cmd: ParsedCommand, upd: Update) -> bool:
        if not self.cfg.has_pin():
            return True                       # chua dat PIN -> khong chan
        given = cmd.options.get("pin") or cmd.options.get("p") or ""
        if given and self.cfg.check_pin(given):
            return True
        until = float(self.store.state.extra.setdefault("unlocked", {}).get(str(upd.chat_id), 0))
        return until > time.time()

    def _do_pair(self, upd: Update, cmd: ParsedCommand) -> None:
        """`/pair <PIN>` - ghep noi chat_id cua bo me voi may nay."""
        token = (cmd.args[0] if cmd.args else "") or cmd.options.get("pin", "")
        if self.cfg.has_pin() and not self.cfg.check_pin(token):
            self.reply(upd, f"{M.EMOJI['fail']} Sai mã PIN. Ghép nối bị từ chối.")
            for chat in self.cfg.telegram.allowed_chat_ids:
                self.notify(f"{M.EMOJI['tamper']} Có người thử ghép nối sai PIN từ chat "
                            f"`{upd.chat_id}` ({upd.from_name or '?'}) lúc "
                            f"{datetime.now():%H:%M:%S}.")
            return
        if upd.chat_id not in self.cfg.telegram.allowed_chat_ids:
            self.cfg.telegram.allowed_chat_ids.append(int(upd.chat_id))
            save_config(self.cfg, self.cfg_path)
        self.store.log("pair", chat=upd.chat_id, who=upd.from_name)
        info = system_info.collect(with_public_ip=False)
        self.reply(upd, M.cmd_ok("GHÉP NỐI", (
            f"Chat `{upd.chat_id}` đã được cấp quyền điều khiển máy `{self.cfg.machine_tag}`.\n"
            f"Máy: `{info.hostname}` · Người dùng: `{info.user}`\n"
            f"Gửi `/help` để xem bộ lệnh.")))
        self.notify(f"{M.EMOJI['ok']} Máy `{self.cfg.machine_tag}` ({info.hostname}) "
                    f"đã ghép nối với chat `{upd.chat_id}`.")

    # ================================================================== #
    # MONITOR / WATCHDOG LOOPS
    # ================================================================== #

    def _monitor_loop(self) -> None:
        # Cho he thong on dinh sau khi boot (Windows can vai giay de co mang/user)
        try:
            delay = float(os.environ.get("REMOTEPC_BOOT_DELAY", MONITOR_BOOT_DELAY))
        except ValueError:
            delay = MONITOR_BOOT_DELAY
        self._stop.wait(max(0.0, delay))
        while not self._stop.is_set():
            try:
                self.monitor.tick()
                self.store.flush_if_dirty()
            except Exception:
                log.exception("monitor tick loi")
            self._stop.wait(MONITOR_INTERVAL)

    def _watchdog_loop(self) -> None:
        self._stop.wait(15)
        while not self._stop.is_set():
            try:
                self.keep_uihost_alive()
                self.prune_state()
            except Exception:
                log.exception("watchdog loi")
            now = time.time()
            if now - self._last_protect > PROTECT_INTERVAL:
                self.protect_self(force=True)
                self.protect_uihost()
            self._stop.wait(WATCHDOG_INTERVAL)

    def prune_state(self) -> None:
        st = self.store.state
        st.prune_fired(time.time() - 8 * 86400)
        self.monitor.engine.prune(time.time() - 8 * 86400)
        self.queue.purge_expired()
        # don anh cu
        try:
            cutoff = time.time() - 3 * 86400
            for f in self.layout.shots_dir.glob("*.png"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
        except OSError:
            pass

    # ================================================================== #
    # DIEU PHOI HANH DONG (guardian -> uihost)
    # ================================================================== #

    @property
    def needs_uihost(self) -> bool:
        return system_info.is_session_zero()

    def do_action(self, action: str, **kw: Any) -> bool:
        """Thuc thi 1 hanh dong; tu chon duong di phu hop voi session."""
        try:
            return self._do_action(action, **kw)
        except Exception as e:
            log.exception("do_action(%s) loi", action)
            self.store.log("action_error", action=action, error=str(e))
            return False

    def _do_action(self, action: str, **kw: Any) -> bool:
        ui_actions = {"popup", "overlay", "lock", "logoff", "screenshot"}
        if action in ui_actions and self.needs_uihost:
            return self._via_uihost(action, **kw)
        if action in ("popup", "overlay"):
            text = str(kw.get("text") or "")
            title = str(kw.get("title") or "Thông báo từ bố/mẹ")
            as_overlay = action == "overlay" or bool(kw.get("overlay"))
            if as_overlay:
                ok, detail = actions.overlay(text=text, title=title,
                                             duration=int(kw.get("duration") or kw.get("timeout") or 60))
            else:
                ok, detail = actions.message_box(text=text, title=title,
                                                 style=str(kw.get("style") or "warn"),
                                                 timeout=int(kw.get("timeout") or 0))
            self.store.log("popup", ok=ok, overlay=as_overlay, text=text[:120])
            if not ok:
                log.warning("Khong hien duoc thong bao: %s", detail)
            return ok
        if action == "lock":
            ok, detail = actions.lock_workstation()
            self.store.log("lock", ok=ok, detail=detail)
            return ok
        if action == "logoff":
            ok, detail = actions.logoff()
            self.store.log("logoff", ok=ok, detail=detail)
            return ok
        if action in ("shutdown", "restart"):
            delay = int(kw.get("delay") if kw.get("delay") is not None else self.cfg.power.shutdown_delay)
            force = bool(kw.get("force", self.cfg.power.force_close_apps))
            msg = kw.get("message") or kw.get("text") or ""
            ok, detail = actions.shutdown(delay=delay, message=msg, force=force,
                                          restart=(action == "restart"))
            self.store.state.pending_shutdown = {"at": time.time(), "delay": delay,
                                                 "action": action, "reason": kw.get("reason", ""),
                                                 "ok": ok}
            self.store.save()
            self.store.log(action, ok=ok, delay=delay, detail=detail, reason=kw.get("reason", ""))
            if ok and self.cfg.notify.on_shutdown:
                self.notify(M.shutdown_report(reason=kw.get("reason") or "lệnh từ xa",
                                              hostname=system_info.collect(with_public_ip=False).hostname,
                                              at=f"{datetime.now():%H:%M:%S} (+{delay}s)"))
            return ok
        if action == "abort":
            ok, detail = actions.abort_shutdown()
            self.store.state.pending_shutdown = {}
            self.store.save()
            self.store.log("abort_shutdown", ok=ok)
            return ok
        if action == "screenshot":
            return bool(self.grab_screenshot())
        log.warning("Hanh dong la: %s", action)
        return False

    def _via_uihost(self, action: str, **kw: Any) -> bool:
        self.keep_uihost_alive(force=True)
        kind_map = {"popup": ipc.JOB_OVERLAY if kw.get("overlay") else ipc.JOB_MESSAGE,
                    "overlay": ipc.JOB_OVERLAY, "lock": ipc.JOB_LOCK,
                    "logoff": ipc.JOB_LOGOFF, "screenshot": ipc.JOB_SCREENSHOT}
        kind = kind_map[action]
        payload: dict[str, Any] = {}
        if kind in (ipc.JOB_MESSAGE, ipc.JOB_OVERLAY):
            payload = {"text": kw.get("text", ""), "title": kw.get("title", "Thông báo từ bố/mẹ"),
                       "style": kw.get("style", "warn"),
                       "timeout": int(kw.get("timeout") or 0),
                       "duration": int(kw.get("duration") or kw.get("timeout") or 60),
                       "topmost": True, "beep": True}
        elif kind == ipc.JOB_SCREENSHOT:
            payload = {"name": f"shot-{int(time.time())}.png"}
        ack = self.queue.request(kind, payload, timeout=float(kw.get("wait", 20)),
                                 wait_ack=action != "popup" or bool(kw.get("wait_ack", False)))
        ok = bool(ack and ack.ok)
        self.store.log("uihost_action", action=action, ok=ok,
                       error=(ack.error if ack else "uihost khong phan hoi"))
        if not ok:
            log.warning("uihost khong thuc thi duoc %s: %s", action,
                        ack.error if ack else "timeout")
        return ok

    def keep_uihost_alive(self, force: bool = False) -> bool:
        """Bao dam uihost dang chay trong session cua con."""
        if not self.needs_uihost:
            return True
        if not force and self.inbox.uihost_alive():
            self._uihost_fail = 0
            return True
        started = self._start_uihost()
        if started:
            self._uihost_fail = 0
        else:
            self._uihost_fail += 1
            if self._uihost_fail in (3, 10, 25):
                self.notify(M.tamper_alert(
                    event="UI host không khởi động được",
                    detail=f"đã thử {self._uihost_fail} lần",
                    hostname=system_info.collect(with_public_ip=False).hostname,
                    at=f"{datetime.now():%H:%M:%S}"))
        return started

    def _start_uihost(self) -> bool:
        """Khoi dong uihost: uu tien scheduled task (chay dung session nguoi dung)."""
        from .installer import uihost_task_name, run_task

        task = uihost_task_name(self.cfg)
        ok, out = run_task(task)
        if ok:
            log.debug("Da goi scheduled task %s de khoi dong uihost", task)
            self._stop.wait(1.5)
            return True
        # Fallback (dev / khong co task): tu chay uihost
        # BAT BUOC truyen --config: neu khong, uihost se doc thu muc du lieu
        # mac dinh va nghe nham cho (job cua guardian khong bao gio duoc xu ly).
        if is_frozen():
            ui_exe = Path(sys.executable).with_name(self.cfg.stealth.uihost_name + ".exe")
            cmd = [str(ui_exe)] if ui_exe.exists() else [str(Path(sys.executable)), "uihost"]
        else:
            cmd = python_command_for("remotepc.uihost")
        cmd += ["--config", str(self.cfg_path)]
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0
            env = dict(os.environ,
                       REMOTEPC_CONFIG=str(self.cfg_path),
                       REMOTEPC_DATA=str(self.layout.base),
                       REMOTEPC_NAME=self.cfg.stealth.display_name)
            subprocess.Popen(cmd, env=env, creationflags=flags,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True)
            return True
        except OSError as e:
            log.error("Khong the khoi dong uihost: %s", e)
            return False

    def current_app(self) -> str:
        """Cua so dang mo (lay tu uihost neu guardian o session 0)."""
        if not self.needs_uihost:
            win = system_info.get_active_window()
            return win.label() if win else ""
        fg = self.inbox.foreground()
        if fg:
            proc, title = fg.get("process") or "", fg.get("title") or ""
            return f"{proc} — {title}" if proc else title
        return ""

    def grab_screenshot(self) -> Optional[bytes]:
        """Chup man hinh, tra ve bytes PNG (None neu that bai)."""
        if not self.needs_uihost:
            ok, path, detail = actions.screenshot()
            if ok and path and path.exists():
                data = path.read_bytes()
                try:
                    path.unlink()
                except OSError:
                    pass
                return data
            log.warning("Chup man hinh that bai: %s", detail)
            return None
        self.keep_uihost_alive(force=True)
        name = f"shot-{int(time.time())}.png"
        ack = self.queue.request(ipc.JOB_SCREENSHOT, {"name": name}, timeout=25)
        if not ack or not ack.ok:
            log.warning("uihost khong chup duoc man hinh: %s", ack.error if ack else "timeout")
            return None
        return self.queue.take_shot(name)

    # ================================================================== #
    # BAO VE (chong End Task)
    # ================================================================== #

    def deny_accounts(self) -> list[str]:
        accs = list(self.cfg.stealth.deny_terminate_accounts or [])
        if not accs and self.cfg.stealth.level >= 1 and IS_WINDOWS:
            try:
                from .stealth import non_admin_accounts
                accs = [a for a in non_admin_accounts() if a.lower() not in
                        ("defaultaccount", "guest", "wdagutilityaccount")]
            except Exception:
                accs = []
        return accs

    def protect_self(self, force: bool = False) -> bool:
        if not (IS_WINDOWS and self.cfg.stealth.level >= 2):
            return False
        now = time.time()
        if not force and now - self._last_protect < PROTECT_INTERVAL:
            return False
        self._last_protect = now
        from .stealth import protect_process
        accs = self.deny_accounts()
        if not accs:
            return False
        ok, msg = protect_process(os.getpid(), accs)
        log.info("Tu bao ve: %s", msg)
        if not ok:
            self.store.log("protect_failed", detail=msg)
        return ok

    def protect_uihost(self) -> bool:
        """Gan quyen bao ve cho uihost (guardian lam viec nay vi no la SYSTEM)."""
        if not (IS_WINDOWS and self.cfg.stealth.level >= 2):
            return False
        pid = self.inbox.uihost_pid()
        if not pid:
            return False
        from .stealth import protect_process
        accs = self.deny_accounts()
        if not accs:
            return False
        ok, msg = protect_process(pid, accs)
        self.store.log("protect_uihost", pid=pid, ok=ok, detail=msg)
        return ok

    # ================================================================== #
    # HANDLERS
    # ================================================================== #

    def h_help(self, cmd: ParsedCommand, upd: Update) -> str:
        return M.HELP_TEXT

    def h_ping(self, cmd: ParsedCommand, upd: Update) -> str:
        return f"{M.EMOJI['ok']} pong · `{self.cfg.machine_tag}` · {datetime.now():%H:%M:%S}"

    def h_status(self, cmd: ParsedCommand, upd: Update) -> str:
        text = self.monitor.describe()
        ui = "đang chạy" if (not self.needs_uihost or self.inbox.uihost_alive()) else "KHÔNG chạy"
        prot = "đang bật" if self.cfg.stealth.level >= 2 else ("cơ bản" if self.cfg.stealth.level == 1 else "tắt")
        extra = (f"\n\n\U0001F6E1️ Bảo vệ: {prot} (mức {self.cfg.stealth.level})"
                 f"\n\U0001F5A5️ UI host: {ui}"
                 f"\n\u23F1️ Agent uptime: {format_duration(time.time() - self.store.state.first_run)}")
        if not self.cfg.has_pin():
            extra += ("\n" + M.EMOJI['warn'] + " CHƯA ĐẶT PIN — gửi `/pin <mã>` để bảo vệ "
                      "các lệnh tắt máy / gỡ tool")
        pend = self.store.state.pending_shutdown
        if pend and pend.get("ok"):
            left = pend.get("at", 0) + pend.get("delay", 0) - time.time()
            if left > 0:
                extra += f"\n{M.EMOJI['warn']} Đang đếm ngược tắt máy: còn {int(left)}s (`/abort` để huỷ)"
        return text + extra

    def h_msg(self, cmd: ParsedCommand, upd: Update) -> str:
        text = cmd.text
        if not text:
            return f"{M.EMOJI['fail']} Cú pháp: `/msg [--full] [--title \"...\"] [--timeout 30] nội dung`"
        full = cmd.has_flag("full", "fullscreen", "top")
        style = "warn"
        for f in ("info", "warn", "warning", "error", "critical", "question"):
            if cmd.has_flag(f):
                style = f
                break
        style = cmd.options.get("style", style)
        title = cmd.options.get("title") or ("THÔNG BÁO QUAN TRỌNG" if full else "Thông báo từ bố/mẹ")
        timeout = int(parse_duration(cmd.options.get("timeout", "0")) or 0)
        if full and not timeout:
            timeout = 0                      # giu den khi con bam "da hieu"
        if not full and not timeout:
            timeout = 0
        ok = self.do_action("overlay" if full else "popup", text=text, title=title, style=style,
                            timeout=timeout, duration=timeout, overlay=full)
        detail = (f"Kiểu: {'toàn màn hình' if full else 'hộp thoại'} · Tiêu đề: {title}"
                  + (f" · Tự đóng sau {timeout}s" if timeout else " · Chờ con bấm OK"))
        return (M.cmd_ok("MSG", detail) if ok
                else M.cmd_fail("MSG", "Không hiện được thông báo. "
                                       "Có thể UI host chưa chạy (thử `/uihost restart`)."))

    def h_lock(self, cmd: ParsedCommand, upd: Update) -> str:
        ok = self.do_action("lock")
        return M.cmd_ok("LOCK", "Màn hình của con đã bị khoá.") if ok else M.cmd_fail("LOCK")

    def h_shutdown(self, cmd: ParsedCommand, upd: Update) -> str:
        return self._power(cmd, restart=False)

    def h_restart(self, cmd: ParsedCommand, upd: Update) -> str:
        return self._power(cmd, restart=True)

    def _power(self, cmd: ParsedCommand, restart: bool) -> str:
        name = "RESTART" if restart else "SHUTDOWN"
        delay = cmd.first_int(None)
        if delay is None and cmd.args:
            delay = parse_duration(cmd.args[0])
        if delay is None:
            delay = self.cfg.power.restart_delay if restart else self.cfg.power.shutdown_delay
        # Loi nhan: bo cac token dau la con so (delay) khoi phan text tu do,
        # roi moi den --msg. Khong lam vay thi `/shutdown 45 --msg "..."` se mat loi nhan.
        words = cmd.text.split()
        while words and (words[0].isdigit() or parse_duration(words[0]) is not None):
            words.pop(0)
        message = " ".join(words) or cmd.options.get("msg") or ""
        message = message or ("Máy sẽ khởi động lại theo yêu cầu của bố/mẹ." if restart
                              else "Máy sẽ tắt theo yêu cầu của bố/mẹ.")
        ok = self.do_action("restart" if restart else "shutdown", delay=delay, message=message,
                            force=self.cfg.power.force_close_apps, reason=f"lệnh /{name.lower()}")
        if ok:
            return M.cmd_ok(name, f"Máy sẽ {'khởi động lại' if restart else 'tắt'} sau *{delay} giây*.\n"
                                  f"Lời nhắn hiện trên máy con: “{message}”\n"
                                  f"Gửi `/abort` để huỷ.")
        return M.cmd_fail(name, "Windows từ chối lệnh. Thử `/status` để kiểm tra.")

    def h_abort(self, cmd: ParsedCommand, upd: Update) -> str:
        ok = self.do_action("abort")
        return M.cmd_ok("ABORT", "Đã huỷ lịch tắt máy.") if ok else M.cmd_fail("ABORT", "Không có lịch nào đang chạy.")

    def h_logoff(self, cmd: ParsedCommand, upd: Update) -> str:
        ok = self.do_action("logoff")
        return M.cmd_ok("LOGOFF", "Đã đăng xuất phiên của con.") if ok else M.cmd_fail("LOGOFF")

    def h_screenshot(self, cmd: ParsedCommand, upd: Update) -> str:
        blob = self.grab_screenshot()
        if not blob:
            return M.cmd_fail("SCREENSHOT", "Không chụp được (UI host chưa chạy hoặc máy đang khoá).")
        info = system_info.collect(with_public_ip=False)
        caption = (f"{M.EMOJI['cam']} Màn hình `{info.hostname}` lúc {datetime.now():%H:%M:%S}\n"
                   f"Cửa sổ đang mở: `{self.current_app() or '?'}`")
        try:
            for chat in self.cfg.telegram.allowed_chat_ids:
                self.tg.send_photo(chat, blob, caption=caption, filename=f"{info.hostname}.png")
        except TelegramError as e:
            return M.cmd_fail("SCREENSHOT", str(e))
        return ""    # da gui anh, khong can text

    def h_apps(self, cmd: ParsedCommand, upd: Update) -> str:
        top = cmd.first_int(15) or 15
        rows = actions.list_processes(top=top)
        if not rows:
            return M.cmd_fail("APPS", "Không đọc được danh sách tiến trình.")
        lines = [f"{M.EMOJI['pc']} *{top} TIẾN TRÌNH NẶNG NHẤT* (theo RAM)", "```"]
        for r in rows:
            lines.append(f"{r['name'][:28]:<28} {r['pid']:>6}  {r.get('mem_kb',0)//1024:>5} MB")
        lines.append("```")
        cur = self.current_app()
        if cur:
            lines.append(f"\n\U0001F3AF Đang mở: `{cur}`")
        lines.append(f"\n\u23F1️ Hôm nay con dùng máy: {format_duration(self.monitor.usage_today())}")
        return "\n".join(lines)

    def h_kill(self, cmd: ParsedCommand, upd: Update) -> str:
        target = cmd.args[0] if cmd.args else ""
        if not target:
            return f"{M.EMOJI['fail']} Cú pháp: `/kill ten_ung_dung.exe` hoặc `/kill 1234`"
        ok, detail = actions.kill_process(target, force=True)
        return (M.cmd_ok("KILL", f"Đã tắt `{target}`.") if ok
                else M.cmd_fail("KILL", detail or f"Không tắt được `{target}`."))

    def h_log(self, cmd: ParsedCommand, upd: Update) -> str:
        n = min(cmd.first_int(20) or 20, 60)
        events = self.store.events.tail(n)
        if not events:
            return "Nhật ký còn trống."
        lines = [f"\U0001F4DC *{len(events)} SỰ KIỆN GẦN NHẤT*", "```"]
        for e in events:
            ts = datetime.fromtimestamp(e.get("ts", 0)).strftime("%d/%m %H:%M:%S")
            kind = str(e.get("kind", "?"))[:16]
            extra = {k: v for k, v in e.items() if k not in ("ts", "kind")}
            detail = " ".join(f"{k}={v}" for k, v in list(extra.items())[:3])[:70]
            lines.append(f"{ts} {kind:<16} {detail}")
        lines.append("```")
        return "\n".join(lines)

    def h_machines(self, cmd: ParsedCommand, upd: Update) -> str:
        info = system_info.collect(with_public_ip=False)
        state = self.store.state
        return (f"{M.EMOJI['pc']} *MÁY NÀY*\n"
                f"Tag: `{self.cfg.machine_tag}`\n"
                f"Hostname: `{info.hostname}`\n"
                f"ID: `{self.cfg.machine_id}`\n"
                f"Người dùng: `{info.user}`\n"
                f"OS: `{system_info.os_full_name()}`\n"
                f"IP LAN: `{info.lan_ip}`\n"
                f"Agent bật từ: `{datetime.fromtimestamp(state.first_run):%d/%m %H:%M}`\n"
                f"_(Nếu nhiều máy dùng chung bot, mỗi máy sẽ tự trả lời tin này.)_")

    def h_curfew(self, cmd: ParsedCommand, upd: Update) -> str:
        return curfew_command(self, cmd)

    def h_config(self, cmd: ParsedCommand, upd: Update) -> str:
        return config_command(self, cmd)

    def h_pin(self, cmd: ParsedCommand, upd: Update) -> str:
        args = [a for a in cmd.args if a]
        if cmd.command == "pin" and len(args) >= 1:
            old, new = (args + [""])[:2]
            if not self.cfg.has_pin():
                new = old
            elif not self.cfg.check_pin(old):
                return M.cmd_fail("PIN", "Mã PIN cũ không đúng.")
            if len(new) < 4:
                return M.cmd_fail("PIN", "PIN mới phải có ít nhất 4 ký tự.")
            self.cfg.set_pin(new)
            save_config(self.cfg, self.cfg_path)
            self.store.log("pin_changed", chat=upd.chat_id)
            return M.cmd_ok("PIN", "Đã đổi mã PIN.")
        if cmd.command == "unlock":
            token = args[0] if args else ""
            if self.cfg.check_pin(token):
                until = time.time() + PIN_SESSION_MINUTES * 60
                self.store.state.extra.setdefault("unlocked", {})[str(upd.chat_id)] = until
                self.store.save()
                return M.cmd_ok("UNLOCK", f"Đã mở khoá {PIN_SESSION_MINUTES} phút cho chat này.")
            return M.cmd_fail("UNLOCK", "Sai mã PIN.")
        return (f"{M.EMOJI['lock']} *PIN*\n"
                f"`/unlock <mã>` — mở khoá 15 phút cho chat này\n"
                f"`/pin <mã_cũ> <mã_mới>` — đổi mã\n"
                f"Lệnh cần PIN: {', '.join(sorted(PIN_COMMANDS))}\n"
                f"Cũng có thể thêm `--pin <mã>` vào cuối lệnh.")

    def h_unlock(self, cmd: ParsedCommand, upd: Update) -> str:
        return self.h_pin(cmd, upd)

    def h_reload(self, cmd: ParsedCommand, upd: Update) -> str:
        cfg, path = load_config(self.cfg_path, self.cfg.stealth.display_name)
        old_token = self.cfg.telegram.bot_token
        self.cfg = cfg
        self.monitor.reload_config(cfg)
        if cfg.telegram.bot_token != old_token:
            try:
                self.tg = TelegramClient(cfg.telegram.bot_token, api_base=cfg.telegram.api_base,
                                         proxy=cfg.telegram.proxy or None)
            except ValueError:
                self.tg = None
        return M.cmd_ok("RELOAD", f"Đã nạp lại `{path}`.\n" + self.monitor.describe())

    def h_protect(self, cmd: ParsedCommand, upd: Update) -> str:
        if not IS_WINDOWS:
            return M.cmd_fail("PROTECT", "Chỉ hỗ trợ Windows.")
        a = self.protect_self(force=True)
        b = self.protect_uihost()
        accs = ", ".join(self.deny_accounts()) or "(không có tài khoản nào)"
        return M.cmd_ok("PROTECT", f"Đã áp lại quyền chống End Task.\n"
                                   f"Tài khoản bị chặn: `{accs}`\n"
                                   f"Guardian: {'OK' if a else 'không áp dụng (level < 2)'} · "
                                   f"UI host: {'OK' if b else 'không áp dụng'}")

    def h_uihost(self, cmd: ParsedCommand, upd: Update) -> str:
        sub = (cmd.args[0] if cmd.args else "status").lower()
        if sub in ("restart", "start", "khoi"):
            alive = self.keep_uihost_alive(force=True)
            return (M.cmd_ok("UIHOST", "Đã khởi động lại UI host.") if alive
                    else M.cmd_fail("UIHOST", "Không khởi động được. Kiểm tra scheduled task."))
        alive = self.inbox.uihost_alive()
        pid = self.inbox.uihost_pid()
        fg = self.inbox.foreground() or {}
        return (f"\U0001F5A5️ *UI HOST*\n"
                f"Trạng thái: {'đang chạy' if alive else 'KHÔNG chạy'}"
                + (f" (pid {pid})" if pid else "") + "\n"
                f"Chế độ guardian: {'session 0 (SYSTEM)' if self.needs_uihost else 'session người dùng'}\n"
                f"Cửa sổ đang mở: `{fg.get('process','?')} — {fg.get('title','')}`\n"
                f"Hàng đợi: {self.queue.queue_depth()} job")

    def h_uninstall(self, cmd: ParsedCommand, upd: Update) -> str:
        from .installer import uninstall
        self.notify(f"{M.EMOJI['warn']} Bố/mẹ vừa ra lệnh GỠ agent khỏi `{self.cfg.machine_tag}`.")
        ok, detail = uninstall(self.cfg, self.cfg_path, self.layout, purge=True)
        if ok:
            self.stop("uninstall")
            return M.cmd_ok("UNINSTALL", detail)
        return M.cmd_fail("UNINSTALL", detail)

    HANDLERS: dict[str, Callable[["Agent", ParsedCommand, Update], str]] = {
        "help": h_help, "start": h_help, "ping": h_ping, "status": h_status,
        "msg": h_msg, "lock": h_lock, "shutdown": h_shutdown, "restart": h_restart,
        "abort": h_abort, "logoff": h_logoff, "screenshot": h_screenshot, "apps": h_apps,
        "kill": h_kill, "log": h_log, "machines": h_machines, "curfew": h_curfew,
        "config": h_config, "pin": h_pin, "unlock": h_unlock, "reload": h_reload,
        "protect": h_protect, "uihost": h_uihost, "uninstall": h_uninstall,
    }


# --------------------------------------------------------------------------- #
# /curfew
# --------------------------------------------------------------------------- #


def curfew_command(agent: Agent, cmd: ParsedCommand) -> str:
    cfg = agent.cfg
    sub = (cmd.args[0] if cmd.args else "").lower()
    cur = cfg.curfew

    def dump() -> str:
        eng = agent.monitor.engine
        lines = [f"{M.EMOJI['clock']} *GIỜ GIỚI NGHIÊM*: " + ("BẬT" if cur.enabled else "TẮT")]
        for i, w in enumerate(eng.windows, 1):
            lines.append(
                f"\n*{i}. {w.name}* — `{format_min(w.start)}` → `{format_min(w.end)}`"
                + (" (qua nửa đêm)" if w.crosses_midnight else "")
                + f"\n   Áp dụng: {'mọi ngày' if not w.days else ', '.join(_DAY_VN[d] for d in sorted(w.days))}"
                + f"\n   Báo trước: {', '.join(str(m) + ' phút' for m in w.warn_before) or 'không'}"
                + f"\n   Khi vào giờ: `{w.on_start}` · Khi hết giờ: `{w.on_end}`"
            )
        lines.append(f"\nTự tắt máy sau {cur.auto_shutdown_after_minutes} phút trong giờ cấm"
                     if cur.auto_shutdown_after_minutes else "\nKhông tự tắt máy.")
        lines.append("\n*Cách đổi:* `/curfew set 23:30 00:00` · `/curfew warn 30,10` · "
                     "`/curfew action popup` · `/curfew end shutdown` · `/curfew on|off`")
        lines.append("\n" + eng.describe(datetime.now()))
        return "\n".join(lines)

    if not sub or sub in ("show", "xem", "list"):
        return dump()

    if sub in ("on", "bat", "enable"):
        cur.enabled = True
    elif sub in ("off", "tat", "disable"):
        cur.enabled = False
    elif sub in ("set", "dat"):
        rest = cmd.args[1:]
        if len(rest) < 2:
            return f"{M.EMOJI['fail']} Cú pháp: `/curfew set 23:30 00:00 [tên khung]`"
        try:
            start_s = format_min(parse_hhmm(rest[0]))
            end_s = format_min(parse_hhmm(rest[1]))
        except ValueError as e:
            return M.cmd_fail("CURFEW", str(e))
        w = _target_window(cur, cmd)
        w.start, w.end = start_s, end_s
        if len(rest) >= 3:
            w.name = rest[2]
    elif sub in ("warn", "bao"):
        w = _target_window(cur, cmd)
        raw = cmd.args[1] if len(cmd.args) > 1 else ""
        mins = {int(x) for x in raw.replace(";", ",").split(",") if x.strip().isdigit()}
        if not mins:
            return f"{M.EMOJI['fail']} Cú pháp: `/curfew warn 30,10` (số phút báo trước)."
        w.warn_before_minutes = sorted(mins)
    elif sub in ("days", "thu"):
        w = _target_window(cur, cmd)
        raw = " ".join(cmd.args[1:])
        try:
            w.days = sorted(parse_days([x for x in raw.replace(",", " ").split() if x]))
        except ValueError as e:
            return M.cmd_fail("CURFEW", str(e))
    elif sub in ("action", "start", "vao"):
        val = (cmd.args[1] if len(cmd.args) > 1 else "").lower()
        if val not in ("none", "notify", "popup", "lock"):
            return f"{M.EMOJI['fail']} Chọn: none / notify / popup / lock"
        _target_window(cur, cmd).on_start = val
    elif sub in ("end", "het"):
        val = (cmd.args[1] if len(cmd.args) > 1 else "").lower()
        if val not in ("none", "notify", "lock", "logoff", "shutdown"):
            return f"{M.EMOJI['fail']} Chọn: none / notify / lock / logoff / shutdown"
        _target_window(cur, cmd).on_end = val
    elif sub in ("text", "loinhan"):
        w = _target_window(cur, cmd)
        w.popup_text = " ".join(cmd.args[1:])
    elif sub in ("auto", "tatmay"):
        val = cmd.first_int(0) or 0
        cur.auto_shutdown_after_minutes = max(0, val)
        if len(cmd.args) > 2 and cmd.args[2].isdigit():
            cur.auto_shutdown_delay = max(5, int(cmd.args[2]))
    elif sub in ("add", "them"):
        rest = cmd.args[1:]
        if len(rest) < 2:
            return f"{M.EMOJI['fail']} Cú pháp: `/curfew add 13:00 14:00 Trưa`"
        try:
            start_s = format_min(parse_hhmm(rest[0]))
            end_s = format_min(parse_hhmm(rest[1]))
        except ValueError as e:
            return M.cmd_fail("CURFEW", str(e))
        from .config import CurfewWindow
        name = rest[2] if len(rest) > 2 else f"Khung {len(cur.windows) + 1}"
        cur.windows.append(CurfewWindow(name=name, start=start_s, end=end_s))
    elif sub in ("del", "remove", "xoa"):
        idx = cmd.first_int(0) or 0
        if 1 <= idx <= len(cur.windows):
            removed = cur.windows.pop(idx - 1)
            agent.monitor.reload_config(cfg)
            save_config(cfg, agent.cfg_path)
            return M.cmd_ok("CURFEW", f"Đã xoá khung `{removed.name}`.")
        return f"{M.EMOJI['fail']} Ghi số thứ tự khung cần xoá (1..{len(cur.windows)})."
    else:
        return f"{M.EMOJI['fail']} Không rõ `/curfew {sub}`.\n" + dump()

    # dong bo xuong config + engine
    _sync_curfew(cfg)
    agent.monitor.reload_config(cfg)
    save_config(cfg, agent.cfg_path)
    agent.store.log("curfew_changed", sub=sub, chat=cmd.raw[:80])
    return M.cmd_ok("CURFEW", "Đã lưu thay đổi.") + "\n\n" + dump()


def _new_window(rest: list[str]):
    from .config import CurfewWindow
    return CurfewWindow(name=(rest[2] if len(rest) > 2 else "Khung mới"), start=rest[0], end=rest[1])


_DAY_VN = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def _target_window(cur, cmd: ParsedCommand):
    """Lay khung can sua: `--w 2` hoac mac dinh khung dau tien."""
    idx = 0
    for k in ("w", "window", "i", "index"):
        if k in cmd.options and str(cmd.options[k]).isdigit():
            idx = int(cmd.options[k]) - 1
            break
    if not cur.windows:
        _new_default(cur)
    if 0 <= idx < len(cur.windows):
        return cur.windows[idx]
    return cur.windows[0]


def _new_default(cur) -> None:
    from .config import CurfewWindow
    cur.windows.append(CurfewWindow())


def _sync_curfew(cfg: Config) -> None:
    """Config dataclass <-> dict: dam bao windows la CurfewWindow hop le."""
    from .config import CurfewWindow
    out: list[CurfewWindow] = []
    for w in cfg.curfew.windows:
        if isinstance(w, CurfewWindow):
            out.append(w)
        elif isinstance(w, dict):
            out.append(CurfewWindow(**{k: v for k, v in w.items() if k in CurfewWindow.__dataclass_fields__}))
    cfg.curfew.windows = out or [CurfewWindow()]


# --------------------------------------------------------------------------- #
# /config
# --------------------------------------------------------------------------- #

SENSITIVE_KEYS = ("pin_hash", "pin_salt", "bot_token")


def config_command(agent: Agent, cmd: ParsedCommand) -> str:
    cfg = agent.cfg
    sub = (cmd.args[0] if cmd.args else "show").lower()
    if sub in ("show", "xem", "list"):
        data = cfg.to_dict()
        for k in SENSITIVE_KEYS:
            if k in data:
                data[k] = "***" if data[k] else ""
        data["telegram"]["bot_token"] = _mask(cfg.telegram.bot_token)
        body = json.dumps(data, ensure_ascii=False, indent=1)
        return f"\u2699️ *CONFIG* (`{agent.cfg_path}`)\n```json\n{body}\n```"
    if sub in ("set", "dat"):
        if len(cmd.args) < 3:
            return f"{M.EMOJI['fail']} Cú pháp: `/config set curfew.enabled false`"
        key, value = cmd.args[1], " ".join(cmd.args[2:])
        try:
            msg = set_by_path(cfg, key, value)
        except (KeyError, AttributeError, ValueError) as e:
            return M.cmd_fail("CONFIG", f"Không đặt được `{key}`: {e}")
        _sync_curfew(cfg)
        agent.monitor.reload_config(cfg)
        save_config(cfg, agent.cfg_path)
        return M.cmd_ok("CONFIG", msg)
    if sub in ("path", "duongdan"):
        return f"`{agent.cfg_path}`"
    return f"{M.EMOJI['fail']} Dùng: `/config show` · `/config set <khoá> <giá trị>` · `/config path`"


def _mask(token: str) -> str:
    if not token:
        return ""
    head, _, tail = token.partition(":")
    return f"{head}:{tail[:3]}***{tail[-3:]}" if len(tail) > 8 else "***"


# --------------------------------------------------------------------------- #
# Diem vao
# --------------------------------------------------------------------------- #


def run_agent(config_path: Optional[str] = None, stealth: bool = True,
              once: bool = False, log_level: int = logging.INFO) -> int:
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg, cfg_path = load_config(config_path)
    layout = layout_for(cfg, cfg_path)
    if not cfg.telegram.bot_token:
        print("Chua co bot_token. Chay: python -m remotepc init", file=sys.stderr)
        return 2
    agent = Agent(cfg, cfg_path, layout, stealth=stealth)
    if once:
        agent.notify(f"{M.EMOJI['ok']} Agent trên `{cfg.machine_tag}` "
                     f"({system_info.collect(with_public_ip=False).hostname}) vừa chạy thử 1 lần.")
        agent.monitor.tick()
        agent.store.save()
        return 0
    try:
        agent.start()
    except Exception:
        traceback.print_exc()
        return 1
    return 0
