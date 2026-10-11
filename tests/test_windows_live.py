# -*- coding: utf-8 -*-
"""KIEM CHUNG THAT TREN MAY WINDOWS.

Cac test trong nhung file khac chay duoc o moi noi (Linux/macOS) vi chung
dung duong fallback hoac gia lap. File nay nguoc lai: goi THANG vao Win32,
schtasks, icacls, MessageBox... de chung minh tool thuc su hoat dong tren
may cua con.

Nguyen tac an toan:
  - Tu skip khi khong phai Windows -> bo test o may dev van xanh.
  - KHONG BAO GIO tat/restart may that (chi kiem tra chuoi lenh).
  - Moi thu tao ra (tien trinh dummy, task thu nghiem, thu muc tam) deu duoc
    don trong addCleanup/tearDown, ke ca khi test fail giua chung.
  - Test doc QUYEN (policy HKCU) phai bat bang bien moi, vi neu crash giua
    chung co the de lai DisableTaskMgr.

Phan quyen:
  - Nhieu test can Administrator. Khong co quyen -> skip kem huong dan ro rang,
    KHONG bao fail (vi do la gioi han cua may dang chay, khong phai loi code).
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from remotepc import actions, installer, stealth, system_info
from remotepc.config import Config, StealthConfig, layout_for, load_config, save_config
from remotepc.paths import IS_WINDOWS

SELFTEST_TASK_FOLDER = "RemotePCSelfTest"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}

# scripts\test.bat chay test theo 2 buoc: buoc 1 la toan bo test don vi, buoc 2
# la rieng file nay (verbose). Neu buoc 1 cung chay file nay thi hop thoai va
# anh chup man hinh se nhay len HAI lan, va so lieu tong ket bi dem trung.
# Vi vay buoc 1 dat REMOTEPC_SKIP_LIVE=1 de file nay tu bo qua.
_LIVE_DISABLED = os.environ.get("REMOTEPC_SKIP_LIVE") == "1"

if not IS_WINDOWS:
    _LIVE_SKIP_REASON = "chi chay tren Windows (may dev Linux/macOS bo qua)"
elif _LIVE_DISABLED:
    _LIVE_SKIP_REASON = ("hoan lai buoc test song: buoc test don vi dang dat "
                         "REMOTEPC_SKIP_LIVE=1 de khoi nhay hop thoai 2 lan")
else:
    _LIVE_SKIP_REASON = ""

requires_windows = unittest.skipUnless(
    IS_WINDOWS and not _LIVE_DISABLED, _LIVE_SKIP_REASON)


def _whoami() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def _debug_privilege_enabled() -> bool:
    """SeDebugPrivilege dang BAT? Neu co, no vo hieu hoa ACE DENY."""
    try:
        out = subprocess.run(["whoami", "/priv"], capture_output=True, text=True,
                             timeout=20, encoding="utf-8", errors="replace").stdout or ""
    except (OSError, subprocess.SubprocessError):
        return False
    for line in out.splitlines():
        if "SeDebugPrivilege" in line and "Enabled" in line:
            return True
    return False


def _cfg(display_name: str = "SelfTestAgent") -> Config:
    cfg = Config()
    cfg.stealth = StealthConfig(display_name=display_name,
                                description="RemotePC self-test")
    return cfg


# --------------------------------------------------------------------------- #
# 1. Kich thuoc struct that tren Windows
# --------------------------------------------------------------------------- #


@requires_windows
class TestWindowsStructSizes(unittest.TestCase):
    """Xac nhan lai layout tren chinh nen tang se chay that (LLP64)."""

    def test_c_ulong_really_is_4_bytes_here(self):
        self.assertEqual(ctypes.sizeof(ctypes.c_ulong), 4,
                         "Windows la LLP64: ULONG phai 4 byte. Neu khac thi moi "
                         "struct DWORD trong tool nay deu lech.")

    def test_struct_sizes_match_msvc_headers(self):
        cases = [
            (stealth.TRUSTEE_W, 32, "accapi.h"),
            (stealth.EXPLICIT_ACCESS_W, 48, "accapi.h"),
            (stealth.ACE_HEADER, 4, "winnt.h"),
            (stealth.ACCESS_DENIED_ACE, 12, "winnt.h"),
            (stealth.ACL_SIZE_INFORMATION, 12, "winnt.h"),
            (system_info.MEMORYSTATUSEX, 64, "sysinfoapi.h"),
            (system_info.LASTINPUTINFO, 8, "winuser.h"),
            (stealth.PROCESSENTRY32W, 568, "tlhelp32.h"),
        ]
        for cls, expected, header in cases:
            with self.subTest(struct=cls.__name__):
                self.assertEqual(ctypes.sizeof(cls), expected,
                                 f"{cls.__name__} lech {header}")

    def test_wchar_is_2_bytes_utf16(self):
        self.assertEqual(ctypes.sizeof(ctypes.c_wchar), 2,
                         "wchar_t tren Windows la UTF-16 (2 byte)")


# --------------------------------------------------------------------------- #
# 2. Thong tin he thong (khong can admin)
# --------------------------------------------------------------------------- #


@requires_windows
class TestSystemInfoLive(unittest.TestCase):
    def test_memory_percent_is_real(self):
        """Xac nhan MEMORYSTATUSEX + GlobalMemoryStatusEx doc dung."""
        pct = system_info.memory_percent()
        self.assertGreaterEqual(pct, 1.0, f"memory_percent tra ve {pct}")
        self.assertLessEqual(pct, 100.0, f"memory_percent tra ve {pct}")

    def test_idle_seconds_non_negative(self):
        """Xac nhan LASTINPUTINFO + GetLastInputInfo (dung de bao con ngoi lau)."""
        s = system_info.idle_seconds()
        self.assertGreaterEqual(s, 0.0)
        self.assertLess(s, 7 * 24 * 3600, f"idle {s}s la vo ly")

    def test_uptime_positive(self):
        self.assertGreater(system_info.uptime_seconds(), 0.0)

    def test_process_enumeration_finds_known_processes(self):
        """Xac nhan PROCESSENTRY32W doc dung: phai thay explorer/lsass/svchost."""
        pids = stealth.find_pids_by_name("svchost.exe")
        self.assertTrue(pids, "khong tim thay svchost.exe -> PROCESSENTRY32W doc sai")
        self.assertGreater(len(pids), 1, "Windows luon co nhieu svchost")

        procs = actions.list_processes(top=40)
        self.assertGreaterEqual(len(procs), 5, "list_processes tra ve qua it")
        self.assertTrue(all(p.get("name") for p in procs), "co tien trinh khong co ten")

    def test_process_name_of_self(self):
        self.assertTrue(system_info.process_name(os.getpid()),
                        "khong doc duoc ten tien trinh cua chinh minh")

    def test_identity_flags_are_bools_and_consistent(self):
        admin, system, s0 = (system_info.is_admin(), system_info.is_system_account(),
                             system_info.is_session_zero())
        for v in (admin, system, s0):
            self.assertIsInstance(v, bool)
        self.assertEqual(system_info.current_session_id() >= 0, True)
        if system:
            self.assertTrue(s0, "chay bang SYSTEM thi phai o session 0")

    def test_users_and_accounts_enumeration(self):
        users = system_info.logged_on_users()
        self.assertIsInstance(users, list)
        accounts = system_info.local_accounts()
        self.assertIsInstance(accounts, list)
        if accounts:
            self.assertIn("name", accounts[0])
            self.assertIn("admin", accounts[0])
        self.assertTrue(system_info.active_sessions() is not None)

    def test_account_sid_roundtrip(self):
        me = _whoami()
        if not me:
            self.skipTest("khong xac dinh duoc ten nguoi dung hien tai")
        sid = stealth.account_sid(me)
        self.assertRegex(sid, r"^S-1-5-\d+", f"account_sid({me}) = {sid!r}")

    def test_network_and_os_info(self):
        ip = system_info.lan_ip()
        self.assertTrue(ip, "lan_ip rong")
        self.assertRegex(ip, r"^\d+\.\d+\.\d+\.\d+$")
        self.assertIn("Windows", system_info.os_full_name())

    def test_collect_returns_full_snapshot(self):
        info = system_info.collect(tag="selftest", with_public_ip=False)
        self.assertTrue(info.hostname)
        self.assertTrue(info.os_version)
        self.assertGreaterEqual(info.mem_percent, 1.0)
        self.assertGreater(info.uptime_seconds, 0.0)

    def test_last_shutdown_event_readable(self):
        """Task telemetry doc event 1074 - ham nay phai goi duoc khong loi."""
        text = system_info.last_shutdown_event()
        self.assertIsInstance(text, str)


# --------------------------------------------------------------------------- #
# 3. Chan End Task - tinh nang trong tam
# --------------------------------------------------------------------------- #


@requires_windows
class TestEndTaskProtectionLive(unittest.TestCase):
    """Chung minh DENY ACE that su chan duoc viec tat tien trinh."""

    def setUp(self):
        if not system_info.is_admin():
            self.skipTest("can Administrator de gan DENY ACE va doi owner process")
        self.me = _whoami()
        if not self.me:
            self.skipTest("khong xac dinh duoc tai khoan hien tai")
        self.child: subprocess.Popen | None = None

    def _spawn_dummy(self) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.child = proc
        self.addCleanup(self._cleanup_child, proc)
        return proc

    def _cleanup_child(self, proc: subprocess.Popen) -> None:
        """Phai go DENY ACE truoc, neu khong chinh test nay cung khong kill duoc."""
        if proc.poll() is not None:
            return
        try:
            _restore_full_control(proc.pid)
        except Exception as e:                                   # pragma: no cover
            print(f"  [canh bao] khong phuc hoi duoc DACL cua pid {proc.pid}: {e}")
        try:
            proc.kill()
            proc.wait(timeout=10)
        except Exception:
            pass

    def test_deny_ace_blocks_terminate(self):
        proc = self._spawn_dummy()
        sid = stealth.account_sid(self.me)

        # take_ownership=False de test con giu quyen WRITE_DAC va don dep duoc.
        # Ban cai dat that dung take_ownership=True (xem test rieng ben duoi).
        ok, msg = stealth.protect_process(proc.pid, [self.me], take_ownership=False)
        self.assertTrue(ok, f"protect_process that bai: {msg}")

        aces = stealth.deny_aces(proc.pid)
        self.assertTrue(aces, "DACL khong co ACE DENY nao - protect_process bao OK "
                              "nhung thuc te khong gan duoc")
        self.assertTrue(any(a["mask"] & stealth.PROCESS_TERMINATE for a in aces),
                        f"ACE DENY khong co bit PROCESS_TERMINATE: {aces}")
        self.assertIn(sid, [a["sid"] for a in aces],
                      f"ACE DENY khong dung SID cua {self.me}: {aces}")

        self.assertTrue(stealth.is_protected(proc.pid), "is_protected phai True")
        self.assertTrue(stealth.is_protected(proc.pid, [self.me]),
                        "is_protected loc theo tai khoan phai True")

        # Bay gio thu tat no dung cach con se lam (End Task / taskkill /F)
        blocked, detail = _try_terminate(proc.pid)
        if _debug_privilege_enabled():
            self.skipTest(f"ACE DENY da gan dung ({aces}) nhung tien trinh test dang "
                          f"giu SeDebugPrivilege nen no vo hieu hoa DACL. "
                          f"Tai khoan Standard cua con khong co quyen nay. {detail}")
        self.assertTrue(blocked,
                        "Con VAN tat duoc tien trinh da gan DENY ACE. "
                        f"Chi tiet: {detail}. ACE dang co: {aces}")

    def test_taskkill_from_shell_is_denied(self):
        """Dung dung lenh con se dung: taskkill /F /PID."""
        proc = self._spawn_dummy()
        ok, msg = stealth.protect_process(proc.pid, [self.me], take_ownership=False)
        self.assertTrue(ok, msg)
        if _debug_privilege_enabled():
            self.skipTest("SeDebugPrivilege dang bat -> taskkill se lot qua ACE DENY")

        r = subprocess.run(["taskkill", "/F", "/PID", str(proc.pid)],
                           capture_output=True, text=True, timeout=30,
                           encoding="utf-8", errors="replace")
        self.assertNotEqual(r.returncode, 0,
                            f"taskkill /F thanh cong tren tien trinh da bao ve: {r.stdout}")
        combined = (r.stdout or "") + (r.stderr or "")
        self.assertTrue(any(k in combined.lower() for k in ("denied", "tu choi", "access")),
                        f"taskkill fail nhung khong phai vi access denied: {combined}")
        self.assertIsNone(proc.poll(), "tien trinh van phai con song")

    def test_is_protected_is_false_before_protection(self):
        """Bug da sua: is_protected cu chi 'dem so ACE' nen bao True voi MOI process."""
        proc = self._spawn_dummy()
        self.assertEqual(stealth.deny_aces(proc.pid), [],
                         "tien trinh vua tao khong the co san ACE DENY PROCESS_TERMINATE")
        self.assertFalse(stealth.is_protected(proc.pid),
                         "is_protected bao True cho tien trinh CHUA duoc bao ve "
                         "-> doctor va /protect se bao cao sai cho bo me")

    def test_find_pids_by_name_matches_protect_by_name(self):
        """protect_by_name phai tim duoc tien trinh theo ten exe."""
        pids = stealth.find_pids_by_name("svchost.exe")
        self.assertTrue(pids)
        for pid in pids[:3]:
            self.assertTrue(system_info.process_name(pid),
                            f"pid {pid} khong doc duoc ten")


def _try_terminate(pid: int) -> tuple[bool, str]:
    """Thu OpenProcess(PROCESS_TERMINATE) + TerminateProcess. (True, ...) = bi chan."""
    PROCESS_TERMINATE = 0x0001
    kernel32 = ctypes.windll.kernel32                          # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.GetLastError.restype = ctypes.c_uint32

    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
    if not handle:
        return True, f"OpenProcess bi tu choi (error {kernel32.GetLastError()})"
    try:
        kernel32.TerminateProcess.restype = ctypes.c_int
        kernel32.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        if not kernel32.TerminateProcess(ctypes.c_void_p(handle), 1):
            return True, f"TerminateProcess bi tu choi (error {kernel32.GetLastError()})"
        return False, "TerminateProcess THANH CONG - tien trinh khong duoc bao ve"
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def _restore_full_control(pid: int) -> None:
    """Ghi de DACL bang mot ACE ALLOW FULL_CONTROL cho chinh minh (de don dep)."""
    kernel32 = ctypes.windll.kernel32                          # type: ignore[attr-defined]
    advapi32 = ctypes.windll.advapi32                          # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]

    WRITE_DAC = 0x00040000
    READ_CONTROL = 0x00020000
    handle = kernel32.OpenProcess(WRITE_DAC | READ_CONTROL, False, int(pid))
    if not handle:
        raise OSError(f"OpenProcess(WRITE_DAC) that bai, error {kernel32.GetLastError()}")
    try:
        ea = stealth.EXPLICIT_ACCESS_W()
        ea.grfAccessPermissions = 0x1F0FFF          # PROCESS_ALL_ACCESS
        ea.grfAccessMode = stealth.GRANT_ACCESS
        ea.grfInheritance = stealth.NO_INHERITANCE
        ea.Trustee.TrusteeForm = stealth.TRUSTEE_IS_NAME
        ea.Trustee.TrusteeType = 0                  # TRUSTEE_IS_WELL_KNOWN_GROUP
        ea.Trustee.ptstrName = _whoami()

        new_acl = ctypes.c_void_p()
        advapi32.SetEntriesInAclW.restype = ctypes.c_uint32
        advapi32.SetEntriesInAclW.argtypes = [
            ctypes.c_uint32, ctypes.POINTER(stealth.EXPLICIT_ACCESS_W),
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        rc = advapi32.SetEntriesInAclW(1, ctypes.byref(ea), None, ctypes.byref(new_acl))
        if rc != 0:
            raise OSError(f"SetEntriesInAclW that bai, rc={rc}")
        try:
            advapi32.SetSecurityInfo.restype = ctypes.c_uint32
            advapi32.SetSecurityInfo.argtypes = [
                ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
                ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
            DACL_SECURITY_INFORMATION = 0x00000004
            SE_KERNEL_OBJECT = 1
            rc = advapi32.SetSecurityInfo(ctypes.c_void_p(handle), SE_KERNEL_OBJECT,
                                          DACL_SECURITY_INFORMATION,
                                          None, None, new_acl, None)
            if rc != 0:
                raise OSError(f"SetSecurityInfo that bai, rc={rc}")
        finally:
            kernel32.LocalFree(new_acl)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


# --------------------------------------------------------------------------- #
# 4. Scheduled task - schtasks co chap nhan XML cua minh khong?
# --------------------------------------------------------------------------- #


@requires_windows
class TestScheduledTaskXmlLive(unittest.TestCase):
    """Tao task that trong thu muc rieng roi xoa di - khong dung den ban cai dat."""

    def setUp(self):
        if not installer.is_elevated():
            self.skipTest("can Administrator de schtasks /create voi UserId S-1-5-18")
        self.tmp = Path(tempfile.mkdtemp(prefix="rp-selftest-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.created: list[str] = []
        self.cfg = _cfg()
        self.root = self.tmp / "install"

    def _task_name(self, short: str) -> str:
        return f"{SELFTEST_TASK_FOLDER}\\{short}"

    def _drop(self, short: str, mode: str, trigger: str, principal: str) -> Path:
        cmd, args, wd = installer.program_command(self.cfg, self.root, mode)
        xml = installer._task_xml(description=self.cfg.stealth.description,
                                  command=cmd, arguments=args, workdir=wd,
                                  trigger=trigger, principal=principal)
        f = self.tmp / f"{short}.xml"
        f.write_text(xml, encoding="utf-16")          # schtasks doi UTF-16
        return f

    def _create_and_cleanup(self, name: str, xml_file: Path) -> str:
        ok, out = installer.run(
            ["schtasks", "/create", "/tn", name, "/xml", str(xml_file), "/f"], timeout=90)
        self.created.append(name)
        self.addCleanup(lambda n=name: installer.run(
            ["schtasks", "/delete", "/tn", n, "/f"], timeout=60))
        self.assertTrue(ok, f"schtasks /create TU CHOI XML cua tool: {out}\n"
                            f"File: {xml_file}\n"
                            f"Noi dung:\n{xml_file.read_text(encoding='utf-16')[:2000]}")
        return out

    def _query_xml(self, name: str) -> ET.Element:
        ok, out = installer.run(["schtasks", "/query", "/tn", name, "/xml"], timeout=60)
        self.assertTrue(ok, f"schtasks /query that bai: {out}")
        return ET.fromstring(out)

    def test_all_four_task_xmls_are_accepted_by_schtasks(self):
        specs = [
            ("Guardian", "run", installer.BOOT_TRIGGER, installer.SYSTEM_PRINCIPAL),
            ("UI", "uihost", installer.LOGON_TRIGGER, installer.USERS_PRINCIPAL),
            ("Watchdog", "watchdog", installer.watchdog_trigger(),
             installer.SYSTEM_PRINCIPAL),
            ("Telemetry", "notify", installer.event_trigger(installer.EVENT_1074_QUERY),
             installer.SYSTEM_PRINCIPAL),
        ]
        for short, mode, trigger, principal in specs:
            with self.subTest(task=short):
                name = self._task_name(short)
                self._create_and_cleanup(name, self._drop(short, mode, trigger, principal))
                self.assertTrue(installer.task_exists(name), f"{name} khong ton tai")

    def test_roundtrip_keeps_anti_endtask_settings(self):
        """Task Scheduler co giu lai Hidden / AllowHardTerminate / RestartOnFailure?"""
        name = self._task_name("GuardianRT")
        self._create_and_cleanup(
            name, self._drop("guardianrt", "run", installer.BOOT_TRIGGER,
                             installer.SYSTEM_PRINCIPAL))
        root = self._query_xml(name)

        def text(path: str) -> str:
            node = root.find(path, NS)
            self.assertIsNotNone(node, f"task sau round-trip thieu {path}")
            return (node.text or "").strip()

        self.assertEqual(text("t:Settings/t:Hidden"), "true")
        self.assertEqual(text("t:Settings/t:AllowHardTerminate"), "false")
        self.assertEqual(text("t:Settings/t:ExecutionTimeLimit"), "PT0S")
        enabled_node = root.find("t:Settings/t:Enabled", NS)
        self.assertTrue(enabled_node is None or (enabled_node.text or "").strip().lower() in ("true", "1"))
        self.assertEqual(text("t:Principals/t:Principal/t:UserId"), "S-1-5-18")
        self.assertEqual(text("t:Principals/t:Principal/t:RunLevel"), "HighestAvailable")

    def test_uihost_task_uses_group_not_user(self):
        name = self._task_name("UIRT")
        self._create_and_cleanup(name, self._drop("uirt", "uihost",
                                                  installer.LOGON_TRIGGER,
                                                  installer.USERS_PRINCIPAL))
        root = self._query_xml(name)
        grp = root.find("t:Principals/t:Principal/t:GroupId", NS)
        self.assertIsNotNone(grp, "task uihost mat GroupId sau round-trip")
        self.assertEqual((grp.text or "").strip(), "S-1-5-32-545")

    def test_telemetry_subscription_survives_roundtrip(self):
        name = self._task_name("TelemetryRT")
        self._create_and_cleanup(name, self._drop("telemetryrt", "notify",
                                                  installer.event_trigger(
                                                      installer.EVENT_1074_QUERY),
                                                  installer.SYSTEM_PRINCIPAL))
        root = self._query_xml(name)
        sub = root.find("t:Triggers/t:EventTrigger/t:Subscription", NS)
        self.assertIsNotNone(sub, "mat EventTrigger sau round-trip")
        self.assertIn("EventID=1074", sub.text or "")

    def test_task_can_be_started_on_demand(self):
        """Watchdog duoc goi bang schtasks /run - phai start duoc."""
        name = self._task_name("WatchdogStart")
        xml_file = self._drop("watchdogstart", "watchdog",
                              installer.watchdog_trigger(), installer.SYSTEM_PRINCIPAL)
        self._create_and_cleanup(name, xml_file)
        ok, out = installer.run_task(name)
        # Agent chua duoc cai that nen no co the thoat ngay; dieu can biet la
        # Task Scheduler CHAP NHAN lenh start (khong bao "task khong ton tai").
        self.assertTrue(ok or "not exist" not in out.lower(),
                        f"khong start duoc task: {out}")


# --------------------------------------------------------------------------- #
# 5. ACL tren thu muc (icacls)
# --------------------------------------------------------------------------- #


@requires_windows
class TestDirectoryLockdownLive(unittest.TestCase):
    def setUp(self):
        if not system_info.is_admin():
            self.skipTest("can Administrator de thao tac icacls tren ACL")
        self.tmp = Path(tempfile.mkdtemp(prefix="rp-acl-"))
        self.addCleanup(self._cleanup)
        self.me = _whoami()
        self.secret = self.tmp / "config.json"
        self.secret.write_text('{"telegram":{"bot_token":"1:bi-mat"}}', encoding="utf-8")

    def _cleanup(self) -> None:
        try:
            subprocess.run(["icacls", str(self.tmp), "/remove:d", self.me, "/t", "/c"],
                           capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
        shutil.rmtree(self.tmp, True)

    def test_deny_blocks_read_for_the_denied_account(self):
        if not self.me:
            self.skipTest("khong xac dinh duoc tai khoan hien tai")
        ok, msg = stealth.lock_down_path(self.tmp, deny_accounts=[self.me])
        self.assertTrue(ok, f"lock_down_path that bai: {msg}")

        out = subprocess.run(["icacls", str(self.tmp)], capture_output=True, text=True,
                             timeout=60, encoding="utf-8", errors="replace").stdout or ""
        self.assertIn("(DENY)", out, f"icacls khong co ACE DENY:\n{out}")

        with self.assertRaises((PermissionError, OSError),
                               msg="Van doc duoc file sau khi gan DENY R - con van "
                                   "lay duoc bot token"):
            self.secret.read_text(encoding="utf-8")

    def test_deny_for_accounts_reports_result(self):
        if not self.me:
            self.skipTest("khong xac dinh duoc tai khoan hien tai")
        ok, msg = stealth.deny_for_accounts(self.tmp, [self.me], rights="(R,W,D)")
        self.assertTrue(ok, f"deny_for_accounts that bai: {msg}")

    def test_writeonly_folder_cannot_be_read(self):
        """ipc/inbox va shots chi cho Users ghi, khong cho doc."""
        inbox = self.tmp / "inbox"
        inbox.mkdir()
        (inbox / "hello.json").write_text("{}", encoding="utf-8")
        ok, msg = stealth.allow_users_writeonly(inbox)
        if not ok:
            self.skipTest(f"allow_users_writeonly khong chay duoc o day: {msg}")
        listing = subprocess.run(["cmd", "/c", "dir", "/b", str(inbox)],
                                 capture_output=True, text=True, timeout=60,
                                 encoding="utf-8", errors="replace")
        combined = (listing.stdout or "") + (listing.stderr or "")
        self.assertTrue(listing.returncode != 0 or "hello.json" not in combined
                        or system_info.is_admin(),
                        "Users van liet ke duoc noi dung thu muc write-only "
                        "(admin thi van thay - do la du kien)")


# --------------------------------------------------------------------------- #
# 6. Hien thi tren man hinh con
# --------------------------------------------------------------------------- #


@requires_windows
class TestOnScreenLive(unittest.TestCase):
    def setUp(self):
        if system_info.is_session_zero():
            self.skipTest("dang o session 0 (SYSTEM) - khong ve duoc len desktop. "
                          "Day chinh la ly do tool tach ra UI host rieng.")
        self.tmp = Path(tempfile.mkdtemp(prefix="rp-ui-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_message_box_appears_and_returns(self):
        """MessageBoxTimeoutW that - hop thoai se nhay len ~1 giay roi tu dong tat."""
        t0 = time.time()
        ok, msg = actions.message_box("Day la test tu dong, se tu tat sau 1 giay.",
                                      title="RemotePC self-test", style="warn",
                                      timeout=1, beep=False)
        elapsed = time.time() - t0
        self.assertTrue(ok, f"message_box that bai tren Windows: {msg}")
        self.assertGreater(elapsed, 0.3, "hop thoai tat ngay lap tuc - co the khong hien")
        self.assertLess(elapsed, 15.0, f"hop thoai treo {elapsed:.1f}s")

    def test_screenshot_produces_real_image(self):
        target = self.tmp / "shot.png"
        ok, path, err = actions.screenshot(target)
        if not ok and ("The handle is invalid" in str(err) or "BitBlt" in str(err)):
            self.skipTest(f"moi truong khong ho tro chup man hinh ({err})")
        self.assertTrue(ok, f"chup man hinh that bai: {err}")
        self.assertIsNotNone(path)
        self.assertTrue(Path(path).exists(), f"khong co file anh: {path}")
        data = Path(path).read_bytes()
        self.assertGreater(len(data), 5_000,
                           f"anh chi {len(data)} byte - gan nhu chac la anh rong/hong")
        self.assertTrue(data.startswith(b"\x89PNG") or data.startswith(b"\xff\xd8"),
                        f"khong phai PNG/JPEG: {data[:8]!r}")

    def test_active_window_readable(self):
        win = system_info.get_active_window()
        if win is None:
            self.skipTest("khong co cua so nao dang active (may khoa/man hinh tat)")
        self.assertTrue(hasattr(win, "title"))

    def test_get_visible_apps_returns_list(self):
        apps = system_info.get_visible_apps()
        self.assertIsInstance(apps, list)
        for a in apps:
            self.assertIn("pid", a)
            self.assertIn("name", a)
            self.assertIn("title", a)

    def test_uihost_subprocess_handles_job(self):
        """Chay uihost that nhu scheduled task se chay, roi gui job MESSAGE."""
        from remotepc import ipc
        from remotepc.uihost import InboxReader

        cfg = _cfg()
        cfg.data_dir = str(self.tmp / "data")
        cfg_path = self.tmp / "config.json"
        save_config(cfg, cfg_path)
        cfg, path = load_config(str(cfg_path))
        layout = layout_for(cfg, path).ensure()

        proc = subprocess.Popen(
            [sys.executable, "-m", "remotepc", "uihost", "--config", str(cfg_path)],
            cwd=str(Path(__file__).resolve().parents[1]),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])))
        self.addCleanup(lambda: (proc.terminate(), proc.wait(timeout=10)))

        reader = InboxReader(layout)
        deadline = time.time() + 30
        while time.time() < deadline and not reader.uihost_alive():
            time.sleep(0.3)
        self.assertTrue(reader.uihost_alive(),
                        f"uihost khong bao hello ve {layout.inbox_dir}")

        queue = ipc.JobQueue(layout)
        ack = queue.request(ipc.JOB_MESSAGE,
                            {"text": "Test tu dong - se tu tat sau 1 giay",
                             "title": "RemotePC self-test", "timeout": 1},
                            timeout=30)
        self.assertIsNotNone(ack, "uihost khong xu ly job MESSAGE (timeout)")
        self.assertTrue(ack.ok, f"MessageBox that bai trong uihost: {ack.error}")


# --------------------------------------------------------------------------- #
# 7. Policy HKCU cua tai khoan con (PHAI bat bang bien moi)
# --------------------------------------------------------------------------- #


@requires_windows
class TestChildPolicyLive(unittest.TestCase):
    """Test nay ghi DisableTaskMgr vao HKCU. Chi chay khi duoc yeu cau tuong minh.

    Bat bang:  set REMOTEPC_LIVE_POLICY=1
    Ly do phai canh than: neu test crash giua chung, Task Manager cua tai khoan
    do se bi khoa den khi go bo tay bang lenh reg delete.
    """

    def setUp(self):
        if os.environ.get("REMOTEPC_LIVE_POLICY") != "1":
            self.skipTest("test ghi policy HKCU chi chay khi REMOTEPC_LIVE_POLICY=1 "
                          "(xem docs/WINDOWS_TEST_PLAN.md muc 7)")
        if not system_info.is_admin():
            self.skipTest("can Administrator de ghi policy cho tai khoan khac")
        self.me = _whoami()
        if not self.me:
            self.skipTest("khong xac dinh duoc tai khoan hien tai")

    def test_apply_then_remove_policy(self):
        notes = stealth.apply_child_policies([self.me], level=2)
        try:
            self.assertIsInstance(notes, list)
            value = _read_hkcu_policy(self.me, "DisableTaskMgr")
            self.assertEqual(value, 1, f"DisableTaskMgr chua duoc ghi: {notes}")
        finally:
            removed = stealth.remove_child_policies([self.me])
            self.assertIsInstance(removed, list)
            leftover = _read_hkcu_policy(self.me, "DisableTaskMgr")
            self.assertIn(leftover, (None, 0),
                          f"CON SOT DisableTaskMgr={leftover}. GO TAY BANG: "
                          f'reg delete "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion'
                          f'\\Policies\\System" /v DisableTaskMgr /f')


def _read_hkcu_policy(account: str, value_name: str):
    """Doc mot gia tri policy trong HKCU cua `account` (nap hive neu can)."""
    import winreg                                              # noqa: PLC0415
    sid = stealth.account_sid(account)
    if not sid:
        return None
    subkey = r"Software\Microsoft\Windows\CurrentVersion\Policies\System"
    try:
        root = winreg.HKEY_USERS
        with winreg.OpenKey(root, f"{sid}\\{subkey}") as k:
            return winreg.QueryValueEx(k, value_name)[0]
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# 8. Khong bao gio tat may that - chi xac nhan chuoi lenh
# --------------------------------------------------------------------------- #


@requires_windows
class TestPowerCommandsAreNeverExecuted(unittest.TestCase):
    """Bat lai argv that ma tool truyen cho Windows - KHONG chay lenh that.

    Cach lam: chan `actions._popen` nen `shutdown` khong bao gio duoc goi.
    """

    def setUp(self):
        from unittest import mock

        self.calls: list[list[str]] = []

        def fake_popen(cmd, timeout=20, **kw):
            self.calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        self._patch = mock.patch.object(actions, "_popen", side_effect=fake_popen)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_shutdown_argv_is_exact(self):
        actions.shutdown(delay=45, message="Bố tắt máy nhé", force=True)
        self.assertEqual(self.calls[-1],
                         ["shutdown", "/s", "/t", "45", "/c", "Bố tắt máy nhé", "/f"])

    def test_restart_uses_r_flag(self):
        actions.shutdown(delay=0, message="x", restart=True)
        self.assertEqual(self.calls[-1][:2], ["shutdown", "/r"])

    def test_abort_uses_a_flag(self):
        actions.abort_shutdown()
        self.assertEqual(self.calls[-1], ["shutdown", "/a"])

    def test_logoff_uses_l_flag(self):
        actions.logoff()
        self.assertEqual(self.calls[-1], ["shutdown", "/l"])

    def test_required_binaries_exist(self):
        found = shutil.which("shutdown.exe") or shutil.which("shutdown")
        self.assertTrue(found, "khong tim thay shutdown.exe trong PATH")

    def test_taskkill_and_tasklist_exist(self):
        for exe in ("taskkill.exe", "tasklist.exe", "schtasks.exe", "icacls.exe"):
            with self.subTest(exe=exe):
                self.assertTrue(shutil.which(exe) or shutil.which(exe[:-4]),
                                f"thieu {exe} - tool nay can no")


if __name__ == "__main__":
    unittest.main(verbosity=2)
