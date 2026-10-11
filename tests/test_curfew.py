"""Test logic gio gioi nghiem - phan quan trong nhat cua tool."""

from __future__ import annotations

import unittest
from datetime import datetime

from remotepc.curfew import (CurfewEngine, Window, active_window, format_min, next_window,
                             parse_days, parse_hhmm)


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")


class TestParse(unittest.TestCase):
    def test_parse_hhmm(self):
        self.assertEqual(parse_hhmm("23:30"), 23 * 60 + 30)
        self.assertEqual(parse_hhmm("00:00"), 0)
        self.assertEqual(parse_hhmm("0:00"), 0)
        self.assertEqual(parse_hhmm("2330"), 23 * 60 + 30)
        self.assertEqual(parse_hhmm("7"), 7 * 60)
        self.assertEqual(parse_hhmm("11:30 pm"), 23 * 60 + 30)

    def test_parse_hhmm_invalid(self):
        for bad in ("24:00", "12:60", "abc", "-1:00"):
            with self.assertRaises(ValueError, msg=bad):
                parse_hhmm(bad)

    def test_format_min(self):
        self.assertEqual(format_min(0), "00:00")
        self.assertEqual(format_min(1410), "23:30")
        self.assertEqual(format_min(1440), "00:00")

    def test_parse_days(self):
        self.assertEqual(parse_days([]), set())
        self.assertEqual(parse_days([0, 6]), {0, 6})
        self.assertEqual(parse_days(["t2", "cn"]), {0, 6})
        self.assertEqual(parse_days("mon-fri"), {0, 1, 2, 3, 4})
        self.assertEqual(parse_days(["sat", "sun"]), {5, 6})
        # T7 -> CN vat qua cuoi tuan
        self.assertEqual(parse_days("sat-mon"), {5, 6, 0})


class TestWindow(unittest.TestCase):
    def test_crosses_midnight(self):
        w = Window(name="Dem", start=parse_hhmm("23:30"), end=parse_hhmm("00:00"))
        self.assertTrue(w.crosses_midnight)
        self.assertEqual(w.duration_minutes, 30)
        start, end = w.occurrence(datetime(2026, 3, 10).date())
        self.assertEqual(start, dt("2026-03-10 23:30:00"))
        self.assertEqual(end, dt("2026-03-11 00:00:00"))

    def test_contains_around_midnight(self):
        w = Window(start=parse_hhmm("23:30"), end=parse_hhmm("00:30"))
        self.assertTrue(w.contains(dt("2026-03-10 23:45:00")))
        self.assertTrue(w.contains(dt("2026-03-11 00:10:00")))
        self.assertFalse(w.contains(dt("2026-03-11 00:30:00")))   # end = doc (exclusive)
        self.assertFalse(w.contains(dt("2026-03-10 23:29:59")))
        self.assertFalse(w.contains(dt("2026-03-11 12:00:00")))

    def test_daytime_window(self):
        w = Window(name="Hoc", start=parse_hhmm("08:00"), end=parse_hhmm("11:00"))
        self.assertFalse(w.crosses_midnight)
        self.assertTrue(w.contains(dt("2026-03-10 09:00:00")))
        self.assertFalse(w.contains(dt("2026-03-10 11:30:00")))

    def test_days_filter(self):
        w = Window(start=parse_hhmm("23:00"), end=parse_hhmm("05:00"), days={5, 6})  # T7, CN
        # 10/03/2026 la thu Ba
        self.assertIsNone(w.contains(dt("2026-03-10 23:30:00")))
        # 14/03/2026 la thu Bay
        self.assertIsNotNone(w.contains(dt("2026-03-14 23:30:00")))

    def test_next_start(self):
        w = Window(start=parse_hhmm("23:30"), end=parse_hhmm("00:00"))
        self.assertEqual(w.next_start(dt("2026-03-10 10:00:00")), dt("2026-03-10 23:30:00"))
        self.assertEqual(w.next_start(dt("2026-03-10 23:45:00")), dt("2026-03-11 23:30:00"))

    def test_str(self):
        w = Window(name="Đêm", start=parse_hhmm("23:30"), end=parse_hhmm("00:00"), days={0, 6})
        self.assertEqual(str(w), "Đêm: 23:30-00:00 [T2,CN]")


class TestActiveWindow(unittest.TestCase):
    def test_active_and_next(self):
        ws = [Window(name="Trưa", start=parse_hhmm("12:00"), end=parse_hhmm("13:00")),
              Window(name="Đêm", start=parse_hhmm("23:30"), end=parse_hhmm("00:00"))]
        now = dt("2026-03-10 12:30:00")
        hit = active_window(ws, now)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0].name, "Trưa")
        nxt = next_window(ws, now)
        self.assertEqual(nxt[0].name, "Đêm")
        self.assertEqual(nxt[1], dt("2026-03-10 23:30:00"))

    def test_none_active(self):
        ws = [Window(start=parse_hhmm("23:30"), end=parse_hhmm("00:00"))]
        self.assertIsNone(active_window(ws, dt("2026-03-10 09:00:00")))


class TestEngine(unittest.TestCase):
    def make(self, **kw):
        w = Window(name="Đêm", start=parse_hhmm(kw.get("start", "23:30")),
                   end=parse_hhmm(kw.get("end", "00:00")),
                   warn_before=kw.get("warn", [30, 10]),
                   on_start=kw.get("on_start", "popup"), on_end=kw.get("on_end", "notify"))
        state: dict = {}
        return CurfewEngine([w], state), state

    def test_warning_sequence(self):
        eng, state = self.make()
        # 23:00 -> canh bao 30 phut
        evs = eng.tick(dt("2026-03-10 23:00:10"))
        self.assertEqual([e.kind for e in evs], ["WARN"])
        self.assertEqual(evs[0].minutes_left, 30)
        # goi lai ngay -> khong lap lai
        self.assertEqual(eng.tick(dt("2026-03-10 23:00:20")), [])
        # 23:20 -> canh bao 10 phut
        evs = eng.tick(dt("2026-03-10 23:20:05"))
        self.assertEqual([e.kind for e in evs], ["WARN"])
        self.assertEqual(evs[0].minutes_left, 10)
        # 23:30 -> START
        evs = eng.tick(dt("2026-03-10 23:30:05"))
        self.assertEqual([e.kind for e in evs], ["START"])
        self.assertEqual(evs[0].action, "popup")
        # giua khung -> im lang
        self.assertEqual(eng.tick(dt("2026-03-10 23:45:00")), [])
        # 00:00 -> END
        evs = eng.tick(dt("2026-03-11 00:00:10"))
        self.assertEqual([e.kind for e in evs], ["END"])
        self.assertEqual(evs[0].action, "notify")
        self.assertIsNone(state["active"])
        # khong lap lai END
        self.assertEqual(eng.tick(dt("2026-03-11 00:00:20")), [])

    def test_boot_inside_curfew_fires_start_only(self):
        """May bat luc 23:45 (da trong gio cam) -> chi bao START, khong bao WARN tre."""
        eng, _ = self.make()
        evs = eng.tick(dt("2026-03-10 23:45:00"))
        self.assertEqual([e.kind for e in evs], ["START"])

    def test_no_duplicate_next_day(self):
        eng, state = self.make()
        eng.tick(dt("2026-03-10 23:30:00"))
        eng.tick(dt("2026-03-11 00:00:30"))
        # Hom sau lai chay tiep
        evs = eng.tick(dt("2026-03-11 23:00:00"))
        self.assertEqual([e.kind for e in evs], ["WARN"])
        evs = eng.tick(dt("2026-03-11 23:30:00"))
        self.assertEqual([e.kind for e in evs], ["START"])

    def test_skipped_days(self):
        eng, _ = self.make()
        w = eng.windows[0]
        w.days = {5}                      # chi thu Bay
        self.assertEqual(eng.tick(dt("2026-03-10 23:45:00")), [])    # thu Ba
        evs = eng.tick(dt("2026-03-14 23:45:00"))                    # thu Bay
        self.assertEqual([e.kind for e in evs], ["START"])

    def test_state_persistence(self):
        """State la dict -> co the cat vao state.json va phuc hoi khong gui lai."""
        eng, state = self.make()
        eng.tick(dt("2026-03-10 23:30:00"))
        eng2 = CurfewEngine([eng.windows[0]], state)      # "khoi dong lai"
        self.assertEqual(eng2.tick(dt("2026-03-10 23:35:00")), [])

    def test_describe(self):
        eng, _ = self.make()
        text = eng.describe(dt("2026-03-10 23:45:00"))
        self.assertIn("ĐANG TRONG GIỜ CẤM", text)
        self.assertIn("23:30", text)


if __name__ == "__main__":
    unittest.main()
