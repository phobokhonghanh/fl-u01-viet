# HDR_EXE: Modular Core Engine (Autoenhance)

Hệ thống module hóa lõi xử lý ảnh HDR phục vụ ứng dụng hợp nhất `hdr_exe`.
Trong giai đoạn Phase 1, toàn bộ lõi xử lý Autoenhance được chuyển giao từ bản phục dựng và tổ chức thành các module chuyên trách độc lập.

## Cấu trúc Core Autoenhance

- `core.autoenhance.constants`: Hằng số API base, presets, sky types, workers, đường dẫn file config.
- `core.autoenhance.auth`: Lưu, đọc, xóa token cục bộ và kiểm tra xác thực.
- `core.autoenhance.client`: HTTP client với timeout, retry và headers chuẩn.
- `core.autoenhance.orders`: Tạo order, phân trang và lấy chi tiết hình ảnh.
- `core.autoenhance.image_processing`: Chuyển JPEG, đọc dimensions, upscale Lanczos + UnsharpMask, xử lý alpha compositing.
- `core.autoenhance.upload`: Tiền xử lý file ảnh, lấy presigned URL và upload streaming lên AWS S3.
- `core.autoenhance.execute`: Ánh xạ cấu hình AI processing và gửi yêu cầu xử lý.
- `core.autoenhance.polling`: Kiểm tra trạng thái hoàn tất đơn hàng và xử lý hủy.
- `core.autoenhance.download`: Tải ảnh streaming, bảo vệ chống ghi đè, quản lý file tạm an toàn và tính toán tiến độ.
- `core.autoenhance.workflow`: Hàm quy trình tích hợp `upload_and_process`.

## Cài đặt và Kiểm thử

```bash
# Cài đặt chế độ editable
pip install -e .

# Chạy toàn bộ unit test độc lập
python3 -m unittest discover -s tests -v

# Chạy ví dụ minh họa thủ công
python3 examples/demo.py
```
