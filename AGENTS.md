# AGENTS.md — hướng dẫn cho AI agent chạy kiểm thử trên Windows

File này dành cho agent (Antigravity, Copilot, Cursor…) vừa clone repo về một
máy **Windows thật**. Mục tiêu: xác nhận tool hoạt động thật, không chỉ "test
đơn vị xanh".

Toàn bộ bối cảnh dự án nằm ở `README.md`. Kế hoạch kiểm thử chi tiết từng mục
nằm ở `docs/WINDOWS_TEST_PLAN.md`.

---

## Quy tắc an toàn — đọc trước khi làm bất cứ gì

Đây là tool **quản lý máy của con**, có khả năng tắt máy. Khi chạy kiểm thử:

1. **KHÔNG BAO GIỜ** gọi `actions.shutdown()` / `restart` thật mà không có
   tiến trình giả chắn trước. Test đã tự lo việc này — đừng "sửa" để nó chạy
   lệnh thật.
2. **KHÔNG** chạy `scripts\install.bat` trừ khi được người dùng yêu cầu rõ ràng
   bằng lời. Cài đặt ghi scheduled task, đổi ACL, và khoá Task Manager của tài
   khoản con.
3. **KHÔNG** chạy test policy HKCU (`-Policy`) nếu không được yêu cầu: nó ghi
   `DisableTaskMgr` vào registry. Test có `finally` để gỡ, nhưng nếu crash cứng
   thì phải gỡ tay (lệnh ở `docs/WINDOWS_TEST_PLAN.md` mục 7).
4. Chạy trên **máy ảo hoặc máy phụ** nếu có thể. Nếu buộc phải chạy trên máy
   thật, bảo đảm tài khoản đang dùng là Administrator và không có việc gì đang
   làm dở.
5. Đừng commit `test-report.txt`, `config.json`, hay bất kỳ file nào trong
   `.gitignore`.

---

## Việc cần làm, theo thứ tự

### Bước 1 — Chạy bộ kiểm tra một lệnh

```bat
scripts\test.bat
```

File này **dành cho agent/shell**: không `pause`, không bật hộp thoại UAC,
không mở cửa sổ mới — stdout bắt được và exit code trả về chuẩn. (Bản
`scripts\test-interactive.bat` là cho người click đôi, **đừng** gọi nó vì nó
sẽ chờ UAC và chờ bấm phím.)

**Chạy từ một shell đã có quyền Administrator** để test được đầy đủ. Script
chạy 3 bước:

| Bước | Nội dung | Kết quả mong đợi |
|---|---|---|
| 1 | `python -m unittest discover -s tests -t .` | `OK` — khoảng **222 test**, trong đó 37 test sống bị skip nếu không phải Windows |
| 2 | `python -m unittest tests.test_windows_live -v` | `OK` — các test sống **chạy thật**, không skip hàng loạt |
| 3 | `python -m pyflakes remotepc tests` | không in gì, exit code 0 |

Dòng cuối phải là `>>> TAT CA XANH <<<` và exit code 0.
Báo cáo đầy đủ được ghi ra `test-report.txt`.

**Nếu Python chưa cài:** script thoát với mã 2 và in hướng dẫn. Cài Python
3.9+ từ python.org, tick *Add python.exe to PATH*, chạy lại.

### Bước 2 — Đọc và phân loại kết quả

Mỗi test skip đều **in lý do**. Hãy phân biệt ba trường hợp, đừng gộp chung:

- **SKIP vì thiếu quyền Admin** → chạy lại từ một shell **đã** elevated
  (*Run as administrator*), không phải lỗi code.
- **SKIP vì đang ở session 0** (chạy qua SSH/dịch vụ, không có desktop) →
  các test hiển thị không chạy được. Cần một phiên đăng nhập thật có màn hình.
- **FAIL** → lỗi thật. Đây là thứ cần báo cáo và sửa.

### Bước 3 — Những mục bắt buộc phải kiểm bằng tay

Có những thứ test tự động **cố ý không làm** vì quá xâm lấn. Xem
`docs/WINDOWS_TEST_PLAN.md` — đặc biệt các mục 8, 9, 10 (cài đặt thật, thử
End Task từ tài khoản Standard của con, thử gỡ cài đặt).

---

## Các mốc quan trọng nếu phải sửa code

- **`--config` phải được chuyển tiếp xuống tiến trình con.** Đây là lỗi từng
  làm hỏng toàn bộ `/msg`, `/lock`, `/screenshot`: guardian ghi job vào
  `ProgramData\<TênNguỵTrang>\ipc\` còn uihost đọc `ProgramData\SysHealthAgent\ipc\`.
  Nếu đổi cách tiến trình con tìm config, phải sửa **đồng thời**
  `_child_argv()` trong `remotepc/__main__.py` **và** `bot._start_uihost()`.
- **DWORD trong struct ctypes phải là `c_uint32`, không phải `c_ulong`.**
  Windows là LLP64 (`ULONG` = 4 byte), Linux/macOS là LP64 (`unsigned long` =
  8 byte). Dùng `c_ulong` làm struct lệch kích thước tuỳ hệ điều hành.
  `tests/test_windows_payloads.py` và `tests/test_windows_live.py` khoá điều này.
- **`is_protected()` phải duyệt từng ACE**, không được "đếm số ACE" — process
  nào cũng có sẵn ACE mặc định nên cách đếm luôn trả về True (báo láo cho bố mẹ).
- **Tiến trình SYSTEM không vẽ được lên desktop người dùng.** Mọi thứ liên quan
  hiển thị phải đi qua UI host. Đừng "gộp lại cho đơn giản".

---

## Cấu trúc nhanh

```
remotepc/
├── bot.py             Agent: long-poll Telegram, định tuyến lệnh
├── monitor.py         vòng giám sát: boot/login/idle/giờ cấm
├── curfew.py          máy trạng thái giờ giới nghiêm (thuần tuý, test kỹ nhất)
├── commands.py        parser lệnh + alias tiếng Việt
├── telegram_client.py Bot API bằng urllib (retry, 429, sendPhoto, proxy)
├── actions.py         tắt máy, hộp thoại, overlay, chụp màn hình, liệt kê process
├── uihost.py          tiến trình hiển thị trong session của con
├── ipc.py             hàng đợi job qua file (outbox/inbox/shots)
├── stealth.py         Win32: DENY ACE, ACL, policy HKCU, ẩn console
├── installer.py       schtasks XML, icacls, cài/gỡ/doctor
├── watchdog.py        hồi sinh guardian/uihost, tạo lại task bị xoá
└── system_info.py     thông tin máy, process, session, bộ nhớ
```

Hai tiến trình khi chạy thật:

| Tiến trình | Quyền | Trigger | Nhiệm vụ |
|---|---|---|---|
| Guardian (`SysHealthAgent.exe`) | SYSTEM | Boot | nghe Telegram, giám sát, tắt máy |
| UI host (`SysHealthUI.exe`) | session của con | Logon | hiện thông báo, khoá màn hình, chụp ảnh |
| Watchdog | SYSTEM | mỗi 1 phút | hồi sinh, tạo lại task, báo can thiệp |
| Telemetry | SYSTEM | EventID 1074 | báo "máy đang tắt" kèm lý do |

---

## Báo cáo lại cho người dùng

Khi xong, tóm tắt theo đúng mẫu này để người dùng đối chiếu nhanh:

```
MÔI TRƯỜNG
  Windows <phiên bản> build <số>, <64/32>-bit, Python <số>, Admin: CÓ/KHÔNG

KẾT QUẢ
  Bước 1 test đơn vị : <số> test, PASS/FAIL
  Bước 2 test sống   : <số> chạy thật / <số> skip, PASS/FAIL
  Bước 3 pyflakes    : PASS/FAIL

ĐÃ XÁC NHẬN HOẠT ĐỘNG THẬT
  - DENY ACE chặn End Task: <kết quả + error code>
  - schtasks chấp nhận XML: <4 task?>
  - icacls chặn đọc thư mục: <kết quả>
  - MessageBox hiện lên: <có/không, mất bao lâu>
  - Chụp màn hình: <kích thước file ảnh>
  - PROCESSENTRY32W đọc đúng: <tìm thấy bao nhiêu svchost>

CÒN SKIP (và lý do)
  - ...

LỖI TÌM THẤY (nếu có)
  - <tên test> : <thông báo> → nguyên nhân nghi ngờ → đã sửa gì
```

Nếu phát hiện lỗi: sửa, chạy lại `scripts\test.bat` cho tới xanh, rồi commit
với message tiếng Việt giải thích **nguyên nhân** chứ không chỉ triệu chứng.
