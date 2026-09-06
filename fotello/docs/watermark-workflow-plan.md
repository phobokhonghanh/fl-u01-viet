# Kế hoạch tích hợp xóa watermark vào hai workflow

Đây là kế hoạch thiết kế đã được duyệt. Hướng dẫn sử dụng phần tích hợp nằm trong [watermark-workflow.md](watermark-workflow.md); kiểm thử tự động dùng API giả lập, chưa xác nhận với job dịch vụ thật.

## Hành vi cần đạt

- WF1: tạo hai lượt xử lý ban đầu cho cùng tập nhóm ảnh gốc; cả hai được gửi xử lý trước khi chờ kết quả. Tải riêng từng lượt, ghép từng ảnh tương ứng, chỉ tạo lượt tiếp theo cho các ảnh chưa đủ bản để xóa watermark. Lặp đến khi hoàn tất hoặc người dùng dừng.
- WF2: tải các listing được chọn, nhận diện những listing thuộc cùng một lần chạy gốc, ghép các bản tương ứng bằng cùng bộ điều phối xóa watermark với WF1.
- Tên lượt tăng `abc01`, `abc02`, `abc03`…; ảnh retry giữ nguyên định danh và tên đầu ra ban đầu. Không đánh số lại ảnh trong tập retry.
- Bracket 1/3/5 được cố định lúc bắt đầu. Retry luôn gửi lại toàn bộ nhóm input đã tạo ra output cần xử lý.
- Chỉ báo hoàn tất khi toàn bộ ảnh mục tiêu đạt điều kiện hoàn tất; giữ nguyên ảnh thô và các kết quả đã thành công khi còn ảnh thiếu hoặc khi dừng.

## Hiện trạng đã kiểm tra

- `backend/service.py::fotello_upload_and_enhance`: natural-sort input, chia bracket và chunk; upload một lần, tạo listing/enhance, poll rồi download. Chưa lưu mapping bền vững giữa nhóm bracket, enhance và file tải về.
- Cuối workflow trên đang truyền toàn bộ `enhance_ids` cho mỗi listing; fallback có thể tải chéo listing. Cần sửa cùng phần quản lý lượt/chunk.
- `backend/downloads.py::fotello_list_enhances_for_listing`: hiện chỉ lấy tên input đầu tiên từ `inputFilenames`, bỏ phần còn lại của nhóm bracket.
- `fotello_download_listing`: ưu tiên ZIP, giải nén theo basename; fallback có cách đặt tên khác. ZIP không cung cấp mapping đáng tin cậy qua thứ tự file.
- `fotello_batch_download`: tách thư mục listing nhưng chưa có bước ghép giữa các listing.
- `main.py::FotelloJob`: trạng thái job chỉ nằm trong bộ nhớ, chỉ có số upload/download. Hai API workflow hiện có thể báo success dù chưa đủ ảnh cần thiết.
- Cleaner hiện có thể trả `success=True, status="preview"`; `success` đơn lẻ không phải tiêu chí đã xóa watermark hoàn tất.

## 1. Định danh và lưu trạng thái

Thêm `backend/watermark_workflow/` gồm models, manifest store, cleaner adapter và coordinator. Giữ backend tải ảnh và cleaner là các thành phần riêng được coordinator gọi.

Lưu manifest có version tại thư mục dữ liệu ứng dụng và bản sao trong output; cập nhật atomically sau các bước tạo listing, tạo enhance, tải ảnh và ghép ảnh. Một writer quản lý mỗi nhóm workflow, tránh ghi đè từ các worker.

Mỗi workflow có `family_id` duy nhất, tên hiển thị gốc, tài khoản/team, thời điểm tạo, snapshot preferences và bracket size. Mỗi output có `output_id` bất biến, thứ tự gốc, tên đầu ra, danh sách input theo đúng thứ tự, dấu nhận dạng file và upload IDs. Mỗi lượt lưu attempt number, từng chunk/listing ID và mapping enhance ID → output ID → file tải về → trạng thái.

Tên prefix dùng để tìm/chọn nhóm; không dùng riêng prefix làm bằng chứng các ảnh tương ứng. Tách các lần chạy khác nhau có cùng tên bằng family ID. Tên remote giữ số lượt và dấu nhận diện family để hỗ trợ khôi phục/chọn manual; không yêu cầu API hỗ trợ trường metadata mới chưa được xác minh.

Lưu quan hệ ngay khi nhận ID từ API. Nếu request tạo tài nguyên timeout sau khi server có thể đã nhận, đánh dấu cần đối soát trước khi gửi lại để tránh tạo trùng. Khả năng idempotency/dò lại tài nguyên phụ thuộc API, cần xác minh.

## 2. Nhóm bracket và tên đầu ra

Tạo một bảng nhóm cố định từ danh sách input natural-sort duy nhất. Không chia bracket lại từ danh sách file đã lọc khi retry.

| Output cố định | Bracket 1 | Bracket 3 | Bracket 5 |
| --- | --- | --- | --- |
| img01 | input 01 | input 01–03 | input 01–05 |
| img02 | input 02 | input 04–06 | input 06–10 |
| img03 | input 03 | input 07–09 | input 11–15 |

Nếu chỉ img02 cần lượt 3, abc03 chỉ chứa nhóm của img02 và vẫn xuất img02. Với bracket 5 đó là input 06–10.

Kiểm tra đủ input trong từng nhóm và upload thành công toàn nhóm trước khi tạo enhance. Đề xuất báo lỗi input không chia hết bracket trước khi upload, thay vì âm thầm gửi nhóm cuối thiếu ảnh. Kiểm tra trùng tên output sau chuẩn hóa tên và phần mở rộng.

Tên output được chốt một lần: giữ stem ảnh gốc khi có tên xác định; bracket nhiều ảnh dùng stem của input đầu nhóm theo quy ước hiện có, lưu toàn bộ nhóm trong manifest. Ví dụ img01/img02 là tên logic đã chốt, không phải thứ tự enhance trả về. Cleaner xuất PNG thật, ví dụ `img01.png`.

## 3. Tải ảnh có mapping rõ ràng

Chuẩn hóa kết quả tải thành record gồm family, lượt, chunk, listing, enhance, output ID, input filenames, loại ảnh/độ phân giải và local path.

Ưu tiên tải từng enhance cho workflow mới để mapping chắc chắn. Chỉ dùng ZIP khi có cách đối chiếu entry với enhance/input không mơ hồ; không ghép theo vị trí ZIP hoặc vị trí query. Mỗi listing chỉ tải enhance thuộc chính listing đó. Chọn cùng loại rendition cho các bản của một output, tránh một bản upsized và một bản thường.

Giữ file thô trong các thư mục lượt riêng, chia thêm theo chunk nếu cần. Kết quả cuối nằm riêng:

```text
<output>/<family>/
  manifest.json
  raw/abc01/img01.jpg ... img05.jpg
  raw/abc02/img01.jpg ... img05.jpg
  raw/abc03/img02.jpg
  clean/img01.png ... img05.png
  reports/img01.json
  attempts/...
```

## 4. Điều phối WF1

1. Validate input, chốt nhóm bracket/preferences/tên output và lưu manifest.
2. Upload input và lưu ID. Xác minh khả năng tái sử dụng upload ID cho nhiều enhance; nếu API không cho phép, upload lại chính nhóm input cần dùng.
3. Tạo abc01 và abc02 cho toàn bộ nhóm, đưa cả hai lượt vào xử lý trước khi poll. Giới hạn concurrency dùng chung để không nhân số worker theo lượt/chunk. Giữ nguyên giới hạn bracket mỗi listing và chính sách license hiện có.
4. Poll/download theo từng enhance với mapping cụ thể; xử lý từng output ngay khi đủ bản của chính nó.
5. Dùng cleaner adapter để thử cặp bản mới với các bản cũ hợp lệ. Không mặc định chỉ so với lượt ngay trước. Không ghép tất cả các bản trùng nhau vào một consensus thiếu cân bằng.
6. Đánh dấu hoàn tất khi ghép đạt tiêu chí. Chỉ đưa các output còn cần bản khác vào abc03, abc04…; mỗi lượt chia chunk theo các nhóm bracket nguyên vẹn.
7. Lỗi mạng tải lại từ enhance có sẵn. Chỉ lỗi thiếu bản phù hợp mới tạo lượt enhance mới. Lỗi mapping/config/input không được biến thành retry tạo job vô hạn.
8. Hỗ trợ dừng trong lúc upload, tạo enhance, poll, download và giữa các lần clean. Lưu checkpoint để có thể tiếp tục hoặc tải manual. Không tạo remote job mới sau khi nhận yêu cầu dừng.

Theo yêu cầu, không đặt giới hạn số lượt cho trường hợp vẫn trùng watermark; timeout từng request/lượt không đồng nghĩa ảnh đã hoàn tất. Không suy ra rằng góc watermark phân bố đều hoặc chắc chắn sẽ đổi sau một số lượt cố định.

## 5. Cleaner adapter và điều kiện hoàn tất

Tái sử dụng validator, detector và compositor hiện có. So sánh nhiều bản cùng output; không giả định detector hiện tại nhận diện được góc watermark từ một ảnh độc lập.

Thêm bước `compare_variant_pair` trước khi ghép, phân biệt cặp cùng góc nhưng watermark khác nét/bytes với cặp thực sự khác góc. Kết quả phải chỉ rõ nguồn watermark của từng bản, vùng sạch thay thế và mức chắc chắn. Không chỉ đếm số ROI khác nhau: cấu hình chiều cao ROI 0.52 làm vùng trên/dưới chồng lấn, có thể đếm một vùng thay đổi hai lần. Kiểm chứng probe bằng fixture cùng góc, khác góc và vùng chồng lấn trước khi dùng làm điều kiện tạo retry/chốt output. Trường hợp chưa phân biệt được phải trả trạng thái chưa chắc chắn.

- `need_variant`: thiếu hai bản, bản trùng hoặc chưa đủ vùng sạch; đưa vào tập cần lượt bổ sung.
- `cleaned`: cleaner thành công, status complete, completion 100%, có output hợp lệ và không có cảnh báo ảnh hưởng chất lượng.
- `preview`: lưu riêng bản xem thử/cảnh báo; không đếm là cleaned. Thử cặp khác có sẵn. Thiếu bằng chứng về vùng sạch có thể cần thêm bản cho riêng output đó; cảnh báo đường ghép dù đã có cặp khác góc chuyển needs_review, không tự tạo vòng retry vô hạn.
- `blocked`: sai mapping, khác nguồn ảnh, sai kích thước/mode không xử lý được, thiếu input gốc hoặc lỗi cấu hình. Báo nguyên nhân và giữ checkpoint; không coi là trùng góc để tạo job liên tục.
- Lỗi download/read/write được phân loại riêng, có retry I/O thích hợp.

Mỗi lần thử cặp dùng thư mục tạm riêng. `clean_case` hiện xóa output cũ trước khi chạy, vì vậy không gọi trực tiếp lên file clean đã hoàn tất. Chỉ publish kết quả đạt yêu cầu bằng atomic replace; giữ report của từng lần thử. Bắt đầu với một worker clean để giới hạn RAM ảnh lớn.

Điểm cần kiểm chứng thực tế: hai enhance từ cùng input/preferences có thể khác nhau ngoài watermark. Validator đang kiểm tra cùng nguồn bằng nội dung ảnh. Nếu dịch vụ tạo ảnh khác nội dung/hình học giữa các lượt, không nới ngưỡng tùy tiện để ép ghép; đây là giới hạn cần xử lý riêng.

## 6. WF2: tải manual và ghép

Lấy đủ `inputFilenames`, thông tin listing/enhance và các metadata khả dụng. Ưu tiên manifest để phục hồi mapping; với listing cũ, chỉ ghép khi family và toàn bộ nhóm input có thể xác định không mơ hồ. Không suy ra tương ứng chỉ từ tên img01 hoặc thứ tự file. Nhóm không đủ metadata được báo cần đối chiếu.

UI hỗ trợ chọn tất cả các lượt cùng family, hiển thị prefix và lượt để người dùng nhận ra abc01/02/03. Sau khi tải, gom biến thể theo output ID và dùng chung cleaner adapter. Retry subset vẫn trở về đúng output ban đầu.

Giả định hiện tại, chưa có phản hồi cho câu hỏi làm rõ: WF2 chỉ tải/ghép các job đã chọn, xuất danh sách output còn thiếu để chọn thêm lượt. Tự tạo remote job bổ sung từ WF2 là tùy chọn cần chốt nếu người dùng muốn; lựa chọn này không ảnh hưởng thiết kế mapping/cleaner dùng chung.

## 7. UI và kết quả workflow

Mở rộng kết quả backend từ số đếm đơn thành summary có target count, downloaded variants, cleaned count, pending, preview, failed và output paths. Cập nhật `main.py`, `ui/app.js`, `ui/index.html`, `ui/style.css` để hiển thị lượt hiện tại, số ảnh sạch/tổng và số ảnh còn chờ.

Chỉ dùng success khi đủ ảnh cleaned. Phân biệt partial, stopped và failed; vẫn cho mở thư mục khi có kết quả một phần. Tên lượt remote nằm trong một job tổng trên ứng dụng, tránh biến mỗi lần retry thành job UI không liên quan.

## 8. Thứ tự triển khai và kiểm thử

1. Models/manifest/grouping và test bracket, retry subset, naming, persistence.
2. Download records, metadata đầy đủ, tải riêng listing/enhance, thống nhất rendition/tên file và kiểm thử ZIP/fallback.
3. Cleaner adapter, phân loại lỗi, thử cặp, publish atomic và test preview/duplicate/source mismatch.
4. Coordinator WF1: hai lượt đầu cùng xử lý, retry phần thiếu, cancellation và checkpoint.
5. WF2 grouping/cleanup và UI/summary của cả hai workflow.
6. Chạy test hồi quy cleaner và test workflow bằng fake API; sau đó kiểm tra live với tập ảnh nhỏ cho bracket 1, 3, 5.

Test nghiệm thu bắt buộc: 5 output ban đầu, lượt 2 chỉ img02/img04 còn trùng, lượt 3 chỉ gửi hai nhóm đó, lượt 4 chỉ còn img04; cuối cùng có đúng 5 tên output gốc. Lặp kịch bản với bracket 3/5 và nhiều chunk, đảo thứ tự kết quả API/download để chứng minh không phụ thuộc thứ tự.

Các ca khác: tất cả bản trùng nhiều lượt rồi dừng; cùng góc nhưng watermark khác bytes; ROI chồng lấn không tạo nhận diện khác góc giả; thiếu/failed enhance; upload thiếu một file trong bracket; input cuối thiếu bracket; hai family cùng prefix; trùng basename; một bản upsized; khởi động lại rồi tải manual; chọn manual thiếu lượt; source mismatch; preview không bị báo success; không ghi đè ảnh clean bởi lần thử lỗi.

Kiểm chứng live cần xác nhận tái sử dụng upload IDs, độ ổn định nội dung qua các lượt và metadata remote. Chưa thực hiện các kiểm chứng này trong bước lập kế hoạch.
