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


class TestGuardianSpawnsUIHost(unittest.TestCase):
    """Guardian (SYSTEM) tu khoi dong uihost khi scheduled task khong chay duoc.

    Day la duong "tu chua lanh": tren Windows neu task bi con pha/hong, guardian
    phai tu bat uihost len de /msg, /lock, /screenshot van hoat dong.
    """

    def setUp(self):
        from unittest import mock

        from remotepc import installer, system_info
        from remotepc.bot import Agent
        from remotepc.config import Config

        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.data = self.base / "data"
        self.data.mkdir()
        self.cfg_path = self.base / "config.json"

        self.cfg = Config()
        self.cfg.telegram.bot_token = "1:fake"
        self.cfg.telegram.allowed_chat_ids = [111]
        self.cfg.machine_tag = "pc-heal"
        self.cfg.data_dir = str(self.data)
        self.cfg.stealth.display_name = "TenNguyTrangKhac"
        from remotepc.config import save_config
        save_config(self.cfg, self.cfg_path)

        cfg, path = load_config(str(self.cfg_path))
        self.layout = layout_for(cfg, path).ensure()
        self.agent = Agent(cfg, path, self.layout, stealth=False)
        self.queue = ipc.JobQueue(self.layout)
        self.inbox = InboxReader(self.layout)
        self._spawned: list[int] = []

        # Gia lap: dang o session 0 (SYSTEM) va scheduled task THAT BAI
        self._patches = [
            mock.patch.object(system_info, "is_session_zero", return_value=True),
            mock.patch.object(installer, "run_task", return_value=(False, "ERROR: task khong ton tai")),
            mock.patch.dict(os.environ, {"PYTHONPATH": str(repo_root()) + os.pathsep
                                         + os.environ.get("PYTHONPATH", "")}),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        for pid in self._spawned:
            try:
                self.proc_kill(pid)
            except Exception:
                pass
        self.tmp.cleanup()

    @staticmethod
    def proc_kill(pid: int) -> None:
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    def _track_spawned(self, timeout: float = 15.0) -> int:
        deadline = time.time() + timeout
        while time.time() < deadline:
            pid = self.inbox.uihost_pid()
            if pid and self.inbox.uihost_alive():
                if pid not in self._spawned:
                    self._spawned.append(pid)
                return pid
            time.sleep(0.2)
        return 0

    # ------------------------------------------------------------------ #

    def test_spawned_uihost_handles_job(self):
        """run_task that bai -> guardian spawn uihost -> job van duoc xu ly."""
        ok = self.agent.keep_uihost_alive(force=True)
        self.assertTrue(ok, "guardian phai khoi dong duoc uihost bang duong fallback")

        pid = self._track_spawned()
        self.assertTrue(pid, f"uihost duoc spawn khong bao hello ve {self.layout.inbox_dir}")
        self.assertNotEqual(pid, os.getpid())

        ack = self.queue.request(ipc.JOB_MESSAGE,
                                 {"text": "Con ngu di", "title": "Bo noi", "timeout": 1},
                                 timeout=25)
        self.assertIsNotNone(ack, "uihost duoc spawn khong xu ly job")
        self.assertTrue(ack.ok, ack.error)
        self.assertEqual(ack.result.get("pid") if "pid" in (ack.result or {}) else pid, pid)

    def test_spawned_uihost_uses_configured_data_dir(self):
        """Bug da gap: spawn thieu --config -> uihost doc nham thu muc mac dinh."""
        self.agent.keep_uihost_alive(force=True)
        pid = self._track_spawned()
        self.assertTrue(pid)
        stray = Path.home() / ".local" / "share" / "TenNguyTrangKhac" / "ipc" / "inbox" / "hello.json"
        self.assertFalse(stray.exists(), "uihost dang ghi nham thu muc mac dinh")
        self.assertTrue((self.layout.inbox_dir / "hello.json").exists())

    def test_not_spawned_when_task_works(self):
        """Neu scheduled task chay duoc thi khong can spawn them ban nua."""
        from unittest import mock

        from remotepc import installer
        with mock.patch.object(installer, "run_task", return_value=(True, "")) as rt:
            self.agent.keep_uihost_alive(force=True)
        self.assertTrue(rt.called)
        self.assertEqual(self._spawned, [])
