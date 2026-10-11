"""IPC bang file giua GUARDIAN (chay SYSTEM, session 0) va UIHOST (chay trong
session cua nguoi dung).

Tai sao can?
  Process chay bang SYSTEM khong hien thi duoc hop thoai len man hinh nguoi dung
  (Windows session isolation). Vi vay:
    - GUARDIAN : nhan lenh Telegram, quyet dinh, tat may, canh bao gio
    - UIHOST   : hien hop thoai / man hinh canh bao, chup man hinh, bao cua so dang mo
  Hai ben noi chuyen qua thu muc `ipc/` trong ProgramData:
    outbox/  guardian -> uihost  (Users: chi DOC, khong ghi/xoa duoc)
    inbox/   uihost -> guardian  (Users: ghi duoc)
    shots/   uihost -> guardian  (Users: chi TAO file, khong doc duoc)

Moi job co id duy nhat; uihost tra loi bang file ack; guardian don dep sau khi doc.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from .paths import Layout

log = logging.getLogger("remotepc.ipc")

# Cac loai job ma uihost xu ly duoc
JOB_MESSAGE = "message"        # hien hop thoai / man hinh canh bao
JOB_OVERLAY = "overlay"        # phu toan man hinh
JOB_LOCK = "lock"              # khoa man hinh
JOB_LOGOFF = "logoff"          # dang xuat
JOB_SCREENSHOT = "screenshot"  # chup man hinh -> shots/
JOB_FOREGROUND = "foreground"  # bao cua so dang mo
JOB_PING = "ping"              # kiem tra uihost con song


@dataclass
class Job:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = ""
    created: float = field(default_factory=time.time)
    timeout: float = 30.0          # uihost bo qua job cu hon muc nay
    wait_ack: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "payload": self.payload,
                "created": self.created, "timeout": self.timeout, "wait_ack": self.wait_ack}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Job":
        return cls(
            kind=str(raw.get("kind") or ""),
            payload=dict(raw.get("payload") or {}),
            id=str(raw.get("id") or ""),
            created=float(raw.get("created") or time.time()),
            timeout=float(raw.get("timeout") or 30.0),
            wait_ack=bool(raw.get("wait_ack", True)),
        )

    @property
    def expired(self) -> bool:
        return (time.time() - self.created) > self.timeout


@dataclass
class Ack:
    job_id: str
    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    at: float = field(default_factory=time.time)


def _atomic_write(path: Path, obj: dict) -> bool:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(obj, ensure_ascii=False)
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return True
        except OSError:
            path.write_text(content, encoding="utf-8")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return True
    except OSError as e:
        log.debug("Ghi %s that bai: %s", path, e)
        return False


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------- #
# Phia GUARDIAN
# --------------------------------------------------------------------------- #


class JobQueue:
    """Giao viec cho uihost va doi ket qua."""

    def __init__(self, layout: Layout):
        self.layout = layout
        self.outbox = layout.outbox_dir
        self.inbox = layout.inbox_dir
        self.shots = layout.shots_dir
        for p in (self.outbox, self.inbox, self.shots):
            p.mkdir(parents=True, exist_ok=True)

    def post(self, job: Job) -> Job:
        if not job.id:
            job.id = f"{int(job.created * 1000)}-{uuid.uuid4().hex[:8]}"
        _atomic_write(self.outbox / f"{job.id}.job.json", job.to_dict())
        log.debug("Da gui job %s (%s)", job.id, job.kind)
        return job

    def wait_ack(self, job_id: str, timeout: float = 15.0, poll: float = 0.25) -> Optional[Ack]:
        """Cho uihost tra loi. None = het han (uihost co the dang chet)."""
        ack_path = self.inbox / f"{job_id}.ack.json"
        deadline = time.time() + timeout
        while time.time() < deadline:
            if ack_path.exists():
                raw = _read_json(ack_path)
                if raw:
                    try:
                        ack_path.unlink()
                    except OSError:
                        pass
                    return Ack(job_id=job_id, ok=bool(raw.get("ok")),
                               result=dict(raw.get("result") or {}), error=str(raw.get("error") or ""),
                               at=float(raw.get("at") or time.time()))
            time.sleep(poll)
        return None

    def post_and_wait(self, job: Job, timeout: float = 15.0) -> Optional[Ack]:
        self.post(job)
        if not job.wait_ack:
            return Ack(job_id=job.id, ok=True, result={}, error="fire-and-forget")
        return self.wait_ack(job.id, timeout=timeout)

    def request(self, kind: str, payload: Optional[dict] = None, timeout: float = 15.0,
                wait_ack: bool = True) -> Optional[Ack]:
        return self.post_and_wait(Job(kind=kind, payload=payload or {}, timeout=max(timeout, 5.0),
                                      wait_ack=wait_ack), timeout=timeout)

    def purge_expired(self) -> int:
        """Xoa job/ack cu (may tat dot ngot giua chung)."""
        removed = 0
        now = time.time()
        for d in (self.outbox, self.inbox):
            for f in d.glob("*.json"):
                try:
                    if now - f.stat().st_mtime > 3600:
                        f.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed

    def queue_depth(self) -> int:
        try:
            return len(list(self.outbox.glob("*.job.json")))
        except OSError:
            return 0

    def shot_path(self, name: str) -> Path:
        return self.shots / name

    def take_shot(self, name: str, delete: bool = True) -> Optional[bytes]:
        p = self.shots / name
        try:
            data = p.read_bytes()
        except OSError:
            return None
        if delete:
            try:
                p.unlink()
            except OSError:
                pass
        return data


# --------------------------------------------------------------------------- #
# Phia UIHOST
# --------------------------------------------------------------------------- #


class JobWorker:
    """Vong lap cua uihost: lay job -> thuc thi -> ack."""

    def __init__(self, layout: Layout, handler):
        self.layout = layout
        self.outbox = layout.outbox_dir
        self.inbox = layout.inbox_dir
        self.shots = layout.shots_dir
        self.handler = handler      # callable(Job) -> (ok: bool, result: dict, error: str)
        self._seen: dict[str, float] = {}

    def iter_pending(self) -> Iterator[Job]:
        try:
            files = sorted(self.outbox.glob("*.job.json"))
        except OSError:
            return
        now = time.time()
        for f in files:
            raw = _read_json(f)
            if not raw:
                continue
            job = Job.from_dict(raw)
            if not job.id:
                continue
            if job.expired:
                self._safe_unlink(f)
                continue
            # Chong xu ly lap: 1 job chi chay 1 lan trong doi uihost nay
            if job.id in self._seen:
                continue
            self._seen[job.id] = now
            yield job

    def process_once(self) -> int:
        n = 0
        for job in self.iter_pending():
            n += 1
            ok, result, error = True, {}, ""
            try:
                ok, result, error = self.handler(job) or (True, {}, "")
            except Exception as e:                  # pragma: no cover
                log.exception("handler job %s loi", job.id)
                ok, result, error = False, {}, f"{type(e).__name__}: {e}"
            self.ack(job, ok, result, error)
        self._gc_seen()
        return n

    def ack(self, job: Job, ok: bool, result: Optional[dict] = None, error: str = "") -> None:
        _atomic_write(self.inbox / f"{job.id}.ack.json", {
            "job_id": job.id, "ok": bool(ok), "result": result or {}, "error": error, "at": time.time(),
        })
        # Job da xu ly xong -> xoa de khong chay lai neu uihost khac len
        for f in self.outbox.glob(f"{job.id}.job.json"):
            self._safe_unlink(f)

    def save_shot(self, name: str, blob: bytes) -> Optional[Path]:
        p = self.shots / name
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(blob)
            return p
        except OSError as e:
            log.warning("Khong ghi duoc anh %s: %s", p, e)
            return None

    def _safe_unlink(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            pass

    def _gc_seen(self) -> None:
        cutoff = time.time() - 600
        self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}
