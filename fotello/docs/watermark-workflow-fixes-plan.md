# Watermark Workflow Fixes & Hardening Plan

## 1. Phát hiện nguyên nhân gốc rễ (Root Cause Analysis with Live Evidence)

### 1.1. Lỗi "trùng góc, lượt upload lại chỉ upload rồi dừng / kết thúc hoàn tất một phần"
- **Bằng chứng thực tế (Live Evidence)**:
  - Khám phá file manifest thực tế `/home/itc/Downloads/hdr-trick/test-/a06da596/manifest.json` (tương ứng với screenshot `Nhóm sep06 · 401efc42` trong `scratch/checkui/image.png`):
    - `img0001` (`IMG_9942`): Có status `"needs_review"`, reason `"The changed regions do not provide reliable evidence of two distinct watermark corners."`.
    - `img0002` (`IMG_9947`): Có status `"needs_review"`, reason `"Meaningful differences were found in multiple regions, but the legacy detector could not confirm exactly one watermark source per distinct corner."`.
  - Khám phá chi tiết trong report `/home/itc/Downloads/hdr-trick/test-/a06da596/reports/IMG_9942.json`:
    - Cả 2 variant của `IMG_9942` đều có watermark ở góc **Bottom-Right (BR)**.
    - Vì cả 2 đều có watermark ở góc BR, pixel chênh lệch giữa 2 ảnh tại góc BR chỉ có 124 pixel (trong khi ngưỡng `required_group_pixels` yêu cầu 358 pixel).
    - `active_corners` trở thành rỗng `[]`, nhưng `groups` có 1 nhóm BR.
    - Theo logic cũ của `compare_variant_pair` trong `cleaner.py`:
      ```python
      if len(active_corners) == 1 and unknown_groups == 0:
          return duplicate
      if not active_corners and not groups:
          return duplicate
      # Fallthrough:
      return uncertain
      ```
      Vì `active_corners` rỗng nhưng `groups` không rỗng, hàm đã trả về `status = "uncertain"` thay vì `status = "duplicate"`!
    - Trong `clean_output`:
      ```python
      elif uncertain_reasons and not duplicate_reasons:
          status = _STATUS_NEEDS_REVIEW
      ```
      Khi `comparison` bị đánh dấu là `uncertain`, `clean_output` gán trạng thái của output group thành `needs_review`!
    - Khi `group["status"]` bị chuyển thành `needs_review`:
      Trong vòng lặp retry của `coordinator.py`:
      ```python
      while not cancelled():
          selected = [g for g in groups.values() if g["status"] == "need_variant"]
          if not selected:
              break
          collect([submit(number, selected)])
      ```
      Vì tất cả các group đều có `status == "needs_review"`, danh sách `selected` rỗng (`len == 0`), vòng lặp retry **dừng ngay lập tức**!
      Hệ thống đánh giá workflow là `partial` (hoàn tất một phần) và không bao giờ thực hiện tiếp lượt tải tiếp theo để tìm góc khác!
  - Đối với trường hợp upload lượt 3: nếu `eligible` trong `submit` lọc `g["status"] == "need_variant"` nhưng trước đó group bị gán `needs_review` hoặc nếu group chỉ upload xong mà `eligible` rỗng thì không tạo listing/enhance, khiến lượt đó chỉ upload mà không tiếp tục xử lý.

### 1.2. Lỗi tên listing mất ngày giờ chuẩn cũ, cột ngày hiện '-' và không sort được
- **Bằng chứng thực tế**:
  - Tên listing cũ:
    - 1 chunk: `<prefix> - %d %m, %Y %H:%M`
    - Nhiều chunks: `[Part X] - <prefix> - %d %m, %Y %H:%M`
  - Tên listing trong tính năng mới:
    - Được sinh bởi `models.attempt_name(...)`: `sep0601 [wm:401efc42-f2de-40e4-9472-d493a4aaf92c:1:1]`.
    - Hoàn toàn không có đuôi ` - %d %m, %Y %H:%M` và không có `[Part X]`.
    - API Fotello Firestore không lưu sẵn `createdAt` chuẩn cho listings mới (hoặc trả về rỗng), khiến fallback `_parse_listing_name_datetime(name)` thất bại (do regex/split cũ tìm ` - ` với định dạng `%d %m, %Y %H:%M`).
    - Kết quả: `created_at = '-'`, `_created_sort = 0.0`, listings xếp lộn xộn, và UI hiện nguyên chuỗi marker thô `[wm:...]`.

### 1.3. Lỗi mất toàn bộ log Step tiếng Việt cũ trên UI
- Code cũ có các Step rõ ràng:
  - `Step 01: Kiểm tra input...`
  - `Step 02: Đang tải lên - <file>...`
  - `Step 03: Hoàn tất upload - X/Y ảnh.`
  - `Step 04: Tạo listing đợt...`
  - `Step 05: Tạo listing thành công...`
  - `Step 06: Kích hoạt xử lý...`
  - `Step 07: Kiểm tra trạng thái ảnh, poll ready/pending...`
- Khi refactor sang `coordinator.py` và `run_auto`, các log này bị thay bằng các log ngắn tiếng Anh/tiếng Việt không đồng bộ theo Step và hiện thẳng các từ ngữ nội bộ máy như `need_variant`, `blocked`, `The variants are identical...`.

---

## 2. Kế hoạch sửa đổi chi tiết

### Module A: Khôi phục và chuẩn hóa Log tiếng Việt theo Step
1. **`fotello/backend/service.py` & `coordinator.py`**:
   - Khôi phục đầy đủ hệ thống Step 01 đến Step 07 cho upload, tạo listing, tạo enhance, và polling.
   - Thêm các Step mới tiếng Việt cho quy trình watermark:
     - `Step 08: Đang tải biến thể - <name>`
     - `Step 09: So sánh watermark - <output_name>`
     - `Step 09: Watermark trùng góc (<goc>) - tiếp tục lấy biến thể cho <output_name>`
     - `Step 10: Đang ghép ảnh sạch watermark - <output_name>`
     - `Step 10: Ghép thành công - <output_name>`
     - `Step 11: Tự động chạy lượt <X> cho <N> ảnh cần biến thể khác...`
   - Dịch toàn bộ các thông điệp lỗi và lý do sang tiếng Việt thân thiện, không hiển thị mã kỹ thuật `need_variant`, `DuplicateWatermarkError`, `blocked` thô lên UI.

### Module B: Sửa lỗi phân loại trùng góc & cơ chế Retry liên tục đến khi hoàn tất
1. **`fotello/backend/watermark_workflow/cleaner.py` (`compare_variant_pair`)**:
   - Khi 2 ảnh có sự sai khác tập trung ở cùng 1 góc (hoặc pixel khác biệt nhỏ do cùng watermark ở 1 góc duy nhất, các góc khác đều sạch):
     - Xác định chính xác là `duplicate` (trùng góc watermark), kèm theo góc cụ thể (`BR`, `TL`, etc.).
     - Trả về `status = "duplicate"` với lý do tiếng Việt rõ ràng.
   - Khi detector phát hiện các góc có độ tin cậy thấp (như `confidence < 0.05`), không để nhiễu này làm hỏng nhận diện 2 góc rõ ràng.
   - Chỉ trả về `needs_review` khi thực sự có cảnh báo chất lượng/đường nối (seam discontinuity) sau khi ghép hoặc bất thường cấu trúc không thể giải quyết.
2. **`clean_output`**:
   - Khi các cặp so sánh là `duplicate` (trùng góc) hoặc chưa đủ biến thể sạch, trả về `status = "need_variant"`.
   - Lưu report chi tiết với mã máy tương thích ngược nhưng có trường mô tả tiếng Việt.
3. **`coordinator.py`**:
   - Đảm bảo lượt 3+ (retry rounds):
     - Nhóm có `status == "need_variant"` tiếp tục được đưa vào `submit`.
     - `eligible` trong `submit` phải bao gồm tất cả các nhóm cần biến thể đã upload thành công.
     - Tạo listing mới, enhance mới, poll, download biến thể mới và ghép với các biến thể trước đó.
     - Giữ nguyên cấu trúc nhóm bracket (1, 3, 5), giữ nguyên tên output không đánh số lại.
     - Chỉ dừng khi toàn bộ ảnh đã sạch (`cleaned`), hoặc gặp lỗi vĩnh viễn (file hỏng, sai kích thước), hoặc user bấm Dừng.

### Module C: Chuẩn hóa tên listing và hiển thị thời gian
1. **`models.py` (`attempt_name`, `parse_attempt_name`)**:
   - Cấu trúc tên listing mới:
     - Khi 1 chunk: `<prefix><attempt:02d> [wm:<family_id>:<attempt>:<chunk>] - %d %m, %Y %H:%M`
     - Khi nhiều chunks: `[Part <chunk>] - <prefix><attempt:02d> [wm:<family_id>:<attempt>:<chunk>] - %d %m, %Y %H:%M`
   - Parser `parse_attempt_name` hỗ trợ đa tương thích:
     - Tên mới đầy đủ (có Part, có marker, có datetime)
     - Tên marker-only hiện tại (`prefix [wm:...]`)
     - Tên legacy cũ (`[Part X] - prefix - datetime` hoặc `prefix - datetime`)
   - `attempt_name` nhận thêm `created_at` hoặc timestamp cố định từ manifest để không bị trôi thời gian khi retry.
2. **`downloads.py`**:
   - Cải tiến `fotello_list_listings`:
     - Ưu tiên `createdAt` từ Firestore doc nếu hợp lệ.
     - Fallback phân tích datetime từ chuỗi tên listing (hỗ trợ cả định dạng mới và cũ).
     - Fallback tìm trong local manifest store qua `family_id` nếu listing chỉ có marker mà không có timestamp.
     - Tách `display_name` sạch (ẩn chuỗi UUID thô `[wm:...]`) cho UI nhưng vẫn giữ nguyên `name` gốc để kết nối Firestore.
     - Sắp xếp chuẩn theo timestamp numeric giảm dần.
3. **`ui/app.js`**:
   - Hiển thị tên listing gọn gàng, hiển thị thời gian đã format chuẩn (Asia/Ho_Chi_Minh hoặc local), ẩn chuỗi marker kỹ thuật.

### Module D: Kiểm thử và xác minh (Testing & Verification)
1. Viết unit tests & integration tests:
   - Test log Step cũ và mới hiển thị đúng tiếng Việt.
   - Test mô phỏng lượt 3 (round 3) retry upload -> enhance -> download -> clean thành công, không dừng non.
   - Test phân biệt chính xác: duplicate (trùng góc) vs needs_review (seam/preview) vs blocked (lỗi vĩnh viễn).
   - Test round-trip `attempt_name` và `parse_attempt_name` với mọi format (legacy, marker-only, new format).
   - Test sort danh sách listing theo ngày giờ khi Firestore thiếu createdAt.
   - Chạy test suite: `rtk proxy .venv/bin/python -m unittest discover -s tests -v`.
   - Kiểm tra JS: `rtk proxy node --check ui/app.js`.
   - Compileall & git diff check.
