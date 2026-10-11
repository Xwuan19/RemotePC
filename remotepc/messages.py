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


def make_bar(pct: float, length: int = 10) -> str:
    """Tạo thanh tiến trình khối [▰▰▰▰▱▱▱▱▱▱]."""
    if pct < 0:
        return "▱" * length
    filled = max(0, min(length, round(pct / 100.0 * length)))
    return "▰" * filled + "▱" * (length - filled)


def boot_report(**kw) -> str:
    return (
        f"🖥 *Remote PC* • `{kw.get('hostname','?')}` (`{kw.get('tag','pc1')}`)\n"
        f"─────────────\n"
        f"🟢 *Trạng thái:* _Máy vừa được bật_\n"
        f"👤 *Người dùng:* `{kw.get('user','?')}`\n"
        f"🕒 *Giờ bật:* `{kw.get('boot_time','?')}`\n"
        f"🌐 *IP LAN:* `{kw.get('lan_ip','?')}`\n"
        f"🌍 *IP công khai:* `{kw.get('public_ip','?')}`\n"
        f"💻 *Windows:* `{kw.get('os','?')}`\n"
        f"─────────────\n"
        f"🛡️ _Agent đang chạy ngầm và đã tự bảo vệ._"
    )


def login_report(**kw) -> str:
    return (
        f"👤 *Có người đăng nhập* • `{kw.get('hostname','?')}`\n"
        f"─────────────\n"
        f"Tài khoản: `{kw.get('user','?')}` · Lúc: `{kw.get('at','?')}`"
    )


def shutdown_report(**kw) -> str:
    return (
        f"{EMOJI['shutdown']} *Máy đang tắt (offline)* • `{kw.get('hostname','?')}`\n"
        f"─────────────\n"
        f"Lý do: {kw.get('reason','không rõ')}\n"
        f"Thời gian: `{kw.get('at','?')}`\n"
        f"─────────────\n"
        f"⚠️ _Máy đã tắt. Mọi lệnh điều khiển tạm dừng cho đến khi nó bật lại._"
    )


def heartbeat(**kw) -> str:
    return (
        f"\U0001F493 Agent vẫn sống · `{kw.get('hostname','?')}`\n"
        f"Uptime: {kw.get('uptime','?')} · Giờ máy: `{kw.get('now','?')}`"
    )


def tamper_alert(**kw) -> str:
    return (
        f"{EMOJI['tamper']} *Cảnh báo can thiệp* • `{kw.get('hostname','?')}`\n"
        f"─────────────\n"
        f"Sự kiện: {kw.get('event','?')}\n"
        f"Chi tiết: `{kw.get('detail','')}` · Lúc: `{kw.get('at','?')}`\n"
        f"─────────────\n"
        f"🛡️ _Agent đã tự khôi phục an toàn._"
    )


# --------------------------------------------------------------------------- #
# Giờ giới nghiêm
# --------------------------------------------------------------------------- #


def curfew_warn(**kw) -> str:
    mins = kw.get("minutes", 0)
    end = kw.get("end", "?")
    return (
        f"{EMOJI['warn']} *Sắp tới giờ giới nghiêm* • `{kw.get('hostname','?')}`\n"
        f"─────────────\n"
        f"Nó còn *{mins} phút* trước {end}.\n"
        f"Đang mở: `{kw.get('app','?')}` · Lúc: `{kw.get('at','?')}`"
    )


def curfew_start(**kw) -> str:
    start_t = kw.get('start', '23:00')
    return (
        f"⏰ *Đã vào giờ giới nghiêm (nhắc đi ngủ {start_t})* • `{kw.get('hostname','?')}`\n"
        f"─────────────\n"
        f"👤 Người dùng: `{kw.get('user','?')}`\n"
        f"📱 Đang mở: `{kw.get('app','?')}` · Lúc: `{kw.get('at','?')}`\n"
        f"─────────────\n"
        f"💬 Máy tính nó đã hiện thông báo nhắc đi ngủ.\n"
        f"_Nếu nó không xin phép học tiếp, anh có thể bấm nút tắt máy bên dưới:_"
    )


def curfew_end(**kw) -> str:
    return (
        f"{EMOJI['clock']} *Hết giờ giới nghiêm* • `{kw.get('hostname','?')}`\n"
        f"─────────────\n"
        f"Khung *{kw.get('window','?')}* đã kết thúc lúc `{kw.get('at','?')}`.\n"
        f"Hành động: {kw.get('action','không làm gì')}"
    )


def curfew_auto_shutdown(**kw) -> str:
    return (
        f"{EMOJI['shutdown']} *Tự động tắt máy*\n"
        f"─────────────\n"
        f"Nó vẫn dùng máy sau giờ giới nghiêm {kw.get('overtime','?')}.\n"
        f"Máy sẽ tắt sau *{kw.get('delay','30')} giây*."
    )


DEFAULT_POPUP_CURFEW = (
    "ĐÃ TỚI GIỜ ĐI NGỦ RỒI!\n\n"
    "Tắt máy đi ngủ nhanh cho tao!\n"
    "Nếu có bài vở thì gọi bảo tao, không tao tắt máy đấy."
)

DEFAULT_POPUP_WARN = "Còn {minutes} phút nữa là tới giờ giới nghiêm ({end}). Lo mà tắt máy đi!"


# --------------------------------------------------------------------------- #
# Phản hồi lệnh
# --------------------------------------------------------------------------- #


def cmd_ok(cmd: str, detail: str = "") -> str:
    return f"{EMOJI['ok']} *{cmd.upper()}* thành công\n{detail}".rstrip()


def cmd_fail(cmd: str, detail: str = "") -> str:
    return f"{EMOJI['fail']} *{cmd.upper()}* thất bại\n{detail}".rstrip()


def need_pin(cmd: str) -> str:
    return (
        f"{EMOJI['lock']} *Yêu cầu mã PIN*\n"
        f"Lệnh `/{cmd}` cần mã PIN để thực hiện.\n\n"
        f"💡 *Cách dùng:*\n"
        f"• Thêm PIN vào lệnh: `/{cmd} --pin <mã>`\n"
        f"• Hoặc mở khoá phiên: `/unlock <mã>`"
    )


def unknown_command(cmd: str) -> str:
    return f"{EMOJI['fail']} Không rõ lệnh `/{cmd}`. Gửi `/help` để xem danh sách lệnh."


def unauthorized(chat_id: int) -> str:
    return (
        f"{EMOJI['fail']} Chat `{chat_id}` chưa được cấp quyền điều khiển máy này.\n"
        f"Hãy chạy `remotepc register` trên máy tính để thêm chat này."
    )


def machine_offline_hint(tag: str) -> str:
    return f"Không có máy nào mang tag `{tag}` đang trực tuyến."


HELP_TEXT = """\
📱 *Remote PC — Bảng lệnh điều khiển*
_(thêm `@tên_máy` sau lệnh nếu quản lý nhiều máy)_

📊 *Theo dõi & Giám sát*
• `/status` — Trạng thái máy, thời gian bật, giờ giới nghiêm
• `/performance` — Hiệu năng CPU / RAM / Ổ cứng
• `/screenshot` — Chụp ảnh màn hình gửi về bot
• `/apps` — Xem các ứng dụng & cửa sổ đang mở
• `/log` — Xem nhật ký các sự kiện gần nhất

💬 *Gửi tin nhắn & Nhắc nhở*
• `/msg <nội dung>` — Hiện hộp thoại thông báo
• `/msg --full <nội dung>` — Phủ kín toàn màn hình
• `/msg --timeout 15 <nội dung>` — Tự tắt sau 15 giây

⚡ *Nguồn & Hẹn giờ*
• `/timer` — Hẹn giờ tắt máy (chọn nút 15p, 30p, 1h, 23:30...)
• `/shutdown [thời gian]` — Tắt máy (`/shutdown 30p`, `23:30` hoặc `60`)
• `/restart [thời gian]` — Khởi động lại máy
• `/abort` — Huỷ lịch tắt máy đang đếm ngược
• `/logoff` — Đăng xuất tài khoản

🌙 *Giờ giới nghiêm*
• `/curfew` — Xem cài đặt giờ giới nghiêm
• `/curfew on` · `/curfew off` — Bật / tắt tính năng
• `/curfew set 23:30 06:00` — Đổi khung giờ cấm
• `/curfew warn 30,10` — Đổi mốc báo trước (phút)

🔒 *Bảo mật & Cấu hình*
• `/unlock <mã_pin>` — Mở khoá phiên điều khiển
• `/pin <mã_cũ> <mã_mới>` — Đổi mã PIN bảo vệ
• `/config show` — Xem toàn bộ cấu hình
• `/machines` — Danh sách máy đang kết nối
"""
