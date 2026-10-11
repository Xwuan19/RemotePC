"""RemotePC - Agent dieu khien may tinh tu xa thong qua bot Telegram.

Tool danh cho phu huynh quan ly may tinh cua con minh:
  * Bao ve bot Telegram khi may vua bat.
  * Canh bao khi con dung may qua gio gioi nghiem (mac dinh 23:30 - 00:00).
  * Tat / khoi dong lai / khoa may tu xa.
  * Hien thi loi nhan tuy chinh cua bo me tren man hinh con.
  * Lop "nguy trang" + tu bao ve de con khong the End Task trong Task Manager.

Chi dung tren may tinh ma ban la chu so huu / nguoi quan tri hop phap.
"""

__version__ = "1.0.0"
__author__ = "RemotePC"

APP_NAME = "RemotePC"

# Ten "nguy trang" mac dinh (co the doi trong config.json).
DEFAULT_DISGUISE_NAME = "SysHealthAgent"
DEFAULT_UIHOST_NAME = "SysHealthUI"
