"""Diem vao dong lenh: `python -m remotepc <lenh>` (hoac file .exe sau khi dong goi).

Chay lan dau:
    python -m remotepc init          # hoi bot token, chat_id, PIN, gio gioi nghiem
    python -m remotepc run           # chay thu (bam Ctrl+C de dung)
    python -m remotepc install       # cai that: SYSTEM + tu bao ve + tu khoi dong
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import __version__, messages as M
from .config import (Config, layout_for, load_config, make_machine_id, save_config,
                     set_by_path)
from .paths import IS_WINDOWS, Layout, is_frozen, repo_root

log = logging.getLogger("remotepc.cli")


def _utf8_stdout() -> None:
    """Dam bao in tieng Viet co dau khong loi tren cmd.exe."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    if IS_WINDOWS:
        try:
            subprocess.run(["chcp", "65001"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            pass


def console(text: str) -> str:
    """Bo ky tu dinh dang Markdown khi in ra terminal."""
    return text.replace("*", "").replace("`", "")


# --------------------------------------------------------------------------- #
# init (wizard)
# --------------------------------------------------------------------------- #


def _ask(prompt: str, default: str = "", hidden: bool = False, required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        try:
            val = (input(f"{prompt}{suffix}: ") if not hidden else __import__("getpass").getpass(f"{prompt}{suffix}: ")).strip()
        except EOFError:
            val = ""
        if not val:
            val = default
        if val or not required:
            return val
        print("  -> Không được để trống.")


def cmd_init(args: argparse.Namespace) -> int:
    print("=" * 62)
    print(" REMOTE PC — THIẾT LẬP BAN ĐẦU")
    print("=" * 62)
    cfg, cfg_path = load_config(args.config)
    print(f"\nFile cấu hình sẽ lưu tại: {cfg_path}\n")

    if getattr(args, "defaults", False):
        token = (args.token or cfg.telegram.bot_token or "").strip()
        if token:
            cfg.telegram.bot_token = token
        if getattr(args, "chat_id", None):
            try:
                cfg.telegram.allowed_chat_ids = [int(x.strip()) for x in str(args.chat_id).split(",") if x.strip()]
            except ValueError:
                pass
        pin = args.pin or ("1234" if not cfg.has_pin() else None)
        if pin:
            cfg.set_pin(pin)
        if getattr(args, "machine_tag", None):
            cfg.machine_tag = args.machine_tag
        if getattr(args, "level", None) is not None:
            cfg.stealth.level = int(args.level)
        if not cfg.machine_id:
            cfg.machine_id = make_machine_id()
        cfg.data_dir = str(cfg.data_layout_dir())
        save_config(cfg, cfg_path)
        print(" ✅ Đã lưu cấu hình (chế độ tự động).")
        print(f"    - Bot token: {'Đã cấu hình' if cfg.telegram.bot_token else 'Chưa có'}")
        print("    - PIN: Đã đặt")
        print(f"    - Chat Admin: {cfg.telegram.allowed_chat_ids or 'Chưa có (ghép nối sau bằng /pair <PIN>)'}")
        print(f"    - Tag máy: {cfg.machine_tag}")
        print("=" * 62)
        return 0

    print("1) Tạo bot: nhắn @BotFather trên Telegram → /newbot → copy token.")
    token = args.token or _ask("Bot token", cfg.telegram.bot_token, hidden=True)
    if token:
        cfg.telegram.bot_token = token.strip()

    if cfg.telegram.bot_token:
        from .telegram_client import TelegramClient, TelegramError
        proxy = _ask("Proxy/VPN nếu Telegram bị chặn (bỏ trống nếu không dùng)",
                     cfg.telegram.proxy or "")
        cfg.telegram.proxy = proxy.strip()
        try:
            me = TelegramClient(cfg.telegram.bot_token,
                                proxy=cfg.telegram.proxy or None).me(force=True)
            print(f"   ✅ Bot hoạt động: @{me.get('username')} ({me.get('first_name')})")
        except TelegramError as e:
            if e.code in (401, 404):
                print(f"   ❌ Token sai hoặc bot đã bị xoá: {e}")
                return 2
            # Khong ket noi duoc (mat mang / Telegram bi chan) -> van luu, sua sau
            print(f"   ⚠️ Chưa kiểm tra được token: {e}")
            print("      Vẫn lưu cấu hình. Nếu bạn ở nơi chặn Telegram, hãy cài VPN")
            print("      hoặc đặt `telegram.proxy` trong config.json rồi chạy `/reload`.")

    print("\n2) Cấp quyền chat: bố/mẹ hãy nhắn bất kỳ tin nào cho bot, rồi Enter.")
    if args.chat_id:
        cfg.telegram.allowed_chat_ids = [int(args.chat_id)]
    else:
        input("   (bấm Enter sau khi đã nhắn tin cho bot) ")
        ids = _discover_chats(cfg.telegram.bot_token, wait=3)
        if ids:
            print("   Tìm thấy các chat vừa nhắn cho bot:")
            for cid, name in ids:
                print(f"     - {cid}  ({name})")
            pick = _ask("   Chọn chat_id của bố/mẹ", str(ids[0][0]))
            try:
                cfg.telegram.allowed_chat_ids = [int(pick)]
            except ValueError:
                print("   ⚠️ Không hợp lệ, sẽ ghép nối sau bằng /pair")
        else:
            print("   ⚠️ Không thấy tin nhắn nào. Có thể ghép nối sau bằng lệnh `/pair <PIN>` gửi cho bot.")

    print("\n3) Mã PIN bảo vệ lệnh nguy hiểm (tắt máy, đổi giờ...).")
    pin = args.pin or _ask("PIN (tối thiểu 4 ký tự)", hidden=True, required=False)
    if pin:
        if len(pin) < 4:
            print("   ⚠️ PIN quá ngắn, bỏ qua.")
        else:
            cfg.set_pin(pin)
            print("   ✅ Đã lưu PIN (dạng băm).")

    print("\n4) Giờ giới nghiêm.")
    cfg.curfew.enabled = True
    w = cfg.curfew.windows[0] if cfg.curfew.windows else None
    if w is None:
        from .config import CurfewWindow
        w = CurfewWindow()
        cfg.curfew.windows = [w]
    start = _ask("Bắt đầu cấm", w.start or "23:30")
    end = _ask("Kết thúc", w.end or "00:00")
    warn = _ask("Báo trước (phút, phân cách bằng dấu phẩy)",
                ",".join(str(x) for x in w.warn_before) or "30,10")
    on_start = _ask("Khi vào giờ thì làm gì? (none/notify/popup/lock)", w.on_start or "popup")
    on_end = _ask("Khi hết giờ thì làm gì? (none/notify/lock/logoff/shutdown)", w.on_end or "notify")
    auto = _ask("Tự tắt máy nếu con vẫn dùng sau bao nhiêu phút trong giờ cấm? (0 = không)",
                str(cfg.curfew.auto_shutdown_after_minutes or 0))
    try:
        from .curfew import parse_hhmm
        w.start, w.end = parse_hhmm(start), parse_hhmm(end)
    except ValueError as e:
        print(f"   ❌ {e}")
        return 2
    w.warn_before = sorted({int(x) for x in warn.replace(";", ",").split(",") if x.strip().isdigit()})
    w.on_start = on_start.lower()
    w.on_end = on_end.lower()
    cfg.curfew.auto_shutdown_after_minutes = max(0, int(auto or 0))
    # config luu dang chuoi HH:MM
    from .curfew import format_min
    w_dict_start, w_dict_end = format_min(w.start), format_min(w.end)
    w.start, w.end = w_dict_start, w_dict_end          # type: ignore[assignment]
    print(f"   ✅ Khung giờ: {w_dict_start} → {w_dict_end}")

    print("\n5) Tên máy (khi 1 bot quản lý nhiều máy, nhắn `/msg @ten ...`).")
    cfg.machine_tag = _ask("Tag máy này", cfg.machine_tag or "pc1")

    print("\n6) Tên nguỵ trang cho agent (hiện trong Task Manager).")
    cfg.stealth.display_name = _ask("Tên tiến trình", cfg.stealth.display_name or "SysHealthAgent")
    cfg.stealth.uihost_name = cfg.stealth.display_name.replace("Agent", "UI") or "SysHealthUI"
    level = _ask("Mức bảo vệ: 0=tắt, 1=cơ bản (SYSTEM+tự khởi động), 2=cao (chặn cả Task Manager của con)",
                 str(cfg.stealth.level if cfg.stealth.level else 2))
    try:
        cfg.stealth.level = max(0, min(2, int(level)))
    except ValueError:
        cfg.stealth.level = 2

    if not cfg.machine_id:
        cfg.machine_id = make_machine_id()

    cfg.data_dir = str(cfg.data_layout_dir())
    save_config(cfg, cfg_path)
    print("\n" + "=" * 62)
    print(f" ✅ Đã lưu cấu hình: {cfg_path}")
    print(" Bước tiếp theo:")
    print("   python -m remotepc run        # chạy thử, xem bot có nhắn về không")
    print("   python -m remotepc install    # cài chính thức (cần Administrator)")
    print("=" * 62)
    return 0


def _discover_chats(token: str, wait: int = 3) -> list[tuple[int, str]]:
    from .telegram_client import TelegramClient, TelegramError
    try:
        tg = TelegramClient(token, timeout=20)
        updates = tg.get_updates(offset=0, timeout=wait)
    except TelegramError as e:
        print(f"   (không đọc được tin nhắn: {e})")
        return []
    seen: dict[int, str] = {}
    for u in updates:
        seen[u.chat_id] = u.from_name or "?"
    return list(seen.items())


# --------------------------------------------------------------------------- #
# run / uihost / watchdog
# --------------------------------------------------------------------------- #


def cmd_run(args: argparse.Namespace) -> int:
    from .bot import run_agent
    level = logging.DEBUG if args.debug else logging.INFO
    stealth = not args.no_stealth
    return run_agent(config_path=args.config, stealth=stealth, once=args.once, log_level=level)


def _child_argv(args: argparse.Namespace, *flags: str) -> list[str]:
    """Dựng argv cho tiến trình con, NHỚ chuyển tiếp --config.

    Thiếu bước này thì uihost/watchdog sẽ rơi về thư mục dữ liệu mặc định và
    nghe nhầm chỗ (guardian ghi job vào một nơi, uihost đọc ở nơi khác).
    """
    argv: list[str] = []
    cfg = getattr(args, "config", None)
    if cfg:
        argv += ["--config", str(cfg)]
    for name in flags:
        if getattr(args, name.lstrip("-").replace("-", "_"), False):
            argv.append(name)
    return argv


def cmd_uihost(args: argparse.Namespace) -> int:
    from .uihost import main as ui_main
    return ui_main(_child_argv(args, "--once", "--debug"))


def cmd_watchdog(args: argparse.Namespace) -> int:
    from .watchdog import main as wd_main
    return wd_main(_child_argv(args, "--loop", "--verbose"))


def cmd_notify_shutdown(args: argparse.Namespace) -> int:
    """Task telemetry goi lenh nay khi Windows ghi su kien 1074 (may sap tat)."""
    cfg, cfg_path = load_config(args.config)
    if not cfg.notify.on_shutdown or not cfg.telegram.bot_token or not cfg.telegram.allowed_chat_ids:
        return 0
    from .system_info import collect, last_shutdown_event
    from .telegram_client import TelegramClient, TelegramError

    info = collect(with_public_ip=False)
    reason = last_shutdown_event()
    text = M.shutdown_report(reason=reason or "(không rõ)", hostname=info.hostname,
                             at=f"{datetime.now():%H:%M:%S %d/%m/%Y}")
    try:
        # Windows chi cho task chay vai giay trong luc dang tat -> gui nhanh, khong retry
        tg = TelegramClient(cfg.telegram.bot_token, timeout=8, proxy=cfg.telegram.proxy or None)
        for chat in cfg.telegram.allowed_chat_ids:
            tg.send_message(chat, text, parse_mode="Markdown", quick=True)
    except TelegramError as e:
        log.warning("notify-shutdown: %s", e)
    return 0


def cmd_notify_boot(args: argparse.Namespace) -> int:
    cfg, _ = load_config(args.config)
    from .system_info import collect, os_full_name
    info = collect(tag=cfg.machine_tag, with_public_ip=True)
    text = M.boot_report(hostname=info.hostname, tag=cfg.machine_tag, user=info.user,
                         boot_time=datetime.fromtimestamp(info.boot_time).strftime("%H:%M:%S %d/%m/%Y"),
                         lan_ip=info.lan_ip or "?", public_ip=info.public_ip or "?",
                         os=os_full_name())
    return _broadcast(cfg, text)


def _broadcast(cfg: Config, text: str, quick: bool = False) -> int:
    from .telegram_client import TelegramClient, TelegramError
    if not cfg.telegram.bot_token:
        print("❌ Chưa có bot_token.")
        return 2
    if not cfg.telegram.allowed_chat_ids:
        print("❌ Chưa cấp phép chat_id nào. Chạy `remotepc init` hoặc gửi `/pair <PIN>` cho bot.")
        return 2
    try:
        tg = TelegramClient(cfg.telegram.bot_token, timeout=10 if quick else 60,
                            proxy=cfg.telegram.proxy or None)
        for chat in cfg.telegram.allowed_chat_ids:
            tg.send_message(chat, text, parse_mode="Markdown", quick=quick)
        print("✅ Đã gửi.")
        return 0
    except TelegramError as e:
        print(f"❌ Gửi thất bại: {e}")
        return 1


def cmd_send(args: argparse.Namespace) -> int:
    cfg, _ = load_config(args.config)
    return _broadcast(cfg, args.text)


# --------------------------------------------------------------------------- #
# install / uninstall / doctor
# --------------------------------------------------------------------------- #


def cmd_install(args: argparse.Namespace) -> int:
    from .installer import install
    cfg, cfg_path = load_config(args.config)
    if not cfg.telegram.bot_token:
        print("❌ Chưa cấu hình. Hãy chạy: python -m remotepc init")
        return 2
    children = args.child or None
    ok, notes = install(cfg, cfg_path, level=args.level, children=children,
                        start_now=not args.no_start, create_tasks=not args.no_tasks)
    print("\n".join(f"  • {n}" for n in notes))
    print("\n✅ Cài đặt hoàn tất." if ok else "\n❌ Cài đặt chưa hoàn tất.")
    if ok:
        print("   Thử ngay: gửi `/status` cho bot trên Telegram.")
    return 0 if ok else 1


def cmd_uninstall(args: argparse.Namespace) -> int:
    from .installer import uninstall
    cfg, cfg_path = load_config(args.config)
    layout = Layout(cfg.data_layout_dir())
    if cfg.has_pin() and not args.force:
        pin = args.pin or _ask("Nhập PIN để xác nhận gỡ", hidden=True, required=True)
        if not cfg.check_pin(pin):
            print("❌ Sai PIN. Không gỡ được.")
            print("   (Nếu bạn là chủ máy và quên PIN: chạy lại với `--force` "
                  "bằng quyền Administrator.)")
            return 3
    if args.force:
        from .installer import is_elevated
        if not is_elevated():
            print("❌ --force chỉ dùng được khi chạy bằng quyền Administrator.")
            return 3
        print("⚠️ Đang gỡ ở chế độ --force (bỏ qua PIN).")
    _broadcast(cfg, f"{M.EMOJI['warn']} Agent trên `{cfg.machine_tag}` đang được GỠ cài đặt "
                    f"lúc {datetime.now():%H:%M:%S}.", quick=True)
    ok, detail = uninstall(cfg, cfg_path, layout, purge=not args.keep_data)
    print(detail)
    print("✅ Đã gỡ." if ok else "❌ Gỡ chưa xong (xem chi tiết ở trên).")
    return 0 if ok else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    from .installer import doctor
    cfg, cfg_path = load_config(args.config)
    layout = layout_for(cfg, cfg_path)
    print(f"Cấu hình: {cfg_path}")
    for line in doctor(cfg, layout):
        print(f"  • {line}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg, cfg_path = load_config(args.config)
    from .bot import Agent
    layout = layout_for(cfg, cfg_path)
    agent = Agent(cfg, cfg_path, layout, stealth=False)
    print(console(agent.monitor.describe()))
    print(f"\nUI host: {'đang chạy' if agent.inbox.uihost_alive() else 'không chạy'}")
    print(f"Đang ở session 0: {'có' if agent.needs_uihost else 'không'}")
    return 0


# --------------------------------------------------------------------------- #
# config / pin / popup / find-chat / build
# --------------------------------------------------------------------------- #


def cmd_config(args: argparse.Namespace) -> int:
    cfg, cfg_path = load_config(args.config)
    if args.show:
        data = cfg.to_dict()
        if data.get("telegram", {}).get("bot_token"):
            t = data["telegram"]["bot_token"]
            data["telegram"]["bot_token"] = t[:6] + "***" + t[-4:] if len(t) > 12 else "***"
        data["pin_hash"] = "***" if data.get("pin_hash") else ""
        data.pop("pin_salt", None)
        print(f"# {cfg_path}")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    if args.set:
        for kv in args.set:
            if "=" not in kv:
                print(f"❌ Sai cú pháp: {kv} (cần khoá=giá_trị)")
                return 2
            key, _, val = kv.partition("=")
            try:
                print("  • " + set_by_path(cfg, key, val))
            except (KeyError, AttributeError, ValueError) as e:
                print(f"❌ {key}: {e}")
                return 2
        save_config(cfg, cfg_path)
        print("✅ Đã lưu.")
        return 0
    print("Dùng --show hoặc --set khoá=giá_trị")
    return 0


def cmd_set_pin(args: argparse.Namespace) -> int:
    cfg, cfg_path = load_config(args.config)
    new = args.new or _ask("PIN mới (≥4 ký tự)", hidden=True, required=True)
    if cfg.has_pin():
        old = args.old or _ask("PIN cũ", hidden=True, required=True)
        if not cfg.check_pin(old):
            print("❌ PIN cũ sai.")
            return 3
    if len(new) < 4:
        print("❌ PIN phải có ít nhất 4 ký tự.")
        return 2
    cfg.set_pin(new)
    save_config(cfg, cfg_path)
    print("✅ Đã đổi PIN.")
    return 0


def cmd_popup(args: argparse.Namespace) -> int:
    from . import actions
    if args.full:
        ok, detail = actions.overlay(args.text, title=args.title, duration=args.timeout)
    else:
        ok, detail = actions.message_box(args.text, title=args.title, style=args.style,
                                         timeout=args.timeout)
    print(("✅ " if ok else "❌ ") + detail)
    return 0 if ok else 1


def cmd_find_chat(args: argparse.Namespace) -> int:
    cfg, _ = load_config(args.config)
    print("Nhắn 1 tin bất kỳ cho bot trong vòng", args.wait, "giây...")
    ids = _discover_chats(cfg.telegram.bot_token, wait=args.wait)
    if not ids:
        print("Không thấy tin nhắn nào.")
        return 1
    for cid, name in ids:
        print(f"  chat_id = {cid}   ({name})")
    if args.add and ids:
        cid = ids[0][0]
        if cid not in cfg.telegram.allowed_chat_ids:
            cfg.telegram.allowed_chat_ids.append(cid)
        _, cfg_path = load_config(args.config)
        save_config(cfg, cfg_path)
        print(f"✅ Đã thêm {cid} vào danh sách được phép.")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Dong goi thanh .exe bang PyInstaller (ten nguỵ trang)."""
    cfg, _ = load_config(args.config)
    name = cfg.stealth.display_name
    ui = cfg.stealth.uihost_name
    out = Path(args.out or (repo_root() / "dist"))
    out.mkdir(parents=True, exist_ok=True)
    cmds = [
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
         "--windowed", "--name", name, "--distpath", str(out),
         "--add-data", f"{repo_root() / 'remotepc'}{os.pathsep}remotepc",
         "--hidden-import", "remotepc", str(repo_root() / "remotepc" / "__main__.py")],
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
         "--windowed", "--name", ui, "--distpath", str(out),
         "--add-data", f"{repo_root() / 'remotepc'}{os.pathsep}remotepc",
         "--hidden-import", "remotepc.uihost", str(repo_root() / "remotepc" / "uihost.py")],
    ]
    for c in cmds:
        print("→", " ".join(c))
        if args.dry_run:
            continue
        rc = subprocess.call(c)
        if rc != 0:
            print(f"❌ PyInstaller trả về {rc}")
            return rc
    print(f"✅ Xong. File nằm ở {out}")
    print("   Chạy tiếp: `%s\\%s.exe install`" % (out, name))
    return 0


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="remotepc",
        description="RemotePC — điều khiển & giám sát máy tính qua bot Telegram (v1.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ví dụ:\n"
            "  python -m remotepc init\n"
            "  python -m remotepc run\n"
            "  python -m remotepc install --level 2 --child Kid\n"
            "  python -m remotepc doctor\n"
        ),
    )
    p.add_argument("--version", action="version", version=f"RemotePC {__version__}")
    p.add_argument("--config", help="đường dẫn config.json (mặc định: trong ProgramData)")
    sub = p.add_subparsers(dest="cmd")

    # `--config` dung duoc ca 2 kieu: `remotepc --config X uihost` va
    # `remotepc uihost --config X`. Phai dat default=SUPPRESS, neu khong
    # argparse se cho default None cua lenh con ghi de gia tri da parse o cap cha.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS,
                        help="đường dẫn config.json")

    def add(name: str, fn, help_text: str, **kw: Any):
        kw.setdefault("parents", []).append(common)
        sp = sub.add_parser(name, help=help_text, **kw)
        sp.set_defaults(func=fn)
        return sp

    sp = add("init", cmd_init, "thiết lập ban đầu (hỏi từng bước)")
    sp.add_argument("--token", help="bot token (bỏ qua bước hỏi)")
    sp.add_argument("--chat-id", help="chat_id của bố/mẹ")
    sp.add_argument("--pin", help="PIN bảo vệ")
    sp.add_argument("--defaults", "-y", "--yes", action="store_true",
                    help="dùng giá trị mặc định (không hỏi tương tác)")
    sp.add_argument("--machine-tag", help="tên đại diện của máy")
    sp.add_argument("--level", type=int, choices=[0, 1, 2], help="mức bảo vệ")


    sp = add("run", cmd_run, "chạy agent (nền trước mặt)")
    sp.add_argument("--no-stealth", action="store_true", help="không ẩn console, in log ra màn hình")
    sp.add_argument("--once", action="store_true", help="chạy 1 vòng rồi thoát (để thử)")
    sp.add_argument("--debug", action="store_true")

    sp = add("uihost", cmd_uihost, "chạy thành phần hiển thị (bình thường do task tự chạy)")
    sp.add_argument("--once", action="store_true")
    sp.add_argument("--debug", action="store_true")

    sp = add("watchdog", cmd_watchdog, "chạy 1 vòng kiểm tra / hoặc --loop liên tục")
    sp.add_argument("--loop", action="store_true")
    sp.add_argument("--verbose", "-v", action="store_true")

    add("notify-shutdown", cmd_notify_shutdown, "gửi thông báo máy đang tắt (task telemetry gọi)")
    add("notify-boot", cmd_notify_boot, "gửi thông báo máy vừa bật (để thử)")

    sp = add("send", cmd_send, "gửi thử 1 tin nhắn về bot")
    sp.add_argument("text")

    sp = add("install", cmd_install, "cài chính thức: SYSTEM + tự bảo vệ + tự khởi động")
    sp.add_argument("--level", type=int, choices=[0, 1, 2], help="0/1/2 mức bảo vệ")
    sp.add_argument("--child", action="append", help="tài khoản của con (lặp lại nhiều lần)")
    sp.add_argument("--no-start", action="store_true", help="cài xong không khởi động ngay")
    sp.add_argument("--no-tasks", action="store_true", help="không tạo scheduled task")

    sp = add("uninstall", cmd_uninstall, "gỡ toàn bộ (cần PIN)")
    sp.add_argument("--pin")
    sp.add_argument("--force", action="store_true",
                    help="bỏ qua PIN (chỉ dành cho chủ máy, cần Administrator)")
    sp.add_argument("--keep-data", action="store_true", help="giữ lại log/cấu hình")

    add("doctor", cmd_doctor, "tự kiểm tra cài đặt & lớp bảo vệ")
    add("status", cmd_status, "in trạng thái máy + giờ giới nghiêm")

    sp = add("config", cmd_config, "xem/sửa cấu hình")
    sp.add_argument("--show", action="store_true")
    sp.add_argument("--set", action="append", metavar="KHOÁ=GIÁ_TRỊ")

    sp = add("set-pin", cmd_set_pin, "đổi PIN")
    sp.add_argument("--old")
    sp.add_argument("--new")

    sp = add("popup", cmd_popup, "thử hiện thông báo trên máy này")
    sp.add_argument("text")
    sp.add_argument("--full", action="store_true", help="phủ toàn màn hình")
    sp.add_argument("--title", default="Thông báo từ bố/mẹ")
    sp.add_argument("--style", default="warn")
    sp.add_argument("--timeout", type=int, default=0)

    sp = add("find-chat", cmd_find_chat, "tìm chat_id của người vừa nhắn cho bot")
    sp.add_argument("--wait", type=int, default=25)
    sp.add_argument("--add", action="store_true", help="thêm luôn vào danh sách được phép")

    sp = add("build", cmd_build, "đóng gói thành .exe (PyInstaller)")
    sp.add_argument("--out", help="thư mục xuất")
    sp.add_argument("--dry-run", action="store_true")

    return p


def main(argv: Optional[list[str]] = None) -> int:
    _utf8_stdout()
    argv = sys.argv[1:] if argv is None else list(argv)

    # Khi PyInstaller dong goi, ten exe quyet dinh che do chay.
    # Van phai nhan ra khi argv da co tham so khac (vd `SysHealthUI.exe --config X`)
    if is_frozen():
        cfg, _ = load_config(os.environ.get("REMOTEPC_CONFIG"))
        known = {"init", "run", "uihost", "watchdog", "notify-shutdown", "notify-boot",
                 "send", "install", "uninstall", "doctor", "status", "config",
                 "set-pin", "popup", "find-chat", "build"}
        if not any(a in known for a in argv):
            exe_name = Path(sys.executable).name.lower()
            mode = "uihost" if exe_name.startswith(cfg.stealth.uihost_name.lower()) else "run"
            argv = [mode] + argv

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "config"):
        args.config = None
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nĐã dừng.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
