"""Luu trang thai chay (state.json) + nhat ky su kien (events.jsonl).

state.json : du lieu nho, doc/ghi lien tuc (lan cuoi gui canh bao, boot_id, ...)
events.jsonl: nhat ky dong, moi dong 1 JSON -> de tail, de cat khi qua gioi han.

Tat ca ghi deu atomic (ghi file tam roi os.replace) de khong hong khi mat dien.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from .paths import Layout


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


@dataclass
class State:
    """Trang thai runtime duoc giua cac lan khoi dong."""

    boot_id: str = ""                 # dinh danh lan boot hien tai
    boot_time: float = 0.0            # epoch luc may bat
    last_seen_login: str = ""         # user dang nhap gan nhat
    last_boot_reported: str = ""      # boot_id da bao ve bot
    last_heartbeat: float = 0.0
    curfew_fired: dict = field(default_factory=dict)   # key -> epoch (da gui canh bao)
    pending_shutdown: dict = field(default_factory=dict)
    daily_usage: dict = field(default_factory=dict)    # "YYYY-MM-DD" -> seconds
    first_run: float = field(default_factory=time.time)
    tamper_count: int = 0
    last_command_at: float = 0.0
    extra: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, path: Path) -> "State":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        st = cls(**known)
        st.extra = dict(raw.get("extra") or {})
        return st

    def save(self, path: Path) -> None:
        atomic_write_json(path, asdict(self))

    # ---------------- tien ich curfew ---------------- #

    def mark_fired(self, key: str, ts: float | None = None) -> None:
        self.curfew_fired[key] = ts if ts is not None else time.time()

    def was_fired(self, key: str) -> bool:
        return key in self.curfew_fired

    def prune_fired(self, keep_after: float) -> None:
        """Xoa cac moc canh bao cu (tranh state.json phinh to)."""
        self.curfew_fired = {k: v for k, v in self.curfew_fired.items() if float(v) >= keep_after}


class EventLog:
    """Nhat ky su kien dang JSONL, tu cat khi vuot gioi han."""

    def __init__(self, path: Path, max_events: int = 5000, keep_days: int = 14):
        self.path = path
        self.max_events = max(max_events, 100)
        self.keep_days = max(keep_days, 1)

    def add(self, event_kind: str, **data: Any) -> dict:
        entry = {"ts": round(time.time(), 3), "kind": event_kind}
        entry.update({k: v for k, v in data.items() if v is not None})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            return entry
        self._rotate()
        return entry

    def tail(self, n: int = 20, kinds: list[str] | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out: list[dict] = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if kinds and e.get("kind") not in kinds:
                continue
            out.append(e)
            if len(out) >= n:
                break
        out.reverse()
        return out

    def _rotate(self) -> None:
        try:
            if self.path.stat().st_size < self.max_events * 220:
                return
        except OSError:
            return
        keep = self.tail(self.max_events // 2)
        try:
            with self.path.open("w", encoding="utf-8") as f:
                for e in keep:
                    f.write(json.dumps(e, ensure_ascii=False) + "\n")
        except OSError:
            pass


class Store:
    """Gom State + EventLog + Layout, co khoa ghi de an toan da luong."""

    def __init__(self, layout: Layout, max_events: int = 5000, keep_days: int = 14):
        self.layout = layout
        self.state = State.load(layout.state_file)
        self.events = EventLog(layout.events_file, max_events=max_events, keep_days=keep_days)
        self._dirty = False

    def save(self) -> None:
        self.state.save(self.layout.state_file)
        self._dirty = False

    def touch(self) -> None:
        self._dirty = True

    def flush_if_dirty(self) -> None:
        if self._dirty:
            self.save()

    def log(self, event_kind: str, **data: Any) -> dict:
        self.touch()
        return self.events.add(event_kind, **data)
