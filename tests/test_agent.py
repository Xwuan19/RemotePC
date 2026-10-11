"""Test tich hop: Agent nhan tin nhan Telegram -> tra loi + thuc thi hanh dong.

Telegram duoc gia lap (khong noi mang), cac lenh nguon dien duoc mock
de test khong vo tinh tat may that.
"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from remotepc import actions, system_info
from remotepc.bot import Agent
from remotepc.config import Config
from remotepc.paths import Layout
from remotepc.telegram_client import Update


class FakeTelegram:
    """Ghi lai moi tin nhan thay vi gui that."""

    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.messages: list[tuple[int, str]] = []      # chi tin nhan text (khong ke caption anh)
        self.photos: list[tuple[int, bytes]] = []
        self.commands_set = False

    def send_message(self, chat_id, text, parse_mode=None, reply_markup=None,
                     silent=False, disable_preview=True):
        self.sent.append((chat_id, text))
        self.messages.append((chat_id, text))
        return {"message_id": len(self.sent)}

    def send_photo(self, chat_id, photo, caption="", filename="x.png", silent=False):
        self.photos.append((chat_id, photo))
        self.sent.append((chat_id, caption))
        return {"message_id": len(self.sent)}

    def set_commands(self, commands):
        self.commands_set = True

    # --- tien ich test ---
    @property
    def last(self) -> str:
        return self.sent[-1][1] if self.sent else ""

    def all_text(self) -> str:
        return "\n".join(t for _, t in self.sent)


def make_update(text: str, chat_id: int = 111, update_id: int = 1, name: str = "Bo") -> Update:
    return Update(update_id=update_id, chat_id=chat_id, text=text, message_id=update_id,
                  from_id=chat_id, from_name=name, date=int(time.time()))


class AgentTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.layout = Layout(Path(self.tmp.name)).ensure()
        self.cfg = Config()
        self.cfg.telegram.bot_token = "123456:ABC-test"
        self.cfg.telegram.allowed_chat_ids = [111]
        self.cfg.machine_tag = "pc1"
        self.cfg.set_pin("1234")
        self.cfg_path = self.layout.config_file
        self.agent = Agent(self.cfg, self.cfg_path, self.layout, stealth=False)
        self.tg = FakeTelegram()
        self.agent.tg = self.tg                       # thay client that
        self.power_calls: list[tuple] = []

        # KHONG goi lenh tat may that trong luc test
        self._patches = [
            mock.patch.object(actions, "shutdown", side_effect=self._fake_power),
            mock.patch.object(actions, "abort_shutdown", side_effect=self._fake_power),
            mock.patch.object(actions, "lock_workstation", side_effect=self._fake_power),
            mock.patch.object(actions, "logoff", side_effect=self._fake_power),
            mock.patch.object(system_info, "is_session_zero", return_value=False),
        ]
        for p in self._patches:
            p.start()

    def _fake_power(self, *a, **kw):
        self.power_calls.append((a, kw))
        return True, "OK (test)"

    def tearDown(self):
        for p in self._patches:
            p.stop()
        import logging
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, logging.FileHandler):
                h.close()
                root.removeHandler(h)
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def send(self, text: str, chat_id: int = 111) -> str:
        before = len(self.tg.sent)
        self.agent._handle_update(make_update(text, chat_id=chat_id))
        new = [t for _, t in self.tg.sent[before:]]
        return "\n".join(new)


class TestAuthorization(AgentTestCase):
    def test_unauthorized_chat_rejected_once(self):
        out = self.send("/status", chat_id=999)
        self.assertIn("chưa được cấp quyền", out)
        out2 = self.send("/status", chat_id=999)
        self.assertEqual(out2, "", "Chat la chi bi nhac 1 lan")
        # bao ve bo ve su co nguoi la
        self.assertIn("chat lạ", self.tg.all_text())

    def test_pair_with_pin(self):
        cfg = self.cfg
        cfg.telegram.allowed_chat_ids = []
        self.agent.cfg = cfg
        out = self.send("/pair 1234", chat_id=555)
        self.assertIn("GHÉP NỐI", out)
        self.assertIn(555, cfg.telegram.allowed_chat_ids)
        # sau khi ghep, chat moi dung duoc
        out = self.send("/ping", chat_id=555)
        self.assertIn("pong", out)

    def test_pair_wrong_pin(self):
        self.cfg.telegram.allowed_chat_ids = []
        out = self.send("/pair 0000", chat_id=555)
        self.assertIn("Sai mã PIN", out)
        self.assertNotIn(555, self.cfg.telegram.allowed_chat_ids)

    def test_pair_no_pin_configured(self):
        self.cfg.telegram.allowed_chat_ids = []
        self.cfg.set_pin("")
        out = self.send("/pair", chat_id=555)
        self.assertIn("GHÉP NỐI", out)


class TestPin(AgentTestCase):
    def test_pin_required_for_shutdown(self):
        out = self.send("/shutdown 60")
        self.assertIn("cần mã PIN", out)
        self.assertEqual(self.power_calls, [])

    def test_inline_pin(self):
        out = self.send("/shutdown 60 --pin 1234")
        self.assertIn("SHUTDOWN", out)
        self.assertTrue(self.power_calls)
        self.assertEqual(self.power_calls[-1][1].get("delay"), 60)

    def test_wrong_pin(self):
        out = self.send("/shutdown 60 --pin 9999")
        self.assertIn("cần mã PIN", out)

    def test_unlock_session(self):
        out = self.send("/unlock 1234")
        self.assertIn("mở khoá", out)
        out = self.send("/restart 10")
        self.assertIn("RESTART", out)
        self.assertTrue(self.power_calls)

    def test_stale_command_is_ignored(self):
        self.send("/unlock 1234")
        before_calls = len(self.power_calls)
        upd = Update(update_id=99, chat_id=111, text="/shutdown 60", date=int(time.time() - 3600))
        self.agent._handle_update(upd)
        self.assertIn("BỎ QUA", self.tg.last)
        self.assertEqual(len(self.power_calls), before_calls)


    def test_unlock_wrong_pin(self):
        out = self.send("/unlock 0000")
        self.assertIn("Sai mã PIN", out)

    def test_abort_needs_no_pin(self):
        """Huy tat may phai lam duoc ngay, khong doi PIN."""
        out = self.send("/abort")
        self.assertIn("ABORT", out)
        self.assertTrue(self.power_calls)

    def test_lock_needs_no_pin(self):
        out = self.send("/lock")
        self.assertIn("loại bỏ", out)

    def test_status_needs_no_pin(self):
        out = self.send("/status")
        self.assertIn("Giờ giới nghiêm", out)

    def test_change_pin(self):
        out = self.send("/pin 1234 5678")
        self.assertIn("Đã đổi mã PIN", out)
        self.assertTrue(self.cfg.check_pin("5678"))
        self.assertFalse(self.cfg.check_pin("1234"))


class TestCommands(AgentTestCase):
    def test_help(self):
        out = self.send("/help")
        self.assertIn("Remote PC", out)
        self.assertIn("/shutdown", out)

    def test_ping(self):
        self.assertIn("pong", self.send("/ping"))

    def test_unknown(self):
        self.assertIn("Không rõ lệnh", self.send("/khongtonthai"))

    def test_plain_text(self):
        self.assertIn("/", self.send("chao bo"))

    def test_msg_shows_popup(self):
        with mock.patch.object(actions, "message_box", return_value=(True, "OK")) as mb:
            out = self.send("/msg Con ngu di")
        self.assertIn("MSG", out)
        self.assertTrue(mb.called)
        self.assertEqual(mb.call_args.kwargs.get("text") or mb.call_args[1].get("text"), "Con ngu di")

    def test_msg_fullscreen(self):
        with mock.patch.object(actions, "overlay", return_value=(True, "OK")) as ov:
            out = self.send('/msg --full --title "Bo noi" Ngu di con')
        self.assertTrue(ov.called)
        kw = ov.call_args.kwargs
        self.assertEqual(kw.get("text"), "Ngu di con")
        self.assertEqual(kw.get("title"), "Bo noi")
        self.assertIn("toàn màn hình", out)

    def test_msg_empty(self):
        self.assertIn("Cú pháp", self.send("/msg"))

    def test_lock(self):
        out = self.send("/lock")
        self.assertIn("loại bỏ", out)

    def test_shutdown_default_delay(self):
        out = self.send("/shutdown --pin 1234")
        self.assertIn(f"{self.cfg.power.shutdown_delay} giây", out)
        self.assertIn("/abort", out)

    def test_abort(self):
        self.send("/shutdown 300 --pin 1234")
        out = self.send("/abort --pin 1234")
        self.assertIn("ABORT", out)

    def test_shutdown_via_alias(self):
        self.send("/tat 30 --pin 1234")
        self.assertTrue(self.power_calls)

    def test_timer_menu(self):
        out = self.send("/timer")
        self.assertIn("Hẹn giờ tắt máy", out)

    def test_timer_schedule(self):
        out = self.send("/timer 30p --pin 1234")
        self.assertIn("SHUTDOWN", out)
        self.assertEqual(self.power_calls[-1][1]["delay"], 1800)

    def test_shutdown_clock_time(self):
        out = self.send("/shutdown 23:30 --pin 1234")
        self.assertIn("SHUTDOWN", out)
        self.assertGreater(self.power_calls[-1][1]["delay"], 0)

    def test_screenshot_no_uihost(self):
        with mock.patch.object(self.agent, "grab_screenshot", return_value=b"\x89PNG-data"):
            self.send("/screenshot")
        self.assertEqual(len(self.tg.photos), 1)
        self.assertEqual(self.tg.photos[0][1], b"\x89PNG-data")
        self.assertIn("Màn hình", self.tg.sent[-1][1])     # caption di kem anh

    def test_screenshot_fail(self):
        with mock.patch.object(self.agent, "grab_screenshot", return_value=None):
            out = self.send("/screenshot")
        self.assertIn("thất bại", out)

    def test_apps(self):
        with mock.patch.object(self.agent, "get_running_apps", return_value=[
                {"name": "chrome.exe", "pid": 1, "title": "Google Chrome"},
                {"name": "game.exe", "pid": 2, "title": "Roblox"}]):
            out = self.send("/apps")
        self.assertIn("chrome.exe", out)
        self.assertIn("game.exe", out)
        self.assertIn("Roblox", out)

    def test_apps_all(self):
        with mock.patch.object(actions, "list_processes", return_value=[
                {"name": "svchost.exe", "pid": 100, "mem_kb": 500_000}]):
            out = self.send("/apps all")
        self.assertIn("svchost.exe", out)

    def test_kill(self):
        with mock.patch.object(actions, "kill_process", return_value=(True, "OK")):
            out = self.send("/kill game.exe --pin 1234")
        self.assertIn("Đã tắt", out)

    def test_log(self):
        self.send("/ping")
        out = self.send("/log 5")
        self.assertIn("sự kiện", out.lower())

    def test_machines(self):
        out = self.send("/machines")
        self.assertIn("pc1", out)


class TestTargeting(AgentTestCase):
    def test_command_for_other_machine_is_ignored(self):
        out = self.send("/msg @pc2 Chao")
        self.assertEqual(out, "", "May khong duoc tra loi lenh danh cho may khac")

    def test_command_for_this_machine(self):
        with mock.patch.object(actions, "message_box", return_value=(True, "OK")):
            out = self.send("/msg @pc1 Chao con")
        self.assertIn("MSG", out)

    def test_all_tag(self):
        with mock.patch.object(actions, "message_box", return_value=(True, "OK")):
            out = self.send("/msg @all Chao cac may")
        self.assertIn("MSG", out)

    def test_require_tag_mode(self):
        self.cfg.require_tag = True
        self.agent.cfg = self.cfg
        out = self.send("/ping")
        self.assertEqual(out, "")
        self.assertIn("pong", self.send("/ping @pc1"))


class TestCurfewCommand(AgentTestCase):
    def test_show(self):
        out = self.send("/curfew")
        self.assertIn("Giờ giới nghiêm", out)
        self.assertIn("23:30", out)

    def test_set_window(self):
        out = self.send("/curfew set 22:00 05:00 --pin 1234")
        self.assertIn("Đã lưu", out)
        self.assertEqual(self.cfg.curfew.windows[0].start, "22:00")
        self.assertEqual(self.cfg.curfew.windows[0].end, "05:00")
        # engine duoc nap lai
        self.assertEqual(self.agent.monitor.windows[0].start, 22 * 60)

    def test_set_invalid_time(self):
        out = self.send("/curfew set 99:99 05:00 --pin 1234")
        self.assertIn("thất bại", out.lower())

    def test_toggle(self):
        self.send("/curfew off --pin 1234")
        self.assertFalse(self.cfg.curfew.enabled)
        self.send("/curfew on --pin 1234")
        self.assertTrue(self.cfg.curfew.enabled)

    def test_warn_and_actions(self):
        self.send("/curfew warn 45,15 --pin 1234")
        self.assertEqual(self.cfg.curfew.windows[0].warn_before_minutes, [15, 45])
        self.send("/curfew action lock --pin 1234")
        self.assertEqual(self.cfg.curfew.windows[0].on_start, "lock")
        self.send("/curfew end shutdown --pin 1234")
        self.assertEqual(self.cfg.curfew.windows[0].on_end, "shutdown")

    def test_auto_shutdown(self):
        self.send("/curfew auto 20 --pin 1234")
        self.assertEqual(self.cfg.curfew.auto_shutdown_after_minutes, 20)

    def test_persisted_to_disk(self):
        self.send("/curfew set 21:00 23:00 --pin 1234")
        from remotepc.config import load_config
        cfg2, _ = load_config(self.cfg_path)
        self.assertEqual(cfg2.curfew.windows[0].start, "21:00")


class TestConfigCommand(AgentTestCase):
    def test_show_masks_token(self):
        out = self.send("/config show")
        self.assertNotIn("123456:ABC-test", out)
        self.assertIn("***", out)

    def test_set_value(self):
        out = self.send("/config set notify.on_boot false --pin 1234")
        self.assertIn("notify.on_boot", out)
        self.assertFalse(self.cfg.notify.on_boot)

    def test_set_unknown_key(self):
        out = self.send("/config set notify.khong_co false --pin 1234")
        self.assertIn("thất bại", out.lower())


class TestUihostRouting(AgentTestCase):
    def test_popup_goes_through_ipc_when_session0(self):
        with mock.patch.object(system_info, "is_session_zero", return_value=True), \
             mock.patch.object(actions, "message_box") as mb, \
             mock.patch.object(self.agent, "keep_uihost_alive", return_value=True), \
             mock.patch.object(self.agent.queue, "request", return_value=None) as req:
            self.send("/msg Chao con")
        self.assertTrue(req.called, "O session 0 phai day qua uihost")
        self.assertEqual(req.call_args.args[0], "message")
        self.assertFalse(mb.called, "Khong duoc tu hien hop thoai o session 0")

    def test_lock_goes_through_ipc_when_session0(self):
        with mock.patch.object(system_info, "is_session_zero", return_value=True), \
             mock.patch.object(self.agent, "keep_uihost_alive", return_value=True), \
             mock.patch.object(self.agent.queue, "request", return_value=None) as req:
            self.agent.do_action("lock")
        self.assertEqual(req.call_args.args[0], "lock")

    def test_shutdown_runs_directly_even_in_session0(self):
        with mock.patch.object(system_info, "is_session_zero", return_value=True):
            self.send("/shutdown 60 --pin 1234")
        self.assertTrue(self.power_calls, "Tat may phai chay duoc tu SYSTEM")

    def test_format_shutdown_reason_remote_bot(self):
        raw = ("The process C:\\Windows\\SYSTEM32\\shutdown.exe (DESKTOP-9FVG46V) has initiated the shutdown "
               "of computer DESKTOP-9FVG46V on behalf of user NT AUTHORITY\\SYSTEM for the following reason: "
               "No title for this reason could be found Reason Code: 0x800000ff Shutdown Type: shutdown "
               "Comment: Tat may di ngu nhanh cho tao.")
        res = system_info.format_shutdown_reason(raw)
        self.assertIn("Tắt máy bởi: *Lệnh từ xa (Bot Telegram)*", res)
        self.assertIn("Lời nhắn: “Tat may di ngu nhanh cho tao.”", res)

    def test_format_shutdown_reason_user_direct(self):
        raw = ("The process C:\\Windows\\explorer.exe (DESKTOP-9FVG46V) has initiated the shutdown of computer "
               "DESKTOP-9FVG46V on behalf of user DESKTOP-9FVG46V\\emtrai for the following reason: "
               "Other (Planned) Reason Code: 0x800000ff Shutdown Type: shutdown")
        res = system_info.format_shutdown_reason(raw)
        self.assertIn("Tắt máy bởi: *Người dùng bấm trực tiếp (emtrai)*", res)

    def test_format_shutdown_reason_aborted(self):
        raw = "The shutdown of computer DESKTOP-9FVG46V has been aborted by user NT AUTHORITY\\SYSTEM."
        res = system_info.format_shutdown_reason(raw)
        self.assertIn("Đã huỷ tắt máy bởi: *Lệnh từ xa (Bot Telegram)*", res)

    def test_do_action_shutdown_notifies(self):
        with mock.patch.object(self.agent, "notify") as mock_notify:
            self.agent.do_action("shutdown", delay=60, message="Ngu di")
        mock_notify.assert_called_once()
        self.assertIn("Ngu di", mock_notify.call_args.args[0])

    def test_cmd_notify_shutdown_filters_bot_and_abort(self):
        from remotepc.__main__ import cmd_notify_shutdown
        import argparse
        args = argparse.Namespace(config=str(self.cfg_path))
        with mock.patch("remotepc.system_info.last_shutdown_event",
                        return_value="Tắt máy bởi: *Lệnh từ xa (Bot Telegram)* · Lời nhắn: “test”"):
            with mock.patch("remotepc.telegram_client.TelegramClient.send_message") as mock_send:
                res = cmd_notify_shutdown(args)
                self.assertEqual(res, 0)
                mock_send.assert_not_called()

        with mock.patch("remotepc.system_info.last_shutdown_event",
                        return_value="Đã huỷ tắt máy bởi: *Lệnh từ xa (Bot Telegram)*"):
            with mock.patch("remotepc.telegram_client.TelegramClient.send_message") as mock_send:
                res = cmd_notify_shutdown(args)
                self.assertEqual(res, 0)
                mock_send.assert_not_called()


if __name__ == "__main__":
    unittest.main()

