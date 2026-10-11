"""Test kênh IPC guardian <-> uihost (hàng đợi bằng file)."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from remotepc import ipc
from remotepc.paths import Layout


class TestIPC(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.layout = Layout(Path(self.tmp.name)).ensure()
        self.queue = ipc.JobQueue(self.layout)

    def tearDown(self):
        self.tmp.cleanup()

    def test_post_and_ack(self):
        handled = []

        def handler(job: ipc.Job):
            handled.append(job)
            return True, {"echo": job.payload.get("text")}, ""

        worker = ipc.JobWorker(self.layout, handler)
        job = self.queue.post(ipc.Job(kind=ipc.JOB_MESSAGE, payload={"text": "Chao con"}))
        self.assertTrue(job.id)

        n = worker.process_once()
        self.assertEqual(n, 1)
        self.assertEqual(len(handled), 1)
        self.assertEqual(handled[0].payload["text"], "Chao con")

        ack = self.queue.wait_ack(job.id, timeout=2)
        self.assertIsNotNone(ack)
        self.assertTrue(ack.ok)
        self.assertEqual(ack.result["echo"], "Chao con")

    def test_job_consumed_once(self):
        worker = ipc.JobWorker(self.layout, lambda j: (True, {}, ""))
        self.queue.post(ipc.Job(kind=ipc.JOB_PING))
        self.assertEqual(worker.process_once(), 1)
        # Sau khi ack, file job bi xoa -> khong xu ly lai
        self.assertEqual(worker.process_once(), 0)

    def test_expired_job_dropped(self):
        job = ipc.Job(kind=ipc.JOB_PING, created=time.time() - 999, timeout=10)
        self.queue.post(job)
        worker = ipc.JobWorker(self.layout, lambda j: (True, {}, ""))
        self.assertEqual(worker.process_once(), 0)
        self.assertEqual(list(self.layout.outbox_dir.glob("*.json")), [])

    def test_wait_ack_timeout(self):
        job = self.queue.post(ipc.Job(kind=ipc.JOB_PING))
        self.assertIsNone(self.queue.wait_ack(job.id, timeout=0.3, poll=0.05))

    def test_handler_exception_becomes_failed_ack(self):
        def boom(job):
            raise RuntimeError("no roi")

        worker = ipc.JobWorker(self.layout, boom)
        job = self.queue.post(ipc.Job(kind=ipc.JOB_MESSAGE))
        worker.process_once()
        ack = self.queue.wait_ack(job.id, timeout=2)
        self.assertIsNotNone(ack)
        self.assertFalse(ack.ok)
        self.assertIn("no roi", ack.error)

    def test_screenshot_bytes_roundtrip(self):
        self.queue.shots.mkdir(parents=True, exist_ok=True)
        (self.queue.shots / "a.png").write_bytes(b"\x89PNG-fake")
        self.assertEqual(self.queue.take_shot("a.png"), b"\x89PNG-fake")
        self.assertIsNone(self.queue.take_shot("a.png"))     # da xoa

    def test_purge_expired(self):
        old = self.layout.outbox_dir / "old.job.json"
        old.write_text("{}", encoding="utf-8")
        os_mtime = time.time() - 7200
        import os
        os.utime(old, (os_mtime, os_mtime))
        self.assertEqual(self.queue.purge_expired(), 1)

    def test_concurrent_worker_thread(self):
        worker = ipc.JobWorker(self.layout, lambda j: (True, {"k": j.kind}, ""))
        stop = threading.Event()

        def loop():
            while not stop.is_set():
                worker.process_once()
                time.sleep(0.02)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        try:
            ack = self.queue.post_and_wait(ipc.Job(kind=ipc.JOB_PING), timeout=3)
            self.assertIsNotNone(ack)
            self.assertTrue(ack.ok)
        finally:
            stop.set()
            t.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
