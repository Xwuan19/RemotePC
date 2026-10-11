"""Cau hinh cua agent (config.json).

Nguyen tac:
  * File JSON de nguoi dung doc/sua duoc, co `config.example.json` lam mau.
  * Khi load, gia tri mac dinh duoc *merge de quy* voi file tren dia
    -> them key moi o ban cap nhat khong lam hong config cu.
  * PIN duoc bam hash (sha256 + salt), khong luu text thuong.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import secrets
import socket
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

from .paths import IS_WINDOWS, data_dir, install_dir

# --------------------------------------------------------------------------- #
# Cac nhom cau hinh
# --------------------------------------------------------------------------- #


@dataclass
class TelegramConfig:
    """Thong tin bot Telegram (lay tu @BotFather)."""

    bot_token: str = ""
    # Chi nhung chat_id nay moi dieu khien duoc. Trong = khoa lai, phai /register.
    allowed_chat_ids: list[int] = field(default_factory=list)
    # Thoi gian long-polling (giay). Telegram cho phep toi da 50.
    poll_timeout: int = 45
    # Tra loi im lang (khong hien thong bao " dang viet...")
    silent_replies: bool = False
    api_base: str = "https://api.telegram.org"


@dataclass
class StealthConfig:
    """Lop nguy trang + tu bao ve."""

    # Ten hien thi (cung la ten thu muc / ten process / ten service).
    display_name: str = "SysHealthAgent"
    # Mo ta hien trong Task Manager / Task Scheduler.
    description: str = "System Health Telemetry Agent"
    # Ten process cua phien hien thi thong bao (chay trong session cua con).
    uihost_name: str = "SysHealthUI"

    # 0 = off (chay binh thuong, de debug)
    # 1 = standard: an cua so console + chay bang SYSTEM + tu khoi dong lai
    # 2 = strict: nhu standard + chan Task Manager/RegEdit cua tai khoan con
    #             + tu choi quyen End Task tren process cua uihost
    level: int = 1

    # An cua so console (bat buoc khi chay nen).
    hide_console: bool = True
    # Gan quyen DENY "Terminate" cho cac tai khoan nay (ho ten hoac SID).
    # Trong = tu dong lay danh sach tai khoan khong phai Administrator luc install.
    deny_terminate_accounts: list[str] = field(default_factory=list)
    # Chan tai khoan con mo Task Manager (chi ap dung cho tai khoan do, khong anh huong bo me).
    block_task_manager_for_child: bool = True
    # Chan tai khoan con mo RegEdit / Task Scheduler.
    block_regedit_for_child: bool = True
    # Chuyen huong phim tat: Ctrl+Alt+Del van mo duoc (Windows khong chan duoc),
    # nhung Task Manager trong do se bi policy chan.
    randomize_process_title: bool = True


@dataclass
class NotificationConfig:
    """Cac su kien gui ve bot."""

    on_boot: bool = True            # may duoc bat
    on_login: bool = True           # co nguoi dang nhap
    on_unlock: bool = False         # mo khoa man hinh
    on_shutdown: bool = True        # may sap tat (best effort)
    on_idle: bool = False           # con roi may
    idle_minutes: int = 30
    heartbeat_minutes: int = 0      # 0 = tat; >0 = cu N phut bao dang song 1 lan
    on_tamper: bool = True          # phat hien ai do co tat/pha agent
    on_curfew: bool = True          # canh bao gio gioi nghiem
    on_command: bool = True         # bao cao ket qua moi lenh
    language: str = "vi"


@dataclass
class CurfewWindow:
    """1 khoang thoi gian cam, vd 23:30 -> 00:00."""

    name: str = "Đêm"
    start: str = "23:30"
    end: str = "00:00"
    # 0=Thu 2 ... 6=Chu nhat. Trong = moi ngay.
    days: list[int] = field(default_factory=list)
    # Cac moc canh bao TRUOC gio (phut). 30 = bao luc 23:00.
    warn_before_minutes: list[int] = field(default_factory=lambda: [30, 10])
    # Hanh dong khi bat dau khoang cam.
    on_start: str = "notify"        # none | notify | popup | lock
    # Hanh dong khi ket thuc khoang cam (vd 00:00).
    on_end: str = "notify"          # none | notify | lock | logoff | shutdown
    # Noi dung hien tren may con (trong = dung mau mac dinh).
    popup_text: str = ""


@dataclass
class CurfewConfig:
    enabled: bool = True
    timezone: str = "local"
    windows: list[CurfewWindow] = field(
        default_factory=lambda: [
            CurfewWindow(name="Đêm", start="23:30", end="00:00", warn_before_minutes=[30, 10],
                         on_start="popup", on_end="notify"),
        ]
    )
    # So phut "an xa" sau khi thuc thi, tranh gui lien tuc.
    repeat_cooldown_minutes: int = 15
    # Gui kem anh man hinh khi vi pham.
    screenshot_on_violation: bool = False
    # Tat may khi vi pham qua N phut (0 = khong bao gio tu tat).
    auto_shutdown_after_minutes: int = 0
    auto_shutdown_delay: int = 60


@dataclass
class MonitoringConfig:
    """Theo doi hoat dong (de bao cao, khong bat buoc)."""

    track_active_window: bool = True
    window_poll_seconds: int = 20
    track_app_usage: bool = True
    keep_events_days: int = 14
    max_events: int = 5000


@dataclass
class PowerConfig:
    shutdown_delay: int = 30
    force_close_apps: bool = True
    restart_delay: int = 30


@dataclass
class Config:
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    stealth: StealthConfig = field(default_factory=StealthConfig)
    notify: NotificationConfig = field(default_factory=NotificationConfig)
    curfew: CurfewConfig = field(default_factory=CurfewConfig)
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    power: PowerConfig = field(default_factory=PowerConfig)

    # May nay ten gi (dung khi 1 bot quan ly nhieu may: `/msg @pccon ...`).
    machine_tag: str = "pc1"
    # Chi phan hoi lenh co @tag khop (True) hay moi lenh (False).
    require_tag: bool = False
    # Duong dan data dir (trong = tu dong).
    data_dir: str = ""
    install_dir: str = ""
    schema_version: int = 1

    # ---------------- phan bo tro ---------------- #
    pin_hash: str = ""
    pin_salt: str = ""
    machine_id: str = ""
    installed: bool = False

    # ---------------- tien ich ---------------- #

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def data_layout_dir(self) -> Path:
        if self.data_dir:
            return Path(self.data_dir)
        return data_dir(self.stealth.display_name)

    def install_layout_dir(self) -> Path:
        if self.install_dir:
            return Path(self.install_dir)
        return install_dir(self.stealth.display_name)

    # ---------------- PIN ---------------- #

    def set_pin(self, pin: str) -> None:
        pin = (pin or "").strip()
        if not pin:
            self.pin_hash = ""
            self.pin_salt = ""
            return
        self.pin_salt = secrets.token_hex(16)
        self.pin_hash = hashlib.sha256((self.pin_salt + pin).encode()).hexdigest()

    def check_pin(self, pin: str) -> bool:
        if not self.pin_hash:
            return False
        guess = hashlib.sha256((self.pin_salt + (pin or "").strip()).encode()).hexdigest()
        return secrets.compare_digest(guess, self.pin_hash)

    def has_pin(self) -> bool:
        return bool(self.pin_hash)

    # ---------------- quyen ---------------- #

    def is_allowed_chat(self, chat_id: int) -> bool:
        if not self.telegram.allowed_chat_ids:
            return False
        return int(chat_id) in {int(x) for x in self.telegram.allowed_chat_ids}


# --------------------------------------------------------------------------- #
# Load / save
# --------------------------------------------------------------------------- #

_DATACLASS_FIELDS = {
    "telegram": TelegramConfig,
    "stealth": StealthConfig,
    "notify": NotificationConfig,
    "curfew": CurfewConfig,
    "monitoring": MonitoringConfig,
    "power": PowerConfig,
}


def _deep_merge(base: Any, override: Any) -> Any:
    """Ghi de `base` bang `override`, giu cac key ma override khong co."""
    if isinstance(base, dict) and isinstance(override, dict):
        out = copy.deepcopy(base)
        for k, v in override.items():
            out[k] = _deep_merge(base.get(k), v) if k in base else copy.deepcopy(v)
        return out
    if override is None:
        return copy.deepcopy(base)
    return copy.deepcopy(override)


def defaults_dict() -> dict[str, Any]:
    return Config().to_dict()


def load_config(path: str | os.PathLike | None = None, disguise: str = "SysHealthAgent") -> tuple[Config, Path]:
    """Doc config.json; tra ve (Config, duong da dung).

    Thu tu tim: --config duoc chi dinh -> REMOTEPC_CONFIG -> data_dir/config.json.
    """
    if path:
        cfg_path = Path(path)
    else:
        env = os.environ.get("REMOTEPC_CONFIG")
        cfg_path = Path(env) if env else data_dir(disguise) / "config.json"

    data: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
        except (json.JSONDecodeError, OSError):
            # Config hong -> dung mac dinh, giu file loi de doi chung.
            try:
                cfg_path.with_suffix(".broken.json").write_text(
                    cfg_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
            except OSError:
                pass
            data = {}

    merged = _deep_merge(defaults_dict(), data)
    cfg = Config(
        telegram=_from_dict(TelegramConfig, merged["telegram"]),
        stealth=_from_dict(StealthConfig, merged["stealth"]),
        notify=_from_dict(NotificationConfig, merged["notify"]),
        curfew=_parse_curfew(merged["curfew"]),
        monitoring=_from_dict(MonitoringConfig, merged["monitoring"]),
        power=_from_dict(PowerConfig, merged["power"]),
        machine_tag=str(merged.get("machine_tag") or "pc1"),
        require_tag=bool(merged.get("require_tag", False)),
        data_dir=str(merged.get("data_dir") or ""),
        install_dir=str(merged.get("install_dir") or ""),
        schema_version=int(merged.get("schema_version", 1)),
        pin_hash=str(merged.get("pin_hash") or ""),
        pin_salt=str(merged.get("pin_salt") or ""),
        machine_id=str(merged.get("machine_id") or ""),
        installed=bool(merged.get("installed", False)),
    )
    if not cfg.machine_id:
        cfg.machine_id = make_machine_id()
    return cfg, cfg_path


def _from_dict(cls, raw: Any):
    """Tao dataclass tu dict, bo qua key la (vd key chu thich `_doc` nguoi dung them vao)."""
    if not isinstance(raw, dict):
        return cls()
    known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
    try:
        return cls(**known)
    except TypeError:
        return cls()


def _parse_curfew(raw: dict[str, Any]) -> CurfewConfig:
    windows_raw = raw.get("windows") or []
    windows: list[CurfewWindow] = []
    for w in windows_raw:
        if isinstance(w, dict):
            windows.append(_from_dict(CurfewWindow, w))
        elif isinstance(w, (list, tuple)) and len(w) >= 2:
            windows.append(CurfewWindow(start=str(w[0]), end=str(w[1])))
    base = {k: v for k, v in raw.items() if k in CurfewConfig.__dataclass_fields__ and k != "windows"}
    cfg = _from_dict(CurfewConfig, base)
    cfg.windows = windows
    return cfg


def save_config(cfg: Config, path: str | os.PathLike) -> Path:
    """Ghi config.json (atomic) va that chat quyen doc (chi SYSTEM/Admin)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".cfg-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    if IS_WINDOWS:
        try:
            from .stealth import lock_down_path  # import tre de tranh vong lap
            lock_down_path(p)
        except Exception:
            pass
    else:
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return p


def make_machine_id() -> str:
    """ID may: uu tien MachineGuid cua Windows, fallback hostname+random."""
    if IS_WINDOWS:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as k:
                guid = winreg.QueryValueEx(k, "MachineGuid")[0]
                return hashlib.sha256(str(guid).encode()).hexdigest()[:16]
        except OSError:
            pass
    try:
        raw = f"{socket.gethostname()}-{os.environ.get('USERNAME','')}-{secrets.token_hex(4)}"
    except Exception:
        raw = secrets.token_hex(8)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def set_by_path(cfg: Config, dotted: str, value: str) -> str:
    """`/config set curfew.enabled false` -> gan gia tri, tra ve mo ta."""
    parts = dotted.strip().split(".")
    obj: Any = cfg
    for part in parts[:-1]:
        obj = getattr(obj, part)
    key = parts[-1]
    if not hasattr(obj, key):
        raise KeyError(dotted)
    current = getattr(obj, key)
    setattr(obj, key, _coerce(value, current))
    return f"{dotted} = {getattr(obj, key)!r}"


def _coerce(value: str, current: Any) -> Any:
    if isinstance(current, bool):
        return value.strip().lower() in {"1", "true", "yes", "y", "on", "bat"}
    if isinstance(current, int):
        return int(value.strip())
    if isinstance(current, float):
        return float(value.strip())
    if isinstance(current, list):
        items: Iterable[str] = [v.strip() for v in value.strip().strip("[]").split(",") if v.strip()]
        if current and isinstance(current[0], int):
            return [int(v) for v in items]
        return list(items)
    return value.strip()
