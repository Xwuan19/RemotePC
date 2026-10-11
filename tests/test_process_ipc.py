"""Test DA TIEN TRINH that: guardian (test nay) <-> uihost (process rieng).

Cac test khac chi kiem tra IPC trong cung 1 process. File nay bat process
uihost that bang `sys.executable -m remotepc uihost --config ...` nen no bat
duoc ca loi "quen chuyen --config xuong tien trinh con" (uihost se doc nham
thu muc du lieu mac dinh va khong bao gio thay job).

Tren Linux cac hanh dong hien thi roi vao nhanh dev (in ra log) nen van
tra ve ok=True; dieu can kiem tra la *duong di* cua job, khong phai GUI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from remotepc import ipc
from remotepc.config import layout_for, load_config
from remotepc.paths import repo_root
from remotepc.uihost import InboxReader

STARTUP_WAIT = 12.0       # giay cho uihost khoi dong + viet hello.json
JOB_TIMEOUT = 20.0


class TestCrossProcessIPC(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data = self.base / "data-custom"       # co tinh dat ten KHAC mac dinh
        self.data.mkdir(parents=True)
        self.cfg_path = self.base / "config.json"
        self.cfg_path.write_text(json.dumps({
            "telegram": {"bot_token": "1:fake", "allowed_chat_ids": [111]},
            "machine_tag": "pc-test",
            "data_dir": str(self.data),
            # ten nguy trang khac mac dinh -> neu uihost bo qua --config,
            # no se di tim thu muc SysHealthAgent va test nay fail ngay
            "stealth": {"display_name": "MotTenHoanToanKhac"},
        }, ensure_ascii=False), encoding="utf-8")

        env = dict(os.environ)
        env.pop("REMOTEPC_CONFIG", None)            # bat buoc phai dung --config
        env["PYTHONPATH"] = str(repo_root()) + os.pathsep + env.get("PYTHONPATH", "")
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "remotepc", "uihost", "--config", str(self.cfg_path)],
            cwd=str(repo_root()), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        cfg, path = load_config(str(self.cfg_path))
        self.layout = layout_for(cfg, path).ensure()
        self.queue = ipc.JobQueue(self.layout)
        self.inbox = InboxReader(self.layout)

        # cho uihost viet hello.json
        deadline = time.time() + STARTUP_WAIT
        while time.time() < deadline and not self.inbox.uihost_alive():
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)

    def tearDown(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        for stream in (self.proc.stdout, self.proc.stderr, self.proc.stdin):
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        self.tmp.cleanup()

    def _crash_detail(self) -> str:
        if self.proc.poll() is None:
            return ""
        out = (self.proc.stdout.read() if self.proc.stdout else "") or ""
        err = (self.proc.stderr.read() if self.proc.stderr else "") or ""
        return f"\n--- uihost da thoat (rc={self.proc.returncode}) ---\n{out}\n{err}"

    # ------------------------------------------------------------------ #

    def test_uihost_starts_and_reports_hello(self):
        self.assertIsNone(self.proc.poll(), f"uihost chet ngay:{self._crash_detail()}")
        self.assertTrue(self.inbox.uihost_alive(),
                        f"hello.json khong xuat hien trong {self.data}{self._crash_detail()}")
        self.assertEqual(self.inbox.uihost_pid(), self.proc.pid)

    def test_hello_written_to_configured_data_dir(self):
        """Bug da gap: uihost ghi nham vao thu muc mac dinh."""
        hello = self.layout.inbox_dir / "hello.json"
        self.assertTrue(hello.exists(),
                        f"thieu {hello}{self._crash_detail()}")
        stray = Path.home() / ".local" / "share" / "MotTenHoanToanKhac" / "ipc" / "inbox" / "hello.json"
        self.assertFalse(stray.exists(), "uihost dang ghi nham cho")

    def test_ping_roundtrip(self):
        ack = self.queue.request(ipc.JOB_PING, {}, timeout=JOB_TIMEOUT)
        self.assertIsNotNone(ack, f"khong nhan duoc ack{self._crash_detail()}")
        self.assertTrue(ack.ok, ack.error)
        self.assertEqual(ack.result.get("pid"), self.proc.pid)

    def test_message_roundtrip(self):
        ack = self.queue.request(
            ipc.JOB_MESSAGE,
            {"text": "Con ngu di", "title": "Bo noi", "style": "warn", "timeout": 1},
            timeout=JOB_TIMEOUT)
        self.assertIsNotNone(ack, f"khong nhan duoc ack{self._crash_detail()}")
        self.assertTrue(ack.ok, ack.error)
        # job da xu ly xong thi phai bi xoa khoi outbox
        self.assertEqual(self.queue.queue_depth(), 0)

    def test_foreground_roundtrip(self):
        ack = self.queue.request(ipc.JOB_FOREGROUND, {}, timeout=JOB_TIMEOUT)
        self.assertIsNotNone(ack, f"khong nhan duoc ack{self._crash_detail()}")
        self.assertTrue(ack.ok, ack.error)
        self.assertIn("window", ack.result)

    def test_job_is_processed_exactly_once(self):
        ack1 = self.queue.request(ipc.JOB_PING, {}, timeout=JOB_TIMEOUT)
        self.assertIsNotNone(ack1)
        pid_reported = ack1.result.get("pid")
        for _ in range(3):
            time.sleep(0.3)
            ack = self.queue.request(ipc.JOB_PING, {}, timeout=JOB_TIMEOUT)
            self.assertIsNotNone(ack)
            self.assertEqual(ack.result.get("pid"), pid_reported)

    def test_uihost_periodically_reports_foreground(self):
        """uihost tu ghi foreground.json de guardian biet con dang mo gi."""
        deadline = time.time() + 20.0
        fg = None
        while time.time() < deadline:
            fg = self.inbox.foreground()
            if fg:
                break
            time.sleep(0.5)
        self.assertIsNotNone(fg, f"khong thay foreground.json{self._crash_detail()}")
        self.assertIn("ts", fg)


if __name__ == "__main__":
    unittest.main()
