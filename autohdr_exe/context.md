# Bối Cảnh Dự Án & Bàn Giao Kỹ Thuật (Context Handover)

Tài liệu này dùng để khôi phục toàn bộ ngữ cảnh kỹ thuật, kiến trúc, quy tắc làm việc, hiện trạng mã nguồn và các đầu việc tồn đọng cho phiên làm việc mới giữa User và AI Assistant.

---

## 1. Thông Tin Môi Trường & Dự Án

* **Workspace:** `d:\workspace\fl-u01-viet`
* **Git Branch:** `deploy/exe`
* **Virtualenv:** `autohdr_exe/.venv/Scripts/python.exe`
* **Cấu hình PYTHONPATH bắt buộc khi chạy CLI / Test:**
  ```powershell
  $env:PYTHONPATH="autohdr_exe;autohdr_exe\1tm"
  ```
* **Mục tiêu hệ thống:** Ứng dụng Desktop xử lý ảnh đa engine (Multi-Engine Photo Processing Client: AutoHDR, Autoenhance, sẵn sàng mở rộng cho Topaz, Photoup...). Xây dựng trên nền tảng CustomTkinter cho UI và `JobRunner` (hướng đối tượng, đa luồng nền) cho Execution Engine.

---

## 2. Quy Tắc Bắt Buộc Phải Tuân Thủ (Strict Rules)

Mọi tương tác và thay đổi code trong dự án **bắt buộc tuân thủ 100%**:
* 📄 Chi tiết quy tắc migration: [rule_migration.md](file:///d:/workspace/fl-u01-viet/autohdr_exe/rule_migration.md)
* 📄 Quy tắc kỹ thuật chung: [rule.md](file:///D:/workspace/metadata/gehihi/rule.md)

### Tóm tắt các Rule sống còn:
1. **Approval First:** AI chỉ phân tích, đề xuất kiến trúc và đưa code mẫu; **tuyệt đối không tự ý sửa/tạo file khi chưa có sự chấp thuận rõ ràng của User** (`"ok"`, `"đồng ý"`, `"thực hiện"`).
2. **Minh bạch về Fallback & Không mang vác vấn đề cũ (Clean & Fresh):**
   * Code mới độc lập 100%, không chắp vá logic tương thích ngược.
   * **Tuyệt đối không dùng `try...except ImportError`** để fallback import.
   * **Tuyệt đối không dùng `@property` facade fallback** để che giấu kiến trúc cũ.
   * Trong mọi đề xuất, AI **bắt buộc phải khai báo**: Có fallback không? Có dính dáng gì đến code cũ/file cũ không?
3. **Single Source of Truth & OOP Architecture:**
   * Mọi engine kế thừa `BaseAuthProvider` và `BaseExecutor`, đăng ký vào `AuthProviderRegistry` và `ExecutorRegistry`.
   * Tách biệt File I/O tập trung tại `1tm/libs/utils/io.py`.
   * Hằng số tập trung tại `1tm/libs/constants/`, không hardcode chuỗi URL hay endpoint trong logic.
4. **Phân Định Ranh Giới Lưu Trữ:**
   * Sessions/Credentials lưu độc lập theo từng engine: `sessions/autohdr.json`, `sessions/autoenhance.json`.
   * Cache UI (`download_dir`, `model_name`, `is_manual_mode`, `options`...) lưu riêng tại `core/cache.py` (`cache/ui_settings.json`). `core.cache` hoàn toàn không lưu trữ credentials.

---

## 3. Kiến Trúc Hệ Thống Hiện Tại (Đã Hoàn Thành)

Hệ thống đã hoàn tất tái cấu trúc UI theo mô hình Component hóa và thống nhất 100% việc thực thi qua `JobRunner`:

```text
autohdr_exe/
├── 1tm/
│   ├── libs/                          # Thư viện lõi, hằng số, auth, base executor
│   │   ├── utils/
│   │   │   ├── __init__.py            # Export io, thread, poll, common, autoenhance
│   │   │   ├── io.py                  # read_json, write_json, delete_file, ensure_parent_dir
│   │   │   ├── thread.py              # run_parallel: ThreadPoolExecutor, responsive cancel & callback
│   │   │   ├── poll.py                # poll_until: Polling đa năng, cancel tức thì (0ms) & timeout
│   │   │   ├── common.py              # make_progress_reporter: Callback tiến độ chuẩn hóa
│   │   │   └── autoenhance.py         # fetch_order_status, poll_order_completion, apply_pil_postprocess, download_order_images
│   │   ├── constants/
│   │   │   ├── autohdr.py             # Base URL (old.autohdr.com), endpoints, storage filename
│   │   │   ├── autoenhance.py         # Base URL (api.autoenhance.ai/v3), endpoints, storage filename
│   │   │   └── autoenhance_options.json # Metadata tùy chọn bầu trời, preset, góc nghiêng
│   │   ├── auth/
│   │   │   ├── base.py                # BaseAuthProvider & AuthProviderRegistry
│   │   │   ├── autohdr.py             # AutoHDRAuthProvider (quản lý sessions/autohdr.json)
│   │   │   ├── autoenhance.py         # AutoenhanceAuthProvider (quản lý sessions/autoenhance.json)
│   │   │   └── session/               # BaseSessionRecord, AutoHDRSessionRecord, AutoenhanceSessionRecord
│   │   └── executor/
│   │       ├── base.py                # BaseExecutor (Template Method & Gatekeeper), ExecutionContext, ExecutorRegistry
│   │       └── __init__.py
│   │
│   ├── jobs/                          # 4 Unified Job Executors & Background Runner
│   │   ├── __init__.py                # Tự động đăng ký 4 Job vào ExecutorRegistry
│   │   ├── autohdr.py                 # "autohdr": AutoHDRJob (File upload -> Presigned -> Run -> Poll -> Unwatermark -> Download)
│   │   ├── autohdr_download.py        # "autohdr_download": AutoHDRDownloadJob (URL/Photoshoot ID -> Poll -> Unwatermark -> Download)
│   │   ├── autoenhance.py             # "autoenhance": AutoenhanceJob (File upload -> S3 -> Process -> Poll -> PIL Postprocess -> Download)
│   │   ├── autoenhance_download.py    # "autoenhance_download": AutoenhanceDownloadJob (URL/Order ID -> Poll -> PIL Postprocess -> Download)
│   │   └── runner.py                  # JobRunner: Bộ điều phối đa luồng, stop/resume khẩn cấp, emit callbacks
│   │
│   └── tests/                         # Unit tests độc lập (100% PASS)
│       ├── test_auth.py               # Test Auth switch/delete/active (4/4 PASS)
│       ├── test_executor_base.py      # Test BaseExecutor & Gatekeeper (6/6 PASS)
│       ├── test_jobs.py               # Test 4 Jobs AutoHDR, AutoHDRDownload, Autoenhance, AutoenhanceDownload (14/14 PASS)
│       ├── test_runner.py             # Test JobRunner đa luồng & cancel (6/6 PASS)
│       └── test_utils.py              # Test I/O, thread, poll, autoenhance utils (10/10 PASS)
│
├── ui/                                # Giao diện CustomTkinter Component Hóa
│   ├── components/                    # 5 Component Độc Lập
│   │   ├── __init__.py                # Export TopBarFrame, ConfigBarFrame, DropZoneFrame, JobListFrame, LogViewerFrame
│   │   ├── top_bar.py                 # Engine Dropdown, Badges tài khoản có cursor='hand2' (click mở thẳng Settings Tab Account), Nút Cài đặt
│   │   ├── config_bar.py              # Tiêu đề Job, Dropdown Preset, Bầu trời, Switch Góc đứng, Khử méo (Tự ẩn/hiện theo Engine)
│   │   ├── drop_zone.py               # Mode File (Drop/Browse) vs Mode URL (Dropdown link). Báo lỗi đỏ show_file_error(), nút [✕ Hủy chọn]
│   │   ├── job_list.py                # Danh sách Card cuộn, Tabs lọc, Scoped mousewheel không cướp sự kiện, set_selected_job(id)
│   │   └── log_viewer.py              # FIFO log rotation (tối đa 500 dòng), Header chi tiết Job, Progress bar, Mở thư mục an toàn
│   ├── tab_accounts.py                # AccountsTabFrame: Quản lý Cookie AutoHDR & API Key Autoenhance (Hộp thoại hỏi xác nhận có parent=self)
│   ├── tab_storage.py                 # StorageTabFrame: Quản lý thư mục lưu mặc định (Download Dir)
│   ├── settings_dialog.py             # SettingsDialog: Modal gộp 2 Tab (Tài khoản & Lưu trữ), mở trực tiếp tab tương ứng
│   ├── screen_main.py                 # Main Controller tinh gọn (~600 dòng): Điều phối 5 Components và JobRunner. Zero Fallbacks!
│   └── app.py                         # App Root: Khởi tạo Application, bỏ hoàn toàn PipelineManager
│
└── main.py                            # Entry point & Smoke Test (--smoke-test kiểm tra 5 components & 4 jobs)
```

---

## 4. Báo Cáo Triệt Tiêu Fallback & Tối Ưu Hóa (Zero Fallbacks)

1. **Loại bỏ hoàn toàn `@property` Facade:**
   * Trong `ScreenMain`, toàn bộ các thuộc tính chuyển tiếp giả lập widget (như `self.drop_label`, `self.url_entry`, `self.job_cards`...) đã bị xóa bỏ hoàn toàn.
   * Controller gọi trực tiếp thuộc tính và method công khai từ 5 component instance (`self.drop_zone.*`, `self.top_bar.*`, `self.config_bar.*`, `self.job_list.*`, `self.log_viewer.*`).
2. **Loại bỏ hoàn toàn hệ thống Pipeline kép (`PipelineManager`):**
   * Trước đây: AutoHDR chạy qua `PipelineManager` (legacy), Autoenhance chạy qua `JobRunner`.
   * Hiện tại: Đã tạo `AutoHDRDownloadJob` kế thừa `AutoHDRJob`. Toàn bộ 4 nghiệp vụ xử lý ảnh đều được thực thi thống nhất qua `JobRunner`. `PipelineManager` đã được gỡ bỏ 100% khỏi `screen_main.py` và `app.py`.
3. **Loại bỏ Callback rác:** Xóa bỏ hoàn toàn hàm rỗng `_on_clear_job_log(self, job_id)` và `on_clear_log` callback trong `LogViewerFrame`. Việc xóa log UI được xử lý khép kín tại component.
4. **Ngăn chặn rò rỉ bộ nhớ (Log FIFO Rotation):** `LogViewerFrame` tự động giới hạn 500 dòng log; khi vượt quá sẽ đẩy dòng cũ nhất ra khỏi mảng và xóa dòng đầu trong `log_textbox`.
5. **Khắc phục lỗi đè Modal Dialog:** Trong `tab_accounts.py`, thêm `parent=self` vào `messagebox.askyesno` để popup xác nhận xóa tài khoản luôn nổi lên trên modal `CTkToplevel`.
6. **Bỏ ghi đĩa liên tục trên sự kiện gõ phím:** Đã bỏ bind `<KeyRelease>` ghi `cache.save()` ở ô nhập địa chỉ; chỉ lưu vào cache khi người dùng rời con trỏ (`<FocusOut>`).
7. **Đa luồng hóa & Smart Retry khi xin presigned S3 (Autoenhance):** Đã nâng cấp bước xin link presigned (`POST /images/`) từ tuần tự sang song song qua `run_parallel` (4 workers), tích hợp Smart Retry (xử lý HTTP 429 với `Retry-After`, 5xx với Linear Backoff), fail-fast với lỗi 4xx client và bổ sung thời gian nghỉ retry đồng bộ cho mọi thất bại khi upload S3.

---

## 5. Danh Sách Mã Nguồn Rác Cần Dọn Dẹp (Dead Code Backlog)

Các file sau đây đã trở thành **Dead Code** (hoàn toàn không còn được import hay sử dụng trong luồng chạy của ứng dụng):
1. `autohdr_exe/core/pipeline.py` (677 dòng) — Hệ thống pipeline cũ, đã được thay thế hoàn toàn bởi `1tm/jobs/runner.py`.
2. `autohdr_exe/steps/` (9 file steps cũ: `step0_session.py` đến `step8_postprocess.py`) — Đã thay thế bởi 4 Job trong `1tm/jobs/`.
3. `autohdr_exe/core/logger.py` — Đã thay thế bởi logging theo job của `JobRunner` và `BaseExecutor`.
4. `autohdr_exe/ui/accounts_dialog.py` — Đã bị xóa trên git, logic chuyển vào `tab_accounts.py` trong `SettingsDialog`.

---

## 6. Các Nhiệm Vụ Tiếp Theo Cho Phiên Chat Mới (Next Tasks)

1. **Dọn dẹp triệt để Dead Code:**
   * Trình User phê duyệt và tiến hành xóa bỏ `autohdr_exe/core/pipeline.py`, thư mục `autohdr_exe/steps/`, và `autohdr_exe/core/logger.py` (nếu không còn module nào tham chiếu).
2. **Cập nhật CI/CD Workflow (`.github/workflows/build-exe.yml`):**
   * Bổ sung các flags thu thập dữ liệu và module cho PyInstaller để đóng gói `.exe` chuẩn xác:
     * `--collect-submodules ui`
     * `--collect-data customtkinter`
     * `--collect-all tkinterdnd2`
3. **Telemetry & Runtime Statistics:**
   * Tích hợp `stats_client.append_runtime()` (`core/stats_client.py`) vào template method của `BaseExecutor` hoặc `JobRunner` để thống kê thời gian chạy nếu nghiệp vụ yêu cầu.
4. **Lưu lịch sử Job giữa các phiên làm việc (Job History Persistence):**
   * Hiện tại danh sách jobs trong `JobRunner` lưu in-memory (`self._jobs = {}`). Khi tắt app và mở lại, danh sách trên giao diện sẽ trống. Nếu người dùng muốn giữ lại lịch sử các job gần nhất, cần thiết kế lưu/đọc `cache/jobs_history.json`.
5. **Chuẩn hóa ảnh đầu vào RAW/DNG (Step 1 Image Preparation):**
   * Kiểm tra và hỗ trợ convert RAW/DNG sang JPG trước khi upload nếu người dùng có nhu cầu xử lý file máy ảnh trực tiếp.

---

## 7. Lệnh Kiểm Tra Hệ Thống Nhanh (Sanity Check)

Chạy lệnh sau tại thư mục gốc để xác nhận tính toàn vẹn 100% của toàn bộ hệ thống:

```powershell
# 1. Kiểm tra Smoke Test toàn bộ 5 UI Components và 4 Job Executors:
$env:PYTHONPATH="autohdr_exe;autohdr_exe\1tm"
autohdr_exe\.venv\Scripts\python.exe autohdr_exe\main.py --smoke-test

# 2. Chạy kiểm thử tích hợp 4 Jobs (AutoHDR, AutoHDRDownload, Autoenhance, AutoenhanceDownload):
autohdr_exe\.venv\Scripts\python.exe autohdr_exe/1tm/tests/test_jobs.py

# 3. Chạy các unit test auth, executor, runner, utils:
autohdr_exe\.venv\Scripts\python.exe autohdr_exe/1tm/tests/test_auth.py
autohdr_exe\.venv\Scripts\python.exe autohdr_exe/1tm/tests/test_executor_base.py
autohdr_exe\.venv\Scripts\python.exe autohdr_exe/1tm/tests/test_runner.py
autohdr_exe\.venv\Scripts\python.exe autohdr_exe/1tm/tests/test_utils.py
```
