# RemotePC — Điều khiển & giám sát máy tính của con qua bot Telegram

Một tool nhỏ chạy **nền trên chính chiếc máy cần quản lý**, nhận lệnh từ **bot Telegram**
trên điện thoại của bố/mẹ:

| Bố/mẹ muốn | Tool làm gì |
|---|---|
| Biết khi nào con bật máy | 🟢 nhắn về Telegram ngay lúc Windows khởi động xong (kèm tên người dùng, IP, giờ) |
| Nhắc con đi ngủ | ⚠️ tự nhắn cảnh báo lúc **23:00**, **23:20** và hiện thông báo trên màn hình con lúc **23:30** |
| Nói một câu với con từ xa | 💬 `/msg Con tắt máy đi ngủ nhé` → hiện hộp thoại (hoặc phủ kín màn hình) trên máy con |
| Tắt máy từ xa | 🔴 `/shutdown 60 --msg "Bố tắt máy nhé"` |
| Con cố tắt tool | 🛡️ End Task → **Access denied**, 60 giây sau tool tự chạy lại, bố/mẹ nhận cảnh báo |

> ⚠️ **Phạm vi sử dụng.** Tool này dành cho **máy tính của chính bạn / của con chưa thành niên
> mà bạn là người giám hộ hợp pháp**, và bạn phải là người cài đặt nó (cần quyền Administrator).
> Đừng dùng để theo dõi máy của người khác, máy của công ty, hay bất kỳ ai không biết và
> không đồng ý. Mọi thông báo đều nói rõ "Thông báo từ bố/mẹ" — đây là công cụ *quản lý*,
> không phải công cụ *rình mò*.

---

## 1. Cách hoạt động

```
        ĐIỆN THOẠI BỐ/MẸ                            MÁY TÍNH CỦA CON
   ┌────────────────────────┐                ┌──────────────────────────────────────┐
   │  Telegram  (@BotFather)│                │  GUARDIAN  (SysHealthAgent.exe)      │
   │                        │   long-poll    │  chạy dưới SYSTEM, session 0         │
   │  /status  /msg  /shutdown ◄───────────► │  • nghe lệnh Telegram                │
   │                        │  https 443     │  • theo dõi boot / login / giờ cấm   │
   └────────────────────────┘                │  • tắt máy, khoá máy, chụp màn hình  │
                                             │  • KHÔNG hiện được UI (session 0)    │
                                             └───────┬──────────────▲───────────────┘
                                                     │ job file     │ ack / ảnh
                                                     ▼              │
                                             ┌──────────────────────────────────────┐
                                             │  UI HOST (SysHealthUI.exe)           │
                                             │  chạy trong session của CON          │
                                             │  • hiện hộp thoại / phủ toàn màn hình│
                                             │  • chụp màn hình, báo app đang mở    │
                                             └──────────────────────────────────────┘
                                                     ▲
                                                     │ mỗi 60 giây: "còn sống không?"
                                             ┌───────┴──────────────────────────────┐
                                             │  WATCHDOG (SYSTEM, mỗi 1 phút)       │
                                             │  guardian chết → bật lại             │
                                             │  task bị xoá   → tạo lại từ bản sao  │
                                             │  uihost chết   → bật lại + báo bố/mẹ │
                                             └──────────────────────────────────────┘
```

Vì sao phải tách 2 tiến trình? Windows **cô lập session**: một tiến trình chạy dưới
`SYSTEM` (session 0) không thể vẽ hộp thoại lên màn hình người dùng. Nên:

- **Guardian** giữ mọi thứ quan trọng (bot token, logic, quyền tắt máy) và chạy dưới SYSTEM
  → tài khoản thường của con **không có quyền đụng tới**.
- **UI host** chỉ làm mỗi việc hiển thị, chạy dưới chính tài khoản của con → con *có thể*
  End Task nó, nhưng (a) guardian phát hiện và bật lại trong vài giây, (b) bố/mẹ nhận cảnh báo,
  (c) lệnh tắt máy vẫn chạy bình thường vì việc đó do guardian làm.

---

## 2. Lớp "nguy trang" & tự bảo vệ

| Việc con có thể thử | Kết quả |
|---|---|
| Task Manager → End task `SysHealthAgent.exe` | ❌ **Access is denied** — tiến trình thuộc về SYSTEM, và bị gắn quyền `DENY PROCESS_TERMINATE` cho tài khoản con |
| `taskkill /f /im SysHealthAgent.exe` trong cmd | ❌ Access denied (như trên) |
| Mở Task Manager | ❌ Bị chặn bằng policy `DisableTaskMgr` **chỉ trong HKCU của tài khoản con** (bố/mẹ vẫn mở bình thường) |
| Mở RegEdit để xoá policy | ❌ `DisableRegistryTools` cũng chỉ áp cho tài khoản con |
| Task Scheduler → Delete task | ❌ File task trong `C:\Windows\System32\Tasks` bị `icacls /deny` với tài khoản con |
| Xoá/sửa `config.json`, code, exe | ❌ ACL: chỉ SYSTEM + Administrators có quyền; Users bị deny `R,W,D` |
| Đọc ảnh chụp màn hình trong `ProgramData` | ❌ Thư mục `shots\` chỉ cho Users quyền *tạo* file, không cho đọc/liệt kê |
| Chặn agent tự khởi động (msconfig / Startup) | ❌ Agent khởi động bằng **Scheduled Task** (BootTrigger + LogonTrigger), không nằm trong mục Startup |
| Đổi giờ máy để lách giờ giới nghiêm | ⚠️ Vẫn bị phát hiện: mốc cảnh báo đã bắn sẽ không bắn lại, và bố/mẹ có thể xem uptime/giờ máy qua `/status` |
| Giết UI host để không thấy thông báo | ⚠️ Được, nhưng watchdog bật lại sau ≤60s và **bố/mẹ nhận cảnh báo can thiệp** |
| Vào Safe Mode | ⚠️ Task không chạy ở Safe Mode. Đây là giới hạn của mọi phần mềm dạng này (xem mục 9) |
| Nhìn thấy tiến trình trong Task Manager | ✅ **Có** — muốn ẩn hoàn toàn phải có driver kernel. Tool đi theo hướng *"nhìn thấy nhưng không tắt được"* |

**3 mức bảo vệ** (`stealth.level` trong `config.json`, hoặc `install --level N`):

| Mức | Nội dung |
|---|---|
| `0` | Không bảo vệ gì. Chỉ dùng khi debug (`remotepc run --no-stealth`) |
| `1` | *(mặc định)* Ẩn cửa sổ console · chạy dưới SYSTEM · 4 scheduled task tự khởi động · ACL khoá file · watchdog tự hồi sinh |
| `2` | Tất cả mức 1 **+** `DENY PROCESS_TERMINATE` cho từng tài khoản con **+** đổi chủ sở hữu tiến trình sang SYSTEM **+** chặn Task Manager/RegEdit của tài khoản con |

---

## 3. Cài đặt (Windows 10/11)

### Bước 1 — Tạo bot Telegram (2 phút, trên điện thoại của bố/mẹ)

1. Mở Telegram, tìm **@BotFather** → gửi `/newbot`
2. Đặt tên hiển thị (vd `Bố quản lý PC`) và username kết thúc bằng `bot` (vd `bo_quan_ly_pc_bot`)
3. BotFather trả về **token** dạng `123456789:AAHdqTcvCH1vGW...` → **copy lại**
4. (Nên làm) Vào bot vừa tạo → *Bot Settings → Group Privacy → Turn on*, và **không** cho ai
   khác biết token. Ai có token thì điều khiển được máy con.

### Bước 2 — Lấy `chat_id` của bố/mẹ

Nhắn cho bot một tin bất kỳ (`hi`), rồi trên máy con chạy:

```bat
python -m remotepc find-chat --add
```

Lệnh sẽ in ra `chat_id` (số âm nếu là nhóm, vd `-1001234567890`) và tự thêm vào cấu hình.
Hoặc bỏ qua bước này và dùng cách ghép nối ở Bước 4.

### Bước 3 — Cài lên máy con (1-Click)

**Cách A — Tiện lợi nhất (Tự động toàn bộ từ source code):**

1. Tải source code / giải nén thư mục `RemotePC` trên máy tính của con.
2. Click đúp vào file **`setup.bat`** ở ngay thư mục gốc (hoặc chuột phải chọn *Run as administrator*).
   - Nếu máy con **chưa có Python**: Script sẽ **tự động tải và cài đặt Python 3.11** (bao gồm PATH, tkinter, pip).
   - Tự động kiểm tra và cài đặt thư viện cần thiết (`mss`).
   - Hướng dẫn bạn dán **Bot Token** Telegram và mã PIN bảo vệ (mặc định: `1234`).
   - Tự động tạo 4 Scheduled Task chạy ngầm, áp chính sách bảo mật cho tài khoản của con và khởi động dịch vụ.

**Cách B — Đóng gói file .exe trước:**

```bat
:: Trên máy BẤT KỲ có Python:
scripts\build.bat
:: → tạo dist\SysHealthAgent.exe và dist\SysHealthUI.exe

:: Copy 2 file đó + thư mục RemotePC sang máy con, rồi chạy (Administrator):
SysHealthAgent.exe install --level 2
```


### Bước 4 — Ghép nối (nếu chưa có `chat_id`)

Từ điện thoại, nhắn cho bot:

```
/pair 1234
```

(`1234` là PIN bạn đặt ở bước cài.) Bot trả về *"✅ GHÉP NỐI thành công"* và từ đó chat này
được phép điều khiển. Nếu **chưa đặt PIN**, lệnh `/pair` không cần tham số.

### Bước 5 — Thử ngay

```
/status          → trạng thái máy, uptime, giờ giới nghiêm
/msg Con thử đọc dòng này nhé
/shutdown 60 --msg "Bố tắt máy thử nhé"     (rồi /abort để huỷ)
```

---

## 4. Bộ lệnh Telegram

Thêm `@tên_máy` sau lệnh nếu một bot quản lý nhiều máy (vd `/msg @pc2 ...`).
Gửi `/msg @all ...` để nói với tất cả các máy.

### 💬 Nhắn cho con
| Lệnh | Ý nghĩa |
|---|---|
| `/msg Con ăn cơm đi` | Hộp thoại Windows (topmost, có tiếng *bíp*) |
| `/msg --full Đã 12h đêm, tắt máy ngay` | **Phủ kín màn hình**, chữ to, có nút "Con đã hiểu" |
| `/msg --title "Bố nói" --style error --timeout 30 ...` | Đổi tiêu đề / icon / tự đóng sau 30s |

`--style`: `info` · `warn` · `error` · `question` — `--timeout`: giây (0 = chờ con bấm OK)

### 🔴 Điều khiển nguồn *(cần PIN)*
| Lệnh | Ý nghĩa |
|---|---|
| `/shutdown` | Tắt máy sau 30s (mặc định) *(cần PIN)* |
| `/shutdown 120 --msg "Bố tắt máy nhé"` | Tắt sau 2 phút, hiện lời nhắn trên máy con |
| `/restart 60` | Khởi động lại *(cần PIN)* |
| `/lock` | Khoá màn hình ngay |
| `/logoff` | Đăng xuất tài khoản của con *(cần PIN)* |
| `/kill game.exe` | Tắt một ứng dụng cụ thể *(cần PIN)* |
| `/abort` | **Huỷ** lệnh tắt máy đang đếm ngược *(không cần PIN — để huỷ được ngay)* |

### 📊 Theo dõi
| Lệnh | Ý nghĩa |
|---|---|
| `/status` | Máy, người dùng, uptime, IP, **hôm nay con dùng máy bao lâu**, khung giờ cấm |
| `/screenshot` | Chụp màn hình gửi về |
| `/apps` | 15 tiến trình nặng nhất + cửa sổ con đang mở |
| `/log 30` | 30 sự kiện gần nhất (boot, login, vi phạm giờ, lệnh đã chạy…) |
| `/machines` | Máy này tên gì, ID gì, bật từ lúc nào |
| `/uihost` | Thành phần hiển thị còn sống không |

### ⏰ Giờ giới nghiêm *(đổi thì cần PIN, xem thì không)*
| Lệnh | Ý nghĩa |
|---|---|
| `/curfew` | Xem toàn bộ khung giờ hiện tại |
| `/curfew on` · `/curfew off` | Bật/tắt |
| `/curfew set 23:30 00:00` | Đổi khung giờ (mặc định **23:30 → 00:00**) |
| `/curfew set 22:00 06:00 Cuoi tuan` | Đổi giờ + đặt tên khung |
| `/curfew warn 60,30,10` | Báo trước 60 / 30 / 10 phút |
| `/curfew action popup` | Khi **vào** giờ: `none` / `notify` / `popup` / `lock` |
| `/curfew end shutdown` | Khi **hết** giờ: `none` / `notify` / `lock` / `logoff` / `shutdown` |
| `/curfew days t2,t3,t4,t5,t6` | Chỉ áp dụng các ngày trong tuần (`t2`…`cn`, hoặc `mon-fri`) |
| `/curfew auto 30 60` | Con vẫn dùng máy **30 phút** trong giờ cấm → tự tắt sau 60s |
| `/curfew add 13:00 14:00 Trưa` | Thêm khung giờ thứ hai (giờ nghỉ trưa) |
| `/curfew del 2` | Xoá khung số 2 |
| `/curfew text "Ngủ đi con, mai còn đi học"` | Lời nhắn hiện trên màn hình con |

### ⚙️ Hệ thống
| Lệnh | Ý nghĩa |
|---|---|
| `/help` | Danh sách lệnh |
| `/unlock 1234` | Mở khoá PIN **15 phút** cho chat này (khỏi phải gõ `--pin` liên tục) |
| `/pin 1234 5678` | Đổi PIN |
| `/config show` | Xem cấu hình (token đã che) |
| `/config set notify.on_idle true` | Sửa một khoá bất kỳ |
| `/reload` | Nạp lại `config.json` sau khi sửa tay |
| `/protect` | Áp lại quyền chống End Task |
| `/uninstall --pin 1234` | Gỡ sạch agent khỏi máy |

Lệnh cần PIN có thể nhận PIN theo 2 cách: `/unlock <mã>` một lần, hoặc thêm `--pin <mã>`
vào cuối từng lệnh.

**Nếu bạn bỏ qua bước đặt PIN** thì các lệnh trên vẫn chạy mà không hỏi gì (chat của bạn
vẫn là cổng chính). Agent sẽ **chủ động nhắn cảnh báo** về bot lúc khởi động và nhắc lại
trong `/status`, vì rủi ro rất thực tế là con mượn được điện thoại của bạn. Đặt PIN bằng:

```
/pin 4821
```

---

## 5. Tin nhắn bot tự gửi về

| Khi nào | Nội dung |
|---|---|
| Máy vừa bật | 🟢 **MÁY VỪA ĐƯỢC BẬT** — tên máy, người dùng, giờ bật, IP LAN, IP công khai, phiên bản Windows |
| Có người đăng nhập | 👤 **CÓ NGƯỜI ĐĂNG NHẬP** — tài khoản nào, lúc mấy giờ |
| Máy sắp tắt | 🔴 **MÁY ĐANG TẮT** — lý do + tiến trình nào ra lệnh (đọc từ Event Log 1074) |
| Trước giờ cấm 30/10 phút | ⚠️ **SẮP TỚI GIỜ GIỚI NGHIÊM** — còn bao nhiêu phút, con đang mở app gì |
| Đúng 23:30 | 🌙 **ĐÃ VÀO GIỜ GIỚI NGHIÊM** — kèm ảnh màn hình nếu bật `screenshot_on_violation` |
| 00:00 | ⏰ **HẾT GIỜ GIỚI NGHIÊM** |
| Con vẫn dùng máy quá giờ | 🔴 **TỰ ĐỘNG TẮT MÁY** (nếu bật `auto_shutdown_after_minutes`) |
| Con rời máy 30 phút | 😴 **CON ĐÃ RỜI MÁY** (nếu bật `notify.on_idle`) |
| Có ai đó phá agent | 🚨 **CẢNH BÁO CAN THIỆP** — task bị xoá / guardian bị tắt / UI host bị tắt liên tục |
| Chat lạ nhắn cho bot | 🚨 báo về số `chat_id` lạ đó |

---

## 6. Cấu hình nâng cao

File: `C:\ProgramData\SysHealthAgent\config.json` (xem [`config.example.json`](config.example.json)
cho bản đầy đủ có chú thích). Sửa xong nhắn `/reload` cho bot.

```jsonc
{
  "machine_tag": "pc-con-trai",     // dùng khi 1 bot quản lý nhiều máy
  "require_tag": false,             // true = bắt buộc mọi lệnh phải có @tên_máy
  "stealth": {
    "display_name": "SysHealthAgent",   // tên tiến trình + tên task + tên thư mục
    "description": "System Health Telemetry Agent",
    "level": 2,
    "deny_terminate_accounts": ["Kid"]  // để trống = tự dò mọi tài khoản không phải Admin
  },
  "curfew": {
    "windows": [
      { "name": "Ngày thường", "start": "23:30", "end": "00:00",
        "days": ["t2","t3","t4","t5"], "warn_before_minutes": [30, 10],
        "on_start": "popup", "on_end": "notify" },
      { "name": "Cuối tuần", "start": "00:30", "end": "07:00",
        "days": ["t6","t7"], "warn_before_minutes": [30], "on_start": "lock" }
    ],
    "screenshot_on_violation": true,   // gửi kèm ảnh màn hình khi vi phạm
    "auto_shutdown_after_minutes": 30, // vẫn dùng máy 30' trong giờ cấm → tắt
    "auto_shutdown_delay": 60
  },
  "notify": {
    "on_boot": true, "on_login": true, "on_shutdown": true,
    "on_idle": true, "idle_minutes": 45,
    "heartbeat_minutes": 0             // >0 = cứ N phút báo "agent còn sống" một lần
  }
}
```

Đổi tên nguỵ trang: sửa `stealth.display_name` **trước khi** cài (vd `RuntimeBrokerHelper`)
rồi chạy lại `install`. Tên này được dùng cho tiến trình, thư mục và cả 4 scheduled task.

---

## 7. ⚠️ Telegram bị chặn ở Việt Nam — bắt buộc phải đọc

Từ giữa 2025 các nhà mạng Việt Nam **chặn Telegram**. Agent nói chuyện với
`api.telegram.org` qua cổng 443, nên nếu máy con không tới được Telegram thì
**mọi thông báo đều không gửi đi được** (giờ giới nghiêm vẫn tính đúng trong máy,
nhưng bố/mẹ không nhận tin và không ra lệnh được).

Ba cách xử lý, chọn một:

**Cách 1 — VPN hệ thống trên máy con (đơn giản nhất)**

Cài một VPN cho *toàn hệ thống* (không phải chỉ trong trình duyệt):
`Settings → Network & Internet → VPN → Add a VPN connection`, hoặc dùng client
của nhà cung cấp. Agent sẽ tự đi qua VPN vì nó dùng kết nối hệ thống.

**Cách 2 — Khai báo proxy cho agent (khuyên dùng nếu bạn đã có v2rayN/Clash)**

```jsonc
// C:\ProgramData\SysHealthAgent\config.json
"telegram": {
  "bot_token": "...",
  "proxy": "http://127.0.0.1:1080"     // cổng SOCKS/HTTP local của client VPN
}
```

hoặc đặt biến môi trường `HTTPS_PROXY` cho tài khoản SYSTEM. Xong thì nhắn
`/reload` cho bot (hoặc `remotepc run --once` để thử). Bước `init` cũng hỏi
mục này ngay từ đầu.

**Cách 3 — Chạy agent trên một máy/VPS không bị chặn**

Vì logic nằm trong máy con nên cách này không áp dụng trực tiếp được; nếu bạn
có VPS ở nước ngoài, hãy cho VPN của máy con kết nối ra VPS đó.

Kiểm tra kết nối từ máy con:

```bat
curl -I https://api.telegram.org
:: hoặc
python -m remotepc send "test ket noi"
```

Nếu `send` báo ✅ thì agent hoạt động được.

---

## 8. Chạy thử / phát triển

```bash
python -m remotepc init                     # wizard tạo config
python -m remotepc run --no-stealth         # chạy nền trước mặt, log ra console, Ctrl+C để dừng
python -m remotepc run --once               # chạy 1 vòng rồi thoát (test thông báo boot)
python -m remotepc popup "Thử thông báo"    # xem hộp thoại trông thế nào
python -m remotepc popup --full --timeout 10 "Thử phủ toàn màn hình"
python -m remotepc doctor                   # tự kiểm tra cài đặt + lớp bảo vệ
python -m remotepc watchdog -v              # chạy 1 vòng watchdog
python -m remotepc status                   # in trạng thái ra terminal
```

**Test** — 222 test, chạy trong ~27 giây, không cần mạng, không cần Windows:

```bash
python -m unittest discover -s tests -t . -v
python -m pyflakes remotepc/*.py tests/*.py     # 0 lỗi
```

| File | Kiểm tra gì |
|---|---|
| `test_curfew.py` | Khung giờ vắt qua nửa đêm, theo thứ trong tuần, mốc báo trước không bắn trùng, phục hồi đúng sau khi agent khởi động lại giữa chừng |
| `test_commands.py` | Phân tích `/msg --full --title "..." nội dung`, alias tiếng Việt, `@tag`, `1m30` → 90s |
| `test_monitor.py` | Báo boot/login/idle, chuỗi 23:00 → 23:30 → 00:00, tự tắt máy khi quá giờ |
| `test_agent.py` | Từng lệnh một: PIN, ghép nối `/pair`, định tuyến sang UI host khi ở session 0 |
| `test_actions.py` | argv thật của lệnh Windows: `shutdown /s /t 45 /f /c`, `/r` khi restart, `/a` khi huỷ, `/l` khi đăng xuất, `taskkill /IM\|/PID`, clamp delay, parse `tasklist` CSV |
| `test_config.py` | Merge mặc định với config cũ, config hỏng, băm/kiểm PIN, `set key=value`, bỏ qua key lạ, tuỳ chọn proxy |
| `test_ipc.py` | Hàng đợi job: ack, hết hạn, lỗi handler, job chỉ chạy một lần |
| `test_e2e.py` | **Server Telegram giả lập** trong máy: tin nhắn, ảnh multipart, retry/429, proxy |
| `test_process_ipc.py` | **Bật `uihost` thành process riêng** → guardian ↔ uihost nói chuyện được thật; và khi scheduled task hỏng thì guardian **tự spawn** uihost vẫn cứu được `/msg` |
| `test_full_stack.py` | **Bật cả agent thành process riêng** (`python -m remotepc run`) → Telegram thật → lệnh → trả lời; lệnh `shutdown` của hệ điều hành được thay bằng script giả để test không tắt máy |
| `test_windows_payloads.py` | Phần chỉ chạy trên Windows nhưng vẫn kiểm chứng được ở máy dev: **layout struct ctypes** (`EXPLICIT_ACCESS_W`, `TRUSTEE_W`, `PROCESSENTRY32W`, `MEMORYSTATUSEX`, `LASTINPUTINFO`) phải khớp header MSVC trên cả x86 lẫn x64, và **XML của 4 scheduled task** phải parse được, đúng `Hidden` / `AllowHardTerminate` / `S-1-5-18` / `S-1-5-32-545` / event 1074, escape đúng khi tên nguỵ trang chứa `& < > "`, quote đúng đường dẫn có dấu cách (kể cả bản đóng gói `.exe` trỏ vào `bin\` chứ không phải thư mục build) |
| `test_windows_live.py` | **Chỉ chạy trên Windows thật** (tự skip ở máy dev): gọi thẳng Win32 để chứng minh — gắn DENY ACE rồi thử `TerminateProcess`/`taskkill /F` và phải *bị từ chối*; `schtasks /create` chấp nhận XML của cả 4 task rồi đọc ngược lại còn nguyên `Hidden`/`AllowHardTerminate`; `icacls` chặn đọc khiến `config.json` ném `PermissionError`; `MessageBoxTimeoutW` hiện hộp thoại thật; chụp màn hình ra ảnh >5 KB; bật `uihost` thành tiến trình thật và đòi ack |

Hai test đa tiến trình tồn tại vì chúng đã bắt được lỗi mà test trong cùng một process
không thấy: `--config` không được chuyển tiếp xuống tiến trình con, khiến
UI host ngồi đọc nhầm thư mục và **mọi lệnh `/msg`, `/lock`, `/screenshot` đều treo**.

### Kiểm tra trên một máy Windows thật

Test đơn vị chạy được ở mọi hệ điều hành, nhưng phần quyết định — *con có
thật sự không End Task được không* — chỉ chứng minh được trên Windows. Vì vậy
repo có sẵn một bộ chạy một lệnh:

```bat
scripts\test-interactive.bat  :: người dùng — tự xin quyền Admin (UAC), giữ cửa sổ lại
scripts\test.bat              :: agent/shell — không pause, không UAC, exit code chuẩn
scripts\test.bat -SkipLive    :: chỉ test đơn vị, không gọi Win32 thật
scripts\test.bat -Policy      :: thêm test ghi policy HKCU (rủi ro, xem kế hoạch mục 7)
```

Ba bước: test đơn vị → test sống trên Windows → `pyflakes`. Xong thì in
`>>> TAT CA XANH <<<`, exit code 0, và ghi báo cáo đầy đủ ra `test-report.txt`.

Bộ này **không bao giờ tắt máy, không cài đặt, không sửa gì ngoài thư mục
tạm** — mọi tiến trình dummy, task thử nghiệm (`RemotePCSelfTest\`), ACL thử
nghiệm đều được dọn trong `addCleanup`, kể cả khi test fail giữa chừng.

| Tài liệu | Dành cho | Nội dung |
|---|---|---|
| `AGENTS.md` | AI agent (Antigravity, Copilot, Cursor…) | Quy tắc an toàn, việc cần làm theo thứ tự, các mốc dễ làm hỏng khi sửa code, mẫu báo cáo |
| `docs/WINDOWS_TEST_PLAN.md` | Người thật + agent | Giải thích từng nhóm test, kết quả mong đợi, và **3 mục thủ công**: cài đặt thật, thử End Task từ tài khoản Standard của con, gỡ cài đặt & khôi phục |

Ba mục thủ công đó cố ý không tự động hoá vì quá xâm lấn: chúng ghi scheduled
task, đổi ACL và khoá Task Manager của tài khoản con.

**Cấu trúc mã nguồn**

```
remotepc/
├── __init__.py         phiên bản + tên nguỵ trang mặc định
├── __main__.py         CLI: init / run / install / uninstall / doctor / build ...
├── bot.py              Agent: vòng lặp Telegram + bộ xử lý lệnh + điều phối hành động
├── monitor.py          Phát hiện boot / login / idle / giờ giới nghiêm / quá giờ
├── curfew.py           Logic khung giờ (thuần tuý, test kỹ nhất)
├── commands.py         Phân tích cú pháp lệnh  /msg --full --title "..." nội dung
├── telegram_client.py  Bot API bằng urllib (long-poll, sendMessage, sendPhoto multipart)
├── actions.py          Tắt máy, khoá máy, hộp thoại, phủ màn hình, chụp ảnh, liệt kê app
├── uihost.py           Tiến trình trong session của con + inbox reader cho guardian
├── ipc.py              Hàng đợi job bằng file (outbox/inbox/shots)
├── stealth.py          Ẩn console, DENY ACE chống End Task, ACL, policy cho tài khoản con
├── installer.py        4 scheduled task, ACL, copy file, install/uninstall/doctor
├── watchdog.py         Hồi sinh guardian/uihost, tạo lại task bị xoá, cảnh báo can thiệp
├── system_info.py      Hostname, uptime, IP, người đăng nhập, cửa sổ đang mở, idle
├── state.py            state.json + events.jsonl (ghi atomic)
├── config.py           Dataclass cấu hình, merge mặc định, băm PIN
├── messages.py         Toàn bộ câu chữ tiếng Việt gửi về bot / hiện trên máy con
└── paths.py            Đường dẫn theo từng hệ điều hành

scripts/
├── install.bat / .ps1  Cài đặt (tự nâng quyền Administrator)
├── uninstall.bat / .ps1 Gỡ cài đặt, kể cả khi chủ máy quên PIN
├── build.bat           Đóng gói thành .exe bằng PyInstaller
├── test.bat / .ps1     Bộ kiểm tra một lệnh (agent/shell: không pause, không UAC)
└── test-interactive.bat Bản cho người click đôi (tự nâng quyền Admin, giữ cửa sổ)

tests/                  222 test — 12 file, xem bảng ở trên
docs/WINDOWS_TEST_PLAN.md   Kế hoạch kiểm thử chi tiết trên Windows
AGENTS.md               Hướng dẫn cho AI agent khi clone repo về máy Windows
```

---

## 9. Giới hạn cần biết (nói thẳng)

1. **Tiến trình vẫn hiện trong Task Manager.** Ẩn hoàn toàn cần driver kernel — đó là lãnh địa
   của rootkit, và tool này không làm vậy. Mục tiêu là *không tắt được*, không phải *không thấy được*.
2. **Safe Mode.** Vào Safe Mode thì scheduled task không chạy. Chặn được việc này cần đặt
   mật khẩu BIOS/UEFI và mật khẩu cho tài khoản Administrator — việc của bạn, không phải của tool.
3. **Con có quyền Admin thì mọi thứ vô nghĩa.** Hãy chắc chắn tài khoản của con là
   **Standard User**: `Settings → Accounts → Family & other users → Change account type`.
   Tool tự phát hiện và chỉ bảo vệ những tài khoản không phải Admin.
4. **Mất mạng = mất điều khiển từ xa.** Các mốc giờ giới nghiêm vẫn chạy đúng vì logic nằm
   trong máy; chỉ là bố/mẹ không nhận được tin và không ra lệnh được.
5. **Ctrl+Alt+Del không chặn được** (Windows cố ý như vậy). Nhưng Task Manager mở ra từ đó
   vẫn bị policy chặn với tài khoản con.
6. **Đổi giờ máy** có thể làm lệch mốc cảnh báo. Chống triệt để thì bật
   `Settings → Time & Language → Set time automatically` và chặn con đổi giờ
   (policy `DisableChangingTimeZone` — có thể thêm vào `stealth.py` nếu bạn cần).

---

## 10. Gỡ cài đặt & khôi phục

```bat
:: Cách 1: từ Telegram
/uninstall --pin 1234

:: Cách 2: trên máy (Administrator)
scripts\uninstall.bat

:: Quên PIN? Bạn vẫn là chủ máy:
scripts\uninstall.bat force
:: hoặc
python -m remotepc uninstall --force
```

Gỡ sẽ: dừng + xoá 4 scheduled task · tắt tiến trình · **gỡ policy Task Manager/RegEdit
của tài khoản con** · lấy lại quyền và xoá thư mục cài đặt + dữ liệu · gửi tin nhắn
xác nhận về Telegram. Nên khởi động lại máy sau khi gỡ.

**Agent không phản hồi?**

```bat
python -m remotepc doctor          :: task còn không, uihost còn không, config ở đâu
python -m remotepc watchdog -v     :: tự sửa: bật lại guardian, tạo lại task bị xoá
```

Lỗi thường gặp:

| Triệu chứng | Nguyên nhân / cách sửa |
|---|---|
| Bot im lặng hoàn toàn | Sai token, hoặc chưa có `chat_id`. Chạy `remotepc find-chat --add` hoặc gửi `/pair <PIN>` |
| `urlopen error` / không gửi được tin nào | **Telegram bị chặn.** Xem mục 7 — bật VPN hoặc đặt `telegram.proxy` |
| `409 Conflict` | Một tiến trình khác đang dùng cùng token (vd bạn chạy thử 2 máy). Dừng bớt |
| `/msg` báo thất bại nhưng `/shutdown` vẫn được | UI host chưa chạy → `/uihost restart`, hoặc `schtasks /run /tn "SysHealthAgent UI"` |
| `/screenshot` ra ảnh đen | Máy đang ở màn hình khoá — Windows không cho chụp desktop đang khoá |
| Cài xong máy con không thấy gì | Đúng rồi: mức 1/2 ẩn console. Kiểm tra bằng `doctor` và `/status` |
| Muốn xem log | `C:\ProgramData\SysHealthAgent\agent.log` và `events.jsonl` (chỉ SYSTEM/Admin đọc được) |

---

## 11. Vài lời cuối

Tool này hiệu quả nhất khi **nói trước với con** rằng máy tính có giờ giới nghiêm và bố/mẹ
có thể tắt máy từ xa. Phần "nguy trang" là để con *không tắt được* quy tắc, không phải để
con không biết nó tồn tại — một quy tắc mà trẻ con tin là "bí mật" thường chỉ tạo ra
một đứa trẻ giỏi giấu hơn.

Giấy phép: MIT. Xem `LICENSE`.
