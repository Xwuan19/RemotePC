"""Phan tich cau lenh gui tu Telegram.

Cu phap:
    /<lenh>[@TenBot] [@may1,@may2] [--co] [tham so] [noi dung tu do]

Vi du:
    /msg --full --title "Nhac nho" Con ngu di, muon roi day!
    /shutdown 120 --msg "Bo me tat may nhe"
    /msg @pc2 Chao con

Ham `parse_message()` tra ve `ParsedCommand` de bot.py dieu phoi.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Optional

# Cac "co" (flag) duoc phep, dung de tach khoi noi dung tu do.
KNOWN_FLAGS = {
    # hien thi
    "full", "fullscreen", "overlay", "top", "topmost", "beep", "nobeeper",
    # kieu hop thoai
    "info", "warn", "warning", "error", "critical", "question",
    # hanh vi
    "silent", "force", "yes", "no", "raw", "brief", "verbose", "all", "test",
    "wait", "nowait",
}

# Alias lenh -> ten chuan.
ALIASES: dict[str, str] = {
    "shut": "shutdown", "tat": "shutdown", "tatmay": "shutdown", "off": "shutdown",
    "reboot": "restart", "khoi": "restart", "khoidonglai": "restart",
    "khoa": "lock", "lockmay": "lock",
    "dangxuat": "logoff", "signout": "logoff",
    "nhan": "msg", "message": "msg", "thongbao": "msg", "tb": "msg", "say": "msg",
    "anh": "screenshot", "screen": "screenshot", "chup": "screenshot", "shot": "screenshot",
    "tt": "status", "trangthai": "status", "info": "status",
    "nhatky": "log", "lichsu": "log", "events": "log",
    "ud": "apps", "ungdung": "apps", "processes": "apps", "procs": "apps",
    "help2": "help", "trogiup": "help", "?": "help",
    "gio": "curfew", "curfew": "curfew", "gioram": "curfew",
    "may": "machines", "devices": "machines",
    "huy": "abort", "cancel": "abort",
}


@dataclass
class ParsedCommand:
    raw: str = ""
    command: str = ""            # ten chuan, vd "msg"
    targets: list[str] = field(default_factory=list)   # @pc1 @all
    flags: set[str] = field(default_factory=set)
    options: dict[str, str] = field(default_factory=dict)   # --title "..."
    args: list[str] = field(default_factory=list)           # vi tri
    text: str = ""             # noi dung tu do con lai (noi bang khoang trang)
    ok: bool = True
    error: str = ""
    other_machine: bool = False      # lenh danh cho may khac -> im lang, khong tra loi

    # -------- tien ich -------- #
    def has_flag(self, *names: str) -> bool:
        return bool(self.flags & set(names))

    def first_int(self, default: Optional[int] = None) -> Optional[int]:
        for a in self.args:
            try:
                return int(a)
            except ValueError:
                continue
        return default

    def reply_key(self) -> str:
        return self.command or "?"


class ParseError(ValueError):
    pass


def normalize_command(word: str) -> str:
    """'/Msg@MyBot' -> 'msg'."""
    w = word.strip().lower()
    if w.startswith("/"):
        w = w[1:]
    if "@" in w:
        w = w.split("@", 1)[0]
    w = "".join(c for c in w if c.isalnum() or c in "_-")
    return ALIASES.get(w, w)


def tokenize(text: str) -> list[str]:
    """Tach chuoi thanh token, ho tro nhay kep; loi cu phap -> fallback split()."""
    text = (text or "").strip()
    if not text:
        return []
    try:
        return shlex.split(text, posix=True, comments=False)
    except ValueError:
        # Vi du: nhay le -> cu split thuong.
        return text.replace('\\"', '"').split()


def targets_match(targets: list[str], my_tag: str, require_tag: bool) -> bool:
    """Lenh co danh cho may nay khong?

    - khong co @tag nao        -> phu thuoc `require_tag`
    - co @all                  -> luon khop
    - co @tag khop (khong phan biet hoa thuong) -> khop
    """
    if not targets:
        return not require_tag
    mine = (my_tag or "").strip().lower()
    for t in targets:
        t = t.strip().lower()
        if t in ("all", "*", "moi"):
            return True
        if t == mine:
            return True
    return False


def parse_message(text: str, my_tag: str = "", require_tag: bool = False) -> ParsedCommand:
    """Phan tich 1 tin nhan thanh lenh."""
    raw = (text or "").strip()
    out = ParsedCommand(raw=raw)
    tokens = tokenize(raw)
    if not tokens:
        out.ok = False
        out.error = "Tin nhan rong."
        return out

    head = tokens[0]
    if not head.startswith("/"):
        # Khong phai lenh -> coi toan bo la noi dung tu do cua /msg? Khong: bao loi nhe.
        out.ok = False
        out.command = "text"
        out.text = raw
        out.error = "Tin nhan phai bat dau bang '/' (gui /help de xem danh sach)."
        return out

    out.command = normalize_command(head)
    rest = tokens[1:]

    # @targets co the nam ngay sau lenh hoac bat ky dau
    positional: list[str] = []
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("@") and len(tok) > 1:
            out.targets.append(tok[1:])
        elif tok.startswith("--") and len(tok) > 2:
            body = tok[2:]
            name = body.lower()
            if "=" in body:
                # --title="Bo noi"
                key, _, val = body.partition("=")
                out.options[key.lower()] = val
            elif name in KNOWN_FLAGS:
                # --full / --silent / --warn : co, khong "nuot" tu tiep theo
                out.flags.add(name)
                out.options.setdefault(name, "true")
            elif i + 1 < len(rest) and not rest[i + 1].startswith("-"):
                # --title "Bo noi" / --timeout 30
                out.options[name] = rest[i + 1]
                i += 1
            else:
                out.flags.add(name)
                out.options.setdefault(name, "true")
        elif tok.startswith("-") and len(tok) == 2 and tok[1].isalpha():
            # -f / -t ... => co ngan
            out.flags.add(tok[1].lower())
        else:
            # tu thuong (co the trung ten voi 1 "co" - vd `/curfew warn ...`)
            positional.append(tok)
        i += 1

    out.args = positional
    out.text = " ".join(positional).strip()
    if not targets_match(out.targets, my_tag, require_tag):
        out.ok = False
        out.other_machine = True
        out.error = f"Lenh nay danh cho may khac ({', '.join('@' + t for t in out.targets)})."
    return out


def parse_duration(text: str) -> Optional[int]:
    """'90' -> 90s | '1m30' -> 90s | '2h' -> 7200s | '1.5h' -> 5400s. Khong hieu -> None."""
    s = str(text or "").strip().lower().replace(" ", "")
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total = 0.0
    num = ""
    matched = False
    for ch in s:
        if ch.isdigit() or ch == ".":
            num += ch
            continue
        if not num:
            return None
        if ch in ("h", "g"):            # gio
            total += float(num) * 3600
        elif ch in ("m", "p"):          # phut
            total += float(num) * 60
        elif ch == "s":                 # giay
            total += float(num)
        else:
            return None
        num = ""
        matched = True
    if num:                             # don vi ngam dinh = giay
        total += float(num)
        matched = True
    return int(total) if matched else None


def format_duration(seconds: float) -> str:
    """125 -> '2 phut 5 giay'."""
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d} ngay")
    if h:
        parts.append(f"{h} gio")
    if m:
        parts.append(f"{m} phut")
    if s or not parts:
        parts.append(f"{s} giay")
    return " ".join(parts)
