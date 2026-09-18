# HDR_EXE: Modular Core Engine & Licensing Architecture

Hệ thống module hóa lõi xử lý ảnh HDR và quản lý bản quyền/job phục vụ ứng dụng hợp nhất `hdr_exe`.

## 1. Cấu trúc hệ thống

### Core Shared (`core/shared/`)
- `core.shared.licensing`: Quản lý kích hoạt bản quyền độc lập theo engine (`fotello`, `autoenhance`, `autohdr`).
  - `activate(engine, key)`: Kích hoạt online và lưu vào `~/.hdr_exe/licensing/keys.json`.
  - `check(engine)`: Xác thực online trước mỗi job/lần restart (POST `/api/key/active`), cấm dùng cache để cấp quyền.
  - `require_access(engine, mode)`: Phân quyền (`lite`: single mode; `plus`: single & batch mode).
  - `clear(engine)`: Xóa key cục bộ mà không reset mã máy trên server.
  - `get_machine_id()`: Nhận diện mã máy phần cứng OS chuẩn hóa sha256 16 ký tự (không fallback ngẫu nhiên).
- `core.shared.jobs`: Bộ điều phối tuần tự và chia job (Job Planner, Sequential Runner, Checkpoint Store).
  - `plan_jobs(engine, outputs, mode, limits)`: Lập kế hoạch job theo sức chứa (Lite: single <= 20 outputs; Plus: single <= 20, batch <= 60 outputs chia 3 job).
  - `run_jobs(plan, execute_job, ...)`: Chạy tuần tự với bước `activation` tự động trước mỗi job.
  - `restart_job(run_id, job_id, execute_job, ...)`: Chạy lại thủ công từ checkpoint, bảo toàn `latest_step` và `server_resources` khi lỗi bản quyền.

### Core Autoenhance (`core/autoenhance/`)
- `core.autoenhance.config`: Tải cấu hình và giới hạn batch (`max_outputs_per_job=20`, `max_jobs_per_batch=3`).
- `core.autoenhance.constants`: Các hằng số API, headers, 8 bước xử lý chuẩn (`JOB_STEPS`).
- `core.autoenhance.auth`: Lưu, đọc, xóa API key đăng nhập dịch vụ Autoenhance (phân biệt với bản quyền app).
- `core.autoenhance.client`: HTTP client với timeout và retry chuẩn.
- `core.autoenhance.orders`: Tạo order, phân trang và lấy chi tiết hình ảnh.
- `core.autoenhance.image_processing`: Chuyển JPEG, đọc dimensions, upscale Lanczos + UnsharpMask, xử lý alpha compositing.
- `core.autoenhance.upload`: Tiền xử lý file ảnh, lấy presigned URL và upload streaming lên AWS S3.
- `core.autoenhance.execute`: Ánh xạ cấu hình AI processing và gửi yêu cầu xử lý.
- `core.autoenhance.polling`: Kiểm tra trạng thái hoàn tất đơn hàng và xử lý hủy.
- `core.autoenhance.download`: Tải ảnh streaming có kiểm tra bản quyền `check("autoenhance")`, bảo vệ chống ghi đè file.
- `core.autoenhance.workflow`: Chuẩn hóa API với `run_workflow(...) -> BatchResult` và `restart_workflow_job(...) -> JobResult`.

### Core Fotello (`core/fotello/`)
- `core.fotello.workflow`: Quy trình xử lý ảnh Fotello chạy qua JobRunner với phân quyền và bản quyền `fotello`.
- `core.fotello.download`: Tải ảnh streaming độc lập có bước `activation` kiểm tra bản quyền `check("fotello")`.

## 2. Quy định bảo mật & phân quyền

1. **Phân quyền gói dịch vụ:**
   - **Lite**: Chỉ được chạy chế độ `single` (tối đa 20 output/job). Bị từ chối khi chọn chế độ `batch`.
   - **Plus**: Được chạy cả `single` (tối đa 20 output/job) và `batch` (tối đa 60 output, chia 3 job tuần tự).
2. **Khởi động lại (Restart):**
   - Trước polling (`prepare`, `create_order`, `upload`, `execute`): Tạo order mới, chạy lại từ đầu.
   - Tại polling: Tái sử dụng `order_id` cũ, không upload lại.
   - Sau polling (`download`, `export`): Tái sử dụng `order_id` cũ, không tải đè các file đã tồn tại trên đĩa.
   - Lỗi bản quyền khi restart: Giữ nguyên `latest_step` và `server_resources` trong checkpoint; không được ghi đè bằng bước `activation`.
3. **An toàn dữ liệu:**
   - Mã kích hoạt (license key) tuyệt đối không được ghi vào log, event, manifest hoặc checkpoint.
   - Khóa lưu riêng biệt tại `~/.hdr_exe/licensing/keys.json` (atomic write, file permission 0o600).

## 3. Cài đặt và Kiểm thử

```bash
# Cài đặt môi trường
pip install -e .

# Chạy kiểm thử toàn diện
pytest tests/ -v

# Chạy ví dụ minh họa
python examples/licensing_demo.py
python examples/run_autoenhance_workflow.py
```
