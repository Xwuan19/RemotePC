"""Duong dan he thong dung chung cho toan bo agent.

Moi thu deu nam o 1 cho de de cai / go cai dat:
  Windows : C:\\Program Files\\<Disguise>\\     (code / exe)
            C:\\ProgramData\\<Disguise>\\       (config, state, log, IPC)
  Linux   : /opt/<disguise>/                   ~/.local/share/<disguise>/
  macOS   : /usr/local/opt/<disguise>/         ~/Library/Application Support/<disguise>/
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def _slug(name: str) -> str:
    """Ten thu muc an toan (khong co ky tu la)."""
    return "".join(c for c in name if c.isalnum() or c in "-_.").strip() or "SysHealthAgent"


def install_dir(disguise: str = "SysHealthAgent") -> Path:
    """Thu muc chua chuong trinh (can quyen admin de ghi)."""
    name = _slug(disguise)
    if IS_WINDOWS:
        pf = os.environ.get("ProgramFiles", r"C:\Program Files")
        return Path(pf) / name
    if IS_MACOS:
        return Path("/usr/local/opt") / name
    return Path("/opt") / name


def data_dir(disguise: str = "SysHealthAgent") -> Path:
    """Thu muc du lieu: config.json, state.json, events.jsonl, ipc/."""
    name = _slug(disguise)
    if IS_WINDOWS:
        pd = os.environ.get("ProgramData", r"C:\ProgramData")
        return Path(pd) / name
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / name
    return Path.home() / ".local" / "share" / name


class Layout:
    """Gom tat ca duong dan con cua 1 data_dir."""

    def __init__(self, base: Path):
        self.base = base
        self.config_file = base / "config.json"
        self.state_file = base / "state.json"
        self.events_file = base / "events.jsonl"
        self.log_file = base / "agent.log"
        self.ipc_dir = base / "ipc"
        self.outbox_dir = self.ipc_dir / "outbox"   # guardian -> uihost (lenh hien thi)
        self.inbox_dir = self.ipc_dir / "inbox"     # uihost -> guardian (ack / ket qua)
        self.shots_dir = base / "shots"
        self.pin_file = base / "pin.dat"

    def ensure(self) -> "Layout":
        for p in (self.base, self.ipc_dir, self.outbox_dir, self.inbox_dir, self.shots_dir):
            p.mkdir(parents=True, exist_ok=True)
        return self

    def as_dict(self) -> dict:
        return {k: str(v) for k, v in vars(self).items() if isinstance(v, Path)}


def repo_root() -> Path:
    """Thu muc goc cua source (dung khi chay truc tiep bang python, khong qua exe)."""
    return Path(__file__).resolve().parent.parent


def is_frozen() -> bool:
    """True khi dang chay tu file .exe do PyInstaller dong goi."""
    return getattr(sys, "frozen", False)


def executable_path() -> Path:
    """Duong dan file dang chay (exe neu frozen, neu khong la python + module)."""
    if is_frozen():
        return Path(sys.executable)
    return Path(sys.executable)


def python_command_for(module: str = "remotepc") -> list[str]:
    """Lenh de chay agent: exe neu frozen, con khong thi `python -m remotepc`."""
    if is_frozen():
        return [str(Path(sys.executable))]
    py = sys.executable or "python"
    return [py, "-m", module]
