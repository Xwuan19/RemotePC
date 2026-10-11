"""Test cac hanh dong he thong - dac biet la argv that cua lenh Windows.

Tren may dev (Linux/macOS) khong the goi `shutdown /s`, nen ta gia lap
IS_WINDOWS va bat lai argv ma tool se truyen cho he dieu hanh.
"""

from __future__ import annotations

import unittest
from unittest import mock

from remotepc import actions


class FakeCompleted:
    returncode = 0
    stdout = ""
    stderr = ""


class TestWindowsPowerArgs(unittest.TestCase):
    def setUp(self):
        self.calls: list[list[str]] = []

        def fake_popen(cmd, timeout=20, **kw):
            self.calls.append(list(cmd))
            return FakeCompleted()

        self._patches = [
            mock.patch.object(actions, "IS_WINDOWS", True),
            mock.patch.object(actions, "_popen", side_effect=fake_popen),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_shutdown_args(self):
        ok, detail = actions.shutdown(delay=45, message="Bo tat may nhe", force=True)
        self.assertTrue(ok)
        self.assertEqual(self.calls[-1][:2], ["shutdown", "/s"])
        self.assertIn("/t", self.calls[-1])
        self.assertEqual(self.calls[-1][self.calls[-1].index("/t") + 1], "45")
        self.assertIn("/f", self.calls[-1])
        self.assertIn("Bo tat may nhe", self.calls[-1])
        self.assertIn("/c", self.calls[-1])

    def test_restart_uses_r_flag(self):
        actions.shutdown(delay=10, message="x", restart=True)
        self.assertIn("/r", self.calls[-1])
        self.assertNotIn("/s", self.calls[-1])

    def test_no_force_flag(self):
        actions.shutdown(delay=10, message="x", force=False)
        self.assertNotIn("/f", self.calls[-1])

    def test_delay_is_clamped(self):
        actions.shutdown(delay=-5, message="x")
        self.assertEqual(self.calls[-1][self.calls[-1].index("/t") + 1], "0")
        actions.shutdown(delay=10**9, message="x")
        # khong duoc truyen mot con so vo ly xuong Windows
        self.assertEqual(self.calls[-1][self.calls[-1].index("/t") + 1], str(10 * 60 * 60))

    def test_message_is_truncated(self):
        actions.shutdown(delay=1, message="a" * 2000)
        msg = self.calls[-1][self.calls[-1].index("/c") + 1]
        self.assertLessEqual(len(msg), 512)

    def test_abort(self):
        ok, _ = actions.abort_shutdown()
        self.assertTrue(ok)
        self.assertEqual(self.calls[-1], ["shutdown", "/a"])

    def test_failure_reported(self):
        class Failed(FakeCompleted):
            returncode = 1
            stderr = "Access denied"

        with mock.patch.object(actions, "_popen", return_value=Failed()):
            ok, detail = actions.shutdown(delay=1, message="x")
        self.assertFalse(ok)
        self.assertIn("Access denied", detail)

    def test_logoff(self):
        ok, _ = actions.logoff()
        self.assertTrue(ok)
        self.assertEqual(self.calls[-1], ["shutdown", "/l"])


class TestUnixPowerArgs(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._patches = [
            mock.patch.object(actions, "IS_WINDOWS", False),
            mock.patch.object(actions, "_popen", side_effect=lambda cmd, timeout=20, **kw:
                              self.calls.append(list(cmd)) or FakeCompleted()),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def test_shutdown_uses_minutes(self):
        actions.shutdown(delay=120, message="x")
        self.assertEqual(self.calls[-1][0], "shutdown")
        self.assertIn("-h", self.calls[-1])
        self.assertIn("+2", self.calls[-1])

    def test_restart(self):
        actions.shutdown(delay=60, message="x", restart=True)
        self.assertIn("-r", self.calls[-1])


class TestProcessList(unittest.TestCase):
    def test_windows_tasklist_csv_parsing(self):
        csv_out = (
            '"chrome.exe","1234","Console","1","123,456 K"\n'
            '"System","4","Services","0","1,234 K"\n'
        )

        class P:
            returncode = 0
            stdout = csv_out
            stderr = ""

        with mock.patch.object(actions, "IS_WINDOWS", True), \
             mock.patch.object(actions, "_popen", return_value=P()):
            rows = actions.list_processes(top=5)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "chrome.exe")
        self.assertEqual(rows[0]["pid"], 1234)
        self.assertEqual(rows[0]["mem_kb"], 123456)

    def test_kill_by_name_and_pid(self):
        calls = []
        with mock.patch.object(actions, "IS_WINDOWS", True), \
             mock.patch.object(actions, "_popen", side_effect=lambda c, timeout=20, **kw:
                               calls.append(list(c)) or FakeCompleted()):
            actions.kill_process("game.exe")
            actions.kill_process("4321")
        self.assertEqual(calls[0][:3], ["taskkill", "/IM", "game.exe"])
        self.assertIn("/F", calls[0])
        self.assertEqual(calls[1][:3], ["taskkill", "/PID", "4321"])


if __name__ == "__main__":
    unittest.main()
