# Bối Cảnh Dự Án & Tiến Độ Migration (Context Handover)

Tài liệu này dùng để khôi phục toàn bộ ngữ cảnh kỹ thuật, kiến trúc, quy tắc làm việc và tiến độ hiện tại cho phiên làm việc mới giữa User và AI Assistant.

---

## 1. Tổng Quan Dự Án & Mục Tiêu

* **Workspace:** `d:\workspace\fl-u01-viet`
* **Virtualenv:** `autohdr_exe/.venv/Scripts/python.exe`
* **Mục tiêu chính:** Hợp nhất (migrate) engine xử lý ảnh **Autoenhance** từ `hdr_exe/core/autoenhance` vào trong ứng dụng client **`autohdr_exe`**.
* **Định hướng kiến trúc:** Chuyển đổi từ hệ thống chạy đơn lẻ AutoHDR sang hệ thống đa engine (Multi-Engine Architecture: AutoHDR, Autoenhance, và sẵn sàng mở rộng cho các engine tương lai như Topaz, Photoup) theo chuẩn hướng đối tượng (OOP), phân tách trách nhiệm (Separation of Concerns) và loại bỏ hoàn toàn các liên kết cứng (hardcode).

---

## 2. Quy Tắc Bắt Buộc Phải Tuân Thủ (Strict Rules)

Mọi tương tác và thay đổi code trong dự án **bắt buộc tuân thủ 100%** theo 2 tài liệu quy tắc đã chốt:
* 📄 Chi tiết quy tắc migration: [rule_migration.md](file:///d:/workspace/fl-u01-viet/autohdr_exe/rule_migration.md)
* 📄 Quy tắc kỹ thuật chung: [rule.md](file:///D:/workspace/metadata/gehihi/rule.md)

### Tóm tắt các Rule sống còn:
1. **Approval First:** AI chỉ đề xuất kiến trúc, phân tích và đưa code mẫu; **tuyệt đối không tự ý sửa/tạo file khi chưa có sự chấp thuận rõ ràng của User** (`"ok"`, `"đồng ý"`, `"thực hiện"`).
2. **Minh bạch về Fallback & Không mang vác vấn đề cũ (Clean & Fresh):**
   * Code mới là độc lập 100%, không tự ý cài cắm logic tương thích ngược với code cũ hay file cũ.
   * Tuyệt đối không dùng `try...except ImportError` để fallback import. Mọi module dùng direct import sạch sẽ từ `libs.*`.
   * Trong mọi đề xuất, AI **bắt buộc phải khai báo**: Có fallback không? Có dính dáng gì đến file cũ không?
3. **Không gian cách ly `1tm`:** Toàn bộ code migration mới nằm độc lập tại `autohdr_exe/1tm/libs/`, không đụng trực tiếp vào code đang chạy của `autohdr_exe` (như `steps/step0_session.py`).
4. **Kiến trúc OOP chuẩn (Strategy / Registry):** Sử dụng Abstract Base Class (`BaseAuthProvider`, `BaseSessionRecord`, `BaseExecutor`) kết hợp với Provider riêng cho từng engine.
5. **Tách biệt File I/O:** Mọi thao tác đọc/ghi/xóa JSON và tạo thư mục dùng chung phải nằm ở `1tm/libs/utils/io.py`.
6. **Constants tập trung:** Toàn bộ URLs, endpoints, filenames, timeouts nằm tại `1tm/libs/constants/`, không hardcode chuỗi trong logic.
7. **Lưu trữ Session riêng theo Engine:** Mỗi engine 1 file JSON riêng trong thư mục `sessions/`:
   * AutoHDR: `sessions/autohdr.json`
   * Autoenhance: `sessions/autoenhance.json`
   * Quản lý phiên active bằng cờ `"is_active": true/false`, hỗ trợ `list_accounts()`, `switch_account(identifier)`, và `delete_account(identifier)`.
8. **Ranh giới `core.cache`:** `core.cache` chỉ lưu UI settings (`download_dir`, `model_name`, `selected_web`, `is_manual_mode`...), hoàn toàn không lưu thông tin Auth.

---

## 3. Cấu Trúc Đã Xây Dựng & Kiểm Thử Hoàn Tất

Toàn bộ hệ sinh thái `1tm` và giao diện `ScreenMain` đã hoàn thành tích hợp và kiểm thử đạt **100% PASS**:

```text
autohdr_exe/
├── 1tm/
│   ├── libs/                          # Thư viện dùng chung, hằng số, auth, executor
│   │   ├── utils/
│   │   │   ├── __init__.py            # Export io, thread, poll, common, autoenhance
│   │   │   ├── io.py                  # read_json, write_json, delete_file, ensure_parent_dir
│   │   │   ├── thread.py              # run_parallel: Quản lý ThreadPoolExecutor, responsive cancel & callback
│   │   │   ├── poll.py                # poll_until: Vòng lặp thăm dò đa năng, responsive cancel (0ms) & timeout
│   │   │   ├── common.py              # make_progress_reporter: Tiện ích tạo callback tiến độ chuẩn hóa
│   │   │   └── autoenhance.py         # fetch_order_status, poll_order_completion, apply_pil_postprocess, download_order_images
│   │   │
│   │   ├── constants/
│   │   │   ├── __init__.py
│   │   │   ├── autohdr.py             # API_BASE (https://old.autohdr.com), ENDPOINT_*, STORAGE_FILENAME
│   │   │   └── autoenhance.py         # API_BASE (https://api.autoenhance.ai/v3), ENDPOINT_*, STORAGE_FILENAME
│   │   │
│   │   ├── auth/
│   │   │   ├── __init__.py            # Facade: authenticate, get_active_session, list_accounts, switch_account, delete_account
│   │   │   ├── base.py                # BaseAuthProvider (Abstract Class) & AuthProviderRegistry
│   │   │   ├── autohdr.py             # AutoHDRAuthProvider (quản lý sessions/autohdr.json)
│   │   │   ├── autoenhance.py         # AutoenhanceAuthProvider (quản lý sessions/autoenhance.json)
│   │   │   └── session/
│   │   │       ├── __init__.py        # Export BaseSessionRecord, AutoHDRSessionRecord, AutoenhanceSessionRecord
│   │   │       ├── base.py            # BaseSessionRecord (identifier, display_name, to_dict, is_expired)
│   │   │       ├── autohdr.py         # AutoHDRSessionRecord (cookie, user_id, email, firstname, lastname, expires)
│   │   │       └── autoenhance.py     # AutoenhanceSessionRecord (api_key, name, created_at, display_name=masked key)
│   │   │
│   │   └── executor/                  # Execution Engine Layer
│   │       ├── __init__.py            # Export BaseExecutor, ExecutionContext, ExecutorRegistry
│   │       └── base.py                # BaseExecutor (Template Method & Gatekeeper hậu kiểm), ExecutionContext, Registry
│   │
│   ├── jobs/                          # Tầng Job nghiệp vụ cho từng Provider (CÙNG CẤP VỚI LIBS)
│   │   ├── __init__.py                # Tự động đăng ký Provider vào ExecutorRegistry; Export các Jobs & JobRunner
│   │   ├── autohdr.py                 # AutoHDRJob: presigned_urls -> upload -> finalize -> associate_and_run -> poll -> unwatermark -> download
│   │   ├── autoenhance.py             # AutoenhanceJob: pre_transform(dims) -> create_order -> upload_s3 -> trigger_process -> poll -> download_and_process(PIL)
│   │   ├── autoenhance_download.py    # AutoenhanceDownloadJob: Tải trực tiếp qua Order ID / URL (không cần file local)
│   │   └── runner.py                  # JobRunner: Bộ điều phối chạy ngầm (Background Threading), stop/resume khẩn cấp
│   │
│   └── tests/                         # Unit Tests độc lập (39/39 PASS 100%)
│       ├── test_auth.py               # Test Auth: List/Switch/Delete, Masked Key display, Auto-active (4/4 PASS)
│       ├── test_executor_base.py      # Test BaseExecutor & Gatekeeper hậu kiểm (6/6 PASS)
│       ├── test_jobs.py               # Test tích hợp Jobs AutoHDR, Autoenhance & AutoenhanceDownload (13/13 PASS)
│       ├── test_runner.py             # Test JobRunner đa luồng, stop & resume khẩn cấp (6/6 PASS)
│       └── test_utils.py              # Test thread, poll, reporter & autoenhance utils (10/10 PASS)
│
└── ui/
    ├── accounts_dialog.py             # Modal Quản lý Tài khoản: Tab AutoHDR & Tab Autoenhance, Switch/Delete/Add
    ├── settings_dialog.py             # Modal Cài đặt: Cấu hình thư mục đầu ra (Download Dir)
    └── screen_main.py                 # Màn hình chính tích hợp đa engine: Topbar tinh gọn, Dropdown-first URL mode
```

---

## 4. Chi Tiết Các Tính Năng & Cải Tiến Đã Hoàn Thành

1. **Khắc phục lỗi log giả "Đã upload 1/1 ảnh" khi upload thất bại:** `run_parallel()` và `make_progress_reporter()` phân biệt chính xác cờ `is_success`.
2. **Khắc phục lỗi lặp log `[ERROR]` 2 lần:** Tập trung ghi log lỗi duy nhất tại Template Method `BaseExecutor.execute()`.
3. **Khắc phục lỗi HTTP 422 trên Autoenhance:** Cập nhật payload `POST /orders/` chuẩn API v3: tự sinh client UUID `order_id` + `name`.
4. **Bổ sung S3 Headers cho AutoHDR upload:** Thêm `Content-Type: application/octet-stream`, `Content-Length`, `x-amz-acl: private`, `Origin`.
5. **Chuẩn hóa toàn diện Autoenhance theo OpenAPI 3.0.3:** `sky_replacement` (boolean), `cloud_type` (enum), `perspective_correction` (boolean), `preview=true` (download), `preset_id`, `ai_version: "latest"`.
6. **Smart Resume (Chạy lại từ bước lỗi):** Tự động phát hiện order/photoshoot cũ trên Cloud để nhảy thẳng đến Polling / Tải ảnh. Tích hợp nút `🔄` chạy lại trên từng hàng job và header panel chi tiết.
7. **Cơ chế chống ghi đè khi tải ảnh trùng tên (Anti-Collision Unique Naming):** Tự động phân giải tên file tăng dần (`image.jpg`, `image_1.jpg`...) tránh đè file.
8. **Tự động lưu ảnh theo ID Job (`<output_dir>/<job_id>/`):** Định tuyến đường dẫn output tự động, nút "📂 Mở thư mục" mở đúng thư mục của job.
9. **Lưu Cache toàn diện các tùy chọn cấu hình:** Web đang chọn, 4 tùy chọn Autoenhance (`ae_preset`, `ae_sky`, `ae_vertical`, `ae_lens`), chế độ FILE/URL.
10. **Tách riêng module tiện ích Autoenhance (`1tm/libs/utils/autoenhance.py`):** Export `fetch_order_status`, `poll_order_completion`, `apply_pil_postprocess`, `download_order_images`.
11. **Tách riêng Job tải URL độc lập (`1tm/jobs/autoenhance_download.py`):** Kế thừa `BaseExecutor`, override `_validate_pre_transform` để tải thẳng từ Cloud Order ID mà không cần file nguồn local.
12. **Chuẩn hóa Giao diện URL Input theo Dropdown (Dispatcher Pattern) & Label Thông Báo:**
    * Dropdown xác định web engine, không tự ý đoán link hay tự chuyển dropdown.
    * Bắt buộc bắt đầu bằng `http://` hoặc `https://`, không nhận ID thuần.
    * Kiểm tra tên miền bằng `urllib.parse` trước khi lấy ID từ cấu trúc Path (`/orders/<id>` hoặc `/photoshoots/<id>`).
    * Nhãn thông báo lỗi (`url_status_label`) nằm trực tiếp **dưới thanh input**, tự động xóa khi gõ phím hoặc đổi dropdown.
13. **Modal Quản Lý Tài Khoản (Phương án 2 - `ui/accounts_dialog.py`):**
    * Tabview chuyên nghiệp cho **AutoHDR (Cookie)** và **Autoenhance (API Key)**.
    * Danh sách tài khoản dạng thẻ cuộn: hiển thị trạng thái `🟢 [Đang dùng]` / `⚪ [Khả dụng]`.
    * Nút bấm: `[Kích hoạt]` chuyển tài khoản 1-click, `[🗑️]` xóa tài khoản có xác nhận.
    * Form thêm mới ở dưới: nạp cookie (hoặc đọc file json/txt) / dán API Key, xác thực ngầm đa luồng.
    * Tự động chuyển active sang tài khoản còn lại khi tài khoản active bị xóa.
    * Top Bar tinh gọn: chỉ hiển thị Badge tài khoản active (`🟢 email` / `🟢 ae_live_...49a1`) + Nút `👥 Quản lý Cookie` / `👥 Quản lý API Key`.
    * Bỏ tiền tố `"Key Chính"` / `"Default Key"`, hiển thị trực tiếp masked key dạng `ae_live_...49a1`.
14. **Loại bỏ 100% Import fallback sử dụng `try...except`:** Dọn sạch toàn bộ import try-except trong `1tm/libs/auth/autohdr.py` và `1tm/libs/auth/autoenhance.py`, tuân thủ nghiêm ngặt Rule 2 (Clean & Fresh).

---

## 5. Bảng Workflow Tổng Thể Cần Migration Tiếp Theo

Tham khảo từ `hdr_exe/core/autoenhance/workflow.py` và `autohdr_exe/migration.md`:

| STT | Bước Workflow | File gốc trong `hdr_exe` | Mục tiêu module hóa vào `1tm` | Trạng thái |
| :---: | :--- | :--- | :--- | :---: |
| **0** | **Auth & Session Management** | `auth.py` | `1tm/libs/auth/` + `ui/accounts_dialog.py` | ✅ **HOÀN THÀNH** |
| **1** | **Prepare & Image Conversion** | `upload.py` (`prepare_upload_files`) | Chuẩn hóa ảnh đầu vào, convert RAW/DNG sang JPG, trích xuất metadata kích thước ảnh gốc. | ⏳ **TIẾP THEO** |
| **2** | **Create Order** | `orders.py` (`create_order`) | Gọi API tạo đơn hàng mới trên Autoenhance v3 (`POST /orders/`), lưu metadata order. | ✅ Đã tích hợp trong `AutoenhanceJob` |
| **3** | **Upload to S3** | `upload.py` (`get_upload_s3_info`, `_upload_one_file`) | Lấy presigned S3 URL và upload ảnh song song đa luồng lên AWS S3. | ✅ Đã tích hợp trong `AutoenhanceJob` |
| **4** | **Execute AI Process** | `execute.py`, `options.py` | Ánh xạ tùy chọn người dùng (preset, góc nhìn, bầu trời...) và POST `/orders/{id}/process`. | ✅ Đã tích hợp trong `AutoenhanceJob` |
| **5** | **Polling Completion** | `polling.py` (`poll_order_completion`) | Thăm dò trạng thái qua GET `/orders/{id}`, hỗ trợ dừng tác vụ an toàn. | ✅ Đã tích hợp trong `AutoenhanceJob` & `utils` |
| **6** | **Final Images Filter** | `orders.py` (`filter_final_processed_images`) | Lọc ảnh thành phẩm cuối cùng qua brackets / metadata heuristics. | ✅ Đã tích hợp trong `AutoenhanceJob` |
| **7** | **Download & Post-processing** | `download.py`, `image_processing.py` | Tải luồng PNG, xử lý kênh Alpha lên nền trắng, resize Lanczos về kích thước gốc, UnsharpMask, xuất JPEG 300 DPI. | ✅ Đã tích hợp trong `AutoenhanceJob` & `utils` |
| **8** | **Pipeline & UI Ráp nối** | `workflow.py` $\rightarrow$ `pipeline.py`, `screen_main.py` | Ráp nối JobRunner & UI ScreenMain: Web dropdown, Cấu hình + Tên dự án chung 1 div, Dropzone, Modal Accounts, Modal Settings, lọc danh sách job theo Web. | ✅ **HOÀN THÀNH** |

---

## 6. Hướng Dẫn Cho AI Assistant Ở Phiên Chat Mới

1. **Đọc kỹ file này trước tiên** để nắm được toàn bộ bối cảnh, kiến trúc hiện tại và các rule đã thống nhất.
2. Tuyệt đối **không tự ý sửa file** mà phải đề xuất giải pháp, phân tích rõ ràng và hỏi ý kiến User (Rule 1: Approval First).
3. Khi đề xuất, luôn khai báo xem có fallback nào không và có đụng chạm file cũ không (Rule 2: Clean & Fresh).
4. Nhiệm vụ tiếp theo: Tinh chỉnh hoặc hoàn thiện **Bước 1 (Prepare & Image Conversion: Chuyển đổi định dạng RAW/DNG, chuẩn hóa kích thước)** nếu người dùng yêu cầu, hoặc tiếp tục các yêu cầu nghiệp vụ mới.
