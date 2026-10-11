"""MONITOR - vong lap theo doi phia may con.

Phat hien va bao ve bot:
  * may vua bat            (boot_id doi)
  * co nguoi dang nhap     (user doi)
  * con roi may qua lau    (idle)
  * sap toi / vao / het gio gioi nghiem
  * con van dung may sau gio cam -> tu dong khoa/tat
  * heartbeat dinh ky

Toan bo thoi gian duoc truyen vao qua `now_fn` / `local_now` de test duoc
ma khong can doi dong ho that.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Callable, Optional

from . import messages as M, system_info
from .config import Config
from .curfew import CurfewEngine, Window, format_min
from .state import Store

log = logging.getLogger("remotepc.monitor")

NotifyFn = Callable[..., None]          # (text, *, photo=None, silent=False, kind="")
ActionFn = Callable[..., bool]          # (action, **kw) -> ok


class Monitor:
    def __init__(self, cfg: Config, store: Store, notify: NotifyFn, do_action: ActionFn,
                 foreground: Callable[[], str] = lambda: "",
                 local_now: Callable[[], datetime] = datetime.now,
                 now_fn: Callable[[], float] = time.time,
                 screenshot: Optional[Callable[[], Optional[bytes]]] = None):
        self.cfg = cfg
        self.store = store
        self.notify = notify
        self.do_action = do_action
        self.foreground = foreground
        self.local_now = local_now
        self.now = now_fn
        self.screenshot = screenshot

        self.windows = windows_from_config(cfg)
        extra = store.state.extra.setdefault("curfew", {})
        self.engine = CurfewEngine(self.windows, extra)
        self._last_tick = now_fn()
        self._last_heartbeat = 0.0
        self._last_protect = 0.0

    # ------------------------------------------------------------------ #

    def reload_config(self, cfg: Config) -> None:
        self.cfg = cfg
        self.windows = windows_from_config(cfg)
        self.engine.windows = self.windows

    def tick(self) -> None:
        """Goi moi 5-10 giay."""
        try:
            self.check_boot()
        except Exception:
            log.exception("check_boot")
        try:
            self.check_login()
        except Exception:
            log.exception("check_login")
        try:
            self.check_usage()
        except Exception:
            log.exception("check_usage")
        try:
            self.check_idle()
        except Exception:
            log.exception("check_idle")
        try:
            self.check_curfew()
        except Exception:
            log.exception("check_curfew")
        try:
            self.check_heartbeat()
        except Exception:
            log.exception("check_heartbeat")

    # ---------------- boot ---------------- #

    def check_boot(self) -> None:
        up = system_info.uptime_seconds()
        boot_ts = self.now() - up if up else self.now()
        boot_id = f"{int(boot_ts)}"
        st = self.store.state
        st.boot_id = boot_id
        st.boot_time = boot_ts
        if st.last_boot_reported == boot_id:
            return              # lan boot nay da bao roi (agent khoi dong lai giua chung)
        st.last_boot_reported = boot_id
        self.store.save()
        self.store.log("boot", boot_time=round(boot_ts, 1),
                       user=", ".join(system_info.logged_on_users()))
        if not self.cfg.notify.on_boot:
            return
        info = system_info.collect(tag=self.cfg.machine_tag, with_public_ip=True)
        text = M.boot_report(
            hostname=info.hostname, tag=self.cfg.machine_tag, user=info.user or "(chua dang nhap)",
            boot_time=datetime.fromtimestamp(boot_ts).strftime("%H:%M:%S %d/%m/%Y"),
            lan_ip=info.lan_ip or "?", public_ip=info.public_ip or "?",
            os=system_info.os_full_name(),
        )
        self.notify(text, kind="boot")
        log.info("May vua bat - da bao ve bot")

    # ---------------- login ---------------- #

    def check_login(self) -> None:
        users = system_info.logged_on_users()
        current = ", ".join(sorted(users))
        st = self.store.state
        if current and current != st.last_seen_login:
            previous = st.last_seen_login
            st.last_seen_login = current
            self.store.save()
            self.store.log("login", user=current, previous=previous)
            if self.cfg.notify.on_login:
                self.notify(M.login_report(user=current, hostname=system_info.collect(
                    with_public_ip=False).hostname, at=datetime.now().strftime("%H:%M:%S %d/%m/%Y")),
                    kind="login")

    # ---------------- thoi gian su dung ---------------- #

    def check_usage(self) -> None:
        now = self.now()
        delta = max(0.0, now - self._last_tick)
        self._last_tick = now
        if delta <= 0 or delta > 600:      # may ngu/qua lau -> bo qua
            return
        idle = system_info.idle_seconds()
        if idle > max(120.0, delta * 2):   # con roi may -> khong tinh la "dang dung"
            return
        day = datetime.now().strftime("%Y-%m-%d")
        st = self.store.state
        used = float(st.daily_usage.get(day, 0.0)) + delta
        st.daily_usage = {k: v for k, v in st.daily_usage.items() if k >= _days_ago(60)}
        st.daily_usage[day] = round(used, 1)
        self.store.touch()

    def usage_today(self) -> float:
        return float(self.store.state.daily_usage.get(datetime.now().strftime("%Y-%m-%d"), 0.0))

    # ---------------- idle ---------------- #

    def check_idle(self) -> None:
        if not self.cfg.notify.on_idle:
            return
        idle = system_info.idle_seconds()
        limit = max(1, int(self.cfg.notify.idle_minutes)) * 60
        extra = self.store.state.extra
        if idle >= limit and not extra.get("idle_reported"):
            extra["idle_reported"] = True
            self.store.log("idle", seconds=round(idle))
            self.notify(
                f"\U0001F634 *CON ĐÃ RỜI MÁY*\n"
                f"Không có thao tác nào trong {int(idle // 60)} phút.\n"
                f"Ứng dụng cuối: `{self.foreground() or '?'}`",
                kind="idle")
        elif idle < limit / 2 and extra.get("idle_reported"):
            extra["idle_reported"] = False

    # ---------------- curfew ---------------- #

    def check_curfew(self) -> None:
        if not self.cfg.curfew.enabled or not self.windows:
            return
        now = self.local_now()
        events = self.engine.tick(now, ts=self.now())
        if not events:
            self._check_overtime(now)
            return
        for ev in events:
            self._handle_curfew_event(ev, now)
        self._check_overtime(now)
        self.store.save()

    def _handle_curfew_event(self, ev, now: datetime) -> None:
        app = self.foreground() or "?"
        host = system_info.collect(with_public_ip=False).hostname
        stamp = now.strftime("%H:%M:%S")
        self.store.log("curfew", kind=ev.kind, window=ev.window.name, action=ev.action)
        log.info("Curfew %s %s -> %s", ev.kind, ev.window, ev.action)

        if ev.kind == "WARN":
            end = format_min(ev.window.end)
            text = ev.popup_text or M.DEFAULT_POPUP_WARN
            if ev.action == "popup" or self.cfg.curfew.screenshot_on_violation:
                self.do_action("popup", text=text.format(minutes=ev.minutes_left, end=end),
                               title="Sắp tới giờ giới nghiêm", style="warn", timeout=20)
            if self.cfg.notify.on_curfew:
                self.notify(M.curfew_warn(minutes=ev.minutes_left, end=end, hostname=host,
                                          at=stamp, app=app), kind="curfew_warn",
                            photo=self._maybe_shot())

        elif ev.kind == "START":
            start, end = format_min(ev.window.start), format_min(ev.window.end)
            if ev.action in ("popup", "lock"):
                text = ev.popup_text or M.DEFAULT_POPUP_CURFEW
                self.do_action("popup", text=text.format(end=end), title="ĐÃ TỚI GIỜ GIỚI NGHIÊM",
                               style="error", timeout=0 if ev.action == "lock" else 30,
                               overlay=(ev.action == "lock"))
            if ev.action == "lock":
                self.do_action("lock")
            if self.cfg.notify.on_curfew:
                self.notify(M.curfew_start(window=ev.window.name, start=start, end=end,
                                           hostname=host, at=stamp, app=app),
                            kind="curfew_start", photo=self._maybe_shot())

        elif ev.kind == "END":
            if ev.action in ("lock", "logoff", "shutdown"):
                self.do_action(ev.action)
            if self.cfg.notify.on_curfew:
                self.notify(M.curfew_end(window=ev.window.name, at=stamp,
                                         action=_ACTION_VN.get(ev.action, ev.action)), kind="curfew_end")
            self.store.state.extra["curfew"]["overtime_since"] = 0

    def _check_overtime(self, now: datetime) -> None:
        """Con van dung may TRONG gio cam qua N phut -> tu dong tat may.

        N = curfew.auto_shutdown_after_minutes (0 = khong bao gio tu tat).
        Dem tu luc bat dau khung cam (hoac tu luc agent phat hien, neu may
        duoc bat giua chung). Moi khung chi tat 1 lan.
        """
        limit = int(self.cfg.curfew.auto_shutdown_after_minutes or 0)
        extra = self.store.state.extra["curfew"]
        act = extra.get("active")
        if not act:
            extra.pop("overtime_done", None)
            return
        if limit <= 0:
            return
        since = float(act.get("since") or self.now())
        elapsed_min = max(0.0, (self.now() - since) / 60.0)
        if elapsed_min < limit or extra.get("overtime_done"):
            return
        extra["overtime_done"] = True
        self.store.log("curfew_auto_shutdown", elapsed_minutes=round(elapsed_min, 1), limit=limit)
        delay = int(self.cfg.curfew.auto_shutdown_delay or 30)
        self.notify(M.curfew_auto_shutdown(overtime=f"{int(elapsed_min)} phút", delay=delay),
                    kind="curfew_shutdown", photo=self._maybe_shot())
        self.do_action("popup",
                       text=f"Máy sẽ TẮT sau {delay} giây vì con vẫn dùng máy "
                            f"quá {limit} phút trong giờ giới nghiêm.",
                       title="TỰ ĐỘNG TẮT MÁY", style="error", timeout=delay, overlay=True)
        self.do_action("shutdown", delay=delay, force=True,
                       reason="Dùng máy quá giờ giới nghiêm")
        self.store.save()

    def _maybe_shot(self) -> Optional[bytes]:
        if not (self.cfg.curfew.screenshot_on_violation and self.screenshot):
            return None
        try:
            return self.screenshot()
        except Exception:
            return None

    # ---------------- heartbeat ---------------- #

    def check_heartbeat(self) -> None:
        mins = int(self.cfg.notify.heartbeat_minutes or 0)
        if mins <= 0:
            return
        if self.now() - self._last_heartbeat < mins * 60:
            return
        self._last_heartbeat = self.now()
        info = system_info.collect(with_public_ip=False)
        from .commands import format_duration
        self.notify(M.heartbeat(hostname=info.hostname, uptime=format_duration(info.uptime_seconds),
                                now=datetime.now().strftime("%H:%M:%S")), kind="heartbeat", silent=True)

    # ---------------- mo ta cho /status ---------------- #

    def describe(self) -> str:
        now = self.local_now()
        info = system_info.collect(with_public_ip=False)
        from .commands import format_duration
        lines = [
            f"{M.EMOJI['pc']} *{info.hostname}* (`{self.cfg.machine_tag}`)",
            f"Người dùng: `{info.user or '?'}`",
            f"Đã bật: {format_duration(info.uptime_seconds)} (từ "
            f"{datetime.fromtimestamp(info.boot_time).strftime('%H:%M %d/%m') if info.boot_time else '?'})",
            f"Hôm nay con dùng máy: {format_duration(self.usage_today())}",
            f"IP LAN: `{info.lan_ip or '?'}`",
            f"RAM: {info.mem_percent:.0f}%" if info.mem_percent >= 0 else "",
            f"Cửa sổ đang mở: `{self.foreground() or '?'}`",
            "",
            M.EMOJI['clock'] + " *GIỜ GIỚI NGHIÊM*",
            ("(đang TẮT)" if not self.cfg.curfew.enabled else ""),
            self.engine.describe(now),
        ]
        return "\n".join(x for x in lines if x)


def windows_from_config(cfg: Config) -> list[Window]:
    """Doi CurfewWindow (dataclass cau hinh, gio dang chuoi) -> Window (da parse ra phut)."""
    out: list[Window] = []
    for w in cfg.curfew.windows or []:
        if isinstance(w, Window):
            out.append(w)
        elif is_dataclass(w):
            out.append(Window.from_dict(asdict(w)))
        elif isinstance(w, dict):
            out.append(Window.from_dict(w))
    return out


def _days_ago(n: int) -> str:
    from datetime import timedelta
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


_ACTION_VN = {
    "none": "không làm gì", "notify": "chỉ thông báo", "popup": "hiện thông báo",
    "lock": "khoá màn hình", "logoff": "đăng xuất", "shutdown": "tắt máy",
}
