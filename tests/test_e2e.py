"""Test end-to-end: agent that <-> bot Telegram gia lap (HTTP server cuc bo).

Kiem tra ca duong di that:
  TelegramClient (urllib, JSON + multipart) -> Agent -> handler -> tra loi
va chieu nguoc lai: Monitor phat hien may vua bat -> gui bao ve bot.

Khong can mang, khong can token that.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from remotepc import actions, system_info
from remotepc.bot import Agent
from remotepc.config import Config
from remotepc.paths import Layout


class FakeTelegramServer:
    """Server gia lap API Telegram, ghi lai moi tin nhan agent gui len."""

    def __init__(self):
        self.updates: list[dict] = []          # hang doi update cho agent lay
        self.fail_methods: set[str] = set()    # method nao thi tra ve loi
        self.fail_status: dict[str, int] = {}  # ma HTTP loi cua tung method (mac dinh 500)
        self.request_count: dict[str, int] = {}
        self.messages: list[dict] = []         # sendMessage
        self.photos: list[dict] = []           # sendPhoto
        self.calls: list[str] = []
        self._offset = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):        # im lang
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                method = self.path.rsplit("/", 1)[-1]
                outer.calls.append(method)
                outer.request_count[method] = outer.request_count.get(method, 0) + 1

                if method in outer.fail_methods:
                    code = outer.fail_status.get(method, 500)
                    data = json.dumps({"ok": False, "error_code": code,
                                       "description": "gia lap loi server"}).encode()
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

                if method == "getUpdates":
                    params = json.loads(body or b"{}")
                    offset = int(params.get("offset") or 0)
                    out = [u for u in outer.updates if u["update_id"] >= offset]
                    payload = {"ok": True, "result": out}
                elif method == "sendMessage":
                    params = json.loads(body or b"{}")
                    outer.messages.append(params)
                    payload = {"ok": True, "result": {"message_id": len(outer.messages)}}
                elif method == "sendPhoto":
                    outer.photos.append({
                        "bytes": len(body),
                        "body_head": body[:400].decode("utf-8", "replace"),
                    })
                    payload = {"ok": True, "result": {"message_id": 1}}
                elif method == "getMe":
                    payload = {"ok": True, "result": {"id": 1, "username": "RemotePC_test_bot",
                                                      "first_name": "RemotePC"}}
                elif method == "setMyCommands":
                    payload = {"ok": True, "result": True}
                else:
                    payload = {"ok": True, "result": True}

                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    # ---------------- API cho test ---------------- #

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def push(self, text: str, chat_id: int = 111, name: str = "Bo") -> None:
        self._offset += 1
        self.updates.append({
            "update_id": self._offset,
            "message": {
                "message_id": self._offset, "date": int(time.time()), "text": text,
                "chat": {"id": chat_id, "type": "private"},
                "from": {"id": chat_id, "first_name": name},
            },
        })

    @property
    def last_text(self) -> str:
        return self.messages[-1].get("text", "") if self.messages else ""

    def all_text(self) -> str:
        return "\n".join(m.get("text", "") for m in self.messages)

    def wait_for(self, needle: str, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if needle in self.all_text():
                return True
            time.sleep(0.05)
        return False

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = FakeTelegramServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.messages.clear()
        self.server.photos.clear()
        self.server.calls.clear()
        self.server.updates.clear()
        self.server.fail_methods.clear()
        self.server.fail_status.clear()
        self.server.request_count.clear()
        self.server._offset = 0

        self.tmp = tempfile.TemporaryDirectory()
        self.layout = Layout(Path(self.tmp.name)).ensure()
        self.cfg_path = self.layout.config_file

        self.cfg = Config()
        self.cfg.telegram.bot_token = "1:fake"
        self.cfg.telegram.api_base = self.server.api_base
        self.cfg.telegram.poll_timeout = 1
        self.cfg.telegram.allowed_chat_ids = [111]
        self.cfg.machine_tag = "pc1"
        self.cfg.notify.heartbeat_minutes = 0
        self.cfg.data_dir = str(self.layout.base)

        self._patches = [
            mock.patch.object(system_info, "is_session_zero", return_value=False),
            mock.patch.object(system_info, "uptime_seconds", return_value=600.0),
            mock.patch.object(system_info, "logged_on_users", return_value=["Kid"]),
            mock.patch.object(system_info, "idle_seconds", return_value=3.0),
            mock.patch.object(system_info, "collect", side_effect=lambda **kw:
                              system_info.SystemInfo(hostname="PC-CON", user="Kid",
                                                     uptime_seconds=600.0,
                                                     boot_time=time.time() - 600,
                                                     lan_ip="192.168.1.20",
                                                     public_ip="1.2.3.4")),
            mock.patch.object(system_info, "os_full_name", return_value="Windows 11"),
            mock.patch.object(system_info, "public_ip", return_value="1.2.3.4"),
            # khong thuc su tat may / khoa may trong luc test
            mock.patch.object(actions, "shutdown", return_value=(True, "OK")),
            mock.patch.object(actions, "abort_shutdown", return_value=(True, "OK")),
            mock.patch.object(actions, "lock_workstation", return_value=(True, "OK")),
            mock.patch.object(actions, "logoff", return_value=(True, "OK")),
            mock.patch.object(actions, "message_box", return_value=(True, "OK")),
            mock.patch.object(actions, "overlay", return_value=(True, "OK")),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def make_agent(self) -> Agent:
        return Agent(self.cfg, self.cfg_path, self.layout, stealth=False)

    # ------------------------------------------------------------------ #

    def test_boot_report_reaches_bot(self):
        agent = self.make_agent()
        agent.monitor.check_boot()
        self.assertTrue(self.server.wait_for("MÁY VỪA ĐƯỢC BẬT", timeout=8),
                        self.server.all_text())
        self.assertIn("PC-CON", self.server.all_text())
        self.assertIn("1.2.3.4", self.server.all_text())

    def test_login_report_reaches_bot(self):
        agent = self.make_agent()
        agent.monitor.check_login()
        self.assertTrue(self.server.wait_for("CÓ NGƯỜI ĐĂNG NHẬP", timeout=8))
        self.assertIn("Kid", self.server.all_text())

    def test_command_roundtrip(self):
        agent = self.make_agent()
        self.server.push("/status")
        t = threading.Thread(target=agent.start, daemon=True)
        t.start()
        try:
            self.assertTrue(self.server.wait_for("GIỜ GIỚI NGHIÊM", timeout=12),
                            self.server.all_text())
            self.assertIn("PC-CON", self.server.all_text())
            self.assertIn("setMyCommands", self.server.calls)     # dang ky menu lenh
        finally:
            agent.stop()
            t.join(timeout=5)

    def test_shutdown_command_roundtrip(self):
        agent = self.make_agent()
        self.cfg.set_pin("1234")
        agent.cfg = self.cfg
        self.server.push("/shutdown 90 --msg \"Ngu di con\" --pin 1234")
        t = threading.Thread(target=agent.start, daemon=True)
        t.start()
        try:
            self.assertTrue(self.server.wait_for("SHUTDOWN", timeout=12),
                            self.server.all_text())
            self.assertIn("90 giây", self.server.all_text())
            self.assertTrue(actions.shutdown.called)
            self.assertEqual(actions.shutdown.call_args.kwargs.get("delay"), 90)
        finally:
            agent.stop()
            t.join(timeout=5)

    def test_pin_blocks_without_code(self):
        agent = self.make_agent()
        self.cfg.set_pin("1234")
        agent.cfg = self.cfg
        self.server.push("/shutdown 90")
        t = threading.Thread(target=agent.start, daemon=True)
        t.start()
        try:
            self.assertTrue(self.server.wait_for("cần mã PIN", timeout=12))
            self.assertFalse(actions.shutdown.called)
        finally:
            agent.stop()
            t.join(timeout=5)

    def test_message_command_shows_popup(self):
        agent = self.make_agent()
        self.server.push("/msg Con lam bai tap di")
        t = threading.Thread(target=agent.start, daemon=True)
        t.start()
        try:
            self.assertTrue(self.server.wait_for("MSG", timeout=12))
            self.assertTrue(actions.message_box.called)
            self.assertEqual(actions.message_box.call_args.kwargs.get("text"), "Con lam bai tap di")
        finally:
            agent.stop()
            t.join(timeout=5)

    def test_unauthorized_chat(self):
        agent = self.make_agent()
        self.server.push("/shutdown 1", chat_id=777)
        t = threading.Thread(target=agent.start, daemon=True)
        t.start()
        try:
            self.assertTrue(self.server.wait_for("chưa được cấp quyền", timeout=12))
            self.assertFalse(actions.shutdown.called)
        finally:
            agent.stop()
            t.join(timeout=5)

    def test_screenshot_uploads_photo(self):
        agent = self.make_agent()
        with mock.patch.object(agent, "grab_screenshot", return_value=b"\x89PNG\r\n\x1a\n" + b"x" * 200):
            self.server.push("/screenshot")
            t = threading.Thread(target=agent.start, daemon=True)
            t.start()
            try:
                deadline = time.time() + 12
                while time.time() < deadline and not self.server.photos:
                    time.sleep(0.05)
                self.assertTrue(self.server.photos, "Khong thay anh gui len")
                self.assertIn("sendPhoto", self.server.calls)
                head = self.server.photos[0]["body_head"]
                self.assertIn('name="photo"', head)          # dung truong multipart
                self.assertIn('name="chat_id"', head)
                self.assertGreater(self.server.photos[0]["bytes"], 200)
            finally:
                agent.stop()
                t.join(timeout=5)

    def test_curfew_violation_notifies(self):
        """Toan bo kich ban chinh: den 23:30 -> canh bao ve bot + hien thong bao."""
        agent = self.make_agent()
        self.cfg.curfew.windows[0].start = "23:30"
        self.cfg.curfew.windows[0].end = "00:00"
        self.cfg.curfew.windows[0].warn_before_minutes = [10]
        agent.monitor.reload_config(self.cfg)

        fake = [datetime_now(2026, 3, 10, 23, 25, 0)]
        with mock.patch.object(agent.monitor, "local_now", side_effect=lambda: fake[0]), \
             mock.patch.object(agent.monitor, "now", side_effect=lambda: fake[0].timestamp()):
            agent.monitor.check_curfew()
            self.assertTrue(self.server.wait_for("SẮP TỚI GIỜ GIỚI NGHIÊM", timeout=8),
                            self.server.all_text())
            self.assertIn("10 phút", self.server.all_text())

            fake[0] = datetime_now(2026, 3, 10, 23, 30, 30)
            agent.monitor.check_curfew()
            self.assertTrue(self.server.wait_for("ĐÃ VÀO GIỜ GIỚI NGHIÊM", timeout=8),
                            self.server.all_text())
        self.assertTrue(actions.overlay.called or actions.message_box.called,
                        "Phai hien thong bao tren may con")


class TestTelegramClientRetry(unittest.TestCase):
    """Khong duoc treo hang chuc giay khi mat mang (may dang tat / dang go)."""

    @classmethod
    def setUpClass(cls):
        cls.server = FakeTelegramServer()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    def setUp(self):
        self.server.fail_methods.add("sendMessage")
        self.server.request_count.clear()
        from remotepc.telegram_client import TelegramClient
        self.tg = TelegramClient("1:fake", api_base=self.server.api_base, timeout=10)
        # Khong doi that giua cac lan thu lai (test chay < 1 giay thay vi 30 giay)
        patcher = mock.patch("remotepc.telegram_client.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.server.fail_methods.clear()
        self.server.fail_status.clear()

    def test_quick_fails_after_one_attempt(self):
        from remotepc.telegram_client import TelegramError
        start = time.time()
        with self.assertRaises(TelegramError):
            self.tg.send_message(111, "test", quick=True)
        self.assertEqual(self.server.request_count.get("sendMessage"), 1)
        self.assertLess(time.time() - start, 5.0)

    def test_normal_retries_several_times(self):
        from remotepc.telegram_client import TelegramError
        with self.assertRaises(TelegramError):
            self.tg.send_message(111, "test")
        self.assertGreaterEqual(self.server.request_count.get("sendMessage"), 3)

    def test_fatal_error_not_retried(self):
        """Token sai (401) -> khong thu lai, tranh bi Telegram chan IP."""
        from remotepc.telegram_client import TelegramError
        self.server.fail_methods.discard("sendMessage")
        self.server.fail_methods.add("getMe")
        self.server.fail_status["getMe"] = 401
        with self.assertRaises(TelegramError):
            self.tg.me(force=True)
        self.assertEqual(self.server.request_count.get("getMe"), 1)

    def test_rate_limit_waits_and_retries(self):
        """429 + retry_after -> cho dung thoi gian Telegram yeu cau roi thu lai."""
        from remotepc.telegram_client import TelegramError
        with mock.patch("remotepc.telegram_client.time.sleep") as slp:
            # lan 1: 429, cac lan sau: 500 (van la loi -> het luot thu)
            self.server.fail_status["sendMessage"] = 429
            with self.assertRaises(TelegramError):
                self.tg.send_message(111, "test")
        self.assertTrue(any(c.args[0] for c in slp.call_args_list), "Phai co cho khi bi 429")


class TestProxyConfig(unittest.TestCase):
    """Proxy phai duoc truyen xuong urllib (Telegram bi chan o nhieu noi)."""

    def test_client_uses_given_proxy(self):
        from remotepc.telegram_client import TelegramClient
        tg = TelegramClient("1:fake", proxy="http://127.0.0.1:1080")
        self.assertEqual(tg.proxy, "http://127.0.0.1:1080")
        opener = tg._opener()
        handlers = [type(h).__name__ for h in opener.handlers]
        self.assertIn("ProxyHandler", handlers)

    def test_client_falls_back_to_env(self):
        from remotepc.telegram_client import TelegramClient
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://env-proxy:8080"}, clear=False):
            tg = TelegramClient("1:fake")
        self.assertEqual(tg.proxy, "http://env-proxy:8080")

    def test_explicit_proxy_wins_over_env(self):
        from remotepc.telegram_client import TelegramClient
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://env-proxy:8080"}, clear=False):
            tg = TelegramClient("1:fake", proxy="http://mine:1080")
        self.assertEqual(tg.proxy, "http://mine:1080")


def datetime_now(*a):
    from datetime import datetime
    return datetime(*a)


if __name__ == "__main__":
    unittest.main()
