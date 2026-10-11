"""Logic gio gioi nghiem (curfew) - thuan tuy, de test, khong phu thuoc he dieu hanh.

Mot "window" la 1 khoang cam, vd 23:30 -> 00:00. Khoang co the vat qua nua dem.

Engine sinh ra 3 loai su kien:
  WARN  : sap toi gio (truoc N phut) - moi moc chi bao 1 lan / ngay
  START : bat dau khoang cam        - thuc thi `on_start` (notify/popup/lock)
  END   : khoang cam vua ket thuc    - thuc thi `on_end`  (notify/lock/shutdown)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date, datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #

_DAY_ALIASES = {
    "mon": 0, "t2": 0, "thu2": 0, "thuhai": 0, "monday": 0,
    "tue": 1, "t3": 1, "thu3": 1, "tuesday": 1,
    "wed": 2, "t4": 2, "thu4": 2, "wednesday": 2,
    "thu": 3, "t5": 3, "thu5": 3, "thursday": 3,
    "fri": 4, "t6": 4, "thu6": 4, "friday": 4,
    "sat": 5, "t7": 5, "thu7": 5, "saturday": 5,
    "sun": 6, "cn": 6, "chunhat": 6, "sunday": 6,
}


def parse_hhmm(text: str) -> int:
    """'23:30' -> 1410 (so phut tinh tu 00:00). Chap nhan '2330', '11:30 PM'."""
    s = str(text or "").strip().lower().replace("h", ":").replace("giờ", ":").replace("gio", ":")
    pm = False
    am = False
    if "pm" in s or "ch" in s:
        pm = True
        s = s.replace("pm", "").replace("ch", "")
    if "am" in s or "sa" in s:
        am = True
        s = s.replace("am", "").replace("sa", "")
    s = s.strip()
    if ":" in s:
        hh, _, mm = s.partition(":")
    elif len(s) in (3, 4) and s.isdigit():
        hh, mm = s[:-2], s[-2:]
    elif len(s) <= 2 and s.isdigit():
        hh, mm = s, "0"
    else:
        raise ValueError(f"Khong hieu gio: {text!r} (dung dang HH:MM)")
    hh_i = int(hh or 0)
    mm_i = int(mm or 0)
    if pm and hh_i < 12:
        hh_i += 12
    if am and hh_i == 12:
        hh_i = 0
    if not (0 <= hh_i <= 23) or not (0 <= mm_i <= 59):
        raise ValueError(f"Gio khong hop le: {text!r}")
    return hh_i * 60 + mm_i


def format_min(minutes: int) -> str:
    """1410 -> '23:30' (du 24h)."""
    minutes = int(minutes) % 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_days(items: Iterable[Any]) -> set[int]:
    """[0,1,2] hoac ['t2','t3'] hoac 'mon-fri' -> {0,1,2,3,4}. Trong = ca tuan."""
    out: set[int] = set()
    if items is None:
        return out
    if isinstance(items, str):
        items = [x.strip() for x in items.replace("|", ",").split(",") if x.strip()]
    for it in items:
        if isinstance(it, bool):
            continue
        if isinstance(it, int):
            out.add(it % 7)
            continue
        s = str(it).strip().lower().replace(".", "")
        if "-" in s:
            a, _, b = s.partition("-")
            ai = _DAY_ALIASES.get(a.strip(), _DAY_ALIASES.get(a.strip()[:3]))
            bi = _DAY_ALIASES.get(b.strip(), _DAY_ALIASES.get(b.strip()[:3]))
            if ai is None or bi is None:
                raise ValueError(f"Khong hieu thu: {it!r}")
            if ai <= bi:
                out.update(range(ai, bi + 1))
            else:
                out.update(range(ai, 7))
                out.update(range(0, bi + 1))
            continue
        if s.isdigit():
            out.add(int(s) % 7)
            continue
        key = _DAY_ALIASES.get(s) or _DAY_ALIASES.get(s[:3]) or _DAY_ALIASES.get(s[:2])
        if key is None:
            raise ValueError(f"Khong hieu thu: {it!r}")
        out.add(key)
    return out


# --------------------------------------------------------------------------- #
# Window
# --------------------------------------------------------------------------- #


@dataclass
class Window:
    """1 khoang thoi gian cam."""

    name: str = "Đêm"
    start: int = 23 * 60 + 30      # 23:30
    end: int = 0                   # 00:00
    days: set[int] = field(default_factory=set)   # trong = moi ngay
    warn_before: list[int] = field(default_factory=lambda: [30, 10])
    on_start: str = "notify"       # none | notify | popup | lock
    on_end: str = "notify"         # none | notify | lock | logoff | shutdown
    popup_text: str = ""

    # ---------------- factory ---------------- #

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Window":
        return cls(
            name=str(raw.get("name") or "Đêm"),
            start=parse_hhmm(raw.get("start", "23:30")),
            end=parse_hhmm(raw.get("end", "00:00")),
            days=parse_days(raw.get("days") or []),
            warn_before=sorted({max(0, int(m)) for m in (raw.get("warn_before_minutes")
                                                          or raw.get("warn_before") or [])}),
            on_start=str(raw.get("on_start") or "notify").lower(),
            on_end=str(raw.get("on_end") or "notify").lower(),
            popup_text=str(raw.get("popup_text") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "start": format_min(self.start),
            "end": format_min(self.end),
            "days": sorted(self.days),
            "warn_before_minutes": list(self.warn_before),
            "on_start": self.on_start,
            "on_end": self.on_end,
            "popup_text": self.popup_text,
        }

    @classmethod
    def from_config(cls, cfg_windows: Sequence[Any]) -> list["Window"]:
        out: list[Window] = []
        for w in cfg_windows or []:
            if isinstance(w, Window):
                out.append(w)
            elif isinstance(w, dict):
                out.append(cls.from_dict(w))
        return out

    # ---------------- tinh toan ---------------- #

    @property
    def crosses_midnight(self) -> bool:
        return self.end <= self.start

    @property
    def duration_minutes(self) -> int:
        if self.crosses_midnight:
            return 1440 - self.start + self.end
        return self.end - self.start

    def applies_on(self, d: _date) -> bool:
        """Khoang bat dau vao ngay `d` co ap dung khong (Python: Mon=0..Sun=6)."""
        if not self.days:
            return True
        return d.weekday() in self.days

    def occurrence(self, day: _date) -> tuple[datetime, datetime]:
        """(bat dau, ket thuc) cua lan dien ra khoi dong tu `day`."""
        start_dt = datetime(day.year, day.month, day.day) + timedelta(minutes=self.start)
        end_dt = datetime(day.year, day.month, day.day) + timedelta(minutes=self.end)
        if self.crosses_midnight:
            end_dt += timedelta(days=1)
        if end_dt == start_dt:            # start == end -> hieu la ca ngay? coi nhu 0 phut
            end_dt += timedelta(days=1)
        return start_dt, end_dt

    def occurrences_around(self, now: datetime, span_days: int = 1) -> list[tuple[datetime, datetime]]:
        """Moi lan dien ra trong khoang [now - span, now + span] ngay."""
        out = []
        for delta in range(-span_days, span_days + 1):
            day = (now + timedelta(days=delta)).date()
            if not self.applies_on(day):
                continue
            start_dt, end_dt = self.occurrence(day)
            if end_dt >= now - timedelta(days=span_days) and start_dt <= now + timedelta(days=span_days):
                out.append((start_dt, end_dt))
        return sorted(out)

    def contains(self, now: datetime) -> Optional[tuple[datetime, datetime]]:
        """Neu `now` nam trong khoang -> tra (start_dt, end_dt) cua lan do."""
        for start_dt, end_dt in self.occurrences_around(now, span_days=1):
            if start_dt <= now < end_dt:
                return start_dt, end_dt
        return None

    def next_start(self, now: datetime, max_days: int = 8) -> Optional[datetime]:
        for delta in range(0, max_days):
            day = (now + timedelta(days=delta)).date()
            if not self.applies_on(day):
                continue
            start_dt, _ = self.occurrence(day)
            if start_dt > now:
                return start_dt
        return None

    def __str__(self) -> str:
        days = "" if not self.days else " [" + ",".join(_DAY_VN[d] for d in sorted(self.days)) + "]"
        return f"{self.name}: {format_min(self.start)}-{format_min(self.end)}{days}"


_DAY_VN = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def active_window(windows: Iterable[Window], now: datetime) -> Optional[tuple[Window, datetime, datetime]]:
    """Khoang dang ap dung tai thoi diem `now` (uu tien khoang bat dau gan nhat)."""
    best: Optional[tuple[Window, datetime, datetime]] = None
    for w in windows:
        hit = w.contains(now)
        if not hit:
            continue
        start_dt, end_dt = hit
        if best is None or start_dt > best[1]:
            best = (w, start_dt, end_dt)
    return best


def next_window(windows: Iterable[Window], now: datetime) -> Optional[tuple[Window, datetime]]:
    """Khoang sap toi gan nhat."""
    best: Optional[tuple[Window, datetime]] = None
    for w in windows:
        nxt = w.next_start(now)
        if nxt and (best is None or nxt < best[1]):
            best = (w, nxt)
    return best


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #


@dataclass
class CurfewEvent:
    kind: str                 # "WARN" | "START" | "END"
    window: Window
    occurrence: str           # ISO ngay bat dau cua lan dien ra
    at: datetime
    action: str = "none"      # notify/popup/lock/logoff/shutdown
    minutes_left: int = 0
    popup_text: str = ""
    key: str = ""             # khoa chong gui lap lai

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "window": self.window.name,
            "occurrence": self.occurrence,
            "action": self.action,
            "minutes_left": self.minutes_left,
            "key": self.key,
        }


class CurfewEngine:
    """Sinh su kien curfew dua tren dong ho; state de chong gui lap.

    state la 1 dict (thuong la State.extra["curfew"]) gom:
      {"fired": {key: epoch}, "active": {"window":..., "occ":..., "since": epoch} | None}
    """

    def __init__(self, windows: Sequence[Window], state: dict[str, Any] | None = None):
        self.windows = list(windows)
        self.state: dict[str, Any] = state if state is not None else {}
        self.state.setdefault("fired", {})
        self.state.setdefault("active", None)

    # ---------------- API ---------------- #

    def _fired(self, key: str) -> bool:
        return key in self.state["fired"]

    def _mark(self, key: str, ts: float) -> None:
        self.state["fired"][key] = ts

    def tick(self, now: datetime, ts: float | None = None) -> list[CurfewEvent]:
        """Goi dinh ky (vd 10s/lan). Tra ve danh sach su kien moi, theo thu tu thoi gian."""
        ts = ts if ts is not None else now.timestamp()
        out: list[CurfewEvent] = []

        # 1) Canh bao truoc gio
        for w in self.windows:
            for start_dt, _end in w.occurrences_around(now, span_days=1):
                if start_dt <= now:
                    continue                      # da vao gio roi -> khong canh bao tre
                for m in w.warn_before:
                    warn_dt = start_dt - timedelta(minutes=m)
                    if warn_dt > now:
                        continue
                    key = f"W|{w.name}|{start_dt.date().isoformat()}|{m}"
                    if self._fired(key):
                        continue
                    self._mark(key, ts)
                    out.append(CurfewEvent(
                        kind="WARN", window=w, occurrence=start_dt.date().isoformat(), at=warn_dt,
                        action="popup" if w.on_start in ("popup", "lock") else "notify",
                        minutes_left=m, popup_text=w.popup_text, key=key,
                    ))

        # 2) START / END theo chuyen trang thai
        cur = active_window(self.windows, now)
        act = self.state.get("active")

        if cur:
            w, start_dt, _end = cur
            occ = start_dt.date().isoformat()
            key = f"S|{w.name}|{occ}"
            if not self._fired(key):
                self._mark(key, ts)
                out.append(CurfewEvent(
                    kind="START", window=w, occurrence=occ, at=start_dt, action=w.on_start,
                    popup_text=w.popup_text, key=key,
                ))
            prev = self.state.get("active") or {}
            if prev.get("occ") == occ and prev.get("window") == w.name:
                prev["since"] = float(prev.get("since") or ts)   # giu moc vao gio that
                self.state["active"] = prev
            else:
                self.state["active"] = {"window": w.name, "occ": occ, "since": ts}
        elif act:
            # vua thoat khoi khoang cam -> END
            w = self._find_window(act.get("window", ""))
            if w is not None:
                key = f"E|{w.name}|{act.get('occ','')}|{w.end}"
                if not self._fired(key):
                    self._mark(key, ts)
                    out.append(CurfewEvent(
                        kind="END", window=w, occurrence=str(act.get("occ", "")), at=now,
                        action=w.on_end, popup_text=w.popup_text, key=key,
                    ))
            self.state["active"] = None

        out.sort(key=lambda e: e.at)
        return out

    def is_in_curfew(self, now: datetime) -> Optional[tuple[Window, datetime, datetime]]:
        return active_window(self.windows, now)

    def describe(self, now: datetime) -> str:
        """Chuoi mo ta cho /status."""
        if not self.windows:
            return "Không có khung giờ cấm nào."
        lines = []
        cur = self.is_in_curfew(now)
        if cur:
            w, start_dt, end_dt = cur
            left = int((end_dt - now).total_seconds() // 60)
            lines.append(f"\U0001F534 ĐANG TRONG GIỜ CẤM: {w.name} "
                         f"({format_min(w.start)}-{format_min(w.end)}), còn {left} phút.")
        nxt = next_window(self.windows, now)
        if nxt:
            w, start_dt = nxt
            delta = start_dt - now
            mins = int(delta.total_seconds() // 60)
            when = f"{format_min(w.start)}" + (" hôm nay" if mins < 1440 else f" ({start_dt:%d/%m})")
            lines.append(f"\u23F3 Kế tiếp: {w.name} lúc {when}, sau {mins} phút.")
        for w in self.windows:
            lines.append(f"  - {w}")
        return "\n".join(lines)

    def _find_window(self, name: str) -> Optional[Window]:
        for w in self.windows:
            if w.name == name:
                return w
        return None

    def prune(self, before_ts: float) -> None:
        self.state["fired"] = {k: v for k, v in self.state["fired"].items() if float(v) >= before_ts}
