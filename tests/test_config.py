"""Test doc/ghi cau hinh, PIN va viec gan gia tri theo duong dan."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from remotepc.config import (Config, load_config, make_machine_id, save_config, set_by_path)


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        cfg, path = load_config(self.dir / "config.json")
        self.assertEqual(cfg.machine_tag, "pc1")
        self.assertTrue(cfg.curfew.enabled)
        self.assertEqual(len(cfg.curfew.windows), 1)
        self.assertEqual(cfg.curfew.windows[0].start, "23:30")
        self.assertEqual(cfg.curfew.windows[0].end, "00:00")
        self.assertTrue(cfg.machine_id)          # tu sinh
        self.assertFalse(cfg.is_allowed_chat(1))

    def test_merge_keeps_defaults_for_missing_keys(self):
        p = self.dir / "config.json"
        p.write_text(json.dumps({"telegram": {"bot_token": "123:abc"},
                                 "curfew": {"enabled": False}}), encoding="utf-8")
        cfg, _ = load_config(p)
        self.assertEqual(cfg.telegram.bot_token, "123:abc")
        self.assertEqual(cfg.telegram.poll_timeout, 45)      # mac dinh van con
        self.assertFalse(cfg.curfew.enabled)
        self.assertEqual(cfg.curfew.windows[0].start, "23:30")  # windows mac dinh van con

    def test_roundtrip(self):
        p = self.dir / "config.json"
        cfg, _ = load_config(p)
        cfg.telegram.bot_token = "999:xyz"
        cfg.telegram.allowed_chat_ids = [111, 222]
        cfg.machine_tag = "may-con"
        cfg.curfew.windows[0].start = "22:00"
        cfg.set_pin("1234")
        save_config(cfg, p)

        cfg2, _ = load_config(p)
        self.assertEqual(cfg2.telegram.bot_token, "999:xyz")
        self.assertEqual(cfg2.telegram.allowed_chat_ids, [111, 222])
        self.assertEqual(cfg2.machine_tag, "may-con")
        self.assertEqual(cfg2.curfew.windows[0].start, "22:00")
        self.assertTrue(cfg2.check_pin("1234"))
        self.assertFalse(cfg2.check_pin("0000"))
        self.assertTrue(cfg2.is_allowed_chat(222))
        self.assertFalse(cfg2.is_allowed_chat(333))

    def test_proxy_option(self):
        """Telegram bi chan o VN -> can cau hinh duoc proxy."""
        cfg = Config()
        self.assertEqual(cfg.telegram.proxy, "")
        p = self.dir / "config.json"
        cfg.telegram.proxy = "http://127.0.0.1:1080"
        save_config(cfg, p)
        cfg2, _ = load_config(p)
        self.assertEqual(cfg2.telegram.proxy, "http://127.0.0.1:1080")

    def test_unknown_keys_ignored(self):
        """Nguoi dung them chu thich `_doc` / key la -> khong duoc crash."""
        p = self.dir / "config.json"
        p.write_text(json.dumps({
            "_doc": "chu thich cua toi",
            "telegram": {"bot_token": "1:x", "_note": "abc"},
            "stealth": {"level": 2, "khoa_khong_ton_tai": True},
            "curfew": {"windows": [{"name": "Đêm", "start": "22:00", "end": "23:00", "_x": 1}]},
        }), encoding="utf-8")
        cfg, _ = load_config(p)
        self.assertEqual(cfg.telegram.bot_token, "1:x")
        self.assertEqual(cfg.stealth.level, 2)
        self.assertEqual(cfg.curfew.windows[0].start, "22:00")
        self.assertEqual(cfg.monitoring.window_poll_seconds, 20)   # mac dinh van con

    def test_broken_config_falls_back(self):
        p = self.dir / "config.json"
        p.write_text("{ khong phai json", encoding="utf-8")
        cfg, _ = load_config(p)
        self.assertEqual(cfg.machine_tag, "pc1")
        self.assertTrue((self.dir / "config.broken.json").exists())

    def test_pin(self):
        cfg = Config()
        self.assertFalse(cfg.has_pin())
        self.assertFalse(cfg.check_pin("1234"))
        cfg.set_pin("1234")
        self.assertTrue(cfg.has_pin())
        self.assertTrue(cfg.check_pin("1234"))
        self.assertTrue(cfg.check_pin(" 1234 "))
        self.assertFalse(cfg.check_pin("12345"))
        cfg.set_pin("")
        self.assertFalse(cfg.has_pin())

    def test_set_by_path(self):
        cfg = Config()
        self.assertIn("curfew.enabled", set_by_path(cfg, "curfew.enabled", "false"))
        self.assertFalse(cfg.curfew.enabled)
        set_by_path(cfg, "curfew.enabled", "on")
        self.assertTrue(cfg.curfew.enabled)
        set_by_path(cfg, "monitoring.window_poll_seconds", "7")
        self.assertEqual(cfg.monitoring.window_poll_seconds, 7)
        set_by_path(cfg, "machine_tag", "pc9")
        self.assertEqual(cfg.machine_tag, "pc9")
        set_by_path(cfg, "curfew.auto_shutdown_after_minutes", "15")
        self.assertEqual(cfg.curfew.auto_shutdown_after_minutes, 15)
        with self.assertRaises(KeyError):
            set_by_path(cfg, "curfew.khong_ton_tai", "1")

    def test_set_by_path_list(self):
        cfg = Config()
        set_by_path(cfg, "telegram.allowed_chat_ids", "1,2,3")
        self.assertEqual(cfg.telegram.allowed_chat_ids, ["1", "2", "3"])

    def test_machine_id(self):
        mid = make_machine_id()
        self.assertEqual(len(mid), 16)
        self.assertTrue(all(c in "0123456789abcdef" for c in mid))


if __name__ == "__main__":
    unittest.main()
