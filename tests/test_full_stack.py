"""Test FULL-STACK: agent that (subprocess) <-> server Telegram gia lap.

Khac voi test_e2e (goi Agent trong cung process), file nay chay
`python -m remotepc run` nhu mot tien trinh doc lap — dung nhu khi cai that —
nen no bat duoc loi o lop CLI, vong long-polling that, va viec ghi state xuong dia.

AN TOAN: lenh `shutdown` that duoc thay bang mot script gia trong PATH
(duong nhien khong ai muon test lam tat may dang chay test).
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from remotepc.paths import repo_root
from tests.test_e2e import FakeTelegramServer

AGENT_STARTUP = 25.0      # giay cho agent khoi dong + gui bao boot
REPLY_WAIT = 25.0         # giay cho mot lenh duoc tra loi


def _pin_hash(pin: str) -> tuple[str, str]:
    """Tinh dung cach agent bam PIN (sha256 + salt)."""
    import hashlib
    salt = "test-salt-0001"
    return hashlib.sha256((salt + pin).encode()).hexdigest(), salt

FAKE_SHUTDOWN = """#!/bin/sh
# Gia lap `shutdown` de test khong tat that
echo "$@" >> "{log}"
exit 0
"""

FAKE_SHUTDOWN_BAT = """@echo off
rem Gia lap `shutdown` tren Windows de test khong tat that
echo %* >> "{log}"
exit /b 0
"""


class TestFullStack(unittest.TestCase):
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
        self.server.request_count.clear()
        self.server._offset = 0

        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data = self.base / "data"
        self.data.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()

        # `shutdown` gia -> ghi lai tham so de test kiem tra
        self.shutdown_log = self.base / "shutdown-calls.txt"
        if sys.platform == "win32":
            fake = self.bin / "shutdown.cmd"
            fake.write_text(FAKE_SHUTDOWN_BAT.format(log=self.shutdown_log), encoding="utf-8")
        else:
            fake = self.bin / "shutdown"
            fake.write_text(FAKE_SHUTDOWN.format(log=self.shutdown_log), encoding="utf-8")
            fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        self.cfg_path = self.base / "config.json"
        self.cfg_path.write_text(json.dumps({
            "machine_tag": "pc-fullstack",
            "data_dir": str(self.data),
            # PIN duoc dat san; test "chua dat PIN" se ghi file rieng
            "pin_hash": _pin_hash("1234")[0],
            "pin_salt": _pin_hash("1234")[1],
            "telegram": {
                "bot_token": "1:fake",
                "api_base": self.server.api_base,
                "allowed_chat_ids": [111],
                "poll_timeout": 1,
            },
            "stealth": {"level": 0, "hide_console": False, "display_name": "TestAgent"},
            "curfew": {
                "enabled": True,
                "windows": [{"name": "Đêm", "start": "23:30", "end": "00:00",
                             "warn_before_minutes": [30], "on_start": "popup",
                             "on_end": "notify"}],
            },
            "power": {"shutdown_delay": 30},
        }, ensure_ascii=False), encoding="utf-8")

        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo_root()) + os.pathsep + env.get("PYTHONPATH", "")
        env["PATH"] = f"{self.bin}{os.pathsep}{env.get('PATH', '')}"
        env["REMOTEPC_SHUTDOWN_BIN"] = str(fake)
        env.pop("REMOTEPC_CONFIG", None)
        env["REMOTEPC_BOOT_DELAY"] = "0.5"      # khong bat test doi 8 giay moi vong
        self.env = env
        self.proc = None

    def tearDown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.proc:
            for st in (self.proc.stdout, self.proc.stderr, self.proc.stdin):
                try:
                    if st:
                        st.close()
                except Exception:
                    pass
        self.tmp.cleanup()

    # ------------------------------------------------------------------ #

    def start_agent(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "remotepc", "run", "--no-stealth",
             "--config", str(self.cfg_path)],
            cwd=str(repo_root()), env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        return self.proc

    def agent_output(self) -> str:
        if not self.proc or self.proc.poll() is None:
            return ""
        try:
            return self.proc.stdout.read() or ""
        except Exception:
            return ""

    def wait_reply(self, needle: str, timeout: float = REPLY_WAIT) -> bool:
        deadline = time.time() + timeout
        needle_lower = needle.lower()
        while time.time() < deadline:
            if needle_lower in self.server.all_text().lower():
                return True
            if self.proc and self.proc.poll() is not None:
                return False
            time.sleep(0.1)
        return False

    # ------------------------------------------------------------------ #

    def test_agent_boots_and_reports(self):
        """May 'vua bat' -> agent phai tu bao ve bot ma khong can ai ra lenh."""
        self.start_agent()
        self.assertTrue(self.wait_reply("MÁY VỪA ĐƯỢC BẬT", timeout=AGENT_STARTUP),
                        f"Khong thay bao boot.\n{self.server.all_text()}\n{self.agent_output()}")
        text = self.server.all_text()
        self.assertIn("pc-fullstack", text)

    def test_status_command(self):
        self.start_agent()
        self.assertTrue(self.wait_reply("MÁY VỪA ĐƯỢC BẬT", timeout=AGENT_STARTUP))
        self.server.push("/status")
        self.assertTrue(self.wait_reply("GIỜ GIỚI NGHIÊM"),
                        f"{self.server.all_text()}\n{self.agent_output()}")
        self.assertIn("23:30", self.server.all_text())
        self.assertIn("pc-fullstack", self.server.all_text())

    def test_message_command(self):
        self.start_agent()
        self.assertTrue(self.wait_reply("MÁY VỪA ĐƯỢC BẬT", timeout=AGENT_STARTUP))
        self.server.push("/msg Con ngu di nhe")
        self.assertTrue(self.wait_reply("MSG"), f"{self.server.all_text()}\n{self.agent_output()}")
        self.assertIn("thành công", self.server.all_text())

    def test_shutdown_blocked_without_pin(self):
        self.start_agent()
        self.assertTrue(self.wait_reply("MÁY VỪA ĐƯỢC BẬT", timeout=AGENT_STARTUP))

        self.server.push("/shutdown 45")
        self.assertTrue(self.wait_reply("cần mã PIN"), self.server.all_text())
        self.assertFalse(self.shutdown_log.exists(),
                         "TUYET DOI khong duoc tat may khi thieu PIN")

    def test_shutdown_wrong_pin(self):
        self.start_agent()
        self.assertTrue(self.wait_reply("MÁY VỪA ĐƯỢC BẬT", timeout=AGENT_STARTUP))
        self.server.push("/shutdown 45 --pin 9999")
        self.assertTrue(self.wait_reply("cần mã PIN"), self.server.all_text())
        self.assertFalse(self.shutdown_log.exists())

    def test_shutdown_runs_with_correct_pin(self):
        """Duong di that: Telegram -> agent -> lenh `shutdown` cua he dieu hanh."""
        self.start_agent()
        self.assertTrue(self.wait_reply("MÁY VỪA ĐƯỢC BẬT", timeout=AGENT_STARTUP))

        self.server.push('/shutdown 45 --msg "Bo tat may nhe" --pin 1234')
        self.assertTrue(self.wait_reply("45 giây", timeout=REPLY_WAIT),
                        f"{self.server.all_text()}\n{self.agent_output()}")
        self.assertIn("45 giây", self.server.all_text())

        # script `shutdown` gia phai duoc goi dung tham so
        deadline = time.time() + 5
        while time.time() < deadline and not self.shutdown_log.exists():
            time.sleep(0.1)
        self.assertTrue(self.shutdown_log.exists(), "lenh shutdown he dieu hanh khong duoc goi")
        called = self.shutdown_log.read_text(encoding="utf-8")
        # Loi nhan tuy chinh cua bo me phai xuong duoc den lenh he dieu hanh
        self.assertIn("Bo tat may nhe", called)
        if sys.platform == "win32":
            self.assertIn("/s", called)
            self.assertIn("/t 45", called)
        else:
            # Unix: `shutdown -h +<phut>`, delay bi lam tron theo phut
            self.assertIn("-h", called)
        # bot cung phai bao "MAY DANG TAT"
        self.assertTrue(self.wait_reply("MÁY ĐANG TẮT"))

    def test_lock_command_needs_no_pin(self):
        self.start_agent()
        self.assertTrue(self.wait_reply("MÁY VỪA ĐƯỢC BẬT", timeout=AGENT_STARTUP))
        self.server.push("/lock")
        self.assertTrue(self.wait_reply("loại bỏ"), self.server.all_text())

    def test_warns_when_no_pin_set(self):
        """Chua dat PIN -> agent phai canh bao ro rang, khong im lang."""
        cfg = json.loads(self.cfg_path.read_text(encoding="utf-8"))
        cfg.pop("pin_hash")
        cfg.pop("pin_salt")
        self.cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")

        self.start_agent()
        self.assertTrue(self.wait_reply("CHƯA ĐẶT MÃ PIN", timeout=AGENT_STARTUP),
                        f"{self.server.all_text()}\n{self.agent_output()}")
        self.assertIn("/pin", self.server.all_text())

    def test_bot_command_menu_registered(self):
        self.start_agent()
        self.assertTrue(self.wait_reply("MÁY VỪA ĐƯỢC BẬT", timeout=AGENT_STARTUP))
        self.assertIn("setMyCommands", self.server.calls)

    def test_offset_persisted_across_restart(self):
        """Khoi dong lai khong xu ly lai tin nhan cu (offset duoc luu xuong dia)."""
        self.start_agent()
        self.assertTrue(self.wait_reply("MÁY VỪA ĐƯỢC BẬT", timeout=AGENT_STARTUP))
        self.server.push("/ping")
        self.assertTrue(self.wait_reply("pong"))

        offset_file = self.data / "offset.dat"
        self.assertTrue(offset_file.exists(), "offset chua duoc luu")
        saved = int(offset_file.read_text().strip())
        self.assertGreaterEqual(saved, 1)

        # tat agent, bat lai: khong duoc tra loi /ping lan nua
        self.proc.terminate()
        self.proc.wait(timeout=8)
        for st in (self.proc.stdout, self.proc.stderr, self.proc.stdin):
            try:
                if st:
                    st.close()
            except Exception:
                pass
        self.server.messages.clear()
        self.proc = None

        self.start_agent()
        time.sleep(6)
        self.assertNotIn("pong", self.server.all_text(),
                         f"Agent xu ly lai tin nhan cu: {self.server.all_text()}")

    def test_state_and_events_written_to_disk(self):
        self.start_agent()
        self.assertTrue(self.wait_reply("MÁY VỪA ĐƯỢC BẬT", timeout=AGENT_STARTUP))
        self.server.push("/ping")
        self.assertTrue(self.wait_reply("pong"))
        time.sleep(1.5)
        self.assertTrue((self.data / "state.json").exists(), "state.json khong duoc ghi")
        self.assertTrue((self.data / "events.jsonl").exists(), "events.jsonl khong duoc ghi")
        kinds = [json.loads(x)["kind"] for x in
                 (self.data / "events.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertIn("boot", kinds)
        self.assertIn("command", kinds)


if __name__ == "__main__":
    unittest.main()
