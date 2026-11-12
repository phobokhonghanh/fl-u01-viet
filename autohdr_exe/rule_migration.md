# Quy Tắc Migration Hệ Thống AutoHDR & Autoenhance (autohdr_exe)

Tài liệu này ghi nhận toàn bộ các nguyên tắc làm việc, tiêu chuẩn kiến trúc và quy ước kỹ thuật đã được thống nhất cho quá trình migration/hợp nhất giữa `autohdr_exe` và `hdr_exe` (engine Autoenhance).

---

## 1. Quy Trình Phê Duyệt & Làm Việc (Approval First)
* **Không tự ý can thiệp code:** AI chỉ đóng vai trò phân tích, thiết kế kiến trúc, đề xuất giải pháp và trình bày code mẫu.
* **Chỉ thực hiện khi có lệnh:** Chỉ được tạo file, sửa code hoặc chạy lệnh hệ thống khi người dùng đã xem xét và đưa ra xác nhận chấp thuận rõ ràng (`"ok"`, `"đồng ý"`, `"thực hiện"`).
* **Kiểm thử độc lập:** Mọi module sau khi được tạo/sửa phải được kiểm thử import và chạy thử hàm để chứng minh tính đúng đắn trước khi bàn giao.

---

## 2. Tính Minh Bạch & Nguyên Tắc "Làm Mới Thuần Túy" (Clean & Fresh)
* **Minh bạch về Fallback:** Trong mọi đề xuất, AI bắt buộc phải chủ động giải trình:
  1. Trong code có cơ chế fallback / retry nào không?
  2. Code mới có phụ thuộc, tham chiếu hay đọc/ghi vào file cũ / code cũ nào không?
* **Không mang vác vấn đề cũ:** Không tự ý cài cắm các cơ chế đọc file cũ (legacy migration) hay chắp vá để giữ tương thích ngược nếu không có yêu cầu cụ thể. Toàn bộ code trong migration là mã nguồn mới 100%, độc lập và sạch sẽ.

---

## 3. Không Gian Cách Ly Migration (`1tm`)
* Mọi mã nguồn tái cấu trúc, module mới đều được đặt biệt lập trong thư mục `autohdr_exe/1tm/libs/`.
* Tuyệt đối không can thiệp trực tiếp vào mã nguồn đang chạy của `autohdr_exe` (như `steps/step0_session.py`, `core/pipeline.py`) cho đến khi toàn bộ các module trong `1tm` được kiểm thử hoàn tất và sẵn sàng ráp nối.

---

## 4. Kiến Trúc Hướng Đối Tượng Chuẩn Hóa (OOP & Strategy Pattern)
* Không dùng code thủ tục gom đống hay các câu lệnh `if/else` chắp vá trong 1 file lớn.
* Bắt buộc xây dựng lớp trừu tượng `BaseAuthProvider` và `AuthProviderRegistry` để quản lý các engine:
  * Mỗi engine là một class provider độc lập (`AutoHDRAuthProvider`, `AutoenhanceAuthProvider`).
  * Chuẩn hóa 4 nghiệp vụ cốt lõi: `verify_credentials()`, `login()`, `get_active_session()`, `logout()`.
  * Hỗ trợ chức năng `list_accounts()` và `switch_account(identifier)` để phục vụ việc chuyển đổi tài khoản/key trên giao diện.

---

## 5. Tách Biệt Data Model Session (`auth/session/`)
* Thư mục `auth/session/` là một package riêng biệt quản lý dữ liệu phiên:
  * `session/base.py`: Lớp trừu tượng `BaseSessionRecord`.
  * `session/autohdr.py`: `AutoHDRSessionRecord` (chuyên biệt cho `cookie`, `email`, `user_id`, `firstname`, `lastname`, `expires`).
  * `session/autoenhance.py`: `AutoenhanceSessionRecord` (chuyên biệt cho `api_key`, `name`, `created_at`).
* **Không dùng trường giả:** Record của engine nào chỉ chứa đúng các trường dữ liệu thực tế của engine đó.
* **Đồng nhất giao diện:** Bất kỳ session nào cũng có thuộc tính `display_name` (để vẽ lên combobox/label) và `identifier` (để switch account).

---

## 6. Tách Biệt File I/O Dùng Chung (`utils/io.py`)
* Toàn bộ thao tác mở file, `json.load`, `json.dump`, xử lý ngoại lệ I/O, tạo thư mục cha tự động (`ensure_parent_dir`) và xóa file (`delete_file`) được gom tập trung tại `1tm/libs/utils/io.py`.
* Các Provider tuyệt đối không viết lặp lại cú pháp mở file cấp thấp.

---

## 7. Quản Lý Tập Trung Endpoints & Hằng Số (`constants/`)
* Toàn bộ URL, API Base, Endpoints, tên file lưu trữ, timeouts... phải được đặt trong thư mục `1tm/libs/constants/`:
  * `constants/autohdr.py`: Hằng số của AutoHDR.
  * `constants/autoenhance.py`: Hằng số của Autoenhance.
* Tuyệt đối không hardcode chuỗi URL hay tên file bên trong code logic.

---

## 8. Quy Tắc Lưu Trữ Session (File riêng theo từng Engine)
* **Không gộp nhiều schema vào một file:** Để tránh lỗi crash và giảm thiểu rủi ro hỏng dữ liệu chéo (blast radius).
* Mỗi Engine có file lưu trữ riêng trong thư mục `sessions/`:
  * AutoHDR: `sessions/autohdr.json`
  * Autoenhance: `sessions/autoenhance.json`
* Mỗi file quản lý trạng thái tài khoản đang hoạt động bằng cờ `"is_active": true/false`.

---

## 9. Phân Định Ranh Giới `core.cache`
* `core.cache` chỉ thuần túy lưu cài đặt và trạng thái hiển thị của UI (`download_dir`, `model_name`, `address`...).
* `core.cache` hoàn toàn không tham gia vào việc lưu trữ session/credentials của Auth.

