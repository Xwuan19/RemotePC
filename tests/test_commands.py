"""Test bo phan tich cau lenh Telegram."""

from __future__ import annotations

import unittest

from remotepc.commands import (format_duration, normalize_command, parse_duration, parse_message,
                               targets_match, tokenize)


class TestParse(unittest.TestCase):
    def test_simple_message(self):
        c = parse_message("/msg Con ngu di, muon roi day!")
        self.assertTrue(c.ok)
        self.assertEqual(c.command, "msg")
        self.assertEqual(c.text, "Con ngu di, muon roi day!")

    def test_bot_suffix(self):
        c = parse_message("/status@RemotePC_bot")
        self.assertEqual(c.command, "status")

    def test_flags_and_options(self):
        c = parse_message('/msg --full --title "Nhac nho" --timeout 30 Chao con')
        self.assertEqual(c.command, "msg")
        self.assertTrue(c.has_flag("full"))
        self.assertEqual(c.options.get("title"), "Nhac nho")
        self.assertEqual(c.options.get("timeout"), "30")
        self.assertEqual(c.text, "Chao con")

    def test_option_with_equals(self):
        c = parse_message('/msg --title="Bo noi" Con an com di')
        self.assertEqual(c.options.get("title"), "Bo noi")
        self.assertEqual(c.text, "Con an com di")

    def test_shutdown_with_delay_and_message(self):
        c = parse_message('/shutdown 120 --msg "May se tat"')
        self.assertEqual(c.command, "shutdown")
        self.assertEqual(c.first_int(), 120)
        self.assertEqual(c.options.get("msg"), "May se tat")

    def test_aliases(self):
        for raw, want in (("/tat 60", "shutdown"), ("/nhan Chao", "msg"),
                          ("/tb Chao", "msg"), ("/anh", "screenshot"),
                          ("/khoa", "lock"), ("/huy", "abort"),
                          ("/khoi dong lai", "restart"), ("/tt", "status")):
            self.assertEqual(parse_message(raw).command, want, raw)

    def test_targets(self):
        c = parse_message("/msg @pc2 Chao", my_tag="pc1")
        self.assertEqual(c.targets, ["pc2"])
        self.assertFalse(c.ok)
        self.assertTrue(c.other_machine)

        c = parse_message("/msg @pc1 Chao", my_tag="pc1")
        self.assertTrue(c.ok)
        self.assertEqual(c.text, "Chao")

        c = parse_message("/msg @all Chao", my_tag="pc1")
        self.assertTrue(c.ok)

    def test_require_tag(self):
        c = parse_message("/status", my_tag="pc1", require_tag=True)
        self.assertFalse(c.ok)
        self.assertTrue(c.other_machine)
        c = parse_message("/status @pc1", my_tag="pc1", require_tag=True)
        self.assertTrue(c.ok)

    def test_not_a_command(self):
        c = parse_message("chao bo")
        self.assertFalse(c.ok)
        self.assertEqual(c.command, "text")
        self.assertIn("/", c.error)

    def test_empty(self):
        self.assertFalse(parse_message("   ").ok)

    def test_unknown_command_still_parses(self):
        c = parse_message("/foobar 1 2")
        self.assertTrue(c.ok)
        self.assertEqual(c.command, "foobar")
        self.assertEqual(c.args, ["1", "2"])

    def test_tokenize_unbalanced_quote(self):
        # nhay le -> khong duoc nem loi
        self.assertEqual(tokenize('/msg "chua dong'), ["/msg", '"chua', "dong"])


class TestNormalize(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_command("/MSG@Bot"), "msg")
        self.assertEqual(normalize_command("/tat"), "shutdown")
        self.assertEqual(normalize_command("shutdown"), "shutdown")


class TestTargets(unittest.TestCase):
    def test_targets_match(self):
        self.assertTrue(targets_match([], "pc1", False))
        self.assertFalse(targets_match([], "pc1", True))
        self.assertTrue(targets_match(["all"], "pc1", True))
        self.assertTrue(targets_match(["PC1"], "pc1", True))
        self.assertFalse(targets_match(["pc2"], "pc1", True))


class TestDuration(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(parse_duration("90"), 90)
        self.assertEqual(parse_duration("1m30"), 90)
        self.assertEqual(parse_duration("2h"), 7200)
        self.assertEqual(parse_duration("1.5h"), 5400)
        self.assertEqual(parse_duration("45s"), 45)
        self.assertIsNone(parse_duration("abc"))
        self.assertIsNone(parse_duration(""))

    def test_format_duration(self):
        self.assertEqual(format_duration(0), "0 giay")
        self.assertEqual(format_duration(125), "2 phut 5 giay")
        self.assertEqual(format_duration(3600), "1 gio")
        self.assertEqual(format_duration(90061), "1 ngay 1 gio 1 phut 1 giay")


if __name__ == "__main__":
    unittest.main()
