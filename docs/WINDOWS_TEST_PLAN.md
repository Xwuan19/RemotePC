# Kế hoạch kiểm thử trên Windows

Tài liệu này dành cho **người thật lẫn AI agent** chạy kiểm thử trên một máy
Windows thật. Phần tự động nằm ở `scripts\test.bat`; phần phải làm bằng tay
nằm ở mục 8–10 bên dưới.

Bối cảnh ngắn: `AGENTS.md` (quy tắc an toàn) · `README.md` (toàn bộ dự án).

---

## Chuẩn bị

| Yêu cầu | Vì sao |
|---|---|
| Windows 10/11, **nên là máy ảo hoặc máy phụ** | Tool có khả năng tắt máy và khoá Task Manager |
| Python 3.9+ (64-bit), có trong `PATH` | Test chạy bằng `python -m unittest` |
| Tài khoản đang dùng là **Administrator** | Cần để gắn DENY ACE và `schtasks /create` |
| Có phiên đăng nhập thật, **không phải SSH/dịch vụ** | Session 0 không có desktop → không test được hiển thị |
| Không có việc gì đang làm dở | Test hiển thị sẽ làm nháy một hộp thoại |

Không cần `pip install` gì: tool chỉ dùng thư viện chuẩn. `mss` và `tkinter`
là tuỳ chọn — thiếu thì test tương ứng tự skip hoặc dùng fallback.

---

## Phần tự động

```bat
scripts\test.bat
```

Kết quả mong đợi: `>>> TAT CA XANH <<<`, exit code 0, báo cáo ở
`test-report.txt`.

### 1. Kích thước struct Win32

Xác nhận lại trên chính nền tảng sẽ chạy thật (Windows là LLP64):
`c_ulong` = 4 byte, `c_wchar` = 2 byte, và

| Struct | Kích thước | Header |
|---|---|---|
| `TRUSTEE_W` | 32 | accapi.h |
| `EXPLICIT_ACCESS_W` | 48 | accapi.h |
| `ACE_HEADER` | 4 | winnt.h |
| `ACCESS_DENIED_ACE` | 12 | winnt.h |
| `ACL_SIZE_INFORMATION` | 8 | accapi.h |
| `MEMORYSTATUSEX` | 64 | sysinfoapi.h |
| `LASTINPUTINFO` | 8 | winuser.h |
| `PROCESSENTRY32W` | 568 | tlhelp32.h |

Sai một con số ở đây nghĩa là WinAPI đọc nhầm bộ nhớ: hoặc không gắn được
DENY ACE, hoặc liệt kê process ra rác.

### 2. Thông tin hệ thống đọc thật

`memory_percent()` ra 1–100 (chứng minh `MEMORYSTATUSEX` + `GlobalMemoryStatusEx`
đúng) · `idle_seconds()` ≥ 0 (`LASTINPUTINFO` + `GetLastInputInfo`) ·
`find_pids_by_name("svchost.exe")` phải ra **nhiều hơn 1** PID (chứng minh
`PROCESSENTRY32W` đúng) · `lan_ip()` ra đúng dạng IPv4 · `local_accounts()`
phân biệt được admin/thường · `collect()` đủ hostname, OS, uptime.

### 3. Chặn End Task — mục quan trọng nhất

Test tạo một tiến trình dummy, gắn bảo vệ, rồi **thử tắt nó đúng cách con sẽ làm**:

1. `protect_process(pid, [tài khoản hiện tại], take_ownership=False)` phải trả `ok=True`
2. `deny_aces(pid)` phải có ACE loại DENY, mask chứa `PROCESS_TERMINATE`
   (0x0001), và SID đúng của tài khoản đó
3. `is_protected(pid)` phải `True`
4. `OpenProcess(PROCESS_TERMINATE)` + `TerminateProcess` phải **bị từ chối**
5. `taskkill /F /PID <pid>` phải fail với chữ *denied* và tiến trình vẫn sống

Hai lưu ý khi đọc kết quả:

- **`take_ownership=False`** là cố ý: giữ quyền `WRITE_DAC` để test dọn dẹp
  được. Bản cài đặt thật dùng `True` (đổi owner sang SYSTEM) — xem mục 9.
- Nếu test **skip** với thông báo về `SeDebugPrivilege`: ACE đã gắn đúng nhưng
  tiến trình test đang giữ đặc quyền debug nên nó vượt qua được DACL. Tài khoản
  **Standard của con không có đặc quyền này**, nên ngoài đời vẫn bị chặn.
  Đây là giới hạn của việc tự test bằng chính tài khoản admin.

Test `test_is_protected_is_false_before_protection` khoá một lỗi từng có:
`is_protected()` cũ chỉ "đếm số ACE" mà process nào cũng có ACE mặc định, nên
nó báo *đã bảo vệ* cho **mọi** tiến trình → `doctor` và `/protect` báo láo.

### 4. Scheduled task — Task Scheduler có chấp nhận XML không

Test sinh XML của cả 4 task, `schtasks /create` vào thư mục riêng
`RemotePCSelfTest\`, rồi **xoá ngay** (không đụng bản cài đặt thật):

- cả 4 task phải được chấp nhận
- `schtasks /query /xml` đọc ngược phải còn nguyên `Hidden=true`,
  `AllowHardTerminate=false`, `ExecutionTimeLimit=PT0S`, `UserId=S-1-5-18`,
  `RunLevel=HighestAvailable`
- task UI host phải dùng `GroupId=S-1-5-32-545` (không phải `UserId`)
- subscription event 1074 phải còn nguyên sau round-trip, không bị escape hai lần
- `schtasks /run` phải khởi động được task

XML được ghi **UTF-16** — `schtasks` từ chối UTF-8 có BOM.

### 5. ACL chặn đọc thư mục

`lock_down_path()` trên thư mục tạm chứa một `config.json` giả, sau đó:
`icacls` phải hiện `(DENY)` và **đọc file phải ném `PermissionError`**.
Đây là bằng chứng con không lấy được bot token.

Thư mục `ipc\inbox` và `shots` chỉ cho Users quyền *ghi*: test kiểm tra không
liệt kê được nội dung (admin thì vẫn thấy — đó là dự kiến).

Dọn dẹp bằng `icacls /remove:d` trong `tearDown`, kể cả khi test fail.

### 6. Hiển thị trên màn hình

- `message_box(timeout=1)` → **một hộp thoại nháy lên khoảng 1 giây** rồi tự
  tắt, hàm trả `ok=True`. Nếu trả về ngay lập tức (<0.3s) thì khả năng cao là
  nó không hiện thật.
- `screenshot()` → file ảnh >5 KB, magic bytes là PNG hoặc JPEG
- chạy `python -m remotepc uihost --config <tmp>` thành **tiến trình thật**,
  chờ `hello.json`, gửi job `MESSAGE` và đòi ack thành công

Nếu cả nhóm này skip vì "đang ở session 0" — đó chính là bằng chứng cho kiến
trúc 2 tiến trình: SYSTEM không vẽ được lên desktop người dùng.

### 7. Policy HKCU — **phải bật có chủ đích**

```bat
scripts\test.bat -Policy
```

Test ghi `DisableTaskMgr=1` vào HKCU của tài khoản hiện tại rồi gỡ ngay trong
`finally`. Mặc định **không chạy** vì nếu crash cứng giữa chừng, Task Manager
của tài khoản đó sẽ bị khoá.

**Nếu bị kẹt** (Task Manager báo bị vô hiệu hoá bởi quản trị viên):

```bat
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System" /v DisableTaskMgr /f
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System" /v DisableRegistryTools /f
```

Nếu policy bị ghi vào tài khoản con chứ không phải tài khoản hiện tại, thay
`HKCU` bằng `HKU\<SID-của-con>`, hoặc nạp hive:

```bat
reg load HKU\tmpchild "C:\Users\<tên-con>\NTUSER.DAT"
reg delete "HKU\tmpchild\Software\Microsoft\Windows\CurrentVersion\Policies\System" /v DisableTaskMgr /f
reg unload HKU\tmpchild
```

---

## Phần thủ công — test tự động cố ý không làm

Ba mục này xâm lấn thật nên phải có người quyết định.

### 8. Cài đặt thật

```bat
scripts\install.bat 2
```

Cần chuẩn bị trước: bot token từ @BotFather, chat ID của bố/mẹ, mã PIN 4 chữ
số. Kiểm tra sau khi cài:

- [ ] `remotepc doctor` báo cả 4 task ✅, config tồn tại, PIN đã đặt
- [ ] Task Scheduler hiện 4 task trong trạng thái Ready
- [ ] Bot nhắn 🟢 *máy vừa bật* (hoặc chạy `remotepc send "test"` để kiểm
      đường ra Telegram — lưu ý Telegram **đang bị chặn ở VN**, cần VPN hoặc
      khai `telegram.proxy`, xem README §7)
- [ ] `/status` trả về đúng tên máy, người dùng, IP
- [ ] `/msg Con đi ngủ đi` → hộp thoại hiện trên màn hình
- [ ] `/msg --full HẾT GIỜ` → phủ kín màn hình (cần `tkinter`)
- [ ] `/shutdown 60 --msg "Bố tắt máy nhé"` → Windows hiện đếm ngược **đúng
      với lời nhắn đó**, rồi `/abort` để huỷ
- [ ] `/screenshot` → nhận được ảnh
- [ ] `/lock` → khoá màn hình ngay

### 9. Thử End Task từ tài khoản Standard của con — bằng chứng quyết định

Mục 3 ở trên tự test bằng chính tài khoản admin nên chưa trọn vẹn. Phải thử
từ đúng góc nhìn của con:

1. Tạo/tài khoản Standard: `net user KidTest MatKhau123 /add`
   (không thêm vào nhóm Administrators)
2. Đăng nhập bằng `KidTest`
3. Mở Task Manager → tìm `SysHealthAgent.exe`
4. **Kỳ vọng:** vẫn *nhìn thấy* tiến trình (tool không hứa ẩn — cần driver
   kernel), nhưng bấm **End task → "Access is denied"**
5. Kiểm tra thêm: `taskkill /F /IM SysHealthAgent.exe` phải báo lỗi
6. Thử mở Task Manager sau khi policy đã áp cho `KidTest` → phải bị chặn,
   trong khi tài khoản admin của bố/mẹ vẫn mở bình thường
7. Thử xoá thư mục `C:\ProgramData\<TênNguỵTrang>` → phải báo không có quyền
8. Thử tắt task trong Task Scheduler → phải bị từ chối
9. Chờ ≥1 phút: nếu bằng cách nào đó guardian chết, watchdog phải bật lại và
   bố/mẹ nhận 🚨

Ghi chú về `take_ownership`: bản cài thật đổi owner tiến trình sang SYSTEM.
Nếu bước này còn `Access denied` cả với tài khoản admin thì đó là **đúng thiết
kế** — chỉ SYSTEM mới gỡ được. Chủ máy luôn gỡ được bằng
`scripts\uninstall.bat force`.

### 10. Gỡ cài đặt và phục hồi

```
/uninstall --pin <mã>       :: từ Telegram
scripts\uninstall.bat force  :: tại máy, cần Administrator
```

- [ ] Cả 4 task biến mất
- [ ] Thư mục cài đặt và dữ liệu bị xoá (hoặc được giữ lại nếu chọn không purge)
- [ ] Policy `DisableTaskMgr` của tài khoản con **đã được gỡ** — đăng nhập lại
      bằng tài khoản con và mở Task Manager để xác nhận
- [ ] ACL deny trên các thư mục không còn làm kẹt file nào
- [ ] Máy khởi động lại bình thường, không còn tiến trình lạ

Nếu gỡ thất bại giữa chừng, chạy lại `scripts\uninstall.bat force` — nó idempotent.

---

## Nếu test fail

1. Gửi nguyên `test-report.txt` — nó chứa cả thông tin môi trường và output
   đầy đủ.
2. Ghi rõ **chạy có quyền Admin hay không**, và **có phải phiên có desktop
   hay không** — hai yếu tố này quyết định phần lớn kết quả skip/fail.
3. Đừng nới lỏng assertion để "cho xanh". Nếu một assertion sai, hãy chứng
   minh bằng tài liệu của Microsoft hoặc bằng output thật.
