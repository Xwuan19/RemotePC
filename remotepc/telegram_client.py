"""Client Telegram Bot API - chi dung thu vien chuan (urllib), khong can pip install.

Ho tro:
  * long polling getUpdates
  * sendMessage (MarkdownV2/HTML, inline keyboard)
  * sendPhoto (multipart, gui anh chup man hinh)
  * tu dong lui khi gap 429 (retry_after) va khi mat mang
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("remotepc.telegram")

DEFAULT_TIMEOUT = 60


class TelegramError(RuntimeError):
    """Loi tu API Telegram."""

    def __init__(self, message: str, code: int = 0, description: str = ""):
        super().__init__(message)
        self.code = code
        self.description = description


@dataclass
class Update:
    """1 tin nhan den tu Telegram."""

    update_id: int
    chat_id: int
    text: str
    message_id: int = 0
    from_id: int = 0
    from_name: str = ""
    date: int = 0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict) -> Optional["Update"]:
        cb = data.get("callback_query")
        if cb:
            msg = cb.get("message") or {}
            chat = msg.get("chat") or {}
            sender = cb.get("from") or {}
            text = cb.get("data") or ""
            name = " ".join(x for x in [sender.get("first_name"), sender.get("last_name")] if x)
            return cls(
                update_id=int(data.get("update_id") or 0),
                chat_id=int(chat.get("id") or sender.get("id") or 0),
                text=str(text),
                message_id=int(msg.get("message_id") or 0),
                from_id=int(sender.get("id") or 0),
                from_name=name or str(sender.get("username") or ""),
                date=int(time.time()),
                raw=data,
            )
        msg = data.get("message") or data.get("edited_message") or data.get("channel_post")
        if not msg:
            return None
        chat = msg.get("chat") or {}
        sender = msg.get("from") or {}
        text = msg.get("text")
        if text is None:
            # caption (anh) cung xu ly nhu text
            text = msg.get("caption") or ""
        name = " ".join(x for x in [sender.get("first_name"), sender.get("last_name")] if x)
        return cls(
            update_id=int(data.get("update_id") or 0),
            chat_id=int(chat.get("id") or 0),
            text=str(text),
            message_id=int(msg.get("message_id") or 0),
            from_id=int(sender.get("id") or 0),
            from_name=name or str(sender.get("username") or ""),
            date=int(msg.get("date") or 0),
            raw=data,
        )


class TelegramClient:
    """Client dong bo (hop voi vong long-polling chay trong thread rieng)."""

    def __init__(self, token: str, api_base: str = "https://api.telegram.org",
                 timeout: int = DEFAULT_TIMEOUT, proxy: Optional[str] = None):
        if not token or ":" not in token:
            raise ValueError("bot_token khong hop le (lay tu @BotFather)")
        self.token = token.strip()
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self.proxy = proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        self._me: Optional[dict] = None
        self._last_error_at = 0.0
        self._error_count = 0

    # ------------------------------------------------------------------ #

    def _opener(self) -> urllib.request.OpenerDirector:
        handlers: list[Any] = []
        if self.proxy:
            handlers.append(urllib.request.ProxyHandler({"https": self.proxy, "http": self.proxy}))
        else:
            handlers.append(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener(*handlers)

    def call(self, method: str, attempts: int = 4, **params: Any) -> Any:
        """Goi 1 method, tra ve truong `result`.

        attempts=4 : binh thuong (tu thu lai khi mat mang, 429...)
        attempts=1 : "fast fail" - dung khi may dang tat hooc dang go cai dat,
                     khong duoc de agent treo hang chuc giay cho mang.
        """
        url = f"{self.api_base}/bot{self.token}/{method}"
        payload = {k: v for k, v in params.items() if v is not None}
        # Telegram muon object duoc truyen nhu JSON string
        for k, v in list(payload.items()):
            if isinstance(v, (dict, list)):
                payload[k] = json.dumps(v, ensure_ascii=False)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "RemotePC-Agent/1.0",
        })
        last_err: Optional[Exception] = None
        for attempt in range(max(1, attempts)):
            try:
                with self._opener().open(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                obj = json.loads(body)
                if not obj.get("ok"):
                    raise TelegramError(obj.get("description", "loi khong ro"),
                                        int(obj.get("error_code") or 0), obj.get("description", ""))
                self._error_count = 0
                return obj.get("result")
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                retry_after = 0
                try:
                    j = json.loads(body)
                    retry_after = int((j.get("parameters") or {}).get("retry_after") or 0)
                    desc = j.get("description", str(e))
                except Exception:
                    desc = f"HTTP {e.code}"
                last_err = TelegramError(desc, e.code, body[:300])
                if e.code == 429 and retry_after:
                    log.warning("Rate limit, cho %ss", retry_after)
                    time.sleep(min(retry_after + 1, 30))
                    continue
                if e.code in (400, 401, 403, 404):
                    # Loi vinh vien (token sai, chat khong ton tai) -> khong thu lai
                    self._note_error()
                    raise last_err
                time.sleep(min(2 ** attempt, 15))
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                self._note_error()
                time.sleep(min(2 ** attempt, 20))
            except json.JSONDecodeError as e:
                last_err = e
                time.sleep(1)
        self._note_error()
        raise TelegramError(f"Khong goi duoc {method}: {last_err}")

    def _note_error(self) -> None:
        self._last_error_at = time.time()
        self._error_count += 1

    # ---------------- API thuong dung ---------------- #

    def me(self, force: bool = False) -> dict:
        if self._me is None or force:
            self._me = self.call("getMe") or {}
        return self._me

    @property
    def username(self) -> str:
        try:
            return str(self.me().get("username", ""))
        except TelegramError:
            return ""

    def get_updates(self, offset: int = 0, timeout: int = 30,
                    allowed: Optional[list[str]] = None) -> list[Update]:
        res = self.call(
            "getUpdates",
            offset=offset or None,
            timeout=timeout,
            allowed_updates=allowed or ["message", "edited_message", "callback_query"],
        )
        out = []
        for item in res or []:
            u = Update.from_api(item)
            if u:
                out.append(u)
        return out

    def send_message(self, chat_id: int, text: str, parse_mode: Optional[str] = None,
                     reply_markup: Optional[dict] = None, silent: bool = False,
                     disable_preview: bool = True, quick: bool = False) -> Optional[dict]:
        if not text:
            return None
        # Telegram gioi han 4096 ky tu
        chunks = _split_text(text, 4000)
        last = None
        for i, part in enumerate(chunks):
            try:
                last = self.call(
                    "sendMessage",
                    attempts=1 if quick else 4,
                    chat_id=chat_id,
                    text=part,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup if i == len(chunks) - 1 else None,
                    disable_notification=silent or None,
                    disable_web_page_preview=disable_preview or None,
                )
            except TelegramError as e:
                if parse_mode and e.code == 400:
                    # Markdown sai cu phap -> gui lai ban thuong (bo ky tu dinh dang)
                    log.warning("parse_mode loi (%s), gui khong dinh dang", e)
                    last = self.call("sendMessage", attempts=1 if quick else 4, chat_id=chat_id,
                                     text=_plain(part), disable_notification=silent or None)
                else:
                    raise
        return last

    def edit_message_text(self, chat_id: int, message_id: int, text: str,
                          parse_mode: Optional[str] = None,
                          reply_markup: Optional[dict] = None) -> Optional[dict]:
        """Sửa nội dung tin nhắn hiện tại khi người dùng bấm nút inline."""
        if not text or not message_id:
            return None
        try:
            return self.call(
                "editMessageText",
                attempts=2,
                chat_id=chat_id,
                message_id=message_id,
                text=text[:4000],
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        except TelegramError as e:
            if "not modified" in str(e).lower():
                return None
            if parse_mode and e.code == 400:
                log.warning("editMessageText parse_mode loi (%s), thu gui plain text", e)
                return self.call("editMessageText", attempts=1, chat_id=chat_id,
                                 message_id=message_id, text=_plain(text[:4000]),
                                 reply_markup=reply_markup)
            raise

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        """Phan hoi callback query khi nguoi dung bam nut inline."""
        if not callback_query_id:
            return False
        try:
            self.call("answerCallbackQuery", attempts=1, callback_query_id=callback_query_id,
                      text=text or None)
            return True
        except Exception:
            return False

    def send_photo(self, chat_id: int, photo: bytes, caption: str = "",
                   filename: str = "screen.png", silent: bool = False) -> Optional[dict]:
        return self._send_file(chat_id, "sendPhoto", "photo", photo, caption, filename, silent)

    def send_document(self, chat_id: int, doc: bytes, caption: str = "",
                      filename: str = "file.txt", silent: bool = False) -> Optional[dict]:
        return self._send_file(chat_id, "sendDocument", "document", doc, caption, filename, silent)

    def _send_file(self, chat_id: int, method: str, field_name: str, blob: bytes,
                   caption: str, filename: str, silent: bool) -> Optional[dict]:
        if not blob:
            return None
        boundary = f"----RemotePC{uuid.uuid4().hex}"
        parts: list[bytes] = []

        def add(name: str, value: Any) -> None:
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
            )

        add("chat_id", chat_id)
        if caption:
            add("caption", _split_text(caption, 1000)[0])
        if silent:
            add("disable_notification", "true")
        ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        parts.append(f"--{boundary}\r\n".encode())
        parts.append(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
        )
        parts.append(f"Content-Type: {ctype}\r\n\r\n".encode())
        parts.append(blob)
        parts.append(f"\r\n--{boundary}--\r\n".encode())

        data = b"".join(parts)
        url = f"{self.api_base}/bot{self.token}/{method}"
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "RemotePC-Agent/1.0",
        })
        try:
            with self._opener().open(req, timeout=self.timeout) as resp:
                obj = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            raise TelegramError(f"{method} that bai: HTTP {e.code}", e.code, body[:300]) from e
        except (urllib.error.URLError, OSError) as e:
            raise TelegramError(f"{method} that bai: {e}") from e
        if not obj.get("ok"):
            raise TelegramError(obj.get("description", "loi"), int(obj.get("error_code") or 0))
        return obj.get("result")

    def answer_inline(self, callback_id: str, text: str = "") -> None:
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text or None)
        except TelegramError:
            pass

    def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            self.call("deleteMessage", chat_id=chat_id, message_id=message_id)
        except TelegramError:
            pass

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        try:
            self.call("setMyCommands",
                      commands=[{"command": c, "description": d} for c, d in commands])
        except TelegramError as e:
            log.debug("setMyCommands bo qua: %s", e)


# --------------------------------------------------------------------------- #


def _split_text(text: str, size: int = 4000) -> list[str]:
    """Cat chuoi dai, uu tien cat o xuong dong."""
    if len(text) <= size:
        return [text]
    out: list[str] = []
    buf = text
    while len(buf) > size:
        cut = buf.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        out.append(buf[:cut])
        buf = buf[cut:].lstrip("\n")
    if buf:
        out.append(buf)
    return out


def _plain(text: str) -> str:
    """Bo dinh dang Markdown de tin nhan luon gui duoc (dung khi API bao 400)."""
    out = text.replace("*", "").replace("`", "")
    out = re.sub(r"(?m)^_+|_+$", "", out)
    return out


def send_once(token: str, chat_id: int, text: str, parse_mode: str = "Markdown") -> bool:
    """Tien ich gui 1 tin nhan ngoai vong lap (dung cho `remotepc notify-test`)."""
    try:
        TelegramClient(token).send_message(chat_id, text, parse_mode=parse_mode)
        return True
    except (TelegramError, ValueError) as e:
        log.error("Gui that bai: %s", e)
        return False
