"""Mau noi dung nguoi dung (co dau tieng Viet).

Toan bo chuoi hien len Telegram / man hinh may con deu nam o day
de de sua ma khong phai dung vao logic.
"""

from __future__ import annotations

from datetime import datetime

EMOJI = {
    "boot": "\U0001F7E2",       # green circle
    "shutdown": "\U0001F534",   # red circle
    "warn": "\u26A0\uFE0F",     # warning
    "lock": "\U0001F512",
    "msg": "\U0001F4AC",
    "cam": "\U0001F4F7",
    "ok": "\u2705",
    "fail": "\u274C",
    "clock": "\u23F0",
    "tamper": "\U0001F6A8",
    "pc": "\U0001F4BB",
    "moon": "\U0001F319",
}


def _t(dt: datetime | None = None) -> str:
    return (dt or datetime.now()).strftime("%H:%M:%S %d/%m/%Y")


# --------------------------------------------------------------------------- #
# Bao ve bot
# --------------------------------------------------------------------------- #


def boot_report(**kw) -> str:
    return (
        f"{EMOJI['boot']} *MÁY VỪA ĐƯỢC BẬT*\n"
        f"────────────────\n"
        f"{EMOJI['pc']} Máy: `{kw.get('hostname','?')}` ({kw.get('tag','pc1')})\n"
        f"\U0001F464 Người dùng: `{kw.get('user','?')}`\n"
        f"\U0001F552 Giờ bật: `{kw.get('boot_time','?')}`\n"
        f"\U0001F310 IP LAN: `{kw.get('lan_ip','?')}`\n"
        f"\U0001F30D IP công khai: `{kw.get('public_ip','?')}`\n"
        f"\U0001F5A5️ Windows: `{kw.get('os','?')}`\n"
        f"────────────────\n"
        f"Agent đang chạy nền và đã tự bảo vệ."
    )


def login_report(**kw) -> str:
    return (
        f"\U0001F464 *CÓ NGƯỜI ĐĂNG NHẬP*\n"
        f"Tài khoản: `{kw.get('user','?')}`\n"
        f"Máy: `{kw.get('hostname','?')}` · Lúc: `{kw.get('at','?')}`"
    )


def shutdown_report(**kw) -> str:
    return (
        f"{EMOJI['shutdown']} *MÁY ĐANG TẮT (OFFLINE)*\n"
        f"────────────────\n"
        f"Lý do: {kw.get('reason','(không rõ)')}\n"
        f"Máy: `{kw.get('hostname','?')}` · Lúc: `{kw.get('at','?')}`\n"
        f"────────────────\n"
        f"⚠️ Máy đã tắt. Mọi lệnh điều khiển tạm dừng cho đến khi con bật máy lại."
    )


def heartbeat(**kw) -> str:
    return (
        f"\U0001F493 Agent vẫn sống · `{kw.get('hostname','?')}`\n"
        f"Uptime: {kw.get('uptime','?')} · Giờ máy: `{kw.get('now','?')}`"
    )


def tamper_alert(**kw) -> str:
    return (
        f"{EMOJI['tamper']} *CẢNH BÁO CAN THIỆP*\n"
        f"Sự kiện: {kw.get('event','?')}\n"
        f"Chi tiết: `{kw.get('detail','')}`\n"
        f"Máy: `{kw.get('hostname','?')}` · Lúc: `{kw.get('at','?')}`\n"
        f"Agent đã tự khôi phục."
    )


# --------------------------------------------------------------------------- #
# Giờ giới nghiêm
# --------------------------------------------------------------------------- #


def curfew_warn(**kw) -> str:
    mins = kw.get("minutes", 0)
    end = kw.get("end", "?")
    return (
        f"{EMOJI['warn']} *SẮP TỚI GIỜ GIỚI NGHIÊM*\n"
        f"Con còn *{mins} phút* trước {end}.\n"
        f"Máy: `{kw.get('hostname','?')}` · Lúc: `{kw.get('at','?')}`\n"
        f"Ứng dụng đang mở: `{kw.get('app','?')}`"
    )


def curfew_start(**kw) -> str:
    return (
        f"{EMOJI['moon']} *ĐÃ VÀO GIỜ GIỚI NGHIÊM*\n"
        f"Khung: {kw.get('window','?')} ({kw.get('start','?')} - {kw.get('end','?')})\n"
        f"Máy: `{kw.get('hostname','?')}` · Lúc: `{kw.get('at','?')}`\n"
        f"Đã hiện thông báo trên màn hình của con.\n"
        f"Ứng dụng đang mở: `{kw.get('app','?')}`"
    )


def curfew_end(**kw) -> str:
    return (
        f"{EMOJI['clock']} *HẾT GIỜ GIỚI NGHIÊM*\n"
        f"Khung {kw.get('window','?')} đã kết thúc lúc `{kw.get('at','?')}`.\n"
        f"Hành động: {kw.get('action','không làm gì')}"
    )


def curfew_auto_shutdown(**kw) -> str:
    return (
        f"{EMOJI['shutdown']} *TỰ ĐỘNG TẮT MÁY*\n"
        f"Con vẫn dùng máy sau giờ giới nghiêm {kw.get('overtime','?')}.\n"
        f"Máy sẽ tắt sau {kw.get('delay','30')} giây."
    )


DEFAULT_POPUP_CURFEW = (
    "ĐÃ QUÁ {end}\n\n"
    "Con hãy tắt máy và đi ngủ nhé.\n"
    "Bố/mẹ đã nhận được thông báo."
)

DEFAULT_POPUP_WARN = "Còn {minutes} phút nữa là tới giờ giới nghiêm ({end})."


# --------------------------------------------------------------------------- #
# Phản hồi lệnh
# --------------------------------------------------------------------------- #


def cmd_ok(cmd: str, detail: str = "") -> str:
    return f"{EMOJI['ok']} *{cmd.upper()}* thành công\n{detail}".rstrip()


def cmd_fail(cmd: str, detail: str = "") -> str:
    return f"{EMOJI['fail']} *{cmd.upper()}* thất bại\n{detail}".rstrip()


def need_pin(cmd: str) -> str:
    return (
        f"{EMOJI['lock']} Lệnh `{cmd}` cần mã PIN.\n"
        f"Cú pháp: `/{cmd} --pin <mã> ...`\n"
        f"hoặc gửi `/pin <mã>` một lần để mở khoá phiên này."
    )


def unknown_command(cmd: str) -> str:
    return f"{EMOJI['fail']} Không rõ lệnh `/{cmd}`. Gửi `/help` để xem danh sách."


def unauthorized(chat_id: int) -> str:
    return (
        f"{EMOJI['fail']} Chat `{chat_id}` chưa được cấp quyền điều khiển máy này.\n"
        f"Hãy chạy `remotepc register` trên máy bố/mẹ để lấy chat_id rồi thêm vào config."
    )


def machine_offline_hint(tag: str) -> str:
    return f"Không có máy nào mang tag `{tag}` đang trực tuyến."


HELP_TEXT = """\
*REMOTE PC — BỘ LỆNH*
_(thêm `@tên_máy` sau lệnh nếu quản lý nhiều máy)_

*Thông báo*
`/status` — trạng thái máy, uptime, giờ giới nghiêm
`/log [n]` — n sự kiện gần nhất
`/apps` — ứng dụng đang chạy / cửa sổ đang mở
`/screenshot` — chụp màn hình gửi về

*Gửi lời nhắn cho con*
`/msg Con ngủ đi` — hộp thoại thông báo
`/msg --full Đã quá 12h, tắt máy ngay` — phủ toàn màn hình
`/msg --title "Bố nói" --style error --timeout 30 ...`

*Điều khiển nguồn*
`/shutdown [giây] [--msg "..."]` — tắt máy
`/restart [giây]` — khởi động lại
`/lock` — khoá màn hình
`/logoff` — đăng xuất
`/abort` — huỷ lệnh tắt máy đang đếm ngược

*Giờ giới nghiêm*
`/curfew` — xem cấu hình hiện tại
`/curfew on` · `/curfew off`
`/curfew set 23:30 00:00` — đổi khung giờ
`/curfew warn 30,10` — đổi các mốc báo trước
`/curfew action lock` — hành động khi vào giờ (none/notify/popup/lock)
`/curfew end shutdown` — hành động khi hết giờ (none/notify/lock/logoff/shutdown)

*Hệ thống*
`/machines` — danh sách máy đang báo về bot này
`/config set <khoá> <giá trị>` · `/config show`
`/pin <mã_cũ> <mã_mới>` — đổi PIN
`/reload` — nạp lại config
`/uninstall --pin <mã>` — gỡ agent khỏi máy
"""
