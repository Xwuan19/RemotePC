"""Test Monitor: bao boot, bao dang nhap, gio gioi nghiem, tu dong tat may.

Dong ho duoc gia lap (fake clock) nen test chay tuc thi va on dinh.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from remotepc import system_info
from remotepc.config import Config, CurfewWindow
from remotepc.monitor import Monitor
from remotepc.paths import Layout
from remotepc.state import Store


class FakeClock:
    """Dong ho gia lap: now (epoch) va local (datetime) di chuyen cung nhau."""

    def __init__(self, start: datetime):
        self.current = start
        self.epoch_base = start.timestamp()

    def local_now(self) -> datetime:
        return self.current

    def now(self) -> float:
        return self.current.timestamp()

    def advance(self, seconds: float = 0, minutes: float = 0, hours: float = 0) -> None:
        self.current = self.current + timedelta(seconds=seconds, minutes=minutes, hours=hours)


class MonitorTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.layout = Layout(Path(self.tmp.name)).ensure()
        self.store = Store(self.layout)
        self.clock = FakeClock(datetime(2026, 3, 10, 20, 0, 0))
        self.sent: list[dict] = []
        self.acted: list[dict] = []

        self._patches = [
            mock.patch.object(system_info, "uptime_seconds", return_value=3600.0),
            mock.patch.object(system_info, "logged_on_users", return_value=["Kid"]),
            mock.patch.object(system_info, "idle_seconds", return_value=5.0),
            mock.patch.object(system_info, "collect", side_effect=lambda **kw: system_info.SystemInfo(
                hostname="PC-CON", user="Kid", uptime_seconds=3600.0,
                boot_time=self.clock.now() - 3600.0, lan_ip="192.168.1.20")),
            mock.patch.object(system_info, "os_full_name", return_value="Windows 11"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    # ------------------------------------------------------------------ #

    def notify(self, text: str, kind: str = "", photo=None, silent: bool = False) -> None:
        self.sent.append({"text": text, "kind": kind, "photo": photo})

    def do_action(self, action: str, **kw):
        self.acted.append({"action": action, **kw})
        return True

    def make_monitor(self, **cfg_kw) -> Monitor:
        cfg = Config()
        cfg.machine_tag = "pc1"
        for k, v in cfg_kw.items():
            obj, _, attr = k.partition("__")
            setattr(getattr(cfg, obj), attr, v)
        mon = Monitor(cfg, self.store, notify=self.notify, do_action=self.do_action,
                      foreground=lambda: "chrome.exe — YouTube",
                      local_now=self.clock.local_now, now_fn=self.clock.now)
        return mon

    def reset(self) -> None:
        """Xoa thong bao boot/login de test curfew duoc ro."""
        self.sent.clear()
        self.acted.clear()

    def texts(self) -> str:
        return "\n".join(s["text"] for s in self.sent)

    def kinds(self) -> list[str]:
        return [s["kind"] for s in self.sent]


class TestBoot(MonitorTestBase):
    def test_boot_report_once(self):
        mon = self.make_monitor()
        mon.tick()
        self.assertIn("boot", self.kinds())
        self.assertIn("MÁY VỪA ĐƯỢC BẬT", self.texts())
        self.assertIn("PC-CON", self.texts())
        self.assertIn("Kid", self.texts())

        before = len(self.sent)
        mon.tick()
        mon.tick()
        self.assertEqual(len(self.sent), before, "Khong duoc bao boot lien tuc")

    def test_boot_report_again_after_reboot(self):
        mon = self.make_monitor()
        mon.tick()
        # may khoi dong lai: uptime ve nho
        with mock.patch.object(system_info, "uptime_seconds", return_value=10.0):
            mon.tick()
        self.assertEqual(self.kinds().count("boot"), 2)

    def test_boot_report_disabled(self):
        mon = self.make_monitor(notify__on_boot=False)
        mon.tick()
        self.assertNotIn("boot", self.kinds())      # van bao login, chi tat bao boot
        self.assertNotIn("MÁY VỪA ĐƯỢC BẬT", self.texts())

    def test_login_report(self):
        mon = self.make_monitor()
        mon.tick()                     # lan dau: boot + login
        self.assertIn("login", self.kinds())
        n = len(self.sent)
        mon.tick()
        self.assertEqual(len(self.sent), n)
        # doi nguoi dung -> bao tiep
        with mock.patch.object(system_info, "logged_on_users", return_value=["Dad"]):
            mon.tick()
        self.assertEqual(self.kinds().count("login"), 2)
        self.assertIn("Dad", self.texts())


class TestCurfew(MonitorTestBase):
    def make_night_monitor(self, **kw):
        mon = self.make_monitor(**kw)
        mon.windows = mon.windows      # giu nguyen
        return mon

    def test_warn_then_start_then_end(self):
        mon = self.make_night_monitor()
        self.clock.current = datetime(2026, 3, 10, 22, 59, 0)
        mon.tick()                       # lan dau: bao boot + login
        self.reset()
        self.assertEqual(self.sent, [])

        self.clock.current = datetime(2026, 3, 10, 23, 0, 30)
        mon.tick()
        self.assertIn("curfew_warn", self.kinds())
        self.assertIn("30 phút", self.texts())
        # popup hien tren may con (vi on_start=popup)
        self.assertTrue(any(a["action"] == "popup" for a in self.acted))

        self.clock.current = datetime(2026, 3, 10, 23, 30, 30)
        mon.tick()
        self.assertIn("curfew_start", self.kinds())
        self.assertIn("ĐÃ VÀO GIỜ GIỚI NGHIÊM", self.texts())
        self.assertIn("chrome.exe", self.texts())

        self.clock.current = datetime(2026, 3, 11, 0, 0, 30)
        mon.tick()
        self.assertIn("curfew_end", self.kinds())

    def test_disabled(self):
        mon = self.make_night_monitor(curfew__enabled=False)
        self.clock.current = datetime(2026, 3, 10, 23, 45, 0)
        mon.tick()
        self.reset()                                # bo qua bao boot/login
        mon.tick()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.acted, [])

    def test_custom_window(self):
        cfg_windows = [CurfewWindow(name="Trưa", start="12:00", end="13:00",
                                    warn_before_minutes=[5], on_start="notify", on_end="none")]
        mon = self.make_monitor()
        mon.cfg.curfew.windows = cfg_windows
        mon.reload_config(mon.cfg)
        self.clock.current = datetime(2026, 3, 11, 11, 0, 0)
        mon.tick()                                  # bao boot + login
        self.reset()
        self.clock.current = datetime(2026, 3, 11, 11, 56, 0)
        mon.tick()
        self.assertIn("5 phút", self.texts())
        self.clock.current = datetime(2026, 3, 11, 12, 0, 30)
        mon.tick()
        self.assertIn("curfew_start", self.kinds())

    def test_lock_action_on_start(self):
        mon = self.make_monitor()
        mon.cfg.curfew.windows = [CurfewWindow(name="Đêm", start="23:30", end="00:00",
                                               warn_before_minutes=[], on_start="lock",
                                               on_end="none")]
        mon.reload_config(mon.cfg)
        self.clock.current = datetime(2026, 3, 10, 23, 31, 0)
        mon.tick()
        kinds = [a["action"] for a in self.acted]
        self.assertIn("popup", kinds)     # canh bao hien tren man hinh con
        self.assertIn("lock", kinds)      # va khoa man hinh

    def test_auto_shutdown_after_minutes(self):
        mon = self.make_monitor()
        mon.cfg.curfew.auto_shutdown_after_minutes = 10
        mon.cfg.curfew.auto_shutdown_delay = 45
        mon.reload_config(mon.cfg)

        self.clock.current = datetime(2026, 3, 10, 23, 31, 0)
        mon.tick()                                  # bao boot + login + vao gio cam
        self.reset()
        self.assertFalse(any(a["action"] == "shutdown" for a in self.acted))

        self.clock.advance(minutes=9)
        mon.tick()
        self.assertFalse(any(a["action"] == "shutdown" for a in self.acted),
                         "Chua du 10 phut thi khong duoc tat")

        self.clock.advance(minutes=2)
        mon.tick()
        self.assertTrue(any(a["action"] == "shutdown" for a in self.acted),
                        "Qua 10 phut trong gio cam -> phai tat may")
        shut = [a for a in self.acted if a["action"] == "shutdown"][0]
        self.assertEqual(shut["delay"], 45)
        self.assertIn("giờ giới nghiêm", self.texts())

        self.clock.advance(minutes=5)
        mon.tick()
        self.assertEqual(len([a for a in self.acted if a["action"] == "shutdown"]), 1,
                         "Chi tat may 1 lan")

    def test_describe(self):
        mon = self.make_monitor()
        self.clock.current = datetime(2026, 3, 10, 23, 45, 0)
        text = mon.describe()
        self.assertIn("PC-CON", text)
        self.assertIn("GIỜ GIỚI NGHIÊM", text)
        self.assertIn("23:30", text)


class TestUsageAndIdle(MonitorTestBase):
    def test_usage_accumulates(self):
        mon = self.make_monitor()
        mon.tick()
        first = mon.usage_today()
        self.clock.advance(seconds=60)
        mon.tick()
        self.assertGreater(mon.usage_today(), first)

    def test_idle_not_counted(self):
        mon = self.make_monitor()
        mon.tick()
        with mock.patch.object(system_info, "idle_seconds", return_value=600.0):
            self.clock.advance(seconds=60)
            mon.tick()
        self.assertLess(mon.usage_today(), 30, "Con roi may thi khong tinh la dang dung")

    def test_idle_alert(self):
        mon = self.make_monitor(notify__on_idle=True, notify__idle_minutes=10)
        mon.tick()
        with mock.patch.object(system_info, "idle_seconds", return_value=700.0):
            mon.tick()
        self.assertIn("idle", self.kinds())
        n = len(self.sent)
        with mock.patch.object(system_info, "idle_seconds", return_value=800.0):
            mon.tick()
        self.assertEqual(len(self.sent), n, "Chi bao 1 lan cho moi lan roi may")


class TestHeartbeat(MonitorTestBase):
    def test_heartbeat(self):
        mon = self.make_monitor(notify__heartbeat_minutes=1)
        mon._last_heartbeat = 0
        mon.tick()
        self.assertIn("heartbeat", self.kinds())
        n = len(self.sent)
        mon.tick()
        self.assertEqual(len(self.sent), n)
        self.clock.advance(minutes=2)
        mon.tick()
        self.assertEqual(self.kinds().count("heartbeat"), 2)


if __name__ == "__main__":
    unittest.main()
