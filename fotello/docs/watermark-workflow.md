# Xóa watermark trong workflow

## Upload và tự tải (WF1)

Chọn thư mục ảnh gốc, bracket 1/3/5 và tên job như bình thường. Tính năng xóa watermark chạy tự động.

- Tên `abc` hoặc `abc01` tạo các lượt `abc01`, `abc02`, `abc03`… Tên listing còn chứa marker family để phân biệt các lần chạy cùng tên.
- Hai lượt đầu xử lý toàn bộ các nhóm bracket trước khi ứng dụng chờ tải kết quả. Các lượt tiếp theo chỉ gửi lại nhóm chưa có cặp watermark phù hợp.
- Ảnh gốc được natural-sort và chia nhóm một lần. Ví dụ bracket 5: nhóm đầu dùng input 01–05, nhóm tiếp dùng 06–10. Retry nhóm thứ hai vẫn gửi nguyên 06–10.
- Tên ảnh cuối lấy từ stem của input đầu nhóm, đuôi `.png`. Các nhóm retry không bị đánh số lại.
- Ứng dụng upload lại input của mỗi lượt; không phụ thuộc vào khả năng tái sử dụng upload ID của dịch vụ.

Số input phải chia hết bracket. File gốc thay đổi trong quá trình chạy, nhóm upload thiếu file hoặc lỗi tạo job chưa xác định kết quả sẽ được báo lỗi thay vì gửi nhóm sai. Watermark trùng góc tiếp tục retry cho đến khi có cặp phù hợp hoặc bấm Dừng. Cảnh báo chất lượng ghép được giữ để kiểm tra, không tạo job liên tục chỉ vì cảnh báo đó.

## Tải manual (WF2)

Trong Listings, chọn các lượt thuộc cùng family; nút chọn cả nhóm hỗ trợ chọn `abc01`, `abc02`, `abc03` cùng lần chạy. Có thể chọn nhiều family trong một lần tải, kết quả được tách riêng.

WF2 tải các enhance có sẵn, ghép theo mapping đã lưu và xuất ảnh sạch. WF2 không tự tạo job xử lý mới. Nếu thiếu biến thể hoặc chưa xác định được mapping, xem log/report rồi chọn bổ sung lượt phù hợp.

Mapping được lưu trong manifest và registry riêng của ứng dụng, nên tải manual vẫn có thể dùng sau khi khởi động lại. Listing cũ không có marker/manifest chỉ được ghép khi có đủ bằng chứng liên kết; tên ảnh hoặc thứ tự tải đơn lẻ không đủ để ghép.

## Kết quả và trạng thái

Trong thư mục kết quả của family:

```text
manifest.json
raw/abc01/part01/<tên ảnh>.jpg
raw/abc02/part01/<tên ảnh>.jpg
clean/<tên ảnh>.png
reports/<output_id>.json
attempts/<output_id>/...
```

`Bản tải` đếm các biến thể tải về. `Sạch x/y` đếm ảnh cuối đã hoàn tất. Ảnh preview có cảnh báo không được tính là ảnh sạch. Job thiếu ảnh hoặc cần kiểm tra được báo hoàn tất một phần; vẫn có thể mở thư mục để xem kết quả và report.

Nếu poll/download timeout, ứng dụng giữ ID enhance để có thể tải manual sau đó. Dừng job không xóa các ảnh thô hoặc ảnh sạch đã có. Hai lượt enhance phải có cùng nội dung và kích thước ngoài watermark; khi dịch vụ tạo nội dung khác nhau, kết quả sẽ cần kiểm tra thay vì ép ghép.

## Kiểm thử

Tại thư mục dự án:

```bash
rtk proxy .venv/bin/python -m unittest discover -s tests -v
rtk proxy node --check ui/app.js
```

Các test workflow dùng API giả lập để kiểm tra retry, bracket, tải manual, cancellation và lỗi; không tạo listing thật. Kiểm tra dịch vụ thật cần tài khoản đang kết nối và bộ ảnh gốc do người dùng chọn trong ứng dụng.

Lần kiểm tra tích hợp ngày 2026-09-06: 61 test đạt, gồm WF1 → registry → WF2 sử dụng cleaner và fixture ảnh thật. Kiểm tra cú pháp Python/JavaScript và `git diff --check` cũng đạt. Chưa chạy tạo job trên dịch vụ Fotello thật trong lần triển khai này.
